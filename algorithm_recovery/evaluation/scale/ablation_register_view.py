#!/usr/bin/env python3
"""Ablation (ABLATION_REGISTER_VIEW.txt): what a register-level view of the recovered words loses.
usage: ablation_register_view.py OUT.json        (scans the result directories listed in the protocol)"""
import collections, glob, json, os, re, sys

B = "/home/jiahao/hal/build"


def load(path):
    """Uniform view: nodes [(word, phase, equation_text, [(kind, name, phase, delay, symbol)])]."""
    G = json.load(open(path))
    if not G.get("available"):
        return None
    out = []
    for n in G["nodes"]:
        if isinstance(n.get("sources"), dict):     # E6 driver dump: symbol -> [kind, name, phase, delay]
            srcs = [(v[0], v[1], v[2], v[3], s) for s, v in n["sources"].items()]
            eq = n["equation"].strip()
            copy = len(srcs) == 1 and eq == "|" + srcs[0][4] + "|"
        else:                                      # hal-recover graph: list of dicts, smt2 text
            srcs = [(s["kind"], s["name"], s["phase"], s["delay"], s["symbol"]) for s in n["sources"]]
            body = n["smt2"].split("(= __out", 1)[-1].strip()
            copy = len(srcs) == 1 and re.fullmatch(r"\|?" + re.escape(srcs[0][4]) + r"\|?\)\)\s*\(check-sat\)", body) is not None
        out.append((n["name"], n["phase"], copy, srcs))
    return out


def scc_members(edges, nodes):
    """Nodes on a cycle (Tarjan; self-loops count)."""
    idx, low, on, st, res, c = {}, {}, set(), [], set(), [0]
    adj = collections.defaultdict(list)
    for a, b in edges:
        adj[a].append(b)
    sys.setrecursionlimit(1000000)

    def visit(v):
        idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
        for w in adj[v]:
            if w not in idx:
                visit(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp = []
            while True:
                w = st.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or (v, v) in set(edges):
                res.update(comp)
    for v in nodes:
        if v not in idx:
            visit(v)
    return res


def analyse(nodes):
    k = collections.Counter(w for w, _, _, _ in nodes)
    nc = [(w, p, s) for w, p, cp, s in nodes if not cp]
    words = set(k)
    merged = {w for w in words if sum(1 for x, _, _ in nc if x == w) >= 2}
    true_src = spurious = delayed = 0
    wedges, e0, eall = set(), set(), set()
    for w, p, cp, srcs in nodes:
        for kind, name, sp, d, _ in srcs:
            if kind != "signal":
                continue
            wedges.add((name, w))
            eall.add(((name, sp), (w, p)))
            if d == 0:
                e0.add(((name, sp), (w, p)))
            if not cp:
                true_src += 1; spurious += k[name] - 1; delayed += int(d >= 1)
    ev = {(w, p) for w, p, _, _ in nodes}
    # unique event-to-event edges: true (as resolved) versus implied by the register view (every event of the word)
    phases = collections.defaultdict(set)
    for w, p, _, _ in nodes:
        phases[w].add(p)
    true_pairs, view_pairs = set(), set()
    for w, p, cp, srcs in nodes:
        if cp:
            continue
        for kind, name, sp, d, _ in srcs:
            if kind == "signal":
                true_pairs.add(((name, sp), (w, p)))
                view_pairs.update(((name, q), (w, p)) for q in phases[name])
    return {"words": len(words), "events": len(nodes), "non_copy_events": len(nc), "merged_words": len(merged),
            "events_in_merged_words": sum(1 for w, _, _ in nc if w in merged),
            "true_sources": true_src, "spurious_candidates": spurious, "delayed_sources": delayed,
            "true_edges_unique": len(true_pairs), "spurious_edges_unique": len(view_pairs - true_pairs),
            "register_view_words_on_cycles": len(scc_members(wedges, words)),
            "events_on_within_sample_cycles": len(scc_members(e0, ev)),
            "events_on_cycles_all_delays": len(scc_members(eall, ev))}


rows = []
sets = [("E5.C", sorted(glob.glob(f"{B}/e5c/e5c_s*/case/graph/graph_training.json"))),
        ("E6", sorted(glob.glob(f"{B}/e6/s1_k*_n*/graph.json"))),
        ("E5.A/B", sorted(glob.glob(f"{B}/bench2_e5/*/graph/graph_training.json"))),
        # frozen eight: the graphs the paper's programs were built from (c3/run_close_chain.sh)
        ("frozen", [f"{B}/bench2_v1/{d}/{g}/graph_training.json" for d, g in
                    [("present", "graph_v1"), ("fir_decim", "graph_v1"), ("small_hb_dec_r2", "graph_v1"), ("ibex_mul", "graph_v1"),
                     ("qdiv", "graph_v1"), ("sha1", "analysis"), ("bambu_arf", "graph_v4"), ("ascon", "analysis")]])]
for exp, paths in sets:
    for p in paths:
        if ".prev" in p or "interrupted" in p or "partial" in p:
            continue
        g = load(p)
        if g is None:
            continue
        r = {"experiment": exp, "design": p.split("/")[-4] if exp in ("E5.C",) else p.split("/")[-3] if exp != "E6" else p.split("/")[-2]}
        r.update(analyse(g))
        rows.append(r)
        print(json.dumps(r))
agg = collections.defaultdict(collections.Counter)
for r in rows:
    for kk, v in r.items():
        if isinstance(v, int):
            agg[r["experiment"]][kk] += v
    agg[r["experiment"]]["designs"] += 1
json.dump({"rows": rows, "aggregate": agg}, open(sys.argv[1], "w"), indent=1)
for e, a in agg.items():
    print(e, dict(a), "spurious/true = %.2f" % (a["spurious_candidates"] / max(a["true_sources"], 1)))
