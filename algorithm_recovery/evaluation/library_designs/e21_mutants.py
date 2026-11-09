#!/usr/bin/env python3
"""Validity control for the E2.1 scorer: write graph mutants that a correct scorer must penalise.
usage: e21_mutants.py GRAPH_JSON OUT_DIR"""
import copy, json, random, sys, os
g = json.load(open(sys.argv[1])); out = sys.argv[2]; os.makedirs(out, exist_ok=True)
rng = random.Random(20261112)
data_nodes = [i for i, n in enumerate(g["nodes"]) if any(s["kind"] == "signal" for s in n["sources"]) and n["width"] > 1]
# (a) drop one write event
m = copy.deepcopy(g); k = rng.choice(data_nodes); del m["nodes"][k]
json.dump(m, open(f"{out}/drop_node.json", "w"))
# (b) shift a producer across the transaction boundary (delay 0 -> 1) on every node that has one
m = copy.deepcopy(g); changed = 0
for n in m["nodes"]:
    for s in n["sources"]:
        if s["kind"] == "signal" and s["delay"] == 0 and s["name"] != n["name"]:
            s["delay"] = 1; changed += 1; break
json.dump(m, open(f"{out}/delay_shift.json", "w"))
# (c) wrong producer word: redirect one source of every data node to a different word
words = sorted(g["words"]); m = copy.deepcopy(g)
for n in m["nodes"]:
    for s in n["sources"]:
        if s["kind"] == "signal" and len(words) > 1:
            s["name"] = next(w for w in words if w != s["name"]); break
json.dump(m, open(f"{out}/wrong_producer.json", "w"))
print("mutants written", out, "delay-shifted nodes", changed)
