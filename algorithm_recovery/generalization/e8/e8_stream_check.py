#!/usr/bin/env python3
"""E8.3 metrics (E8_PREMISES_PROTOCOL.txt): V3 correctness and V1/V2 value coverage of a recovered Bambu design.

usage: e8_stream_check.py CASE_DIR KERNEL OUT.json [STREAM]
V3: the lifted and flat programs are run as a stream of STREAM transactions (default 2,000) from a zero loop-carried state
    (Bambu's static variables start at zero), inputs held per transaction as in the protocol; every observed output is
    compared with kernels.evaluate on the same stream.
V1: value coverage on 200 transactions of the stream (after 3 warm-up transactions). The recovered words are HAL groups
    whose bits need not be in a kernel value's bit order (one word may mix control and data bits), so values are matched
    bitwise: each bit position of a recovered write (v[(word, phase)] of the flat program) has a signature (its bit over
    the 200 transactions); a kernel node value (sample delay 0 or 1, i.e. stored in the transaction or carried to the
    next) is FOUND if every one of its non-constant bits has the signature of some bit of ONE recovered write;
    V1_bit_coverage is the share of non-constant value bits present in any recovered write. Nodes whose value is
    constant on the stream are not scored; bits constant on the stream (e.g. bit 0 of an even multiple) are skipped.
V2: recovered writes per kernel operation; kernel values that are not found are listed as chained/merged (absent).
"""
import json, os, random, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kernels as KM

case_dir, kname, out = sys.argv[1], sys.argv[2], sys.argv[3]
S = int(sys.argv[4]) if len(sys.argv) > 4 else 2000
kern = KM.KERNELS[kname]()
W, M = KM.W, (1 << KM.W) - 1
contract = json.load(open(f"{case_dir}/input/evaluation_contract.json"))
prog = f"{case_dir}/program"
iface = json.load(open(f"{prog}/interface.json"))
reads = sorted({(b[3:], int(ph)) for b, ph in iface["pad_reads"]})
state_keys = [tuple(k) for k in iface.get("state_keys", [])]
by_bit = {str(p["physical_bit"]): p for p in contract["port_bits"]}
rng = random.Random(20261104)
samples = [{i: rng.getrandbits(W) for i in kern["inputs"]} for _ in range(S)]
ref = KM.evaluate(kern, samples)


def load(path, capture):
    src = open(path).read()
    if capture:
        src = src.replace("    v = {}\n", "    v = _CAP.new()\n", 1)
    ns = {"__name__": "prog"}
    if capture:
        class Cap:
            def __init__(self):
                self.all = []
            def new(self):
                d = {}; self.all.append(d); return d
        ns["_CAP"] = Cap()
    exec(compile(src, path, "exec"), ns)
    return ns


def run(ns):
    state = {k: 0 for k in state_keys}
    outs = []
    for n in range(S):
        pads = {}
        for pad, ph in reads:
            p = by_bit[pad]
            pads[(f"pad{pad}", ph)] = (samples[n].get(p["name"], 0) >> p["offset"]) & 1
        o, state = ns["transaction"](pads, state)
        outs.append(o)
    return outs


def compare(outs):
    bad = 0
    for n in range(S):
        by_port = {}
        for (port, ph), v in outs[n].items():
            by_port.setdefault(port, set()).add(v & M)
        for o, node in kern["outputs"].items():
            if by_port.get(o) != {ref[n][node]}:
                bad += 1
    return bad


res = {"case": os.path.basename(case_dir.rstrip("/")), "kernel": kname, "stream_transactions": S}
lifted = load(f"{prog}/program.py", False)
res["V3_lifted_mismatches"] = compare(run(lifted))
flat = load(f"{prog}/program_flat.py", True)
res["V3_flat_mismatches"] = compare(run(flat))
writes = flat["_CAP"].all            # one dict per transaction: (word, phase) -> value
keys = sorted(writes[0])
warm, NV = 3, 200                    # delayed values need a few transactions of history
rngT = range(warm, warm + NV)
bitsig = {}                          # bit signature -> set of writes holding it
for k in keys:
    width = max(writes[n].get(k, 0) for n in rngT).bit_length()
    for b in range(width):
        bitsig.setdefault(tuple((writes[n].get(k, 0) >> b) & 1 for n in rngT), set()).add(k)
found, absent, bits_total, bits_found = {}, [], 0, 0
for v in kern["nodes"]:
    best = None
    for d in (0, 1):
        t = [ref[n - d][v] for n in rngT]
        if len(set(t)) <= 1:
            continue
        sigs = [tuple((x >> i) & 1 for x in t) for i in range(W)]
        sigs = [g for g in sigs if len(set(g)) > 1]
        hit = [bitsig.get(g, set()) for g in sigs]
        one = set.intersection(*hit) if hit else set()
        cand = (bool(one), sum(1 for h in hit if h), len(sigs), d, sorted(map(str, one))[:2])
        if best is None or cand[:2] > best[:2]:
            best = cand
    if best is None:                 # constant on the stream: not scored
        continue
    bits_total += best[2]; bits_found += best[1]
    if best[0]:
        found[v] = {"delay": best[3], "writes": best[4]}
    else:
        absent.append(v)
scored = len(found) + len(absent)
res["case"] = os.path.basename(os.path.dirname(case_dir.rstrip("/"))) if os.path.basename(case_dir.rstrip("/")) == "case" else res["case"]
res.update({"kernel_nodes": len(kern["nodes"]), "V1_transactions": NV, "V1_scored_nodes": scored,
            "V1_values_found": len(found), "V1_coverage": round(len(found) / scored, 4) if scored else None,
            "V1_bit_coverage": round(bits_found / bits_total, 4) if bits_total else None,
            "V2_recovered_writes": len(keys), "V2_writes_per_operation": round(len(keys) / len(kern["nodes"]), 3),
            "V2_absent_values": absent, "found": found})
json.dump(res, open(out, "w"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k not in ("found",)}))
