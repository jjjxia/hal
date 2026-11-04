#!/usr/bin/env python3
"""Register-view ablation with the paper's E2.2 metric: storage_reuse.py (unchanged) on every recovered event graph.
E6 graphs (e6_fold dump) are converted to the hal-recover graph format first (name, phase, width, smt2, sources).
usage: ablation_e22.py OUT.json"""
import glob, json, os, subprocess, sys, tempfile

B = "/home/jiahao/hal/build"
SR = "/home/jiahao/hal/algorithm_recovery/generalization/storage_reuse.py"
FROZEN = [("present", "graph_v1"), ("fir_decim", "graph_v1"), ("small_hb_dec_r2", "graph_v1"), ("ibex_mul", "graph_v1"),
          ("qdiv", "graph_v1"), ("sha1", "analysis"), ("bambu_arf", "graph_v4"), ("ascon", "analysis")]
sets = [("frozen", d, f"{B}/bench2_v1/{d}/{g}/graph_training.json") for d, g in FROZEN]
sets += [("E5.A/B", p.split("/")[-3], p) for p in sorted(glob.glob(f"{B}/bench2_e5/*/graph/graph_training.json"))
         if not any(x in p for x in (".prev", "interrupted", "partial"))]
sets += [("E5.C", p.split("/")[-4], p) for p in sorted(glob.glob(f"{B}/e5c/e5c_s*/case/graph/graph_training.json"))]
sets += [("E6", p.split("/")[-2], p) for p in sorted(glob.glob(f"{B}/e6/s1_k*_n*/graph.json")) if ".prev" not in p]


def as_hal(path):
    g = json.load(open(path))
    nodes = [{"name": n["name"], "phase": n["phase"], "width": 8, "smt2": n["equation"],
              "sources": [{"kind": v[0], "name": v[1], "phase": v[2], "delay": v[3], "symbol": s} for s, v in n["sources"].items()]}
             for n in g["nodes"]]
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"period": g["period"], "excluded": g["excluded"], "nodes": nodes, "available": g["available"]}, f); f.close()
    return f.name


rows = []
for exp, design, path in sets:
    if not os.path.exists(path) or not json.load(open(path)).get("available"):
        continue
    src = as_hal(path) if exp == "E6" else path
    r = json.loads(subprocess.run(["python3", SR, src], capture_output=True, text=True, check=True).stdout)
    if exp == "E6":
        os.unlink(src)
    b, rc = r["register_level_baseline"], r["recurrences"]
    row = {"experiment": exp, "design": design, "words": r["words"], "events": r["nodes"],
           "multi_role_words": r["multi_role_registers"], "max_functions_per_word": r["max_distinct_functions"],
           "event_operand_edges": b["event_operand_edges"], "spurious_if_merged": b["spurious_operand_edges_if_merged"],
           "register_view_cyclic_words": rc["register_level_cyclic_words"], "delay0_acyclic": rc["event_graph_delay0_acyclic"],
           "cross_period_sources": rc["cross_period_sources"]}
    rows.append(row)
    print(json.dumps(row))
agg = {}
for r in rows:
    a = agg.setdefault(r["experiment"], {"designs": 0, "words": 0, "events": 0, "multi_role_words": 0, "event_operand_edges": 0,
                                         "spurious_if_merged": 0, "register_view_cyclic_words": 0, "all_delay0_acyclic": True,
                                         "max_functions_per_word": 0})
    a["designs"] += 1
    for k in ("words", "events", "multi_role_words", "event_operand_edges", "spurious_if_merged", "register_view_cyclic_words"):
        a[k] += r[k]
    a["all_delay0_acyclic"] &= r["delay0_acyclic"]
    a["max_functions_per_word"] = max(a["max_functions_per_word"], r["max_functions_per_word"])
json.dump({"rows": rows, "aggregate": agg}, open(sys.argv[1], "w"), indent=1)
for e, a in agg.items():
    print(e, a)
