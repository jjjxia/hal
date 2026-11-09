"""E5 evaluation-side reference for case keccak_f (OpenCores sha3 by H. Hsing, low_throughput_core, top f_permutation).

Documented algorithm: Keccak-f[1600] (Keccak reference / FIPS 202 Sec. 3.2-3.4: theta, rho, pi, chi, iota, 24 rounds),
written here from the specification -- the round constants come from the rc(t) LFSR (FIPS 202 Alg. 5) and the rho
offsets from the (x, y) -> (y, 2x + 3y) walk (Alg. 2) -- not transcribed from the RTL. Python stdlib only.
Self-tested at import: (1) Keccak-f[1600] of the all-zero state (lane 0 = 0xF1258F7940E1DDE7, the known value; also
the first vector of the repository testbench test_f_permutation.v); (2) a sponge built on this permutation reproduces
hashlib.sha3_256 and hashlib.sha3_512 (rate 576 bits, the rate this core absorbs) for several messages.

What one transaction computes (from f_permutation.v):
  * absorb: round_in = accept ? {in ^ out[1599:1024], out[1023:0]} : out (f_permutation.v:56), i.e. the 576-bit input
    is XORed into the rate part (lanes 0..8) of the state register `out`; the state persists across blocks (sponge).
  * this protocol pulses reset (synchronous, f_permutation.v:36-42/48-50/64-66) in transaction cycle 0, which zeroes the
    state, then in_ready in cycle 1 (accept = in_ready & ~calc, :34); 24 rounds follow, one per cycle (:36-44, :61-68).
  * so one transaction = Keccak-f[1600]( in || 0^1024 ): the first absorb+permute step of a sponge with r = 576
    (SHA3-512 / Keccak-512 rate) from the zero state. out (1600 bits) is read in cycle 25 (= cycle 0 of the next
    transaction, where out_ready = 1).
Bit/lane convention of the RTL (round.v:17-18, :42, :159): lane (x, y) is out[1599-64(5y+x) -: 64] and lane bit z is
bit z of that 64-bit field (rot_up = rotate towards the MSB = Keccak's cyclic shift z -> z + r). As integers:
lane L = (state >> (1536 - 64 L)) & (2^64 - 1); input lane L (L < 9) = (in >> (512 - 64 L)) & (2^64 - 1).
This is a pure packing convention (no byte reordering inside f_permutation; the byte-level padding/endianness of the
full SHA-3 core lives in keccak.v/padder.v, which are not part of this benchmark). No deviation from Keccak-f[1600].
"""

M64 = (1 << 64) - 1


def _rc_bit(t):
    """FIPS 202 Algorithm 5; R is the bit string R[0..7] (R = 10000000 initially)."""
    if t % 255 == 0:
        return 1
    R = [1, 0, 0, 0, 0, 0, 0, 0]
    for _ in range(t % 255):
        R = [0] + R
        R[0] ^= R[8]
        R[4] ^= R[8]
        R[5] ^= R[8]
        R[6] ^= R[8]
        R = R[:8]
    return R[0]


ROUND_CONSTANTS = []
for _ir in range(24):
    _rc = 0
    for _j in range(7):
        _rc |= _rc_bit(_j + 7 * _ir) << ((1 << _j) - 1)
    ROUND_CONSTANTS.append(_rc)

RHO = [[0] * 5 for _ in range(5)]  # RHO[x][y]
_x, _y = 1, 0
for _t in range(24):
    RHO[_x][_y] = ((_t + 1) * (_t + 2) // 2) % 64
    _x, _y = _y, (2 * _x + 3 * _y) % 5


def _rotl(v, n):
    n %= 64
    return ((v << n) | (v >> (64 - n))) & M64 if n else v


def keccak_f1600(lanes):
    """lanes: list of 25 64-bit ints, index 5*y + x. Returns the permuted list."""
    A = [[lanes[5 * y + x] for y in range(5)] for x in range(5)]  # A[x][y]
    for rnd in range(24):
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ _rotl(C[(x + 1) % 5], 1) for x in range(5)]
        A = [[A[x][y] ^ D[x] for y in range(5)] for x in range(5)]  # theta
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                B[y][(2 * x + 3 * y) % 5] = _rotl(A[x][y], RHO[x][y])  # rho + pi
        A = [[B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]  # chi
        A[0][0] ^= ROUND_CONSTANTS[rnd]  # iota
    return [A[x][y] for y in range(5) for x in range(5)]


def _sponge(msg, rate_bytes, out_bytes, suffix=0x06):
    lanes = [0] * 25
    padded = bytearray(msg) + bytes([suffix])
    while len(padded) % rate_bytes:
        padded.append(0)
    padded[-1] |= 0x80
    for off in range(0, len(padded), rate_bytes):
        block = padded[off:off + rate_bytes]
        for i in range(rate_bytes // 8):
            lanes[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        lanes = keccak_f1600(lanes)
    out = b"".join(l.to_bytes(8, "little") for l in lanes)[:rate_bytes]
    assert out_bytes <= rate_bytes
    return out[:out_bytes]


def rtl_state_to_lanes(state):
    return [(state >> (1536 - 64 * L)) & M64 for L in range(25)]


def lanes_to_rtl_state(lanes):
    return sum(l << (1536 - 64 * L) for L, l in enumerate(lanes))


def transaction(block576):
    """One protocol transaction: reset (state = 0), absorb the 576-bit input into lanes 0..8, Keccak-f[1600]."""
    lanes = [(block576 >> (512 - 64 * L)) & M64 for L in range(9)] + [0] * 16
    return lanes_to_rtl_state(keccak_f1600(lanes))


def _self_test():
    import hashlib
    assert ROUND_CONSTANTS[0] == 1 and ROUND_CONSTANTS[1] == 0x8082 and ROUND_CONSTANTS[23] == 0x8000000080008008
    assert RHO[1][0] == 1 and RHO[0][1] == 36 and RHO[4][4] == 14
    z = keccak_f1600([0] * 25)
    assert z[0] == 0xF1258F7940E1DDE7
    repo_tb = int("f1258f7940e1dde784d5ccf933c0478ad598261ea65aa9eebd1547306f80494d8b284e056253d057ff97a42d7f8e6fd490fee5a0a44647c48c5bda0cd6192e76ad30a6f71b19059c30935ab7d08ffc64eb5aa93f2317d635a9a6e6260d71210381a57c16dbcf555f43b831cd0347c82601f22f1a11a5569f05e5635a21d9ae6164befef28cc970f2613670957bc46611b87c5a554fd00ecb8c3ee88a1ccf32c8940c7922ae3a26141841f924a2c509e416f53526e70465c275f644e97f30a13beaf1ff7b5ceca249", 16)
    assert transaction(0) == repo_tb  # test_f_permutation.v, first block (in = 0 from reset)
    for msg in (b"", b"abc", b"NetNotary E5 keccak_f self test", bytes(range(200))):
        assert _sponge(msg, 136, 32) == hashlib.sha3_256(msg).digest()
        assert _sponge(msg, 72, 64) == hashlib.sha3_512(msg).digest()


_self_test()


def trial(rng, contract):
    block = rng.getrandbits(576)
    per_cycle = {1: {"in": block}}  # data_mode "cycle": sampled only at the edge ending cycle 1 (accept)
    return per_cycle, {"out": transaction(block)}
