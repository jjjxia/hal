#!/usr/bin/env python3
"""Second admitted run for the Lemma-1 cross-run check: identical non-data pads,
independently random data pads at every event. usage: STIMULUS PROTOCOL OUT SEED"""
import json, random, sys

stim, proto, out, seed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
s = json.load(open(stim)); fixed = set(json.load(open(proto))["non_data_inputs"]); rng = random.Random(seed)
s["events"] = [[v if b in fixed else rng.getrandbits(1) for b, v in zip(s["input_bits"], row)] for row in s["events"]]
s["description"] = "Lemma-1 cross-run check: same non-data pads as the reference stimulus, random data pads every event."
json.dump(s, open(out, "w"))
