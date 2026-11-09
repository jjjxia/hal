#!/usr/bin/env python3
"""Collect the E5.A/B per-design summaries (build/bench2_e5/<case>/e5_summary.json) into one table.
usage: e5_collect.py CASE...   -> fixed-width text table on stdout, JSON lines on stderr"""
import json, os, sys

B = "/home/jiahao/hal/build/bench2_e5"


def row(case):
    p = f"{B}/{case}/e5_summary.json"
    if not os.path.exists(p):
        sj = f"{B}/{case}/stages.jsonl"
        if os.path.exists(sj):   # the frozen C1 pipeline stopped; e5_run_case.sh writes no summary then
            st = [json.loads(l) for l in open(sj) if l.startswith("{")]
            bad = [x for x in st if x.get("exit", 0) != 0]
            if bad:
                rc = bad[0]["exit"]
                why = {134: "abort (std::bad_alloc under the 6 GiB limit)" if "bad_alloc" in
                       open(f"{B}/{case}/{bad[0]['stage']}.log", errors="replace").read() else "abort",
                       124: "timeout (3 h)"}.get(rc, f"exit {rc}")
                return {"case": case, "outcome": f"{bad[0]['stage']} stage: {why}",
                        "seconds": round(sum(x.get("seconds", 0) for x in st))}
        prep = f"{B}/{case}.prepare.log"
        if os.path.exists(prep) and not os.path.exists(f"{B}/{case}/input/anonymization_equivalence.json"):
            log = open(prep, errors="replace").read().strip()
            return {"case": case, "outcome": "prepare: " + (log.splitlines()[-1][:120] if log else
                                                            "no output (stopped at the 3 h stage limit)")}
        return {"case": case, "outcome": "not run"}
    s = json.load(open(p))
    st = {x["stage"]: x for x in s.get("stages", [])}
    secs = sum(x.get("seconds") or 0 for x in s.get("stages", []))
    peak = max([x.get("peak_kb") or 0 for x in s.get("stages", [])] or [0])
    pr, rf, cc, rt, orc, cm = (s.get(k) or {} for k in ("program", "reference", "close_chain", "rtl", "oracle", "compaction"))
    cert = s.get("certificate") or {}
    return {
        "case": case, "outcome": s.get("outcome"), "period_cycles": s.get("period"),
        "ffs": cert.get("flip_flops"), "cert_s": cert.get("seconds") and round(cert["seconds"], 1),
        "lines": pr and f'{pr.get("lines_flat")}/{pr.get("lines_rolled")}/{pr.get("lines_lifted")}',
        "loops": pr.get("loops"), "tables": pr and {k: f'{t["inputs"]}b x{t["instances"]}' for k, t in (pr.get("tables") or {}).items()},
        "validation": pr.get("validation") and f'{pr["validation"].get("compared")}',
        "reference": rf and f'{rf.get("status")} {rf.get("trials")}/{rf.get("mismatches")}',
        "closed": cc and all(v["closed"] for v in cc.values()), "writes": cc.get("program.py", {}).get("writes"),
        "rtl": (rt.get("mismatch_free") if rt else ("refused" if st.get("rtl", {}).get("exit") else None)),
        "recall": orc.get("write_recall") is not None and round(orc["write_recall"], 3) or orc.get("write_recall"),
        "precision": orc.get("write_precision") is not None and round(orc["write_precision"], 3) or orc.get("write_precision"),
        "producer": orc.get("producer_exact_match") is not None and round(orc["producer_exact_match"], 3) or orc.get("producer_exact_match"),
        "delay": orc.get("producer_delay_accuracy"), "truth_events": orc.get("truth_write_events"),
        "compaction": cm.get("program_vs_flat"), "seconds": round(secs), "peak_gb": round(peak / 2**20, 2) if peak else None,
    }


rows = [row(c) for c in sys.argv[1:]]
cols = ["case", "outcome", "period_cycles", "ffs", "lines", "loops", "tables", "reference", "closed", "rtl",
        "recall", "precision", "producer", "delay", "compaction", "seconds", "peak_gb"]
w = {c: max(len(c), *(len(str(r.get(c))) for r in rows)) for c in cols}
print("  ".join(c.ljust(w[c]) for c in cols))
for r in rows:
    print("  ".join(str(r.get(c)).ljust(w[c]) for c in cols))
    print(json.dumps(r), file=sys.stderr)
