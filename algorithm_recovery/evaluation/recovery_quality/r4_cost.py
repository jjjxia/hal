#!/usr/bin/env python3
"""R4b-d (RECOVERY_QUALITY_PROTOCOL.txt): stage cost against size, inverse folding alone (E6), parametric families.
Stage seconds come from the receipts of the reported runs (C1: stages.jsonl; E5: e5_stages.jsonl; E8.3: e8_stages.jsonl).
The program stage and the CPython checks are taken from the current chain's runs (2026-11-08/09, new_chain/), since the
Python generator was replaced. Groups of stages: frontend = import + groups; trace = trace + variant_trace + crossrun;
certify = analyze (the certificate; in current runs part of graph); graph = the E5/E8 graph stage (certificate + fold);
program = recover-program generate; checks = validate_programs.py + close_chain.py.
usage: r4_cost.py OUT.json"""
import glob, json, math, os, re, sys

B = "/home/jiahao/hal/build"
NEW = f"{B}/program_recovery_comparison_20261108/new_chain"


def jl(p):
    return [json.loads(l) for l in open(p) if l.startswith("{")] if os.path.exists(p) else []


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None


def new_chain_times():
    t = {}
    for f in sorted(glob.glob(f"{NEW}/run*/summary.txt")):
        for line in open(f):
            m = re.match(r"(\S+) rc=(\S+) t=(\d+)/(\d+)/(\d+)/", line)
            if m and m.group(2).split("/")[2] == "0":       # the check finished
                t[m.group(1)] = {"program": int(m.group(3)), "validate": int(m.group(4)), "check": int(m.group(5))}
    return t


def groups_of(c1, late):
    s = lambda names, rs: round(sum(x.get("seconds") or 0 for x in rs if x["stage"] in names), 1)
    return {"frontend": s({"import", "groups"}, c1), "trace": s({"trace", "variant_trace", "crossrun"}, c1),
            "certify": s({"analyze"}, c1), "graph": s({"graph"}, c1 + late)}


def fit_slope(xs, ys):
    pts = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if x > 0 and y > 0]
    if len(pts) < 2:
        return None
    mx = sum(p[0] for p in pts) / len(pts); my = sum(p[1] for p in pts) / len(pts)
    den = sum((p[0] - mx) ** 2 for p in pts)
    return round(sum((p[0] - mx) * (p[1] - my) for p in pts) / den, 3) if den else None


if __name__ == "__main__":
    nc = new_chain_times()
    rows = []
    for D in sorted(glob.glob(f"{B}/bench2_e5/*/")):
        case = os.path.basename(D.rstrip("/"))
        if "." in case or not os.path.exists(f"{D}/stages.jsonl"):
            continue
        cert = load(f"{D}/graph/certificate/certificate.json") or load(f"{D}/analysis/certificate/certificate.json") or {}
        late = jl(f"{D}/e5_stages.jsonl")
        r = {"population": "E7" if case in ("sha256_lattice", "sha256_yosys_top") else "E5", "design": case,
             "flip_flops": cert.get("flip_flops"), "period_events": cert.get("period") and 2 * cert["period"],
             "seconds": groups_of(jl(f"{D}/stages.jsonl"), late),
             "peak_gb": round(max([x.get("peak_kb") or 0 for x in late] or [0]) / 2 ** 20, 2)}
        if case in nc:
            r["seconds"].update(nc[case])
        rows.append(r)
    for D in sorted(glob.glob(f"{B}/e8/bambu/*_c*_m*/")):
        case = os.path.basename(D.rstrip("/"))
        if not os.path.exists(f"{D}/case/stages.jsonl"):
            continue
        cert = load(f"{D}/case/graph/certificate/certificate.json") or load(f"{D}/case/analysis/certificate/certificate.json") or {}
        late = jl(f"{D}/e8_stages.jsonl")
        r = {"population": "E8.3", "design": case, "flip_flops": cert.get("flip_flops"),
             "period_events": cert.get("period") and 2 * cert["period"], "seconds": groups_of(jl(f"{D}/case/stages.jsonl"), late),
             "peak_gb": round(max([x.get("peak_kb") or 0 for x in late] or [0]) / 2 ** 20, 2)}
        if case in nc:
            r["seconds"].update(nc[case])
        rows.append(r)
    # R4c: E6 fold alone
    e6 = []
    for D in sorted(glob.glob(f"{B}/e6/s1_k*_n*")):
        m = re.search(r"_k(\d+)_n(\d+)$", D)
        st = {x["stage"]: x for x in jl(f"{D}/e6_stages.jsonl")}
        S = load(f"{D}/score.json") or {}
        if "fold" in st:
            e6.append({"K": int(m.group(1)), "N": int(m.group(2)), "fold_seconds": st["fold"]["seconds"],
                       "fold_peak_gb": round((st["fold"].get("peak_kb") or 0) / 2 ** 20, 2),
                       "equations_seconds": st.get("equations", {}).get("seconds"), "exact": S.get("exact")})
    slopes = {n: fit_slope([r["K"] for r in e6 if r["N"] == n], [r["fold_seconds"] for r in e6 if r["N"] == n]) for n in (4, 16, 64)}
    # R4d: parametric families
    fam = {}
    for f in ("qmults", "qdiv"):
        for n, case in ((16, f"{f}_n16"), (32, f), (64, f"{f}_n64")):
            r = next((x for x in rows if x["design"] == case), None)
            if r:
                fam.setdefault(f, []).append({"N": n, "flip_flops": r["flip_flops"], **r["seconds"]})
    # share of each stage group in the total, for designs with a program
    share = {}
    full = [r for r in rows if "program" in r["seconds"]]
    for g in ("frontend", "trace", "certify", "graph", "program", "check"):
        share[g] = round(sum(r["seconds"].get(g, 0) for r in full) / sum(sum(v for k, v in r["seconds"].items() if k != "validate") for r in full), 3)
    out = {"rows": rows, "e6": e6, "e6_loglog_slope_fold_seconds_vs_K": slopes, "families": fam,
           "stage_share_designs_with_program": share, "designs_with_program": len(full)}
    json.dump(out, open(sys.argv[1], "w"), indent=1)
    print("E6 slopes", slopes)
    print("stage shares", share, "over", len(full), "designs")
    for f, v in fam.items():
        print(f, v)
    for r in sorted(e6, key=lambda r: (r["N"], r["K"])):
        print(" E6", r)
