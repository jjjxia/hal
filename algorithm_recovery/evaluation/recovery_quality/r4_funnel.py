#!/usr/bin/env python3
"""R4a (RECOVERY_QUALITY_PROTOCOL.txt): how far every gate-level design gets, and why the others stop.
Stages: netlist -> words -> schedule (certified) -> graph -> exact (truth populations) | program -> closed -> reference.
A design stops at its first failing stage, with one class: frontend-resource, frontend-refused, frontend-wrong,
applicability, function-unknown, truth-dead, performance, defect (see r2_collect.py and the protocol).
Library outcomes are the current results (E5 amendment o: the CPython checks with 16 GiB); for the ARX designs the
6 GiB outcome (<case>.oom6g) is primary and the 16 GiB rerun is listed next to it.
usage: r4_funnel.py OUT.json"""
import collections, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r2_collect

B = "/home/jiahao/hal/build"
LIB_STAGES = ["netlist", "words", "schedule", "graph", "program", "closed", "reference"]
TRUTH_STAGES = ["netlist", "words", "schedule", "graph", "exact"]


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None


def c1(case_dir):
    """(last stage reached, failure class or None) of the C1 pipeline in case_dir."""
    st = r2_collect.stages(case_dir)
    if not st:
        return "netlist", "performance"            # preparation did not finish (Keccak: > 3 h)
    bad = [s for s in st if s.get("exit", 0) != 0]
    if bad:
        s = bad[0]
        log = open(f"{case_dir}/{s['stage']}.log", errors="replace").read() if os.path.exists(f"{case_dir}/{s['stage']}.log") else ""
        if s["stage"] == "import":
            return "netlist", "applicability"      # no flip-flops (combinational netlist)
        if s["stage"] == "groups":
            return "netlist", ("frontend-resource" if ("bad_alloc" in log or s["exit"] in (124, 137)) else "frontend-refused")
        if s["stage"] in ("trace", "variant_trace", "crossrun"):
            return "words", "performance" if s["exit"] in (124, 137) else "defect"
    cert = load(f"{case_dir}/graph/certificate/certificate.json") or load(f"{case_dir}/analysis/certificate/certificate.json")
    if not cert or cert.get("status") != "PROTOCOL_SCHEDULE_CERTIFIED":
        return "words", "applicability"
    G = load(f"{case_dir}/graph/graph_training.json")
    if not (G and G.get("available")):
        p = f"{case_dir}/e5_stages.jsonl"
        late = [json.loads(l) for l in open(p) if l.startswith("{")] if os.path.exists(p) else []
        if any(x["stage"] == "graph" and x.get("exit") in (124, 137) for x in late):
            return "schedule", "performance"       # certified, but the graph stage exceeded its budget
        if os.path.isdir(f"{case_dir}/analysis") and not late:
            return "schedule", "not run"           # old layout: the separate graph stage never ran (interrupted rerun)
        return "schedule", "applicability"
    return "graph", None


def library_row(pop, case, case_dir, summary, closed, reference_ok, program_ok):
    stage, cls = c1(case_dir)
    if cls:
        return {"population": pop, "design": case, "stage": stage, "class": cls}
    if not program_ok:
        return {"population": pop, "design": case, "stage": "graph", "class": "performance"}
    if closed is None:
        return {"population": pop, "design": case, "stage": "program", "class": "performance"}
    if not all(closed.values()):
        cc = load(f"{os.path.dirname(case_dir) if case_dir.endswith('/case') else case_dir}/close_chain.json") or {}
        kinds = {f[2] if len(f) > 2 else None for p in cc.get("programs", []) if not p["closed"] for f in p.get("failures", [])}
        return {"population": pop, "design": case, "stage": "program",
                "class": "performance" if kinds <= {"timeout", "unknown"} else "defect",
                "open": sorted(k for k, v in closed.items() if not v)}
    if not reference_ok:
        return {"population": pop, "design": case, "stage": "closed", "class": "reference-failed"}
    return {"population": pop, "design": case, "stage": "reference", "class": None}


def e5_rows():
    E7 = {"sha256_lattice", "sha256_yosys_top"}
    for spec in sorted(glob.glob("/home/jiahao/hal/algorithm_recovery/evaluation/library_designs/specs/e5/*.json")):
        case = os.path.basename(spec)[:-5]
        pop = "E7" if case in E7 else "E5"
        for D, label in ((f"{B}/bench2_e5/{case}.oom6g", "6 GiB"), (f"{B}/bench2_e5/{case}", None)):
            if label and not os.path.isdir(D):
                continue
            s = load(f"{D}/e5_summary.json") or {}
            cc = s.get("close_chain")
            r = library_row(pop, case, D, s, {k: v["closed"] for k, v in cc.items()} if cc else None,
                            (s.get("reference") or {}).get("status") == "PASS", bool(s.get("program")))
            if os.path.isdir(f"{B}/bench2_e5/{case}.oom6g"):
                r["budget"] = label or "16 GiB rerun (E5 amendment n)"
            if case in ("aes_sw128", "aes_sw_core", "sha256_lattice") and label is None:
                r["note"] = "checks with 16 GiB (E5 amendment o)"
            yield r


def e83_rows():
    M = load(f"{B}/e8/bambu/manifest.json")
    for case in M["designs"]:
        D = f"{B}/e8/bambu/{case}"
        for cd, label in ((f"{D}/case.oom6g", "6 GiB"), (f"{D}/case", None)):
            if label and not os.path.isdir(cd):
                continue
            cc = load(f"{D}/close_chain.json") if label is None else None
            closed = {os.path.basename(p["program"]): p["closed"] for p in cc["programs"]} if cc else None
            vm = load(f"{D}/v_metrics.json") if label is None else None
            ok = bool(vm) and vm.get("V3_lifted_mismatches") == 0 and vm.get("V3_flat_mismatches") == 0
            r = library_row("E8.3", case, cd, None, closed, ok, os.path.exists(f"{cd}/program/program.py"))
            if os.path.isdir(f"{D}/case.oom6g"):
                r["budget"] = label or "16 GiB rerun (E8 amendment 8c)"
            yield r


def truth_rows():
    pops = [("E5.C", d) for d in sorted(glob.glob(f"{B}/e5c/e5c_s*")) if not d.endswith(("_chain", "_rotate"))]
    pops += [("E8.2", d) for d in sorted(glob.glob(f"{B}/e8/fold/*"))]
    pops += [("R2a", d) for d in sorted(glob.glob(f"{B}/r2/random/e5c_s*"))]
    pops += [("R2b", d) for d in sorted(glob.glob(f"{B}/r2/kernels/*"))]
    for pop, d in pops:
        T, S = load(f"{d}/gen/truth.json"), load(f"{d}/score.json")
        cls = r2_collect.classify(d, f"{d}/case", T, S)
        if cls == "running":
            continue
        stage = {"exact": "exact", "frontend-wrong": "graph", "function-unknown": "graph", "truth-dead": "graph",
                 "inverse-folding": "graph"}.get(cls) or c1(f"{d}/case")[0]
        rw = load(f"{d}/rtl_words/score.json")
        yield {"population": pop, "design": os.path.basename(d), "stage": stage, "class": None if cls == "exact" else cls,
               "rtl_words_exact": rw.get("exact") if rw else None}


if __name__ == "__main__":
    rows = list(e5_rows()) + list(e83_rows()) + list(truth_rows())
    rows += [{"population": "frozen eight", "design": d, "stage": "reference", "class": None, "note": "paper RQ2 records"}
             for d in ("present", "fir_decim", "small_hb_dec_r2", "ibex_mul", "qdiv", "sha1", "bambu_arf", "ascon")]
    agg = {}
    for pop in ("E5", "E7", "frozen eight", "E8.3", "E5.C", "E8.2", "R2a", "R2b"):
        rs = [r for r in rows if r["population"] == pop and r.get("budget") != "16 GiB rerun (E5 amendment n)"
              and r.get("budget") != "16 GiB rerun (E8 amendment 8c)"]
        if not rs:
            continue
        stages = TRUTH_STAGES if pop in ("E5.C", "E8.2", "R2a", "R2b") else LIB_STAGES
        reached = {s: sum(1 for r in rs if stages.index(r["stage"]) >= i) for i, s in enumerate(stages)}
        agg[pop] = {"designs": len(rs), "reached": reached,
                    "stopped": dict(collections.Counter(f"{r['stage']}: {r['class']}" for r in rs if r["class"])),
                    "rtl_words_exact": sum(1 for r in rs if r.get("rtl_words_exact")),
                    "extended_16GiB": [f"{r['design']}: {r['stage']} {r['class'] or 'complete'}" for r in rows
                                       if r["population"] == pop and str(r.get("budget", "")).startswith("16 GiB")]}
    json.dump({"aggregate": agg, "rows": rows}, open(sys.argv[1], "w"), indent=1)
    for k, a in agg.items():
        print(k, json.dumps(a))
