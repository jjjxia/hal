#!/usr/bin/env python3
"""Summarise one E5 design (metrics M1-M7 of E5_EXPANSION_PROTOCOL.txt) from its result directory.
usage: e5_summarize.py CASE DIR   -> JSON on stdout"""
import gzip, json, os, sys

case, D = sys.argv[1:3]
j = lambda p: json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else None
out = {"case": case}
stages = []
for f in ("stages.jsonl", "e5_stages.jsonl"):
    p = f"{D}/{f}"
    if os.path.exists(p):
        stages += [json.loads(l) for l in open(p) if l.startswith("{")]
out["stages"] = stages
cert = j(f"{D}/analysis/certificate/certificate.json")
G = j(f"{D}/graph/graph_training.json")
contract = j(f"{D}/input/evaluation_contract.json")
out["period"] = contract and contract["period_cycles"]
# M1 outcome
if not cert:
    failed = [s for s in stages if s.get("exit", 0) != 0]
    out["outcome"] = "C1 failed: " + (failed[0]["stage"] + f" exit {failed[0]['exit']}" if failed else "no certificate")
else:
    out["certificate"] = {k: cert.get(k) for k in ("status", "origin", "period", "flip_flops", "control_nets", "seconds")}
    if cert.get("status") != "PROTOCOL_SCHEDULE_CERTIFIED":
        out["outcome"] = f"not certified ({cert.get('status')})"
    elif not (G and G.get("available")):
        out["outcome"] = "certified; no event graph"
    else:
        out["outcome"] = "certified; graph"
if G and G.get("available"):
    out["graph"] = {"period_events": G["period"], "origin": G["origin"], "nodes": len(G["nodes"]), "words": len(G["words"]),
                    "excluded": len(G.get("excluded", {}))}
# M3/M5 program
rep = j(f"{D}/program/report.json")
if rep:
    out["program"] = {"status": rep.get("status"), "lines_flat": rep.get("flat_program_lines"),
                      "lines_rolled": rep.get("rolled_program_lines"), "lines_lifted": rep.get("program_lines"),
                      "loops": [l.get("iterations") for l in rep.get("loops", [])],
                      "tables": {k: {"inputs": t.get("inputs"), "instances": t.get("instances")} for k, t in rep.get("tables", {}).items()},
                      "arithmetic": len(rep.get("arithmetic", [])), "validation": rep.get("validation"),
                      "state_values": rep.get("state_values")}
    if out["outcome"] == "certified; graph":
        out["outcome"] = "program" if rep.get("status") == "PASS" else f"program {rep.get('status')}"
elif G and G.get("available"):
    log = f"{D}/program.log"
    tail = open(log).read().strip().splitlines()[-1:] if os.path.exists(log) else []
    out["program_refused"] = tail[0][:300] if tail else None
    out["outcome"] = "graph; program refused"
ref = j(f"{D}/program/reference_check.json")
if ref:
    out["reference"] = {"status": ref.get("status"), "trials": ref.get("fresh_random_trials"), "mismatches": ref.get("mismatches"),
                        "compared": ref.get("compared_values")}
cc = j(f"{D}/close_chain.json")
if cc:
    out["close_chain"] = {os.path.basename(p["program"]): {"closed": p["closed"], "writes": p["writes"], "outputs": p["outputs"]}
                          for p in cc["programs"]}
rtl = j(f"{D}/rtl/report.json")
if rtl:
    out["rtl"] = {"registers": rtl.get("word_registers"), "lines": rtl.get("verilog_lines"),
                  "mismatch_free": all(v.get("first_mismatch") is None for v in rtl.get("validation", [])),
                  "cycles": [v.get("cycles") for v in rtl.get("validation", [])]}
orc = j(f"{D}/oracle.json")
if orc:
    out["oracle"] = {k: orc.get(k) for k in ("truth_write_events", "write_recall", "write_precision", "producer_scored_events",
                                              "producer_exact_match", "producer_delay_accuracy", "rtl_registers_split_over_words",
                                              "words_merging_registers")}
# M5 compaction (gzip -9 bytes)
def gz(p):
    return len(gzip.compress(open(p, "rb").read(), 9)) if os.path.exists(p) else None
# as in Loop Rerolling: the same representation before and after roll-up/lifting (flat vs final program); netlist for scale
net = gz(f"{D}/input/recovered.v")
flat = gz(f"{D}/program/program_flat.py")
prog = gz(f"{D}/program/program.py")
out["compaction"] = {"netlist_gzip": net, "flat_program_gzip": flat, "program_gzip": prog,
                     "program_vs_flat": round(prog / flat, 3) if flat and prog else None}
print(json.dumps(out, indent=1))
