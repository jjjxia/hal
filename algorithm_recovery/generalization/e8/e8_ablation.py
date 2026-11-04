#!/usr/bin/env python3
"""E8.4 ablation by obstruction (E8_PREMISES_PROTOCOL.txt) on the E6 designs: exact share of DFG nodes for the full
method and with one resolution step removed (A1..A7). Reads build/e6/<design>/{gen/truth.json, graph.json, score.json}.
usage: e8_ablation.py OUT.json"""
import collections, glob, json, os, sys

B = "/home/jiahao/hal/build/e6"
rows = []
for d in sorted(glob.glob(f"{B}/s1_k*_n*")):
    if ".prev" in d or not os.path.exists(f"{d}/score.json"):
        continue
    T = json.load(open(f"{d}/gen/truth.json")); G = json.load(open(f"{d}/graph.json")); S = json.load(open(f"{d}/score.json"))
    N, nodes, P = T["N"], T["nodes"], G["period"]
    assert P == N and G["origin"] % N == 0
    by_reg = collections.defaultdict(list)
    for v, nd in nodes.items():
        by_reg[nd["reg"]].append(v)
    ev = {(n["name"], n["phase"]) for n in G["nodes"]}
    words = {n["name"] for n in G["nodes"]}
    ops = {v: {(s, dl) for s, dl in nd["operands"]} for v, nd in nodes.items()}
    exact = {"full": 0, "A1_no_splitting": 0, "A2_no_phase_specialization": 0, "A3_no_hold_detection": 0,
             "A4_no_copy_collapse": 0, "A5_single_period": 0, "A6_origin_off_by_one": 0}
    full_ok = S["exact"]
    shift = 1                                     # A6: origin moved by one slot (one event per cycle in E6)
    rho = lambda ph: int(ph < shift)
    for v, nd in nodes.items():
        exact["full"] += int(full_ok)
        r = nd["reg"]
        shared = lambda u: len(by_reg[nodes[u]["reg"]]) > 1
        # A1: one node per register; ambiguous if v's or any operand producer's register holds another node
        a1 = not shared(v) and all(s not in nodes or not shared(s) for s, _ in nd["operands"])
        exact["A1_no_splitting"] += int(a1)
        # A2: operand set = union over all nodes written into v's register
        union = set().union(*(ops[u] for u in by_reg[r]))
        exact["A2_no_phase_specialization"] += int(union == ops[v])
        # A3: every (word, phase) is a write: an operand read resolves to the immediately preceding phase of its word;
        # correct only if the true producer write is exactly there (same-sample producer at slot(v) - 1, or a delayed
        # operand whose copy register was written at slot N-1 and v is at slot 0)
        def a3_ok(s, dl):
            if s not in nodes:
                return True
            if dl == 0:
                return nodes[s]["slot"] == nd["slot"] - 1
            return nd["slot"] == 0 and dl == 1      # copy register d_s_1 written at N-1, read at slot 0 of the next sample
        exact["A3_no_hold_detection"] += int(all(a3_ok(s, dl) for s, dl in nd["operands"]))
        # A4: copy events stay nodes: any delayed operand is read through a copy register (E6 delays use copies only)
        exact["A4_no_copy_collapse"] += int(all(dl == 0 for s, dl in nd["operands"] if s in nodes))
        # A5: single-period reading: producers in an earlier period cannot be resolved
        exact["A5_single_period"] += int(all(dl == 0 for s, dl in nd["operands"]))
        # A6: no Theorem 5 correction after moving the origin by one slot
        exact["A6_origin_off_by_one"] += int(all(rho(nodes[s]["slot"]) == rho(nd["slot"]) for s, dl in nd["operands"] if s in nodes))
    holds = len(words) * P - len(ev)
    row = {"design": os.path.basename(d), "K": T["K"], "N": N, "dfg_nodes": len(nodes),
           **{k: round(v / len(nodes), 4) for k, v in exact.items()},
           "A3_spurious_hold_events": holds, "A4_spurious_copy_events": len(G["nodes"]) - len(nodes),
           "A7_period_2N_node_ratio": 2.0}
    rows.append(row)
    print(json.dumps(row))
tot = collections.Counter()
nn = sum(r["dfg_nodes"] for r in rows)
for r in rows:
    for k in ("full", "A1_no_splitting", "A2_no_phase_specialization", "A3_no_hold_detection", "A4_no_copy_collapse",
              "A5_single_period", "A6_origin_off_by_one"):
        tot[k] += r[k] * r["dfg_nodes"]
agg = {k: round(v / nn, 4) for k, v in tot.items()}
json.dump({"rows": rows, "node_weighted": agg, "dfg_nodes": nn}, open(sys.argv[1], "w"), indent=1)
print("node-weighted exact share:", agg, "over", nn, "DFG nodes")
