"""E5 evaluation-side reference for case aes_sw128 (secworks/aes aes_core behind the benchmark wrapper
aes128_enc_top, which ties encdec = 1 (encipher) and keylen = 0 (AES-128) and drives key = {key, 128'h0}).

Documented algorithm: AES-128 encryption of one 128-bit block (FIPS-197), written here from the standard
(S-box derived from the GF(2^8) inverse and the affine map of FIPS-197 Sec. 5.1.1, key expansion Sec. 5.2,
cipher Sec. 5.1), not transcribed from the RTL. Python stdlib only. Self-tested at import against FIPS-197
Appendix A.1 (key expansion), Appendix B (cipher example) and Appendix C.1 (AES-128 example vector).

Port convention: key, block, result are 128-bit big-endian integers of the FIPS-197 byte sequence (byte 0 =
bits [127:120]; aes_encipher_block.v:120-123/138-146 use w0 = data[127:96] as state column 0). No deviation.

Protocol timing (evaluation side, measured on the RTL, see README.md): init = 1 in transaction cycle 0, next = 1 in
cycle 15 (first cycle with ready = 1). The key is sampled only at the edge ending cycle 2 (aes_key_mem.v:239-245,
CTRL_GENERATE with round_ctr_reg == 0); the block only at the edge ending cycle 16 (aes_encipher_block.v:278/285,
CTRL_INIT). result (= the block registers, held) is read in cycle 68 (= cycle 0 of the next transaction), where
result_valid = 1.
"""

M128 = (1 << 128) - 1


def _xtime(b):
    b <<= 1
    return (b ^ 0x11B) if b & 0x100 else b


def _gmul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = _xtime(a)
        b >>= 1
    return r


def _make_sbox():
    inv = [0] * 256
    for x in range(1, 256):
        for y in range(1, 256):
            if _gmul(x, y) == 1:
                inv[x] = y
                break
    sbox = []
    for x in range(256):
        b = inv[x]
        s = 0
        for i in range(8):  # b'_i = b_i ^ b_{i+4} ^ b_{i+5} ^ b_{i+6} ^ b_{i+7} ^ c_i, c = 0x63
            bit = ((b >> i) ^ (b >> ((i + 4) % 8)) ^ (b >> ((i + 5) % 8)) ^ (b >> ((i + 6) % 8)) ^ (b >> ((i + 7) % 8)) ^ (0x63 >> i)) & 1
            s |= bit << i
        sbox.append(s)
    return sbox


SBOX = _make_sbox()


def key_expansion(key):
    """FIPS-197 Sec. 5.2 for Nk=4: 44 words w[0..43] (each 4 bytes)."""
    w = [list(key[4 * i:4 * i + 4]) for i in range(4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return w


def encrypt_block(key, block):
    """AES-128 Cipher (FIPS-197 Sec. 5.1) on bytes; state s[r][c] = in[r + 4c]."""
    w = key_expansion(key)
    s = [[block[r + 4 * c] for c in range(4)] for r in range(4)]

    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[4 * rnd + c][r]

    add_round_key(0)
    for rnd in range(1, 11):
        for r in range(4):
            for c in range(4):
                s[r][c] = SBOX[s[r][c]]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        if rnd != 10:
            for c in range(4):
                a = [s[r][c] for r in range(4)]
                s[0][c] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
                s[1][c] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
                s[2][c] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
                s[3][c] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)
        add_round_key(rnd)
    return bytes(s[r][c] for c in range(4) for r in range(4))


def aes128_encrypt_int(key, pt):
    """128-bit integers, byte 0 = most significant byte."""
    ct = encrypt_block((key & M128).to_bytes(16, "big"), (pt & M128).to_bytes(16, "big"))
    return int.from_bytes(ct, "big")


def _self_test():
    h = bytes.fromhex
    assert SBOX[0x00] == 0x63 and SBOX[0x53] == 0xED and SBOX[0xFF] == 0x16
    w = key_expansion(h("2b7e151628aed2a6abf7158809cf4f3c"))  # Appendix A.1
    assert bytes(w[4]) == h("a0fafe17") and bytes(w[43]) == h("b6630ca6")
    assert encrypt_block(h("2b7e151628aed2a6abf7158809cf4f3c"), h("3243f6a8885a308d313198a2e0370734")) == h("3925841d02dc09fbdc118597196a0b32")  # App. B
    assert encrypt_block(h("000102030405060708090a0b0c0d0e0f"), h("00112233445566778899aabbccddeeff")) == h("69c4e0d86a7b0430d8cdb78070b4c55a")  # App. C.1


_self_test()


def trial(rng, contract):
    key, pt = rng.getrandbits(128), rng.getrandbits(128)
    per_cycle = {2: {"key": key}, 16: {"block": pt}}  # data_mode "cycle": the only cycles in which they are sampled
    return per_cycle, {"result": aes128_encrypt_int(key, pt)}
