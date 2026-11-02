#!/usr/bin/env python3
"""Evaluation side: does a recovered transaction program compute the documented algorithm
on FRESH random inputs (not trace data)? Port names and references are used only here.
usage: check_reference.py PROGRAM_DIR CONTRACT_JSON CASE [N]"""
import json, random, sys

prog_dir, contract_path, case = sys.argv[1:4]
N = int(sys.argv[4]) if len(sys.argv) > 4 else 2000
contract = json.load(open(contract_path))
ns = {}
exec(open(f"{prog_dir}/program.py").read(), ns)
report = json.load(open(f"{prog_dir}/report.json"))
assert report["state_values"] == 0, "reference check here covers pure transaction functions"
by_bit = {p["physical_bit"]: p for p in contract["port_bits"] if p["direction"] == "input"}
src = open(f"{prog_dir}/program.py").read()
import re
reads = sorted(set(re.findall(r"pads\[\('pad(\d+)', (\d+)\)\]", src)))
M = lambda w: (1 << w) - 1


def S(x):
    return [0xC, 5, 6, 0xB, 9, 0, 0xA, 0xD, 3, 0xE, 0xF, 8, 4, 7, 1, 2][x]


def present80(pt, key):
    state = pt
    for r in range(1, 32):
        state ^= key >> 16
        state = sum(S((state >> (4 * i)) & 0xF) << (4 * i) for i in range(16))
        out = 0
        for i in range(64):
            out |= ((state >> i) & 1) << ((16 * i) % 63 if i != 63 else 63)
        state = out
        key = ((key << 61) | (key >> 19)) & M(80)
        key = (S(key >> 76) << 76) | (key & M(76))
        key ^= r << 15
    return state ^ (key >> 16)


def qdiv(a, b):
    ma, mb = a & M(31), b & M(31)
    mag = (ma << 15) // mb
    out = (((a ^ b) >> 31) << 31) | ((mag & ~1) & M(31))
    return out, int(mag >= (1 << 32))


rng = random.Random(20261104)
mismatch, first = 0, None
for trial in range(N):
    per_cycle = {}
    if case == "present":
        key, pt = rng.getrandbits(80), rng.getrandbits(80)
        per_cycle = {0: {"data_i": key}, 1: {"data_i": pt}}
        expected = {"data_o": present80(pt & M(64), key)}
    elif case == "ibex_mul":
        a, b = rng.getrandbits(32), rng.getrandbits(32)
        per_cycle = {c: {"op_a_i": a, "op_b_i": b} for c in range(33)}
        expected = {"result_o": (a * b) & M(32)}
    elif case == "qdiv":
        a, b = rng.getrandbits(32), rng.getrandbits(32)
        while b & M(31) == 0:
            b = rng.getrandbits(32)
        per_cycle = {0: {"i_dividend": a, "i_divisor": b}}
        q, ovf = qdiv(a, b)
        expected = {"o_quotient_out": q, "o_overflow": ovf}
    else:
        raise SystemExit("no reference for " + case)
    pads = {}
    for pad, phase in reads:
        p = by_bit[int(pad)]; cycle = int(phase) // 2
        value = per_cycle.get(cycle, {}).get(p["name"], rng.getrandbits(64))  # unread/free cycles: random
        pads[(f"pad{pad}", int(phase))] = (value >> p["offset"]) & 1
    outs, _ = ns["transaction"](pads, {})
    got = {}
    for (port, phase), v in outs.items():
        got[port] = v
    for port, v in expected.items():
        if got.get(port) != v:
            mismatch += 1
            if first is None:
                first = {"trial": trial, "port": port, "program": got.get(port), "reference": v}
result = {"case": case, "fresh_random_trials": N, "mismatches": mismatch, "first": first,
          "status": "PASS" if mismatch == 0 else "FAIL", "pad_reads": len(reads)}
json.dump(result, open(f"{prog_dir}/reference_check.json", "w"), indent=1)
print(json.dumps(result))
