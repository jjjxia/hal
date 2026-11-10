#!/usr/bin/env python3
"""R2 (RECOVERY_QUALITY_PROTOCOL.txt): folding strength. Per design: N, sharing (operations per unit, values per result
register), the stage reached and failure class, NetNotary exactness and exact-node share, B0/B1 from the folding (the
views with correct words), C1 stage times. Populations: R2a build/r2/random (seeds 101-120), R2b build/r2/kernels
(4min, 8min) with E8.2 build/e8/fold (min, 2min).
Failure classes: not generated; frontend-resource (grouping out of memory or time); frontend-refused (grouping stopped
by a consistency check); frontend-wrong (words wrong: the scorer's grouping outcome); function-unknown (structure exact, z3 unknown on some
node functions); applicability (no certified
periodic schedule or no event graph); truth-dead (the only missing nodes reach no output, so synthesis removed them;
amendment 1); inverse-folding (any other mismatch with correct words); exact.
usage: r2_collect.py OUT.json"""
import collections, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r1_structure import folding_views

B = "/home/jiahao/hal/build"


def observable(T):
    """DFG nodes from which an output is reachable along operand edges."""
    readers = collections.defaultdict(set)
    for v, nd in T["nodes"].items():
        for u, _ in nd["operands"]:
            readers[u].add(v)
    seen, stack = set(T["outputs"]), list(T["outputs"])
    while stack:
        v = stack.pop()
        for u, _ in T["nodes"][v]["operands"]:
            if u in T["nodes"] and u not in seen:
                seen.add(u); stack.append(u)
    return seen


def stages(case_dir):
    p = f"{case_dir}/stages.jsonl"
    return [json.loads(l) for l in open(p) if l.startswith("{")] if os.path.exists(p) else []


def classify(d, case_dir, T, S):
    st = stages(case_dir)
    failed = [s for s in st if s.get("exit", 0) != 0]
    if T is None:
        return "not generated"
    if failed and failed[0]["stage"] == "groups":
        log = open(f"{case_dir}/groups.log", errors="replace").read() if os.path.exists(f"{case_dir}/groups.log") else ""
        return "frontend-resource" if ("bad_alloc" in log or failed[0]["exit"] in (124, 137)) else "frontend-refused"
    if S is None and not failed:
        return "running"
    if S is None or not S.get("graph_available"):
        return "applicability" if not failed or failed[0]["stage"] in ("graph", "analyze") else f"stage {failed[0]['stage']}"
    if S.get("exact"):
        return "exact"
    if S.get("outcome") == "grouping":
        return "frontend-wrong"
    if S.get("nodes_found") == S.get("nodes_scored") == S.get("nodes_edges_exact") and not S.get("spurious_result_events"):
        return "function-unknown"             # structure exact; z3 neither proves nor refutes some node functions
    missing = {f["node"] for f in S.get("failures", []) if not f.get("found")}
    wrong = [f for f in S.get("failures", []) if f.get("found")]
    if missing and not wrong and not S.get("spurious_result_events") and not (missing & observable(T)) \
            and S.get("nodes_found") + len(missing) == S.get("nodes_scored"):
        return "truth-dead"
    return "inverse-folding"


def designs():
    for d in sorted(glob.glob(f"{B}/r2/random/e5c_s*")):
        yield "R2a", d, f"{d}/case"
    for root in (f"{B}/e8/fold", f"{B}/r2/kernels"):
        for d in sorted(glob.glob(f"{root}/*")):
            if os.path.isdir(d):
                yield "R2b", d, f"{d}/case"


if __name__ == "__main__":
    rows = []
    for pop, d, case_dir in designs():
        tp, sp = f"{d}/gen/truth.json", f"{d}/score.json"
        T = json.load(open(tp)) if os.path.exists(tp) else None
        S = json.load(open(sp)) if os.path.exists(sp) else None
        if T is None and not os.path.exists(f"{d}/gen"):
            continue
        r = {"population": pop, "design": os.path.basename(d), "class": classify(d, case_dir, T, S)}
        if T is not None:
            V = folding_views(T)
            r.update(N=T["N"], nspec=T.get("nspec"), policy=T.get("policy"), kernel=T.get("kernel"), V=V["V"], E=V["E"],
                     units=V["units"], result_registers=V["result_registers"],
                     ops_per_unit=round(V["V"] / V["units"], 3) if V["units"] else None,
                     values_per_register=round(V["V"] / V["result_registers"], 3),
                     B0={"node_precision": round(V["B0"]["nodes_matched"] / V["B0"]["nodes"], 4),
                         "exact_node_share": round(V["B0"]["exact_nodes"] / V["V"], 4),
                         "edge_recall": round(V["B0"]["edges_matched"] / V["E"], 4) if V["E"] else None},
                     B1={"node_recall": round(V["B1"]["truth_nodes_matched"] / V["V"], 4),
                         "exact_node_share": round(V["B1"]["exact_nodes"] / V["V"], 4),
                         "edge_recall": round(V["B1"]["truth_edges_matched"] / V["E"], 4) if V["E"] else None,
                         "candidate_precision": round(V["B1"]["candidates_true"] / V["B1"]["candidates_all"], 4) if V["B1"]["candidates_all"] else None})
            r["exact_node_share"] = round(S["nodes_exact"] / V["V"], 4) if S and S.get("graph_available") and "nodes_exact" in S else 0.0
            if r["class"] == "truth-dead":
                obs = observable(T)
                r["exact_node_share_observable"] = round(S["nodes_exact"] / len(obs & set(T["nodes"])), 4)
        # NetNotary with correct words, structural (found with equal operand edges, as B0/B1 are scored): the gate-level
        # graph when the words are right, else the control with RTL words (amendment 2)
        rw = f"{d}/rtl_words/score.json"
        if T is not None:
            if r["class"].startswith("frontend") and os.path.exists(rw):
                RW = json.load(open(rw))
                r["correct_words_structural_share"] = round(RW["nodes_exact"] / len(T["nodes"]), 4)
            elif S and S.get("graph_available") and "nodes_edges_exact" in S and not r["class"].startswith("frontend"):
                r["correct_words_structural_share"] = round(S["nodes_edges_exact"] / len(T["nodes"]), 4)
        r["stage_seconds"] = {s["stage"]: s.get("seconds") for s in stages(case_dir)}
        rows.append(r)
    agg = {}
    for pop in ("R2a", "R2b"):
        by = collections.defaultdict(list)
        for r in rows:
            if r["population"] == pop and "N" in r and r["class"] != "running":
                by[r["N"] if pop == "R2a" else r["nspec"]].append(r)
        for lvl, rs in sorted(by.items(), key=lambda x: str(x[0]).zfill(6)):
            agg[f"{pop} {lvl}"] = {
                "designs": len(rs), "classes": dict(collections.Counter(r["class"] for r in rs)),
                "ops_per_unit_mean": round(sum(r["ops_per_unit"] for r in rs) / len(rs), 3),
                "values_per_register_mean": round(sum(r["values_per_register"] for r in rs) / len(rs), 3),
                "NetNotary_exact_node_share": round(sum(r["exact_node_share"] * r["V"] for r in rs) / sum(r["V"] for r in rs), 4),
                "B1_node_recall": round(sum(r["B1"]["node_recall"] * r["V"] for r in rs) / sum(r["V"] for r in rs), 4),
                "B1_exact_node_share": round(sum(r["B1"]["exact_node_share"] * r["V"] for r in rs) / sum(r["V"] for r in rs), 4),
                "B0_exact_node_share": round(sum(r["B0"]["exact_node_share"] * r["V"] for r in rs) / sum(r["V"] for r in rs), 4),
                "NetNotary_correct_words_structural_share": (round(sum(r["correct_words_structural_share"] * r["V"] for r in rs if "correct_words_structural_share" in r)
                                                                   / sum(r["V"] for r in rs if "correct_words_structural_share" in r), 4)
                                                             if any("correct_words_structural_share" in r for r in rs) else None),
                "designs_without_correct_words_result": sum(1 for r in rs if "correct_words_structural_share" not in r),
                "B1_edge_recall": round(sum(r["B1"]["edge_recall"] * r["E"] for r in rs) / sum(r["E"] for r in rs), 4),
                "B0_node_precision_mean": round(sum(r["B0"]["node_precision"] for r in rs) / len(rs), 4),
                "C1_seconds_median": sorted(sum(v or 0 for v in r["stage_seconds"].values()) for r in rs)[len(rs) // 2]}
    json.dump({"aggregate": agg, "rows": rows}, open(sys.argv[1], "w"), indent=1)
    for k, a in agg.items():
        print(k, json.dumps(a))
