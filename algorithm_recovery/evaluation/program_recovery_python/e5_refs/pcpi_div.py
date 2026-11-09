"""Evaluation-side reference for case `pcpi_div` (Python stdlib only): picorv32_pcpi_div executing DIVU.

Documented algorithm: RISC-V M extension DIVU (RISC-V Unprivileged ISA, "M" chapter): rd = rs1 / rs2 as unsigned
32-bit integers, rounded toward zero; division by zero returns 2^32 - 1 (all bits set). No overflow case for DIVU.
pcpi_insn is a non-data pad fixed to DIVU a0, a0, a1 (0x02b55533; only opcode, funct3 and funct7 are decoded,
picorv32.v:2444-2450). pcpi_rs1 / pcpi_rs2 are held for the whole transaction (PCPI: operands valid while
pcpi_valid; sampled at the start edge, picorv32.v:2472-2478). No deviation from the ISA definition is modelled.
Timing (measured): pcpi_valid = 1 in cycles 0..36 and 0 in cycle 37 (the core drops pcpi_valid after pcpi_ready);
pcpi_ready = pcpi_wr = 1 and pcpi_rd = result in cycle 36 only (pcpi_rd is 'bx in all other cycles in the RTL).
"""
PERIOD = 38
M32 = (1 << 32) - 1


def divu(rs1, rs2):
    rs1 &= M32; rs2 &= M32
    return M32 if rs2 == 0 else rs1 // rs2


def _self_test():
    assert divu(7, 2) == 3 and divu(0xFFFFFFFF, 1) == 0xFFFFFFFF and divu(5, 0) == M32
    assert divu(0x80000000, 0xFFFFFFFF) == 0  # unsigned, unlike DIV


_self_test()


def expect(per_cycle):
    d = per_cycle[0]
    return {"pcpi_rd": divu(d["pcpi_rs1"], d["pcpi_rs2"])}


def trial(rng, contract):
    a, b = rng.getrandbits(32), rng.getrandbits(32)
    if rng.random() < 0.25:            # also exercise small divisors (large quotients) and division by zero
        b = rng.getrandbits(rng.randrange(0, 9))
    period = contract.get("period_cycles", PERIOD)
    per_cycle = {c: {"pcpi_rs1": a, "pcpi_rs2": b} for c in range(period)}  # data_mode "hold"
    return per_cycle, expect(per_cycle)
