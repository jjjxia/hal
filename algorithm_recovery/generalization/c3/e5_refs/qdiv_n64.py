"""Evaluation-side reference for case `qdiv_n64` (Python stdlib only): qdiv, N=64, Q=31.
Generalises the frozen qdiv (N=32, Q=15) reference, function qdiv in algorithm_recovery/generalization/c3/check_reference.py.

Documented algorithm (oc_fixedpoint ReadMe.txt "qdiv.v", qdiv.v header): fixed-point division in the library's
(Q,N) sign-magnitude format (bit N-1 sign, N-2..0 magnitude with Q fraction bits): sign = XOR of the operand
signs, magnitude = (|a| << Q) // |b| (restoring shift-subtract, N+Q iterations, qdiv.v:86-105), result read when
o_complete is asserted: cycle 96 = N + Q + 1 after the start edge (measured).
Modelled deviations (upstream RTL, identical in RTL simulation and in the netlist):
  * LSB defect: reg_quotient is copied from reg_working_quotient in the same edge that sets quotient bit 0
    (qdiv.v:92 and :99), so bit 0 of the output magnitude is always 0;
  * o_overflow tests reg_working_quotient[2N+Q-3:N] (qdiv.v:100): it is 1 iff the magnitude >= 2^N; a magnitude
    in [2^(N-1), 2^N) loses its bit N-1 without a flag;
  * |b| = 0 (not handled upstream, qdiv.v:73): every compare succeeds, magnitude = 2^(N+Q)-1, overflow = 1.
    trial() excludes |b| = 0 like the c3 reference; expect() models it (the frozen stimulus may contain it).
trial() draws uniform operands, but in 1/4 of the trials a divisor magnitude below 2^(N-Q+1), so that
overflow and the band [2^(N-1), 2^N) are exercised (uniform operands almost never reach them).
"""
N, Q = 64, 31
PERIOD = 96
M = lambda w: (1 << w) - 1


def qdiv(a, b):
    ma, mb = a & M(N - 1), b & M(N - 1)
    mag = (ma << Q) // mb if mb else M(N + Q)
    out = ((((a ^ b) >> (N - 1)) & 1) << (N - 1)) | ((mag & ~1) & M(N - 1))
    return out, int(mag >= (1 << N))


def _self_test():
    one = 1 << Q
    assert qdiv(6 * one, 2 * one) == (3 * one, 0)                              # 6 / 2 = 3
    assert qdiv((1 << (N - 1)) | 3 * one, 2 * one) == ((1 << (N - 1)) | (3 * one // 2), 0)  # -3 / 2 = -1.5
    assert qdiv(one, 3 * one)[0] == ((one << Q) // (3 * one)) & ~1             # 1/3: LSB forced to 0


_self_test()


def expect(per_cycle):
    d = per_cycle[0]
    q, ovf = qdiv(d["i_dividend"], d["i_divisor"])
    return {"o_quotient_out": q, "o_overflow": ovf}


def trial(rng, contract):
    a, b = rng.getrandbits(N), rng.getrandbits(N)
    if rng.random() < 0.25:            # small divisors: large quotients, overflow and the unflagged bit N-1 band
        b = (b & (1 << (N - 1))) | rng.getrandbits(rng.randrange(1, N - Q + 2))
    while b & M(N - 1) == 0:
        b = rng.getrandbits(N)
    period = contract.get("period_cycles", PERIOD)
    per_cycle = {c: {"i_dividend": a, "i_divisor": b} for c in range(period)}  # data_mode "hold"
    return per_cycle, expect(per_cycle)
