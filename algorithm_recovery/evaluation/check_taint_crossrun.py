#!/usr/bin/env python3
"""Empirical Lemma-1 check: every net the certificate reports as untainted in the
reference run must have the same pre-edge value in another admitted run (same
non-data pads, different free data). Reads certify's taint_observed.txt and a
second native trace with the identical observed-net list."""
import json, sys

cert_dir, reference_trace, other_trace = sys.argv[1:4]
ref = json.load(open(f"{reference_trace}/native_trace.json"))
oth = json.load(open(f"{other_trace}/native_trace.json"))
assert ref["observed"] == oth["observed"], "observed net lists differ"
rows = open(f"{cert_dir}/taint_observed.txt").read().split()
other = [line.split()[0] for line in open(f"{other_trace}/native_trace.txt")]
assert len(rows) == len(other), "event counts differ"
compared = disagreements = tainted = 0
first = None
for t, (taint, value) in enumerate(zip(rows, other)):
    for i, (a, b) in enumerate(zip(taint, value)):
        if a in "01":
            compared += 1
            if a != b:
                disagreements += 1
                if first is None:
                    first = {"event": t, "net": ref["observed"][len(taint) - 1 - i], "untainted": a, "other": b}
        elif a == "t":
            tainted += 1
print(json.dumps({"status": "PASS" if disagreements == 0 else "FAIL", "events": len(rows),
                  "untainted_values_compared": compared, "tainted_values": tainted,
                  "disagreements": disagreements, "first_disagreement": first}, indent=1))
sys.exit(0 if disagreements == 0 else 1)
