#!/usr/bin/env python3
"""R1 (RECOVERY_QUALITY_PROTOCOL.txt): node, edge and delay recovery of three views of the same equations -- B0 cycle
view, B1 register view, NetNotary event graph -- on the designs with a generator truth (E5.C, E8.2, E6), plus NetNotary
strict versus up to the theorems on the E5.C chain/rotate controls.
A design enters the B0/B1/NetNotary comparison if its recovered graph is structure-exact by its scorer; then the event
graph equals the folded DFG and B0/B1 are computed from the generator's folding. The functions folding_views() and
truth_edges() are shared with R2 (r2_collect.py).
usage: r1_structure.py OUT.json"""
import collections, glob, json, os, sys

B = "/home/jiahao/hal/build"


def truth_edges(T):
    """{v: {(u, d)}} with u a DFG node or an input."""
    return {v: {(u, d) for u, d in nd["operands"]} for v, nd in T["nodes"].items()}


def folding_views(T):
    """Counts of the protocol's metrics for B0 and B1 from the generator's folding (registers, slots, copy registers)."""
    N, nodes, inputs = T["N"], T["nodes"], set(T["inputs"])
    ops = truth_edges(T)
    by_reg = collections.defaultdict(list)
    for v, nd in nodes.items():
        by_reg[nd["reg"]].append(v)
    copies = [f"d_{u}_{k}" for u, k in T["delay_registers"]]
    E = [(u, v, d) for v in nodes for u, d in ops[v]]
    delayed = [e for e in E if e[2] >= 1]
    src_reg = lambda u, d: (f"d_{u}_{d}" if d >= 1 else (u if u in inputs else nodes[u]["reg"]))
    # B0: one node per (data register, cycle); writes read their source registers at the previous cycle (inputs at the
    # same cycle), holds and copy registers one edge per cycle.
    b0_nodes = (len(by_reg) + len(copies)) * N
    b0_edges = sum(len({src_reg(u, d) for u, d in ops[v]}) for v in nodes) + (len(by_reg) * N - len(nodes)) + len(copies) * N
    b0_ok = [(u, v, d) for u, v, d in E if d == 0 and (u in inputs or nodes[u]["slot"] == nodes[v]["slot"] - 1)]
    def chain(u, v, d):
        s = nodes[v]["slot"]
        if u in inputs:
            return 1 if d == 0 else (d - 1) * N + s + 2
        return d * N + s - nodes[u]["slot"]
    steps = [chain(*e) for e in E]
    # B1: one node per data register, an edge per (source register, target register), no delays
    alone = lambda v: len(by_reg[nodes[v]["reg"]]) == 1
    b1_edges = {(src_reg(u, d), nodes[v]["reg"]) for v in nodes for u, d in ops[v]}
    for u, k in T["delay_registers"]:
        b1_edges.add((src_reg(u, 0) if k == 1 else f"d_{u}_{k - 1}", f"d_{u}_{k}"))
    b1_ok = [(u, v, d) for u, v, d in E if d == 0 and alone(v) and (u in inputs or alone(u))]
    # exact nodes (the node and every operand edge matched; the ablations' notion of an exact node)
    b0_set, b1_set = set(b0_ok), set(b1_ok)
    b0_exact = sum(1 for v in nodes if all((u, v, d) in b0_set for u, d in ops[v]))
    b1_exact = sum(1 for v in nodes if alone(v) and all((u, v, d) in b1_set for u, d in ops[v]))
    reads = collections.defaultdict(set)                      # register -> source registers its next state reads
    for v in nodes:
        for u, d in ops[v]:
            reads[nodes[v]["reg"]].add(src_reg(u, d))
    def values(r):
        if r in by_reg:
            return set(by_reg[r])
        if r.startswith("d_") and r not in inputs:
            return {r[2:].rsplit("_", 1)[0]}
        return {r}
    cand_true = cand_all = cand_hit = 0
    for v in nodes:
        cand = set().union(*(values(r) for r in reads[nodes[v]["reg"]]))
        prod = {u for u, _ in ops[v]}
        cand_true += len(prod); cand_all += len(cand); cand_hit += len(prod & cand)
    return {
        "V": len(nodes), "E": len(E), "E_delayed": len(delayed), "N": N, "result_registers": len(by_reg), "copy_registers": len(copies),
        "units": len(T.get("units", {})),
        "B0": {"nodes": b0_nodes, "nodes_matched": len(nodes), "edges": b0_edges, "edges_matched": len(b0_ok), "delayed_matched": 0,
               "chain_steps_sum": sum(steps), "chain_steps_max": max(steps) if steps else 0, "exact_nodes": b0_exact},
        "B1": {"nodes": len(by_reg) + len(copies), "nodes_matched": sum(1 for r in by_reg if len(by_reg[r]) == 1),
               "truth_nodes_matched": sum(1 for v in nodes if alone(v)), "edges": len(b1_edges),
               "edges_matched": len({(src_reg(u, 0), nodes[v]["reg"]) for u, v, d in b1_ok}), "truth_edges_matched": len(b1_ok),
               "delayed_matched": 0, "exact_nodes": b1_exact, "candidates_true": cand_true, "candidates_all": cand_all, "candidates_hit": cand_hit},
    }


def structure_exact(S):
    if S.get("exact"):
        return True
    return bool(S.get("graph_available") and S.get("period_as_predicted") and not S.get("grouping_issues")
                and S.get("nodes_found") == S.get("nodes_scored") == S.get("nodes_edges_exact")
                and not S.get("spurious_result_events") and not S.get("unresolved_reads"))


def control_strict(T, S):
    """NetNotary against the original DFG on a control design whose recovery matches the theorem's prediction."""
    ops = truth_edges(T); nodes = T["nodes"]
    E = [(u, v, d) for v in nodes for u, d in ops[v]]
    nE, nD, nV = len(E), sum(1 for e in E if e[2] >= 1), len(nodes)
    if T["control"] == "rotate":          # recovered: the 2-unfolding; copy 0 is the strict candidate of each node
        d0 = sum(1 for e in E if e[2] == 0)
        return {"nodes": 2 * nV, "nodes_matched": nV, "truth_nodes_matched": nV, "edges": 2 * nE, "edges_matched": d0,
                "truth_edges_matched": d0, "delayed_matched": 0}
    u, v = T["chain_edge"]
    dead = u in S.get("dead_chained_producer", [])
    rec_v = (ops[v] - {(u, 0)}) | ops[u]
    lost = {(u, v, 0)} | ({(w, u, d) for w, d in ops[u]} if dead else set())
    spurious = {(w, v, d) for w, d in rec_v} - {(w, v, d) for w, d in ops[v]}
    matched = nE - len(lost)
    return {"nodes": nV - (1 if dead else 0), "nodes_matched": nV - (1 if dead else 0), "truth_nodes_matched": nV - (1 if dead else 0),
            "edges": matched + len(spurious), "edges_matched": matched, "truth_edges_matched": matched,
            "delayed_matched": nD - sum(1 for e in lost if e[2] >= 1)}


def populations():
    for d in sorted(glob.glob(f"{B}/e5c/e5c_s*")):
        n = os.path.basename(d)
        yield ("E5.C control" if n.endswith(("_chain", "_rotate")) else "E5.C"), n, d, f"{d}/gen/truth.json", f"{d}/score.json"
    for d in sorted(glob.glob(f"{B}/e8/fold/*")):
        yield "E8.2", os.path.basename(d), d, f"{d}/gen/truth.json", f"{d}/score.json"
    for d in sorted(glob.glob(f"{B}/e6/s1_k*_n*")):
        yield "E6", os.path.basename(d), d, f"{d}/gen/truth.json", f"{d}/score.json"


def ratio(a, b):
    return round(a / b, 4) if b else None


if __name__ == "__main__":
    rows, excluded = [], []
    for pop, name, d, tp, sp in populations():
        if not (os.path.exists(tp) and os.path.exists(sp)):
            excluded.append({"population": pop, "design": name, "outcome": "no score"}); continue
        T, S = json.load(open(tp)), json.load(open(sp))
        if not structure_exact(S):
            excluded.append({"population": pop, "design": name, "outcome": S.get("outcome")}); continue
        if pop == "E5.C control":
            rows.append({"population": pop, "design": name, "control": T["control"], "V": len(T["nodes"]),
                         "E": sum(len(x) for x in truth_edges(T).values()),
                         "E_delayed": sum(1 for x in truth_edges(T).values() for _, dd in x if dd >= 1),
                         "strict": control_strict(T, S), "up_to_theorem_exact": bool(S.get("exact"))})
            continue
        r = {"population": pop, "design": name, "exact": bool(S.get("exact")), **folding_views(T)}
        r["NetNotary"] = {"nodes": r["V"], "nodes_matched": r["V"], "edges": r["E"], "edges_matched": r["E"], "delayed_matched": r["E_delayed"]}
        rows.append(r)
    agg = {}
    for pop in ("E5.C", "E8.2", "E6"):
        rs = [r for r in rows if r["population"] == pop]
        if not rs:
            continue
        V, E, D = (sum(r[k] for r in rs) for k in ("V", "E", "E_delayed"))
        a = {"designs": len(rs), "excluded": sum(1 for x in excluded if x["population"] == pop), "V": V, "E": E, "E_delayed": D}
        for view in ("B0", "B1", "NetNotary"):
            s = lambda k: sum(r[view][k] for r in rs)
            tn = "truth_nodes_matched" if view == "B1" else "nodes_matched"
            te = "truth_edges_matched" if view == "B1" else "edges_matched"
            a[view] = {"M1_node_recall": ratio(s(tn), V), "M2_node_precision": ratio(s("nodes_matched"), s("nodes")),
                       "M3_edge_recall": ratio(s(te), E), "M4_edge_precision": ratio(s("edges_matched"), s("edges")),
                       "M5_delay_recovery": ratio(s("delayed_matched"), D)}
        a["B1"]["candidate_precision"] = ratio(sum(r["B1"]["candidates_true"] for r in rs), sum(r["B1"]["candidates_all"] for r in rs))
        a["B1"]["candidate_recall"] = ratio(sum(r["B1"]["candidates_hit"] for r in rs), sum(r["B1"]["candidates_true"] for r in rs))
        a["B0"]["chain_steps_mean"] = ratio(sum(r["B0"]["chain_steps_sum"] for r in rs), E)
        a["B0"]["chain_steps_max"] = max(r["B0"]["chain_steps_max"] for r in rs)
        agg[pop] = a
    ctl = {}
    for c in ("chain", "rotate"):
        rs = [r for r in rows if r["population"] == "E5.C control" and r["control"] == c]
        if rs:
            s = lambda k: sum(r["strict"][k] for r in rs)
            V, E, D = (sum(r[k] for r in rs) for k in ("V", "E", "E_delayed"))
            ctl[c] = {"designs": len(rs), "up_to_theorem_exact": sum(r["up_to_theorem_exact"] for r in rs),
                      "strict": {"M1_node_recall": ratio(s("truth_nodes_matched"), V), "M2_node_precision": ratio(s("nodes_matched"), s("nodes")),
                                 "M3_edge_recall": ratio(s("truth_edges_matched"), E), "M4_edge_precision": ratio(s("edges_matched"), s("edges")),
                                 "M5_delay_recovery": ratio(s("delayed_matched"), D)}}
    json.dump({"aggregate": agg, "controls": ctl, "rows": rows, "excluded": excluded}, open(sys.argv[1], "w"), indent=1)
    print(json.dumps({"aggregate": agg, "controls": ctl}, indent=1))
    print("excluded:", collections.Counter((x["population"], x["outcome"]) for x in excluded))
