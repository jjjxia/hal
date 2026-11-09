"""Evaluation-side reference for case `qmults_n64` (Python stdlib only): qmults, N=64, Q=31.

Documented algorithm (oc_fixedpoint ReadMe.txt "qmults.v", qmults.v header): fixed-point multiplication in the
library's (Q,N) format: bit N-1 is the sign, bits N-2..0 the magnitude with Q fraction bits (sign-magnitude, not
two's complement). Result sign = XOR of the operand signs; result magnitude = (|a| * |b|) >> Q truncated to N-1
bits; o_overflow = 1 iff (|a| * |b|) >> (N-1+Q) != 0 (qmults.v:83). Results are read when o_complete is asserted
("N+1 clocks" after the start edge, ReadMe.txt): cycle 66 = N + 2 of the transaction.

MODELLED DEVIATION (synthesis, not upstream arithmetic): the shift-and-add loop (qmults.v:73-88) runs for
reg_count = 0..N. Its last step (reg_count == N) evaluates reg_multiplicand_temp[reg_count] with an out-of-range
index (the register is [N-1:0], qmults.v:38,74). Verilog defines that read as x, so RTL simulation (Icarus) takes no
add; the frozen Yosys 0.33 iCE40 netlist resolves it to bit 0 (index mod N) and therefore adds the shifted
multiplier (|b| << N) once more when |a| is odd. The overflow flag is computed from the value before that add and
is unaffected. Fitted and confirmed on 3 x 256 gate-level transactions (N = 16, 32, 64; see README).
  expect          the frozen gate-level netlist (= what recovery sees)           <- used by trial()
  expect_rtl_sim  the RTL under Verilog x-semantics (= textbook algorithm)
"""
N, Q = 64, 31
PERIOD = 66
M = lambda w: (1 << w) - 1


def qmults_textbook(a, b):
    ma, mb = a & M(N - 1), b & M(N - 1)
    prod = ma * mb
    sign = ((a ^ b) >> (N - 1)) & 1
    return (sign << (N - 1)) | ((prod >> Q) & M(N - 1)), int((prod >> (N - 1 + Q)) != 0)


def qmults_netlist(a, b):
    ma, mb = a & M(N - 1), b & M(N - 1)
    prod = ma * mb + (ma & 1) * (mb << N)       # extra add of the final out-of-range step (see above)
    sign = ((a ^ b) >> (N - 1)) & 1
    return (sign << (N - 1)) | ((prod >> Q) & M(N - 1)), qmults_textbook(a, b)[1]


def _self_test():
    one = 1 << Q
    assert qmults_textbook(3 * one, 2 * one) == (6 * one, 0)                                 # 3 * 2 = 6
    assert qmults_textbook((1 << (N - 1)) | (one // 2), 3 * one) == ((1 << (N - 1)) | (3 * one // 2), 0)  # -0.5*3
    assert qmults_textbook(M(N - 1), M(N - 1))[1] == 1                                       # overflow
    assert qmults_netlist(2 * one, 3 * one) == qmults_textbook(2 * one, 3 * one)              # even |a|: no change


_self_test()


def _out(fn, per_cycle):
    d = per_cycle[0]
    out, ovf = fn(d["i_multiplicand"], d["i_multiplier"])
    return {"o_result_out": out, "o_overflow": ovf}


def expect(per_cycle):
    return _out(qmults_netlist, per_cycle)


def expect_rtl_sim(per_cycle):
    return _out(qmults_textbook, per_cycle)


def trial(rng, contract):
    a, b = rng.getrandbits(N), rng.getrandbits(N)
    period = contract.get("period_cycles", PERIOD)
    per_cycle = {c: {"i_multiplicand": a, "i_multiplier": b} for c in range(period)}  # data_mode "hold"
    return per_cycle, expect(per_cycle)
