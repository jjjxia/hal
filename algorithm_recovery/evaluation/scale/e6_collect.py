#!/usr/bin/env python3
"""Collect E6 results (build/e6/s<seed>_k<K>_n<N>/) into one table.  usage: e6_collect.py [DIR...]"""
import glob, json, os, re, sys

B = "/home/jiahao/hal/build/e6"
dirs = sys.argv[1:] or sorted((d for d in glob.glob(f"{B}/s*_k*_n*") if ".prev" not in d),
                              key=lambda d: tuple(int(x) for x in re.findall(r"_k(\d+)_n(\d+)", d)[0]))
cols = ["design", "K", "N", "gates", "words", "events", "eqs", "eq_MB", "t_rtl", "t_eqs", "t_fold", "fold_GB",
        "outcome", "nodes_exact", "syn_mismatch"]
rows = []
for d in dirs:
    r = {"design": os.path.basename(d)}
    k, n = re.findall(r"_k(\d+)_n(\d+)", d)[0]
    r["K"], r["N"] = int(k), int(n)
    st = {}
    if os.path.exists(f"{d}/e6_stages.jsonl"):
        st = {x["stage"]: x for x in map(json.loads, open(f"{d}/e6_stages.jsonl"))}
    g = f"{d}/gates_stat.txt"
    if os.path.exists(g):
        m = re.findall(r"Number of cells:\s+(\d+)", open(g).read())
        r["gates"] = int(m[-1]) if m else None
    fl = f"{d}/fold.log"
    if os.path.exists(fl) and open(fl).read().strip().startswith("{"):
        f = json.loads(open(fl).read().strip().splitlines()[-1])
        r.update(words=f["words"], events=f["nodes"], eqs=f["distinct_equations"], t_fold=round(f["seconds_fold"], 2),
                 t_fold_total=round(f["seconds_total"], 2))
    if os.path.exists(f"{d}/equations.json"):
        r["eq_MB"] = round(os.path.getsize(f"{d}/equations.json") / 2**20, 1)
    for s in ("rtl", "equations", "selfcheck", "score", "gates"):
        if s in st:
            r["t_" + {"equations": "eqs"}.get(s, s)] = round(st[s]["seconds"], 1)
    if "fold" in st and st["fold"].get("peak_kb"):
        r["fold_GB"] = round(st["fold"]["peak_kb"] / 2**20, 2)
    failed = [s for s, x in st.items() if x["exit"] != 0]
    if os.path.exists(f"{d}/score.json"):
        s = json.load(open(f"{d}/score.json"))
        r.update(outcome=s["outcome"], nodes_exact=f'{s.get("nodes_exact")}/{s.get("dfg_nodes")}',
                 syn_mismatch=s.get("nodes_edges_syntactic_mismatch"), spurious=s.get("spurious_events"))
    elif failed:
        x = st[failed[0]]
        r["outcome"] = f"{failed[0]}: " + {124: "timeout (3 h)", 137: "killed (memory)"}.get(x["exit"], f"exit {x['exit']}")
    rows.append(r)
w = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols} if rows else {}
print("  ".join(c.ljust(w[c]) for c in cols))
for r in rows:
    print("  ".join(str(r.get(c, "")).ljust(w[c]) for c in cols))
    print(json.dumps(r), file=sys.stderr)
