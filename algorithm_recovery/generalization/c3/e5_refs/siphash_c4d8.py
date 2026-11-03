#!/usr/bin/env python3
"""E5 evaluation-side reference for case siphash_c4d8 (secworks/siphash, top siphash_core): SipHash-4-8.

Documented algorithm, not a transcription of the RTL: SipHash-c-d of Aumasson and Bernstein ("SipHash: a fast
short-input PRF", INDOCRYPT 2012, sec. 2) with c = 4 compression rounds and d = 8 finalization rounds, 64-bit
output. The initialization constants are the ASCII string "somepseudorandomlygeneratedbytes" as in the paper.
The core leaves message splitting, padding and the length byte to the caller (README.md: "the core does not
know the length of the message and does not perform final padding"), so the reference is the CORE-LEVEL function
on given message words:
    v0..v3 = init(k0, k1);  for each word m: v3 ^= m; c SipRounds; v0 ^= m;
    v2 ^= 0xff; d SipRounds; return v0 ^ v1 ^ v2 ^ v3.
One transaction compresses exactly ONE message word.  For a message of L <= 7 bytes the standard SipHash value
equals this function on the single word m = (L << 56) | int.from_bytes(msg, "little"); self_test() uses this.

Port conventions (siphash_core.v:254-264, 269, 145; repo tb/tb_siphash_core.v:235, 250):
  key = int.from_bytes(16-byte key, "little"), i.e. k0 = key[63:0], k1 = key[127:64];
  mi  = int.from_bytes(8-byte message block, "little");
  siphash_word = {siphash_word1_reg, siphash_word0_reg}; with long = 0 only word 0 is written (FINAL0_END), so the
  observed 128-bit port value is the 64-bit tag in bits [63:0] and 0 in bits [127:64] (reset value, never written).
Protocol timing used by trial(): initalize in cycle 0 latches key in cycle 0; compress in cycle 1 latches mi in
cycle 1 (data_mode "cycle": all other cycles carry random data); the tag is observed at cycle 17 (observe.json).

Validation (self_test, run at import): SipHash-2-4 against the reference implementation's published vectors
(vectors.h, key 00..0f, messages 00, 00 01, ...; lengths 0..8, also reproduced in the core's
src/model/short_test_vectors.txt) and the paper's Appendix A vector (15-byte message, a129ca6149be45e5);
SipHash-1-3 against CPython 3.12 hash() of bytes (sys.hash_info.algorithm == "siphash13"; PYTHONHASHSEED=0 gives the
all-zero key), values recorded below and re-checked live by `python3 ref.py`. No published SipHash-4-8 vectors are
known to us; that configuration shares the same parameterized code.

trial(rng, contract) -> (per_cycle, expected) with the conventions of c3/check_reference.py.
"""
import os
import subprocess
import sys

C_ROUNDS, D_ROUNDS = 4, 8
M64 = (1 << 64) - 1
_CONST = [int.from_bytes(b"somepseudorandomlygeneratedbytes"[8 * i:8 * i + 8], "big") for i in range(4)]


def _rotl(x, n):
    return ((x << n) | (x >> (64 - n))) & M64


def sipround(v0, v1, v2, v3):
    v0 = (v0 + v1) & M64; v1 = _rotl(v1, 13); v1 ^= v0; v0 = _rotl(v0, 32)
    v2 = (v2 + v3) & M64; v3 = _rotl(v3, 16); v3 ^= v2
    v0 = (v0 + v3) & M64; v3 = _rotl(v3, 21); v3 ^= v0
    v2 = (v2 + v1) & M64; v1 = _rotl(v1, 17); v1 ^= v2; v2 = _rotl(v2, 32)
    return v0, v1, v2, v3


def siphash_words(k0, k1, words, c=C_ROUNDS, d=D_ROUNDS):
    """Core-level SipHash-c-d: init, compress the given 64-bit words, finalize (64-bit output)."""
    v0, v1, v2, v3 = k0 ^ _CONST[0], k1 ^ _CONST[1], k0 ^ _CONST[2], k1 ^ _CONST[3]
    for m in words:
        v3 ^= m
        for _ in range(c):
            v0, v1, v2, v3 = sipround(v0, v1, v2, v3)
        v0 ^= m
    v2 ^= 0xFF
    for _ in range(d):
        v0, v1, v2, v3 = sipround(v0, v1, v2, v3)
    return v0 ^ v1 ^ v2 ^ v3


def siphash_bytes(key16, msg, c=C_ROUNDS, d=D_ROUNDS):
    """Standard SipHash-c-d of a byte string (paper sec. 2: 8-byte little-endian words, last word with length byte)."""
    k0, k1 = int.from_bytes(key16[:8], "little"), int.from_bytes(key16[8:], "little")
    n = len(msg) // 8
    words = [int.from_bytes(msg[8 * i:8 * i + 8], "little") for i in range(n)]
    words.append(((len(msg) & 0xFF) << 56) | int.from_bytes(msg[8 * n:], "little"))
    return siphash_words(k0, k1, words, c, d)


# SipHash-2-4, key 00 01 .. 0f, message 00 01 .. (L-1): reference implementation vectors.h (vectors_sip64)
VECTORS_2_4 = {0: 0x726FDB47DD0E0E31, 1: 0x74F839C593DC67FD, 2: 0x0D6C8009D9A94F5A, 3: 0x85676696D7FB7E2D,
               4: 0xCF2794E0277187B7, 5: 0x18765564CD99A68D, 6: 0xCBC9466E58FEE3CE, 7: 0xAB0200F58B01D137,
               8: 0x93F5F5799A932462, 15: 0xA129CA6149BE45E5}  # 15: paper Appendix A
# SipHash-1-3, all-zero key, message 00 01 .. (L-1): CPython 3.12.3 hash(bytes) with PYTHONHASHSEED=0
VECTORS_1_3 = {1: 0x68A914128E01E473, 2: 0x010BAC45C41E3669, 3: 0x4D4C9A4A8EF6E0AD, 4: 0x7CC43F98813E4DBD,
               5: 0x5ABE2169DFF36275, 6: 0xE3C25F87624F1CDB, 7: 0x2F098AB0C751325A, 8: 0xEAD411E67EBE2EEA,
               15: 0xF30EB725BB91C9EA}


def self_test():
    key = bytes(range(16))
    kport = int.from_bytes(key, "little")
    n = 0
    for L, v in VECTORS_2_4.items():
        assert siphash_bytes(key, bytes(range(L)), 2, 4) == v, f"SipHash-2-4 vector L={L} failed"
        n += 1
        if L <= 7:  # the exact transaction shape: one word, port-level conventions
            m = (L << 56) | int.from_bytes(bytes(range(L)), "little")
            assert siphash_words(kport & M64, kport >> 64, [m], 2, 4) == v
    for L, v in VECTORS_1_3.items():
        assert siphash_bytes(bytes(16), bytes(range(L)), 1, 3) == v, f"SipHash-1-3 vector L={L} failed"
        n += 1
    return n


def cpython_crosscheck(trials=200):
    """Live check of SipHash-1-3 against this interpreter's hash() (zero key via PYTHONHASHSEED=0; note that
    python -I / -E would ignore PYTHONHASHSEED, hence -S)."""
    import random
    rng = random.Random(1)
    msgs = [bytes(rng.getrandbits(8) for _ in range(rng.randrange(1, 40))) for _ in range(trials)]
    code = "import sys; print(sys.hash_info.algorithm); [print(hash(bytes.fromhex(l.strip())) & (2**64-1)) for l in sys.stdin]"
    env = dict(os.environ, PYTHONHASHSEED="0")
    out = subprocess.run([sys.executable, "-S", "-c", code], input="\n".join(m.hex() for m in msgs), env=env,
                         capture_output=True, text=True, check=True).stdout.split()
    if out[0] != "siphash13":
        return f"skipped: hash algorithm is {out[0]}"
    bad = sum(int(h) != siphash_bytes(bytes(16), m, 1, 3) for m, h in zip(msgs, out[1:]))
    assert bad == 0, f"{bad} CPython siphash13 mismatches"
    return f"{trials}/{trials} random messages (1..39 bytes) equal CPython hash() (siphash13, zero key)"


def trial(rng, contract):
    key, m = rng.getrandbits(128), rng.getrandbits(64)
    per_cycle = {0: {"key": key}, 1: {"mi": m}}
    return per_cycle, {"siphash_word": siphash_words(key & M64, key >> 64, [m])}


self_test()

if __name__ == "__main__":
    print(f"siphash_c{C_ROUNDS}d{D_ROUNDS} reference: {self_test()} published/CPython vectors passed "
          f"(SipHash-2-4 lengths 0-8 and 15; SipHash-1-3 lengths 1-8 and 15)")
    print("CPython cross-check:", cpython_crosscheck())
