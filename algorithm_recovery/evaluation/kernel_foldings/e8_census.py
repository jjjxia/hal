#!/usr/bin/env python3
"""E8.1 difficulty census (E8_PREMISES_PROTOCOL.txt): what the equations leave open inside the premises.
usage: e8_census.py OUT.json        (reads the graphs listed in e6/ablation_e22.py: frozen, E5.A/B, E5.C, E6)"""
import collections, glob, json, os, re, sys

B = "/home/jiahao/hal/build"
FROZEN = [("present", "graph_v1"), ("fir_decim", "graph_v1"), ("small_hb_dec_r2", "graph_v1"), ("ibex_mul", "graph_v1"),
          ("qdiv", "graph_v1"), ("sha1", "analysis"), ("bambu_arf", "graph_v4"), ("ascon", "analysis")]
sets = [("frozen", d, f"{B}/bench2_v1/{d}/{g}/graph_training.json") for d, g in FROZEN]
sets += [("E5.A/B", p.split("/")[-3], p) for p in sorted(glob.glob(f"{B}/bench2_e5/*/graph/graph_training.json"))
         if not any(x in p for x in (".prev", "interrupted", "partial"))]
sets += [("E5.C", p.split("/")[-4], p) for p in sorted(glob.glob(f"{B}/e5c/e5c_s*/case/graph/graph_training.json"))]
sets += [("E6", p.split("/")[-2], p) for p in sorted(glob.glob(f"{B}/e6/s1_k*_n*/graph.json")) if ".prev" not in p]


def load(path):
    G = json.load(open(path))
    if not G.get("available"):
        return None
    nodes = []
    for n in G["nodes"]:
        if isinstance(n.get("sources"), dict):
            srcs = [(v[0], v[1], v[2], v[3]) for s, v in n["sources"].items()]
            eq = n["equation"].strip()
            copy = len(n["sources"]) == 1 and eq == "|" + next(iter(n["sources"])) + "|"
        else:
            srcs = [(s["kind"], s["name"], s["phase"], s["delay"]) for s in n["sources"]]
            body = n["smt2"].split("(= __out", 1)[-1].strip()
            copy = len(n["sources"]) == 1 and re.fullmatch(r"\|?" + re.escape(n["sources"][0]["symbol"]) + r"\|?\)\)\s*\(check-sat\)", body) is not None
        nodes.append((n["name"], n["phase"], copy, srcs))
    return G["period"], nodes


def census(P, nodes):
    by_word = collections.defaultdict(list)
    for w, p, cp, srcs in nodes:
        by_word[w].append((p, cp, srcs))
    nc = [(w, p, s) for w, p, cp, s in nodes if not cp]
    shared = {w for w, ev in by_word.items() if sum(1 for _, cp, _ in ev if not cp) >= 2}
    # D2 operand multiplexing: distinct operand-word sets among a word's non-copy events
    muxed, max_sets = 0, 0
    for w, ev in by_word.items():
        sets_ = {frozenset((k, nm) for k, nm, _, _ in s if not (k == "signal" and nm == w)) for _, cp, s in ev if not cp}
        max_sets = max(max_sets, len(sets_))
        muxed += int(len(sets_) >= 2)
    # D3 holds: (word, phase) pairs without an event
    pairs = len(by_word) * P
    holds = pairs - len(nodes)
    partial = sum(1 for ev in by_word.values() if len(ev) < P)
    # D4 copy chains
    ev_index = {(w, p): (cp, s) for w, p, cp, s in nodes}
    def chain(key, seen=0):
        cp, s = ev_index.get(key, (False, []))
        if not cp or seen > 256:
            return 0
        k, nm, ph, d = s[0]
        return 1 + (chain((nm, ph), seen + 1) if k == "signal" else 0)
    copies = sum(1 for _, _, cp, _ in nodes if cp)
    longest = max([chain((w, p)) for w, p, cp, _ in nodes if cp] or [0])
    # D5 cross-sample reads, D6 origin dependence (shift by P/2: rho = 1 for phases < s)
    srcs_all = [(w, p, k, nm, ph, d) for w, p, cp, s in nodes for k, nm, ph, d in s if k == "signal"]
    delayed = sum(1 for *_, d in srcs_all if d >= 1)
    histories = len({(nm, ph, d) for _, _, k, nm, ph, d in srcs_all if d >= 1})
    s_ = P // 2
    rho = lambda ph: int(ph < s_)
    origin_dep = sum(1 for w, p, k, nm, ph, d in srcs_all if rho(ph) != rho(p))
    return {"period": P, "words": len(by_word), "events": len(nodes), "non_copy_events": len(nc),
            "D1_shared_words": len(shared), "D1_max_events_per_word": max(sum(1 for _, cp, _ in ev if not cp) for ev in by_word.values()),
            "D1_events_in_shared_words": sum(1 for w, _, _ in nc if w in shared),
            "D2_muxed_words": muxed, "D2_max_operand_sets": max_sets,
            "D3_hold_pairs": holds, "D3_word_phase_pairs": pairs, "D3_partially_written_words": partial,
            "D4_copy_events": copies, "D4_longest_copy_chain": longest,
            "D5_signal_sources": len(srcs_all), "D5_delayed_sources": delayed, "D5_history_entries": histories,
            "D6_origin_dependent_sources": origin_dep}


rows = []
for exp, design, path in sets:
    if not os.path.exists(path):
        continue
    g = load(path)
    if g is None:
        continue
    r = {"experiment": exp, "design": design}
    r.update(census(*g))
    rows.append(r)
agg = {}
for r in rows:
    a = agg.setdefault(r["experiment"], collections.Counter())
    a["designs"] += 1
    for k, v in r.items():
        if isinstance(v, int) and not k.startswith(("D1_max", "D2_max", "D4_longest", "period")):
            a[k] += v
    for k in ("D1_max_events_per_word", "D2_max_operand_sets", "D4_longest_copy_chain"):
        a[k] = max(a[k], r[k])
    for k in ("D1_shared_words", "D2_muxed_words", "D5_delayed_sources", "D6_origin_dependent_sources", "D4_copy_events"):
        a["designs_with_" + k] += int(r[k] > 0)
json.dump({"rows": rows, "aggregate": agg}, open(sys.argv[1], "w"), indent=1)
for e, a in agg.items():
    print(e, dict(a))
