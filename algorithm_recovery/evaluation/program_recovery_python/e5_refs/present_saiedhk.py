"""Evaluation-side reference for case `present_saiedhk` (Python stdlib only).

Documented algorithm: PRESENT-80 encryption (Bogdanov et al., CHES 2007): 31 rounds of addRoundKey, sBoxLayer and
pLayer, then a final addRoundKey with K32; key schedule: rotate the 80-bit key register left by 61, S-box on the top
nibble, XOR the round counter into bits 19..15. Verified below against the four test vectors of the CHES paper.
No deviation from the specification is modelled (none was observed: 128/128 RTL and gate-level transactions).
Interface (present_encrypt.v:28-34): idat = 64-bit plaintext, key = 80-bit key, both loaded when load = 1 (cycle 0);
odat = dreg ^ kreg[79:16] is combinational on the state registers and equals the ciphertext in cycle 32.
"""
PERIOD = 32
SBOX = [0xC, 5, 6, 0xB, 9, 0, 0xA, 0xD, 3, 0xE, 0xF, 8, 4, 7, 1, 2]
M = lambda w: (1 << w) - 1


def present80(pt, key):
    state = pt & M(64)
    key &= M(80)
    for r in range(1, 32):
        state ^= key >> 16
        state = sum(SBOX[(state >> (4 * i)) & 0xF] << (4 * i) for i in range(16))
        state = sum(((state >> i) & 1) << (63 if i == 63 else (16 * i) % 63) for i in range(64))
        key = ((key << 61) | (key >> 19)) & M(80)
        key = (SBOX[key >> 76] << 76) | (key & M(76))
        key ^= r << 15
    return state ^ (key >> 16)


def _self_test():
    f = M(64); k = M(80)
    for pt, key, ct in [(0, 0, 0x5579C1387B228445), (0, k, 0xE72C46C0F5945049),
                        (f, 0, 0xA112FFC72F68417B), (f, k, 0x3333DCD3213210D2)]:
        assert present80(pt, key) == ct, hex(present80(pt, key))


_self_test()


def expect(per_cycle):
    d = per_cycle[0]  # plaintext and key are loaded in cycle 0
    return {"odat": present80(d["idat"], d["key"])}


def trial(rng, contract):
    per_cycle = {0: {"idat": rng.getrandbits(64), "key": rng.getrandbits(80)}}  # data_mode "cycle"
    return per_cycle, expect(per_cycle)
