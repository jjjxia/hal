#!/usr/bin/env python3
"""E5 evaluation-side reference for case chacha_r12 (secworks/chacha, top chacha_core): ChaCha12, 256-bit key.

Documented algorithm, not a transcription of the RTL: the ChaCha block function of D. J. Bernstein ("ChaCha, a
variant of Salsa20", 2008) with 12 rounds (6 double rounds: column round then diagonal round), 256-bit key,
constants "expand 32-byte k", ORIGINAL layout with a 64-bit block counter in state words 12-13 and a 64-bit
nonce (IV) in words 14-15 (the core's layout; RFC 7539 uses a 32-bit counter and a 96-bit nonce in the same
words). Output of one transaction: data_out = data_in XOR keystream block, i.e. ONE 64-byte block of ChaCha
encryption at block counter `ctr` (the core increments the counter only on `next`, which is never asserted here).

Port conventions (chacha_core.v:94-97, 327-368, 340-342, 505-518, 584-585):
  key  = int.from_bytes(32-byte key, "big")      (key[255:248] is key byte 0; the core byte-swaps each word);
  iv   = int.from_bytes(8-byte nonce, "big");
  ctr  = 64-bit block counter as an integer: ctr[31:0] -> state word 12, ctr[63:32] -> state word 13;
  data_out = data_in XOR int.from_bytes(64-byte keystream block, "big")  (data_in[511:504] meets keystream byte 0).
Protocol timing used by trial(): init in cycle 0 latches ctr (cycle 0); the core reads key/iv in cycle 1 (state
init) AND again combinationally in the finalize cycle 14 (chacha_core.v:505, init_state_word + state), and
data_in in cycle 14; hence data_mode "hold": every data pad keeps one value for the whole transaction, and
per_cycle lists all cycles 0..period-1. data_out is observed at cycle 15 (observe.json).

Validation (self_test, run at import): draft-strombergson-chacha-test-vectors (256-bit key cases TC1 with 8/12/20
rounds, TC2 and TC7 with 8 rounds; transcribed from the core's src/tb/tb_chacha_core.v), RFC 7539 sec. 2.3.2
(block function, key 00..1f, counter 1, nonce 00:00:00:09:00:00:00:4a:00:00:00:00, mapped into the 64/64 layout)
and RFC 7539 A.1 test vector #2 (all-zero key and nonce, counter 1).

trial(rng, contract) -> (per_cycle, expected) with the conventions of c3/check_reference.py.
"""
import struct

ROUNDS = 12
PERIOD = 15
M32 = (1 << 32) - 1


def _rotl(x, n):
    return ((x << n) | (x >> (32 - n))) & M32


def _qr(s, a, b, c, d):
    s[a] = (s[a] + s[b]) & M32; s[d] = _rotl(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & M32; s[b] = _rotl(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & M32; s[d] = _rotl(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & M32; s[b] = _rotl(s[b] ^ s[c], 7)


def chacha_block(key32, counter, nonce8, rounds=ROUNDS):
    """64-byte ChaCha keystream block, 256-bit key, 64-bit counter (words 12-13), 64-bit nonce (words 14-15)."""
    assert len(key32) == 32 and len(nonce8) == 8 and rounds % 2 == 0
    state = list(struct.unpack("<4I", b"expand 32-byte k")) + list(struct.unpack("<8I", key32)) + \
        [counter & M32, (counter >> 32) & M32] + list(struct.unpack("<2I", nonce8))
    x = list(state)
    for _ in range(rounds // 2):
        _qr(x, 0, 4, 8, 12); _qr(x, 1, 5, 9, 13); _qr(x, 2, 6, 10, 14); _qr(x, 3, 7, 11, 15)  # column round
        _qr(x, 0, 5, 10, 15); _qr(x, 1, 6, 11, 12); _qr(x, 2, 7, 8, 13); _qr(x, 3, 4, 9, 14)  # diagonal round
    return struct.pack("<16I", *[(a + b) & M32 for a, b in zip(x, state)])


def data_out_port(key, iv, ctr, data_in, rounds=ROUNDS):
    ks = chacha_block(key.to_bytes(32, "big"), ctr, iv.to_bytes(8, "big"), rounds)
    return data_in ^ int.from_bytes(ks, "big")


# (key port, iv port, rounds, keystream block as the 512-bit data_out with data_in = 0); ctr = 0
DRAFT_VECTORS = [
    (0, 0, 8, "3e00ef2f895f40d67f5bb8e81f09a5a12c840ec3ce9a7f3b181be188ef711a1e984ce172b9216f419f445367456d5619314a42a3da86b001387bfdb80e0cfe42"),
    (0, 0, 12, "9bf49a6a0755f953811fce125f2683d50429c3bb49e074147e0089a52eae155f0564f879d27ae3c02ce82834acfa8c793a629f2ca0de6919610be82f411326be"),
    (0, 0, 20, "76b8e0ada0f13d90405d6ae55386bd28bdd219b8a08ded1aa836efcc8b770dc7da41597c5157488d7724e03fb8d84a376a43b8f41518a11cc387b669b2ee6586"),
    (1 << 248, 0, 8, "cf5ee9a0494aa9613e05d5ed725b804b12f4a465ee635acc3a311de8740489ea289d04f43c7518db56eb4433e498a1238cd8464d3763ddbb9222ee3bd8fae3c8"),
    (0x00112233445566778899aabbccddeeffffeeddccbbaa99887766554433221100, 0x0f1e2d3c4b596877, 8,
     "60fdedbd1a280cb741d0593b6ea0309010acf18e1471f68968f4c9e311dca149b8e027b47c81e0353db013891aa5f68ea3b13dd2f3b8dd0873bf3746e7d6c567"),
]
RFC7539_232 = ("10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e"
               "d2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e")
RFC7539_A1_2 = ("9f07e7be5551387a98ba977c732d080dcb0f29a048e3656912c6533e32ee7aed"
                "29b721769ce64e43d57133b074d839d531ed1f28510afb45ace10a1f4b794d6f")


def self_test():
    n = 0
    for key, iv, rounds, ks in DRAFT_VECTORS:
        assert data_out_port(key, iv, 0, 0, rounds) == int(ks, 16), f"draft vector (rounds {rounds}) failed"
        n += 1
    # RFC 7539 2.3.2: 32-bit counter 1 in word 12; nonce words 13..15 = 0x09000000, 0x4a000000, 0
    key = bytes(range(32))
    assert chacha_block(key, 1 | (0x09000000 << 32), bytes.fromhex("0000004a00000000"), 20).hex() == RFC7539_232
    assert chacha_block(bytes(32), 1, bytes(8), 20).hex() == RFC7539_A1_2
    # data_in XOR path at port level: encrypting with data_in = keystream gives zero
    ks = data_out_port(int.from_bytes(key, "big"), 0x0102030405060708, 7, 0, ROUNDS)
    assert data_out_port(int.from_bytes(key, "big"), 0x0102030405060708, 7, ks, ROUNDS) == 0
    return n + 2


def trial(rng, contract):
    vals = {"key": rng.getrandbits(256), "iv": rng.getrandbits(64), "ctr": rng.getrandbits(64),
            "data_in": rng.getrandbits(512)}
    period = contract.get("period_cycles", PERIOD) if isinstance(contract, dict) else PERIOD
    per_cycle = {c: dict(vals) for c in range(period)}  # data_mode hold
    return per_cycle, {"data_out": data_out_port(vals["key"], vals["iv"], vals["ctr"], vals["data_in"])}


self_test()

if __name__ == "__main__":
    print(f"chacha_r{ROUNDS} reference: {self_test()} published vectors passed "
          f"(draft-strombergson TC1 r8/r12/r20, TC2 r8, TC7 r8 with 256-bit keys; RFC 7539 2.3.2 and A.1 #2)")
