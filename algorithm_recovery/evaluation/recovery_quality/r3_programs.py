#!/usr/bin/env python3
"""R3b-d (RECOVERY_QUALITY_PROTOCOL.txt) on the programs of the structure-exact folded designs (<design>/r3/program/):
loops found on loop-free sources, lifting share, and operator identification (a DFG node is identified if the report
has an arithmetic entry with proof UNSAT for its event whose output bits are exactly its register's bits in order and
whose form is the node's operator). Also the lifting share of the library programs (a directory of regenerated programs).
usage: r3_programs.py OUT.json [LIBRARY_PROGRAM_ROOT]"""
import collections, glob, json, os, re, sys

B = "/home/jiahao/hal/build"
REG = re.compile(r"^(r\d+|d_[A-Za-z][A-Za-z0-9]*_\d+|cyc|odd)$")


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None


def form_of(a, W):
    """The operator an arithmetic entry proves, or None."""
    lin, const, kind = a.get("linear") or [], a.get("const") or 0, a.get("kind")
    m = (1 << a["width"]) - 1
    if kind == "bilinear" and (a.get("bilinear") or {}).get("0,1") == 1 and not any(lin):
        return ("mul", None)
    if kind == "bitwise":
        return (a.get("bitwise"), None)
    if kind == "linear":
        nz = [c % (1 << a["width"]) for c in lin if c % (1 << a["width"])]
        if len(lin) == 2 and nz and [c % (1 << a["width"]) for c in lin] == [1, 1] and const % (1 << a["width"]) == 0:
            return ("add", None)
        if len(lin) == 2 and [c % (1 << a["width"]) for c in lin] == [1, m]:
            return ("sub", None)
        if len(lin) == 1 and const % (1 << a["width"]) == 0:
            return ("mulc", lin[0] % (1 << a["width"]))
        if len(lin) == 1 and lin[0] % (1 << a["width"]) == 1:
            return ("addc", const % (1 << a["width"]))
    return None


def identify(d):
    T, R, G = load(f"{d}/gen/truth.json"), load(f"{d}/r3/program/report.json"), load(f"{d}/case/graph/graph_training.json")
    K, synth = load(f"{d}/case/input/evaluation_contract.json"), load(f"{d}/gen/fold_top.json")
    if not (T and R and G and K and synth):
        return None
    W = T["width"]
    bit_reg = {}
    for name, nn in synth["modules"]["fold_top"]["netnames"].items():
        if REG.match(name):
            for i, b in enumerate(nn["bits"]):
                if isinstance(b, int):
                    bit_reg[b] = (name, i)
    pos = {}                                               # register -> (word, [word bit of register bit i])
    for w, info in G["words"].items():
        for j, b in enumerate(info["bits"]):
            o = bit_reg.get(int(b[1:]))
            if o:
                pos.setdefault(o[0], {}).setdefault(w, {})[o[1]] = j
    entries = collections.defaultdict(list)
    for a in R.get("arithmetic", []):
        if a.get("proof") == "UNSAT" and isinstance(a["node"][1], int):
            entries[(a["node"][0], a["node"][1])].append(a)
    got = collections.Counter(); tot = collections.Counter()
    for v, nd in T["nodes"].items():
        tot[nd["op"]] += 1
        homes = pos.get(nd["reg"], {})
        if len(homes) != 1:
            continue
        (w, bits), = homes.items()
        order = [bits.get(i) for i in range(W)]
        if None in order:
            continue
        hit = False
        for ph in (2 * nd["slot"], 2 * nd["slot"] + 1):
            for a in entries.get((w, ph), []):
                if a["output_order"] == order:
                    f = form_of(a, W)
                    want = (nd["op"], nd.get("const") % (1 << W) if nd["op"] in ("mulc", "addc") else None)
                    if f and f[0] == want[0] and (f[1] is None or f[1] == want[1]):
                        hit = True
        got[nd["op"]] += int(hit)
    return {"by_operator": {op: [got[op], tot[op]] for op in sorted(tot)}, "identified": sum(got.values()), "nodes": sum(tot.values())}


def lifting_share(R):
    ls = R.get("lifting", {})
    return round(ls.get("program_lifted_bits", 0) / ls["program_bits"], 4) if ls.get("program_bits") else None


if __name__ == "__main__":
    rows = []
    for pop, pat in (("E5.C", f"{B}/e5c/e5c_s*"), ("E8.2", f"{B}/e8/fold/*"), ("R2a", f"{B}/r2/random/e5c_s*"), ("R2b", f"{B}/r2/kernels/*")):
        for d in sorted(glob.glob(pat)):
            R = load(f"{d}/r3/program/report.json")
            if not R:
                continue
            V = load(f"{d}/r3/program/validation.json") or {}
            rows.append({"population": pop, "design": os.path.basename(d), "validation": V.get("status"),
                         "loops": [l["iterations"] for l in R["loops"]], "lines": [R["flat_program_lines"], R["rolled_program_lines"], R["program_lines"]],
                         "lifting_share": lifting_share(R), "operators": identify(d)})
    lib = []
    if len(sys.argv) > 2:
        for p in sorted(glob.glob(f"{sys.argv[2]}/*/report.json")):
            R = load(p)
            lib.append({"design": os.path.basename(os.path.dirname(p)), "lifting_share": lifting_share(R),
                        "loops": [[l["iterations"], l.get("steps_per_iteration", 1)] for l in R["loops"]],
                        "lines": [R["flat_program_lines"], R["rolled_program_lines"], R["program_lines"]]})
    agg = {}
    for pop in ("E5.C", "E8.2", "R2a", "R2b"):
        rs = [r for r in rows if r["population"] == pop]
        if not rs:
            continue
        ops = collections.Counter(); tot = collections.Counter()
        for r in rs:
            for op, (g, t) in (r["operators"] or {}).get("by_operator", {}).items():
                ops[op] += g; tot[op] += t
        agg[pop] = {"programs": len(rs), "validation_pass": sum(r["validation"] == "PASS" for r in rs),
                    "programs_with_loops": sum(1 for r in rs if r["loops"]), "loops": [r["loops"] for r in rs if r["loops"]],
                    "lifting_share_mean": round(sum(r["lifting_share"] or 0 for r in rs) / len(rs), 4),
                    "operators_identified": {op: f"{ops[op]}/{tot[op]}" for op in sorted(tot)},
                    "identified_total": f"{sum(ops.values())}/{sum(tot.values())}"}
    json.dump({"aggregate": agg, "rows": rows, "library": lib}, open(sys.argv[1], "w"), indent=1)
    for k, a in agg.items():
        print(k, json.dumps(a))
    for r in lib:
        print(" lib", r)
