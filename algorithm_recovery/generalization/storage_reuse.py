#!/usr/bin/env python3
"""E2.2 / E2.5: storage reuse in a recovered event graph and what a register-level view loses.

Reads the graph model embedded in program_<profile>/recovered.cpp. A register is
multi-role if its write events within one period compute >= 2 distinct functions
(distinct raw expression hashes; identical functions at different slots count once).
Register-level baseline: one node per register whose operand set is the union over its
write events. Spurious dependencies = union operands absent from a given event.
usage: storage_reuse.py PROGRAM_DIR"""
import collections, json, sys

d = sys.argv[1]
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
print(json.dumps({
    "period": m["period"], "words": len(by_word), "nodes": len(m["nodes"]),
    "write_events_per_word_histogram": dict(sorted(events_hist.items())),
    "multi_role_registers": len(multi_role), "multi_role_bits": sum(r["width"] for r in multi_role),
    "max_distinct_functions": multi_role[0]["distinct_functions"] if multi_role else 1,
    "register_level_baseline": {"operand_edges_union": reg_edges, "event_operand_edges": event_edges,
                                "spurious_operand_edges_if_merged": spurious},
    "signal_source_delay_histogram": dict(sorted(delays.items())),
    "excluded_words": len(m["excluded"]), "top_multi_role": multi_role[:8],
}, indent=1))
