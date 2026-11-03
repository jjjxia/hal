#!/usr/bin/env python3
"""E5 evaluation-side reference for case sha256_oc (OpenCores sha_core by marsgod, rtl/sha256.v, top sha256).

Documented algorithm, not a transcription of the RTL: the FIPS 180-4 SHA-256 compression function (sec. 6.2.2,
steps 1-4) applied to ONE 512-bit message block, starting from the SHA-256 initial hash value H(0) (sec. 5.3.3).
K0..K63 and H(0) are derived here from their FIPS 180-4 definitions (first 32 bits of the fractional parts of
the cube roots of the first 64 primes, resp. of the square roots of the first 8 primes), not copied from the RTL.
The core leaves padding and length encoding to the caller (the command "first block" = cmd_i 3'b010 starts from
H(0); 3'b110 would continue a chain), so the reference is the core-level function
digest = H(0) + compress(H(0), W0..W15) of one block.

Port conventions (same 32-bit command interface as the frozen sha1 case):
  text_i carries the message words W0..W15 (W0 first, each word big-endian as in FIPS 180-4), one per cycle;
  after the read command text_o carries H0..H7 (H0 first), one word per cycle.
Protocol timing used by trial() (measured on the RTL, see README.md): the "first block" command written in
transaction cycle 0 takes effect at cycle 1; rounds 0..15 then latch text_i, so word j is sampled in cycle 1 + j
(data_mode "cycle": every other cycle carries random data). The read command is written in cycle 68 and the
digest words are observed at cycles 71..78 (observe.json), i.e. the k-th observation of text_o is Hk.

trial(rng, contract) -> (per_cycle, expected) with the conventions of c3/check_reference.py.
"""
import hashlib
import struct

M32 = (1 << 32) - 1


def _primes(n):
    out, k = [], 2
    while len(out) < n:
        if all(k % p for p in out if p * p <= k):
            out.append(k)
        k += 1
    return out


def _iroot(x, e):
    """floor(x ** (1/e)) for integers (Newton iteration, exact)."""
    r = 1 << ((x.bit_length() + e - 1) // e)
    while True:
        s = ((e - 1) * r + x // r ** (e - 1)) // e
        if s >= r:
            return r
        r = s


# FIPS 180-4 sec. 4.2.2 and 5.3.3: fractional parts of cube / square roots of the first primes.
K = [_iroot(p << 96, 3) & M32 for p in _primes(64)]
H0 = [_iroot(p << 64, 2) & M32 for p in _primes(8)]
assert K[0] == 0x428A2F98 and K[63] == 0xC67178F2 and H0[0] == 0x6A09E667 and H0[7] == 0x5BE0CD19


def _rotr(x, n):
    return ((x >> n) | (x << (32 - n))) & M32


def sha256_compress(words, h=H0):
    """FIPS 180-4 sec. 6.2.2: one block (16 x 32-bit words, W0 first) -> new hash value (8 words)."""
    w = list(words)
    for t in range(16, 64):
        s0 = _rotr(w[t - 15], 7) ^ _rotr(w[t - 15], 18) ^ (w[t - 15] >> 3)
        s1 = _rotr(w[t - 2], 17) ^ _rotr(w[t - 2], 19) ^ (w[t - 2] >> 10)
        w.append((w[t - 16] + s0 + w[t - 7] + s1) & M32)
    a, b, c, d, e, f, g, hh = h
    for t in range(64):
        t1 = (hh + (_rotr(e, 6) ^ _rotr(e, 11) ^ _rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[t] + w[t]) & M32
        t2 = ((_rotr(a, 2) ^ _rotr(a, 13) ^ _rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) & M32
        hh, g, f, e, d, c, b, a = g, f, e, (d + t1) & M32, c, b, a, (t1 + t2) & M32
    return [(x + y) & M32 for x, y in zip(h, (a, b, c, d, e, f, g, hh))]


def block_port(block64):
    return int.from_bytes(block64, "big")


def digest_port(words):
    out = 0
    for x in words:
        out = (out << 32) | x
    return out


def words_of_block_port(block):
    return [(block >> (32 * (15 - j))) & M32 for j in range(16)]


def self_test():
    """Single-block messages of several lengths (incl. FIPS 180-4 example "abc") against hashlib.sha256."""
    msgs = [b"", b"abc", b"NetNotary E5 SHA-256 single-block self test", bytes(range(55))]
    for msg in msgs:
        assert len(msg) <= 55
        blk = msg + b"\x80" + b"\x00" * (55 - len(msg)) + struct.pack(">Q", 8 * len(msg))
        got = digest_port(sha256_compress(words_of_block_port(block_port(blk))))
        assert got == int.from_bytes(hashlib.sha256(msg).digest(), "big"), f"SHA-256 self test failed for {msg!r}"
    # FIPS 180-4 / NIST example "abc"
    assert hashlib.sha256(b"abc").hexdigest() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    # two-block message: chaining with an explicit H (checks the compression function itself, not only H(0))
    msg = bytes(range(100))
    pad = msg + b"\x80" + b"\x00" * ((55 - len(msg)) % 64) + struct.pack(">Q", 8 * len(msg))
    h = H0
    for i in range(0, len(pad), 64):
        h = sha256_compress(struct.unpack(">16I", pad[i:i + 64]), h)
    assert digest_port(h) == int.from_bytes(hashlib.sha256(msg).digest(), "big")
    return len(msgs) + 1


def trial(rng, contract):
    words = [rng.getrandbits(32) for _ in range(16)]
    per_cycle = {1 + j: {"text_i": words[j]} for j in range(16)}
    return per_cycle, {("text_o", k): v for k, v in enumerate(sha256_compress(words))}


self_test()

if __name__ == "__main__":
    n = self_test()
    print(f"sha256_oc reference: {n} hashlib.sha256 checks passed (single-block lengths 0, 3, 44, 55 and one 2-block chain)")
