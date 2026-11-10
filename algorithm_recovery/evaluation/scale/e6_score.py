#!/usr/bin/env python3
"""E6 scorer (protocol E6_SCALING_PROTOCOL.txt): recovered event graph vs the generator's DFG.
usage: e6_score.py TRUTH.json GRAPH.json OUT.json [--mutate delay|producer]
Every DFG node v needs one event (reg(v), slot(v)). Events of no DFG node must be identity moves (copy events; their
single source is followed, delays added). Each source of v's event resolves to (producer, sample delay); v's recovered
function, with every source replaced by the symbol of its resolution, must be proved equal by z3 to the DFG function
over the operand symbols. Graph origin mod N must be 0 (graph phases = slots, zero retiming)."""
import json, sys, time
import z3

t0 = time.time()
T = json.load(open(sys.argv[1]))
G = json.load(open(sys.argv[2]))
out = sys.argv[3]
mutate = sys.argv[sys.argv.index("--mutate") + 1] if "--mutate" in sys.argv else None
N, W, nodes = T["N"], T["width"], T["nodes"]
res = {"K": T.get("K", len(nodes)), "N": N, "graph_available": G["available"], "graph_period": G["period"], "origin": G["origin"]}
if not G["available"] or G["period"] != N or G["origin"] % N:
    res.update(exact=False, outcome=f"period/origin mismatch (period {G['period']}, origin {G['origin']}, N {N})")
    json.dump(res, open(out, "w"), indent=1); print(json.dumps(res)); sys.exit(0)

ev = {(n["name"], n["phase"]): n for n in G["nodes"]}
of_event = {(nd["reg"], nd["slot"]): v for v, nd in nodes.items()}
if mutate:   # injected error: must be rejected
    for n in G["nodes"]:
        if (n["name"], n["phase"]) in of_event:
            for s, src in n["sources"].items():
                if src[0] == "signal":
                    if mutate == "delay":
                        src[3] += 1
                    else:   # producer: another event phase of the same word
                        other = [p for (w, p) in ev if w == src[1] and p != src[2]]
                        if not other:
                            continue
                        src[2] = other[0]
                    res["mutation"] = {"event": [n["name"], n["phase"]], "symbol": s, "kind": mutate}
                    break
            if "mutation" in res:
                break


def is_copy(n):
    return len(n["sources"]) == 1 and n["equation"] == "|" + next(iter(n["sources"])) + "|"


def resolve(src):
    kind, name, phase, delay = src
    if kind == "input":
        return (name, delay)
    seen = 0
    while True:
        e = ev.get((name, phase))
        if e is None:
            return ("<no event>", name, phase)
        v = of_event.get((name, phase))
        if v is not None:
            return (v, delay)
        if not is_copy(e):
            return ("<spurious>", name, phase)
        k, name, phase, d = next(iter(e["sources"].values()))
        if k == "input":
            return (name, delay + d)
        delay += d
        seen += 1
        if seen > 64:
            return ("<copy loop>", name, phase)


keysym = {}


def K(key):
    if key not in keysym:
        keysym[key] = z3.BitVec("k_" + "_".join(map(str, key)), W)
    return keysym[key]


missing, wrong, edge_syn_bad, spurious = [], [], 0, []
decl_cache = {}
mapped = set(of_event)
for (name, phase), n in ev.items():
    if (name, phase) not in mapped and not is_copy(n):
        spurious.append([name, phase])
solver = z3.Solver()
proved_by_simplify = 0
for v, nd in nodes.items():
    e = ev.get((nd["reg"], nd["slot"]))
    if e is None:
        missing.append(v); continue
    syms = {s: z3.BitVec(s, W) for s in e["sources"]}
    expr = z3.parse_smt2_string(f"(assert (= #x00 {e['equation']}))" if False else
                                f"(declare-const __o (_ BitVec {W}))(assert (= __o {e['equation']}))",
                                decls={s: x for s, x in syms.items()})[0].arg(1)
    subs = [(syms[s], K(resolve(src))) for s, src in e["sources"].items()]
    rec = z3.substitute(expr, *subs) if subs else expr
    ops = [K((s, d)) if s in nodes else K((s, d)) for s, d in nd["operands"]]
    a = ops[0]
    exp = {"add": lambda: a + ops[1], "sub": lambda: a - ops[1], "xor": lambda: a ^ ops[1], "and": lambda: a & ops[1],
           "mulc": lambda: a * z3.BitVecVal(nd.get("const", 1), W), "mul": lambda: a * ops[1],
           "addc": lambda: a + z3.BitVecVal(nd.get("const", 0), W)}[nd["op"]]()   # mul, addc: E8 kernels (recovery_quality R2)
    if z3.is_true(z3.simplify(rec == exp)):
        proved_by_simplify += 1
    else:
        solver.push(); solver.add(rec != exp)
        r = solver.check()
        solver.pop()
        if r != z3.unsat:
            wrong.append({"node": v, "event": [nd["reg"], nd["slot"]], "recovered": str(rec), "expected": str(exp)})
            continue
    want = {(s, d) for s, d in nd["operands"]}
    got = {resolve(src) for src in e["sources"].values()}
    edge_syn_bad += int(want != got)
res.update({"dfg_nodes": len(nodes), "graph_nodes": len(G["nodes"]), "missing": len(missing), "wrong": len(wrong),
            "spurious_events": len(spurious), "nodes_exact": len(nodes) - len(missing) - len(wrong),
            "nodes_edges_syntactic_mismatch": edge_syn_bad, "proved_by_simplify": proved_by_simplify,
            "examples": {"missing": missing[:3], "wrong": wrong[:3], "spurious": spurious[:3]},
            "seconds": round(time.time() - t0, 1)})
res["exact"] = not missing and not wrong and not spurious
res["outcome"] = "exact" if res["exact"] else "mismatch"
json.dump(res, open(out, "w"), indent=1)
print(json.dumps({k: res[k] for k in res if k != "examples"}))
