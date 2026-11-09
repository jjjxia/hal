#!/usr/bin/env python3
"""E2.2 / E2.5: storage reuse in a recovered event graph and what a register-level view loses.

Reads the graph model embedded in program_<profile>/recovered.cpp. A register is
multi-role if its write events within one period compute >= 2 distinct functions
(distinct raw expression hashes; identical functions at different slots count once).
Register-level baseline: one node per register whose operand set is the union over its
write events. Spurious dependencies = union operands absent from a given event.
usage: storage_reuse.py PROGRAM_DIR | GRAPH_JSON"""
import collections, json, sys

d = sys.argv[1]
if d.endswith(".json"):  # a certified event graph (hal-recover graph / analyze: graph_<profile>.json)
    import hashlib
    g = json.load(open(d))
    m = {"period": g["period"], "excluded": g["excluded"],
         "nodes": [dict(n, raw_sha256=hashlib.sha256(n["smt2"].encode()).hexdigest()) for n in g["nodes"]]}
else:
    p = json.load(open(f"{d}/program.json"))
    m = json.loads(open(f"{d}/recovered.cpp", "rb").read()[p["model_offset"]:p["model_offset"] + p["model_length"]])
by_word = collections.defaultdict(list)
for n in m["nodes"]:
    by_word[n["name"]].append(n)
events_hist = collections.Counter(len(v) for v in by_word.values())
multi_role, spurious, event_edges, reg_edges = [], 0, 0, 0
for word, nodes in by_word.items():
    functions = {n["raw_sha256"] for n in nodes}
    ops = [{(s["kind"], s["name"]) for s in n["sources"] if s["name"] != word or s["kind"] != "signal"} for n in nodes]
    union = set().union(*ops) if ops else set()
    reg_edges += len(union)
    for o in ops:
        event_edges += len(o); spurious += len(union - o)
    if len(functions) >= 2:
        multi_role.append({"word": word, "write_events": len(nodes), "distinct_functions": len(functions),
                           "width": nodes[0]["width"]})
multi_role.sort(key=lambda r: -r["distinct_functions"])
delays = collections.Counter(s["delay"] for n in m["nodes"] for s in n["sources"] if s["kind"] == "signal")
# E2.5: recurrences. A register-level dependency graph (one node per register, an edge for any
# read) has cycles for every iterated register; it cannot tell an iteration INSIDE one
# transaction from state carried ACROSS transactions. In the event graph, iteration inside a
# period is a delay-0 chain of write events (acyclic), and only delay>=1 sources carry state.
reg_graph = collections.defaultdict(set)
for n in m["nodes"]:
    for s_ in n["sources"]:
        if s_["kind"] == "signal":
            reg_graph[s_["name"]].add(n["name"])
index, low, on, stack, sccs, counter = {}, {}, set(), [], [], [0]
import sys as _sys
_sys.setrecursionlimit(100000)
def strong(v):
    index[v] = low[v] = counter[0]; counter[0] += 1; stack.append(v); on.add(v)
    for w in reg_graph.get(v, ()):
        if w not in index:
            strong(w); low[v] = min(low[v], low[w])
        elif w in on:
            low[v] = min(low[v], index[w])
    if low[v] == index[v]:
        comp = []
        while True:
            w = stack.pop(); on.discard(w); comp.append(w)
            if w == v:
                break
        sccs.append(comp)
for v in list(by_word):
    if v not in index:
        strong(v)
cyclic = sorted(w for comp in sccs for w in comp if len(comp) > 1 or w in reg_graph.get(w, ()))
event_index = {(n["name"], n["phase"]): n for n in m["nodes"]}
delay0 = collections.defaultdict(set)
for n in m["nodes"]:
    for s_ in n["sources"]:
        if s_["kind"] == "signal" and s_["delay"] == 0 and (s_["name"], s_["phase"]) in event_index:
            delay0[(s_["name"], s_["phase"])].add((n["name"], n["phase"]))
# delay-0 subgraph of the event graph must be acyclic (Kahn)
indeg = collections.Counter(t for vs in delay0.values() for t in vs)
queue = [k for k in event_index if indeg[k] == 0]; seen = 0
while queue:
    k = queue.pop(); seen += 1
    for t in delay0.get(k, ()):
        indeg[t] -= 1
        if indeg[t] == 0:
            queue.append(t)
carried = sorted({(s_["name"], s_["phase"], s_["delay"]) for n in m["nodes"] for s_ in n["sources"] if s_["kind"] == "signal" and s_["delay"] >= 1})
carried_words = sorted({w for w, _, _ in carried})
print(json.dumps({
    "period": m["period"], "words": len(by_word), "nodes": len(m["nodes"]),
    "write_events_per_word_histogram": dict(sorted(events_hist.items())),
    "multi_role_registers": len(multi_role), "multi_role_bits": sum(r["width"] for r in multi_role),
    "max_distinct_functions": multi_role[0]["distinct_functions"] if multi_role else 1,
    "register_level_baseline": {"operand_edges_union": reg_edges, "event_operand_edges": event_edges,
                                "spurious_operand_edges_if_merged": spurious},
    "signal_source_delay_histogram": dict(sorted(delays.items())),
    "excluded_words": len(m["excluded"]), "top_multi_role": multi_role[:8],
    "recurrences": {"register_level_cyclic_words": len(cyclic), "register_level_cycle_words": cyclic,
                    "event_graph_delay0_acyclic": seen == len(event_index),
                    "words_with_cross_period_sources": len(carried_words), "cross_period_sources": len(carried),
                    "register_level_cycles_without_cross_period_state": sorted(set(cyclic) - set(carried_words))},
}, indent=1))
