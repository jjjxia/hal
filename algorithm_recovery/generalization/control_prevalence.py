#!/usr/bin/env python3
"""T1.3: how often synthesis puts data on FF control pins (enable / reset / set / derived clock).

Reads one analysed case (analyze --protocol output) and its trace. A control net is
data-carrying at an event if the protocol-relative taint marks it; the trap applies to any
recovery that fixes it to its trace value. Counts are over the certified periodic region
[origin, origin + period). usage: control_prevalence.py ANALYSIS_DIR TRACE_DIR [ORIGINAL_JSON]"""
import json, sys

analysis, trace_dir = sys.argv[1], sys.argv[2]
cert = json.load(open(f"{analysis}/certificate/certificate.json"))
meta = json.load(open(f"{trace_dir}/native_trace.json"))
observed = meta["observed"]
controls = [c for c in meta["controls"] if isinstance(c, int)]
pos = {b: i for i, b in enumerate(observed)}
rows = open(f"{analysis}/certificate/taint_observed.txt").read().split()
origin, period = cert["origin"], cert["period"]
data_carrying, per_phase = set(), []
for t in range(origin, origin + period):
    row = rows[t]; n = 0
    for c in controls:
        if row[len(row) - 1 - pos[c]] == "t":
            data_carrying.add(c); n += 1
    per_phase.append(n)
ff_pins = {}
if len(sys.argv) > 3:
    module = list(json.load(open(sys.argv[3]))["modules"].values())[0]
    for cell in module["cells"].values():
        if cell["type"].startswith("SB_DFF"):
            for pin in ("E", "R", "S"):
                if pin in cell["connections"]:
                    ff_pins.setdefault(cell["connections"][pin][0], []).append(pin)
sse = json.load(open(f"{analysis}/sse_report.json"))
fold = json.load(open(f"{analysis}/fold_report.json"))
profile = next(iter(fold.values()))
affected_ffs = sum(len(ff_pins.get(c, [])) for c in data_carrying)
total_ffs = sum(len(v) for v in ff_pins.values())
print(json.dumps({
    "certificate": cert["status"], "origin": origin, "period": period,
    "control_nets": len(controls), "data_carrying_control_nets": len(data_carrying),
    "data_carrying_per_phase_max": max(per_phase), "data_carrying_per_phase_mean": sum(per_phase) / period,
    "ff_control_pin_uses": total_ffs or None, "ff_control_pin_uses_on_data_carrying_nets": affected_ffs if ff_pins else None,
    "complete_update_words": sse.get("complete_update_words"), "words": sse.get("groups"),
    "graph_nodes": profile.get("nodes"), "excluded_words": len(profile.get("excluded", {})),
}, indent=1))
