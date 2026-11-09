#!/usr/bin/env python3
"""E8.4 ablation by obstruction on the gate-level ground-truth designs (E5.C in class, E8.2): for a design whose full
recovery is exact, the recovered graph equals the folded DFG, so each single-step ablation (A1..A6, definitions in
E8_PREMISES_PROTOCOL.txt, as in e8_ablation.py) is evaluated on the generator's truth (registers, slots, operands).
Designs whose full recovery is not exact are listed and excluded from the ablation shares.
usage: e8_ablation_truth.py OUT.json"""
import collections, glob, json, os, sys

H = "/home/jiahao/hal/build"
sets = [("E5.C", d) for d in sorted(glob.glob(f"{H}/e5c/e5c_s*")) if not d.endswith(("_chain", "_rotate")) and ".prev" not in d]
sets += [("E8.2", d) for d in sorted(glob.glob(f"{H}/e8/fold/*"))]
KEYS = ["A1_no_splitting", "A2_no_phase_specialization", "A3_no_hold_detection", "A4_no_copy_collapse",
        "A5_single_period", "A6_origin_off_by_one"]
rows, skipped = [], []
for exp, d in sets:
    if not os.path.exists(f"{d}/score.json") or not os.path.exists(f"{d}/gen/truth.json"):
        continue
    S = json.load(open(f"{d}/score.json")); T = json.load(open(f"{d}/gen/truth.json"))
    if not S.get("exact"):
        skipped.append({"experiment": exp, "design": os.path.basename(d), "outcome": S.get("outcome")}); continue
    N, nodes = T["N"], T["nodes"]
    by_reg = collections.defaultdict(list)
    for v, nd in nodes.items():
        by_reg[nd["reg"]].append(v)
    ops = {v: {(s, dl) for s, dl in nd["operands"]} for v, nd in nodes.items()}
    shared = lambda u: len(by_reg[nodes[u]["reg"]]) > 1
    ok = collections.Counter()
    for v, nd in nodes.items():
        ok["A1_no_splitting"] += int(not shared(v) and all(s not in nodes or not shared(s) for s, _ in nd["operands"]))
        ok["A2_no_phase_specialization"] += int(set().union(*(ops[u] for u in by_reg[nd["reg"]])) == ops[v])
        def a3(s, dl):
            if s not in nodes:
                return dl == 0 or (nd["slot"] == 0 and dl == 1)
            return nodes[s]["slot"] == nd["slot"] - 1 if dl == 0 else (nd["slot"] == 0 and dl == 1)
        ok["A3_no_hold_detection"] += int(all(a3(s, dl) for s, dl in nd["operands"]))
        ok["A4_no_copy_collapse"] += int(all(dl == 0 for s, dl in nd["operands"]))
        ok["A5_single_period"] += int(all(dl == 0 for s, dl in nd["operands"]))
        ok["A6_origin_off_by_one"] += int(all((nodes[s]["slot"] < 1) == (nd["slot"] < 1) for s, dl in nd["operands"] if s in nodes))
    rows.append({"experiment": exp, "design": os.path.basename(d), "N": N, "dfg_nodes": len(nodes), "full": 1.0,
                 **{k: round(ok[k] / len(nodes), 4) for k in KEYS}})
agg = {}
for exp in ("E5.C", "E8.2"):
    rs = [r for r in rows if r["experiment"] == exp]
    n = sum(r["dfg_nodes"] for r in rs)
    if n:
        agg[exp] = {"designs": len(rs), "dfg_nodes": n, **{k: round(sum(r[k] * r["dfg_nodes"] for r in rs) / n, 4) for k in KEYS}}
json.dump({"rows": rows, "node_weighted": agg, "excluded_not_exact": skipped}, open(sys.argv[1], "w"), indent=1)
for e, a in agg.items():
    print(e, a)
print("excluded (full recovery not exact):", skipped)
