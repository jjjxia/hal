#!/usr/bin/env python3
"""Evaluation-only adapter for the frozen upstream qtrstage C2 case.

This is a manually supplied word-equation frontend, NOT anonymous word recovery.
Source roles/provenance stay in oracle.json; InvFold receives recovery.json only.
`prepare DIR` writes inputs without simulation. `run-reference DIR` compiles the
unchanged pinned RTL and validates every supplied word update before recovery.
"""
import argparse
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

PIN = "3378b77d83a98b06184656a5cb9b54e50dfe4485"
SOURCE = Path("/tmp/netnotary-bench-fft-20261101")
PERIOD, ORIGIN, WINDOW_END, NCYC = 8, 20, 116, 156
# Explicit word-boundary/order prior. Packed complex words are split into their
# numeric components; every actual FF, including convround outputs, is covered.
# Entries are evaluation-only source names, width, and a hierarchical RTL probe.
WORDS = [
    ("sum_r", 9, "dut.sum_r"), ("sum_i", 9, "dut.sum_i"),
    ("diff_r", 9, "dut.diff_r"), ("diff_i", 9, "dut.diff_i"),
    ("ob_a_r", 9, "dut.ob_a[17:9]"), ("ob_a_i", 9, "dut.ob_a[8:0]"),
    ("ob_b_r", 9, "dut.ob_b_r"), ("ob_b_i", 9, "dut.ob_b_i"),
    ("imem0_r", 8, "dut.imem[0][15:8]"), ("imem0_i", 8, "dut.imem[0][7:0]"),
    ("imem1_r", 8, "dut.imem[1][15:8]"), ("imem1_i", 8, "dut.imem[1][7:0]"),
    ("omem0_r", 9, "dut.omem[0][17:9]"), ("omem0_i", 9, "dut.omem[0][8:0]"),
    ("omem1_r", 9, "dut.omem[1][17:9]"), ("omem1_i", 9, "dut.omem[1][8:0]"),
    ("rnd_sum_r", 9, "dut.rnd_sum_r"), ("rnd_sum_i", 9, "dut.rnd_sum_i"),
    ("rnd_diff_r", 9, "dut.rnd_diff_r"), ("rnd_diff_i", 9, "dut.rnd_diff_i"),
    ("o_data_r", 9, "dut.o_data[17:9]"), ("o_data_i", 9, "dut.o_data[8:0]"),
    ("wait_for_sync", 1, "dut.wait_for_sync"),
    ("pipeline", 3, "dut.pipeline"), ("iaddr", 3, "dut.iaddr"),
    ("o_sync", 1, "dut.o_sync"),
]
W = {"w%d" % i: width for i, (_, width, _) in enumerate(WORDS)}
OUTPUTS = {"y0": "w20", "y1": "w21", "y2": "w25"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2) + "\n")


def signed(v, width):
    return v - (1 << width) if v & (1 << (width - 1)) else v


def bv(value, width):
    return "#b" + format(value & ((1 << width) - 1), "0%db" % width)


def sexpr(term):
    return term if isinstance(term, str) else "(" + " ".join(map(sexpr, term)) + ")"


def extract(term, hi, lo):
    return (("_", "extract", str(hi), str(lo)), term)


def extend(term):
    return (("_", "sign_extend", "1"), term)


def leaves(term, widths, path=()):
    """Operand-child paths, excluding operator heads, including repetitions."""
    if isinstance(term, str):
        if term in widths:
            yield {"path": list(path), "symbol": term, "width": widths[term]}
        return
    for i, child in enumerate(term[1:]):
        yield from leaves(child, widths, path + (i,))


def stimulus():
    rng = random.Random(0xC2F004)
    directed = [(0, 0), (127, 128), (128, 127), (255, 1),
                (1, 255), (64, 192), (192, 64), (85, 170)]
    rows = []
    for t in range(NCYC):
        active = t - 4
        data = ((0, 0) if t < ORIGIN or t >= 148 else
                directed[t - ORIGIN] if t - ORIGIN < len(directed) else
                (rng.randrange(256), rng.randrange(256)))
        rows.append({"reset": int(t < 4), "sync": int(active >= 0 and active % 8 == 0),
                     "x0": data[0], "x1": data[1]})
    return rows


def transition(before, inp):
    """Independent source operational model (nonblocking RHS use old values).

    qtrstage.v:123-161,170-206; convround.v:67-72 (equal widths).
    None represents an uninitialized word; it is never silently zero-filled.
    """
    old, new = before, dict(before)
    addr, pipeline = old["w24"], old["w23"]
    if inp["reset"]:
        new.update(w22=1, w24=0, w23=0, w25=0)
    else:
        if not old["w22"] or inp["sync"]:
            new["w24"], new["w22"] = (addr + 1) & 7, 0
        new["w23"] = ((pipeline << 1) | ((addr >> 1) & 1)) & 7
        new["w25"] = int(addr == 5)
    new["w8"], new["w9"] = inp["x0"], inp["x1"]
    new["w10"], new["w11"] = old["w8"], old["w9"]
    if addr & 2:
        for component in range(2):
            a, b = old["w%d" % (10 + component)], inp["x%d" % component]
            for k, sign in ((component, 1), (2 + component, -1)):
                new["w%d" % k] = None if a is None else (
                    signed(a, 8) + sign * signed(b, 8)) & 511
    for k in range(4):
        new["w%d" % (16 + k)] = old["w%d" % k]
    new["w4"], new["w5"] = old["w16"], old["w17"]
    if addr & 1:
        new["w6"] = old["w19"]
        new["w7"] = None if old["w18"] is None else (-old["w18"]) & 511
    else:
        new["w6"], new["w7"] = old["w18"], old["w19"]
    new["w12"], new["w13"] = old["w6"], old["w7"]
    new["w14"], new["w15"] = old["w12"], old["w13"]
    a = 4 if pipeline & 4 else 14
    new["w20"], new["w21"] = old["w%d" % a], old["w%d" % (a + 1)]
    return new


def equations(t, before, inp):
    """Correct words under the explicit fixed-control protocol, no provenance.

    Source branch predicates are specialized only after evaluating known control
    state. run-reference independently checks every control word against the RTL.
    """
    q = {w: "%s_o_Q_%d" % (w, t) for w in W}
    x = {p: "%s_%d" % (p, t) for p in ("x0", "x1")}
    terms, enabled = dict(q), {w: True for w in W}
    terms.update(w8=x["x0"], w9=x["x1"], w10=q["w8"], w11=q["w9"])
    for k in range(4):
        w = "w%d" % k
        enabled[w] = bool(before["w24"] & 2)
        if enabled[w]:
            component = k % 2
            terms[w] = ("bvadd" if k < 2 else "bvsub",
                        extend(q["w%d" % (10 + component)]), extend(x["x%d" % component]))
        terms["w%d" % (16 + k)] = q[w]
    terms.update(w4=q["w16"], w5=q["w17"], w12=q["w6"], w13=q["w7"],
                 w14=q["w12"], w15=q["w13"])
    if before["w24"] & 1:
        terms.update(w6=q["w19"], w7=("bvneg", q["w18"]))
    else:
        terms.update(w6=q["w18"], w7=q["w19"])
    base = 4 if before["w23"] & 4 else 14
    terms.update(w20=q["w%d" % base], w21=q["w%d" % (base + 1)])
    if inp["reset"]:
        terms.update(w22=bv(1, 1), w23=bv(0, 3), w24=bv(0, 3), w25=bv(0, 1))
    else:
        enabled["w22"] = enabled["w24"] = not before["w22"] or bool(inp["sync"])
        if enabled["w24"]:
            terms.update(w22=bv(0, 1), w24=("bvadd", q["w24"], bv(1, 3)))
        terms["w23"] = ("concat", extract(q["w23"], 1, 0), extract(q["w24"], 1, 1))
        # Like the data selectors, this fixed-protocol control is specialized.
        # Keeping a symbolic counter comparison would yield a valid 4-slot
        # equation family with an 8-cycle state trajectory, a different C2
        # representation from this explicitly frozen 8-slot family.
        terms["w25"] = bv(int(before["w24"] == 5), 1)
    return terms, enabled


def logical_role(word, t):
    phase = (t - ORIGIN) % PERIOD
    k = int(word[1:])
    if k < 4:
        return "block%d.pair%d.%s.%s" % (
            phase // 4, phase % 2, "sum" if k < 2 else "difference", "real" if k % 2 == 0 else "imag")
    # Pipeline/control writes remain distinct raw events, including dead writes.
    return "%s.slot%d" % (WORDS[k][0], phase)


def prepare(out, source):
    out.mkdir(parents=True, exist_ok=True)
    pin = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if pin != PIN:
        raise ValueError("upstream commit does not match frozen contract")
    sources = [source / "rtl/qtrstage.v", source / "rtl/convround.v"]
    for p in sources:
        frozen = subprocess.check_output(["git", "-C", str(source), "show", "%s:%s" % (PIN, p.relative_to(source))])
        if p.read_bytes() != frozen:
            raise ValueError("upstream source has local modifications: %s" % p)
    rows = stimulus()
    before = {w: None for w in W}
    before.update(w22=1, w23=0, w24=0, w25=0)  # actual RTL initial blocks
    tags = {w: ["init", w, 0] for w in W}
    trace = {"schema": "C2_TRACE_V1", "inputs": {p: [] for p in ("x0", "x1")},
             "before": {w: [] for w in W}, "after": {w: [] for w in W},
             "outputs": {p: [] for p in OUTPUTS}, "compare_from": ORIGIN,
             "reference_scope": "Prepared source operational model; original RTL validation pending."}
    eqs, events, we = [], [], {w: [] for w in W}
    for t, inp in enumerate(rows):
        terms, enabled = equations(t, before, inp)
        after = transition(before, inp)
        sym_widths = {"%s_o_Q_%d" % (w, t): width for w, width in W.items()}
        sym_widths.update({"x0_%d" % t: 8, "x1_%d" % t: 8})
        symbol_tags = {"%s_o_Q_%d" % (w, t): tag for w, tag in tags.items()}
        symbol_tags.update({"%s_%d" % (p, t): ["input", p, t] for p in ("x0", "x1")})
        new_tags = dict(tags)
        for w in W:
            target, term = w + " - i_D", terms[w]
            eqs.append({"T": t, "target": target, "smt2": sexpr(term)})
            we[w].append(int(enabled[w]))
            trace["before"][w].append(before[w])
            trace["after"][w].append(after[w])
            if enabled[w]:
                role, sample = logical_role(w, t), (t - ORIGIN) // PERIOD
                operands = list(leaves(term, sym_widths))
                for operand in operands:
                    operand["producer"] = list(symbol_tags[operand["symbol"]])
                copy = isinstance(term, str) and term in sym_widths and sym_widths[term] == W[w]
                events.append({"T": t, "target": target, "logical": role, "sample": sample,
                               "kind": "copy" if copy else "compute", "width": W[w], "operands": operands})
                new_tags[w] = ["op", role, sample]
        for port, word in OUTPUTS.items():
            term, target = "%s_o_Q_%d" % (word, t), "top_module - " + port
            eqs.append({"T": t, "target": target, "smt2": term})
            events.append({"T": t, "target": target, "logical": "observe.%s.slot%d" % (port, (t - ORIGIN) % PERIOD),
                           "sample": (t - ORIGIN) // PERIOD, "kind": "output", "width": W[word],
                           "operands": [{"path": [], "symbol": term, "producer": list(tags[word]), "width": W[word]}]})
            trace["outputs"][port].append(before[word])
        for p in trace["inputs"]:
            trace["inputs"][p].append(inp[p])
        before, tags = after, new_tags
    if any(v is None for values in trace["before"].values() for v in values[ORIGIN:]):
        raise ValueError("declared initialization boundary is not fully initialized")
    meta = {"window": list(range(ORIGIN, WINDOW_END)), "widths": W,
            "ports": {"in": {"x0": 8, "x1": 8}, "out": {p: W[w] for p, w in OUTPUTS.items()}},
            "write_enable": we}
    dump(out / "recovery.json", {"equations": eqs, "meta": meta})
    dump(out / "oracle.json", {"period": PERIOD, "origin": ORIGIN, "events": events,
                              "source_word_map": {"w%d" % k: {"source": n, "width": w, "probe": p}
                                                  for k, (n, w, p) in enumerate(WORDS)}})
    dump(out / "trace.json", trace)
    dump(out / "source_model_trace.json", trace)
    dump(out / "stimulus.json", rows)
    (out / "stimulus.hex").write_text("".join("%05x\n" % ((r["reset"] << 17) | (r["sync"] << 16) |
                                                            (r["x0"] << 8) | r["x1"]) for r in rows))
    fmt = "%d %s " + " ".join(["%b"] * len(WORDS)) + "\\n"
    probes = ", ".join(p for _, _, p in WORDS)
    # json.dumps also quotes these ordinary absolute paths for Verilog strings.
    tb = '''module test;
reg clk=0, reset=1, sync=0; reg [15:0] data=0;
wire [17:0] result; wire osync;
reg [17:0] stimuli [0:%d]; integer t, fd;
qtrstage #(.IWIDTH(8),.OWIDTH(9),.LGWIDTH(3),.INVERSE(0),.SHIFT(0))
 dut(.i_clk(clk),.i_reset(reset),.i_ce(1'b1),.i_sync(sync),.i_data(data),.o_data(result),.o_sync(osync));
initial begin
 $readmemh(%s, stimuli); fd=$fopen(%s,"w"); #1;
 for(t=0;t<%d;t=t+1) begin
  clk=0; {reset,sync,data}=stimuli[t]; #1;
  $fwrite(fd,"%s",t,"B",%s);
  clk=1; #1;
  $fwrite(fd,"%s",t,"A",%s);
  clk=0; #1;
 end
 $fclose(fd); $finish;
end
endmodule
''' % (NCYC - 1, json.dumps(str(out / "stimulus.hex")), json.dumps(str(out / "rtl_trace.txt")),
       NCYC, fmt, probes, fmt, probes)
    (out / "reference_tb.v").write_text(tb)
    manifest = {"case": "qtrstage", "source_commit": PIN, "source_files": {str(p): sha(p) for p in sources},
                "adapter_sha256": sha(__file__), "parameters": {"IWIDTH": 8, "OWIDTH": 9, "LGWIDTH": 3,
                                                               "INVERSE": 0, "SHIFT": 0},
                "priors": ["All 202 FF bits supplied as 26 ordered words; packed real/imag lanes split.",
                           "Fixed protocol: CE=1; reset cycles0..3; sync every8cycles starting4.",
                           "Manually source-derived equations, phase-specialized control predicates.",
                           "No logical roles or producers in recovery.json; no periodic extension."],
                "scope": "All data and control FFs, both array elements, all three output lanes.",
                "period": PERIOD, "compare_from": ORIGIN, "window_end": WINDOW_END, "cycles": NCYC,
                "source_lines": ["qtrstage.v:73-206", "convround.v:67-72"],
                "prepared_hashes": {name: sha(out / name) for name in
                                    ("recovery.json", "oracle.json", "source_model_trace.json", "stimulus.json",
                                     "stimulus.hex", "reference_tb.v")}}
    dump(out / "manifest.json", manifest)
    print(json.dumps({"status": "PREPARED_NOT_RTL_VALIDATED", "directory": str(out), "words": len(W),
                      "bits": sum(W.values()), "cycles": NCYC}))


def run_reference(out):
    manifest = json.loads((out / "manifest.json").read_text())
    if manifest["adapter_sha256"] != sha(__file__):
        raise ValueError("adapter changed after prepare; re-freeze adapter before validation")
    for path, digest in manifest["source_files"].items():
        if sha(path) != digest:
            raise ValueError("source changed after prepare: " + path)
    for name, digest in manifest["prepared_hashes"].items():
        if sha(out / name) != digest:
            raise ValueError("prepared artifact changed: " + name)
    compile_cmd = ["iverilog", "-g2012", "-s", "test", "-o", str(out / "reference_sim"),
                   *manifest["source_files"], str(out / "reference_tb.v")]
    for cmd, logfile in ((compile_cmd, "iverilog.log"), (["vvp", str(out / "reference_sim")], "vvp.log")):
        result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        (out / logfile).write_text(result.stdout)
        if result.returncode:
            raise RuntimeError("reference tool failed: " + str(cmd))
    trace = json.loads((out / "source_model_trace.json").read_text())
    model = json.loads((out / "source_model_trace.json").read_text())
    raw = {side: {w: [None] * NCYC for w in W} for side in ("before", "after")}
    seen = set()
    for line in (out / "rtl_trace.txt").read_text().splitlines():
        fields = line.split()
        if len(fields) != len(W) + 2:
            raise ValueError("malformed RTL observation")
        t, side = int(fields[0]), {"B": "before", "A": "after"}[fields[1]]
        if (t, side) in seen or not 0 <= t < NCYC:
            raise ValueError("duplicate/out-of-range RTL observation")
        seen.add((t, side))
        for w, bits in zip(W, fields[2:]):
            if len(bits) != W[w]:
                raise ValueError("RTL probe width mismatch: " + w)
            raw[side][w][t] = bits
            trace[side][w][t] = None if set(bits) - set("01") else int(bits, 2)
    if len(seen) != 2 * NCYC:
        raise ValueError("incomplete RTL trace")
    failures, known_model, unknown = [], 0, 0
    for side in ("before", "after"):
        for w in W:
            for t, got in enumerate(trace[side][w]):
                expected = model[side][w][t]
                if got is None:
                    unknown += 1
                if expected is not None:
                    known_model += 1
                    if got != expected:
                        failures.append({"check": "source_model", "T": t, "side": side, "word": w,
                                         "expected": expected, "actual": got})
                if t >= ORIGIN and got is None:
                    failures.append({"check": "unknown_after_initialization", "T": t, "side": side, "word": w})
    # Use the frozen expression evaluator, never Folded/resolve/build/recovery.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "invfold"))
    import smt2
    recovery = json.loads((out / "recovery.json").read_text())
    equation_checks, hold_checks, unknown_equations = 0, 0, 0
    for eq in recovery["equations"]:
        t, target = eq["T"], eq["target"]
        widths = {"%s_o_Q_%d" % (w, t): width for w, width in W.items()}
        widths.update({"x0_%d" % t: 8, "x1_%d" % t: 8})
        env = {"%s_o_Q_%d" % (w, t): trace["before"][w][t] for w in W}
        env.update({"%s_%d" % (p, t): trace["inputs"][p][t] for p in ("x0", "x1")})
        fn, syms, width = smt2.compile_term(eq["smt2"], widths)
        if target.startswith("top_module - "):
            port = target.split(" - ")[1]
            expected = trace["before"][OUTPUTS[port]][t]
            expected_width = W[OUTPUTS[port]]
        else:
            word = target.split(" - ")[0]
            expected = trace["after"][word][t]
            expected_width = W[word]
            if not recovery["meta"]["write_enable"][word][t]:
                hold_checks += 1
                if trace["before"][word][t] != expected:
                    failures.append({"check": "effective_hold", "T": t, "word": word})
        if width != expected_width:
            raise ValueError("equation result width mismatch: " + target)
        if any(env[s] is None for s in syms):
            unknown_equations += 1
            continue
        equation_checks += 1
        got = fn(env)
        if got != expected:
            failures.append({"check": "word_equation", "T": t, "target": target,
                             "expected": expected, "actual": got})
    for port, word in OUTPUTS.items():
        trace["outputs"][port] = trace["before"][word]
    trace.update(reference_scope="Finite unchanged original RTL agreement for every supplied word update and output; not a universal certificate.",
                 rtl_binary=raw, reference_status="PASS" if not failures else "FAIL")
    dump(out / "trace.json", trace)
    receipt = {"status": "PASS" if not failures else "FAIL", "cycles": NCYC, "words": len(W),
               "bits": sum(W.values()), "known_source_model_checks": known_model,
               "word_and_output_equation_checks": equation_checks, "hold_checks": hold_checks,
               "unknown_word_observations": unknown, "unknown_equations_skipped_in_prefix": unknown_equations,
               "compare_from": ORIGIN, "failures": failures,
               "trace_sha256": sha(out / "trace.json"), "recovery_sha256": sha(out / "recovery.json"),
               "oracle_sha256": sha(out / "oracle.json"), "source_commit": PIN}
    dump(out / "reference_validation.json", receipt)
    print(json.dumps({k: v for k, v in receipt.items() if k != "failures"}))
    if failures:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run-reference"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    out = args.directory.resolve()
    if args.command == "prepare":
        prepare(out, args.source.resolve())
    else:
        run_reference(out)


if __name__ == "__main__":
    main()
