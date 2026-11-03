"""Evaluation-side reference for case `cordic` (Python stdlib only).

Documented algorithm: CORDIC in rotation mode (Volder 1959), as described in the header of Drinkard's
verilog_cordic_core cordic.v (lines 35-51, 133-139): starting from (x, y, z) = (x_i, y_i, theta_i), for i = 0..15
    d = -1 if z < 0 else +1
    x <- x - d * (y >> i);  y <- y + d * (x >> i);  z <- z - d * atan(2^-i)
(simultaneous update, arithmetic right shifts), the result is (x_o, y_o, theta_o) after 16 micro-rotations.
No gain compensation inside the core: with x_i = CORDIC_1 = 19896 (~2^15 / 1.647), y_i = 0, y_o ~ sin, x_o ~ cos.

Number format (cordic.v:80-106): x, y and z are 17-bit two's-complement words; angles are U(1,15) radians + sign.
Modelled deviations from the real-number textbook algorithm (explicit, not RTL transcription):
  * arctan constants are the 16 values of the source's table (cordic.v:284-299), i.e. atan(2^-i) * 2^15 with the
    author's rounding (25735 for 25735.93, 4075 for 4074.87, ...), not recomputed;
  * every addition wraps to 17 bits (the data pads carry arbitrary 17-bit words, also outside the convergence
    range |theta| <= 1.74 rad and with |x|,|y| close to 2^16, where the 17-bit registers overflow).
Timing (measured, see README): init in cycle 0 loads the operands; the 16 micro-rotations run in cycles 1..16;
the result is in the output registers in cycle 17 only (the free-running iteration counter keeps rotating).
"""
import math

PERIOD = 17
BITS = 17
ITER = 16
ATAN = [25735, 15192, 8027, 4075, 2045, 1024, 512, 256, 128, 64, 32, 16, 8, 4, 2, 1]
CORDIC_1 = 19896
M = (1 << BITS) - 1


def signed(v):
    v &= M
    return v - (1 << BITS) if v >> (BITS - 1) else v


def cordic_rotate(x, y, z):
    """x, y, z: 17-bit words (unsigned encodings). Returns the three 17-bit result words."""
    x, y, z = signed(x), signed(y), signed(z)
    for i in range(ITER):
        if z < 0:
            x, y, z = signed(x + (y >> i)), signed(y - (x >> i)), signed(z + ATAN[i])
        else:
            x, y, z = signed(x - (y >> i)), signed(y + (x >> i)), signed(z - ATAN[i])
    return x & M, y & M, z & M


def _self_test():
    for i, a in enumerate(ATAN):  # the source's table is atan(2^-i) in U(1,15), within one LSB
        assert abs(a - math.atan(2.0 ** -i) * 2 ** 15) < 1.0, i
    for k in range(-50, 51):  # sin/cos over the first and fourth quadrant
        th = k * (math.pi / 2) / 50
        x, y, _ = cordic_rotate(CORDIC_1, 0, round(th * 2 ** 15) & M)
        assert abs(signed(x) / 2 ** 15 - math.cos(th)) < 1e-3 and abs(signed(y) / 2 ** 15 - math.sin(th)) < 1e-3, k


_self_test()


def expect(per_cycle):
    d = per_cycle[0]  # the operands are loaded by init in cycle 0
    x, y, z = cordic_rotate(d["x_i"], d["y_i"], d["theta_i"])
    return {"x_o": x, "y_o": y, "theta_o": z}


def trial(rng, contract):
    d = {"x_i": rng.getrandbits(BITS), "y_i": rng.getrandbits(BITS), "theta_i": rng.getrandbits(BITS)}
    per_cycle = {0: d}  # data_mode "cycle": every other cycle carries free random data
    return per_cycle, expect(per_cycle)
