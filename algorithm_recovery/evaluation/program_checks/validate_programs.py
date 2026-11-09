#!/usr/bin/env python3
"""Run the generated programs (flat, rolled, lifted) on the held-out transactions of the original-gate trace.

Each program is executed by CPython exactly as saved. A transaction starts at theta + k*P (theta: first transaction
event, P: period events) and is held out if it starts at or after training_end_exclusive. Data pads are read from the
trace at their phases; the loop-carried state values (word, phase, delay) are the trace values of word after the event
start + phase - delay*P. Every observation (port, phase) is compared with the trace bits of that port at start + phase.
Transactions with an unknown pad or state bit are skipped; unknown observed bits are counted, not compared.
usage: validate_programs.py GRAPH_JSON TRACE_DIR CONTRACT_JSON PROGRAM_DIR
Writes PROGRAM_DIR/validation.json; exit status 1 if a program mismatches or compares nothing."""
import collections, json, os, sys

graph_path, trace_dir, contract_path, program_dir = sys.argv[1:5]
G = json.load(open(graph_path))
contract = json.load(open(contract_path))
interface = json.load(open(os.path.join(program_dir, "interface.json")))
words, inputs = G["words"], G["inputs"]
P, origin = G["period"], G["origin"]
shift = (contract["transactions"][0]["first_event"] - origin) % P
theta = origin + shift
training_end = contract["training_end_exclusive"]
pad_reads = [tuple(k) for k in interface["pad_reads"]]
state_keys = [tuple(k) for k in interface["state_keys"]]
ports = collections.defaultdict(list)
for p in contract["port_bits"]:
    if p["direction"] == "output":
        ports[p["name"]].append(p)
for bits in ports.values():
    bits.sort(key=lambda p: p["offset"])

meta = json.load(open(os.path.join(trace_dir, "native_trace.json")))
pos = {b: i for i, b in enumerate(meta["observed"])}
rows = [line.split() for line in open(os.path.join(trace_dir, "native_trace.txt"))]


def bitval(b, t, post=False):
    r = rows[t][1 if post else 0]
    c = r[len(r) - 1 - pos[b]]
    return None if c not in "01" else int(c)


def wordval(name, t, post):
    v = 0
    for i, b in enumerate(words[name]["bits"]):
        x = bitval(int(b[1:]), t, post)
        if x is None:
            return None
        v |= x << i
    return v


def validate(path):
    ns = {}
    exec(compile(open(path).read(), path, "exec"), ns)
    results = collections.Counter()
    first_failure = None
    for start in range(theta, len(rows) - 2 * P, P):
        if start < training_end:
            continue
        pads = {(base, ph): bitval(int(inputs[base]["bits"][0][1:]), start + ph) for (base, ph) in pad_reads}
        state = {(name, ph, d): wordval(name, start + ph - d * P, True) for (name, ph, d) in state_keys}
        if any(v is None for v in state.values()) or any(v is None for v in pads.values()):
            results["skipped_unknown"] += 1
            continue
        outs, _ = ns["transaction"](pads, state)
        for (port, ph), value in outs.items():
            expected = 0
            for i, p in enumerate(ports[port]):
                x = bitval(p["physical_bit"], start + ph)
                if x is None:
                    expected = None
                    break
                expected |= x << i
            if expected is None:
                results["unknown_observation"] += 1
                continue
            results["compared"] += 1
            if value != expected:
                results["mismatch"] += 1
                if first_failure is None:
                    first_failure = {"start": start, "port": port, "phase": ph, "program": value, "trace": expected}
        results["transactions"] += 1
    return {"results": dict(results), "first_failure": first_failure}


programs = {name: validate(os.path.join(program_dir, name)) for name in ("program_flat.py", "program_rolled.py", "program.py")}
ok = all(v["results"].get("compared") and not v["results"].get("mismatch") for v in programs.values())
report = {"status": "PASS" if ok else "FAIL",
          "validated_on": "held-out transactions (start >= training_end_exclusive), state from the trace",
          "programs": programs}
json.dump(report, open(os.path.join(program_dir, "validation.json"), "w"), indent=1)
print(json.dumps(report, indent=1))
sys.exit(0 if ok else 1)
