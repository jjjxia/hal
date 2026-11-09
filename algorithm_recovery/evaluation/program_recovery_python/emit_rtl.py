#!/usr/bin/env python3
"""C3 folded-RTL backend: certified event graph -> cycle-accurate Verilog with recovered word registers.

The module has one register per recovered word, a cycle counter that replaces the netlist's control state machine
(the certified schedule: P graph events = P/2 clock cycles), and for every cycle a `case` branch that writes the words
updated at that cycle with their node functions specialised by the certificate's per-phase constants. Output ports
are their original functions, specialised per cycle. The module is valid from the certified origin onward, under the
protocol (control pads follow the certified schedule; data pads are inputs).

Validation: Icarus simulates the module from the trace state at the origin with the trace's data-pad values and
compares every register after every cycle, and every observed output before every edge, with the original-gate trace.
usage: emit_rtl.py GRAPH_JSON OUT_DIR TRACE_DIR [TRACE_DIR ...]   (c3 venv python: z3; needs iverilog/vvp)"""
import collections, json, os, re, subprocess, sys
import z3

graph_path, out = sys.argv[1:3]
trace_dirs = sys.argv[3:]
os.makedirs(out, exist_ok=False)
G = json.load(open(graph_path))
P, origin = G["period"], G["origin"]
words, inputs = G["words"], G["inputs"]
assert G["available"] and G["certificate"]["certified"] and G["certificate"]["phases_aligned"]


def parse(smt2):
    eq = z3.parse_smt2_string(smt2)[0]
    return eq.arg(1)


def z3_vars(e):
    seen, found, st = set(), [], [e]
    while st:
        x = st.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            found.append(x)
        st.extend(x.children())
    return found


def specialise(expr, phase):
    subs = []
    for v in z3_vars(expr):
        kind, _, base = str(v).partition(":")
        if kind not in ("word", "input"):
            continue
        known = (words if kind == "word" else inputs)[base]["certified_known_lsb_first"][phase]
        w = v.size()
        bv = lambda bits: z3.BitVecVal(int(bits[::-1], 2), w)
        if "x" not in known:
            subs.append((v, bv(known)))
        elif known.strip("x"):
            mask = bv("".join("1" if c != "x" else "0" for c in known))
            val = bv("".join(c if c != "x" else "0" for c in known))
            subs.append((v, (v & ~mask) | val))
    return z3.simplify(z3.substitute(expr, *subs)) if subs else z3.simplify(expr)


# ---- schedule: writes happen on one clock edge parity -------------------------------------------
parities = {n["phase"] % 2 for n in G["nodes"]}
assert len(parities) == 1 and P % 2 == 0, "writes must fall on one edge parity of a two-event clock"
par = parities.pop()
NC = P // 2


def specialise_common(expr, phases):
    """Substitute only the certified-known bits that are equal at every phase of the group."""
    subs = []
    for v in z3_vars(expr):
        kind, _, base = str(v).partition(":")
        if kind not in ("word", "input"):
            continue
        rows = [(words if kind == "word" else inputs)[base]["certified_known_lsb_first"][ph] for ph in phases]
        known = "".join(col[0] if len(set(col)) == 1 else "x" for col in zip(*rows))
        w = v.size()
        bv = lambda bits: z3.BitVecVal(int(bits[::-1], 2), w)
        if "x" not in known:
            subs.append((v, bv(known)))
        elif known.strip("x"):
            mask = bv("".join("1" if c != "x" else "0" for c in known))
            val = bv("".join(c if c != "x" else "0" for c in known))
            subs.append((v, (v & ~mask) | val))
    return z3.simplify(z3.substitute(expr, *subs)) if subs else z3.simplify(expr)


# Per word: cycles whose unspecialised equations are identical share one update rule (time roll-up in RTL);
# each rule is specialised only by the constants common to its cycles, so it is valid at every one of them.
raw_by_word = collections.defaultdict(lambda: collections.defaultdict(list))  # word -> smt2 -> [phase]
for n in G["nodes"]:
    raw_by_word[n["name"]][n["smt2"]].append(n["phase"])
rules = {}  # word -> [(cycles, expr)]
for wname, groups in raw_by_word.items():
    rules[wname] = [(sorted((ph - par) // 2 for ph in phases), specialise_common(parse(smt2), phases))
                    for smt2, phases in groups.items()]
written = sorted(rules)
bit_owner = {}
for name, w in words.items():
    for i, b in enumerate(w["bits"]):
        bit_owner[b] = ("word", name, i)
for name, inp in inputs.items():
    for i, b in enumerate(inp["bits"]):
        bit_owner[b] = ("input", name, i)


def leaf_substitute(expr, phase):
    subs = []
    for v in z3_vars(expr):
        kind, name, i = bit_owner[str(v)]
        width = len((words if kind == "word" else inputs)[name]["bits"])
        subs.append((v, z3.Extract(i, i, z3.BitVec(f"{kind}:{name}", width))))
    return specialise(z3.substitute(expr, *subs), phase)


outputs = {}  # port -> list of (bit index, physical net, per-cycle expr)
for port, entries in G["outputs"].items():
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or e.get("tristate"):
            continue
        b = parse(e["smt2"])
        bv = z3.If(b, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)) if z3.is_bool(b) else b
        outputs.setdefault(port, []).append((i, e["bit"], [leaf_substitute(bv, 2 * k + par) for k in range(NC)]))


# ---- Verilog expression printer (every non-leaf term becomes a named wire) -----------------------
class VPrinter:
    """Inlines a subterm used once, unless Verilog needs a name (part-select, sign extension, rotation operand)."""
    NEEDS_NAME = (z3.Z3_OP_EXTRACT, z3.Z3_OP_SIGN_EXT, z3.Z3_OP_ROTATE_LEFT, z3.Z3_OP_ROTATE_RIGHT)

    def __init__(self, prefix, decls, root=None):
        self.prefix, self.decls, self.memo, self.n = prefix, decls, {}, 0
        self.refs = collections.Counter()
        if root is not None:
            seen, st = set(), [root]
            while st:
                x = st.pop()
                for c in x.children():
                    self.refs[c.get_id()] += 1
                if x.get_id() not in seen:
                    seen.add(x.get_id()); st.extend(x.children())

    def leaf(self, e):
        if z3.is_true(e):
            return "1'b1"
        if z3.is_false(e):
            return "1'b0"
        if z3.is_bv_value(e):
            return f"{e.size()}'h{e.as_long():x}"
        kind, _, base = str(e).partition(":")
        return f"w_{base}" if kind == "word" else base

    def wire(self, e, code):
        self.n += 1
        name = f"{self.prefix}_{self.n}"
        w = 1 if z3.is_bool(e) else e.size()
        self.decls.append(f"wire {'' if w == 1 else f'[{w - 1}:0] '}{name} = {code};")
        return name

    def emit(self, e, need_name=False):
        if e.get_id() in self.memo:
            return self.memo[e.get_id()]
        if e.num_args() == 0:
            code = self.leaf(e)
            if need_name and code[0].isdigit():  # a literal cannot be part-selected
                code = self.wire(e, code)
            self.memo[e.get_id()] = code
            return code
        k, w = e.decl().kind(), (1 if z3.is_bool(e) else e.size())
        a = [self.emit(c, k in self.NEEDS_NAME) for c in e.children()]
        cw = e.children()[0].size() if z3.is_bv(e.children()[0]) else 1
        binop = {z3.Z3_OP_BAND: "&", z3.Z3_OP_BOR: "|", z3.Z3_OP_BXOR: "^", z3.Z3_OP_BADD: "+", z3.Z3_OP_BMUL: "*",
                 z3.Z3_OP_AND: "&&", z3.Z3_OP_OR: "||", z3.Z3_OP_XOR: "^"}
        if k in binop:
            code = f" {binop[k]} ".join(a)
        elif k == z3.Z3_OP_BSUB:
            code = f"{a[0]} - {a[1]}"
        elif k == z3.Z3_OP_BNOT:
            code = f"~{a[0]}"
        elif k == z3.Z3_OP_BNEG:
            code = f"-{a[0]}"
        elif k == z3.Z3_OP_NOT:
            code = f"!{a[0]}"
        elif k == z3.Z3_OP_EXTRACT:
            hi, lo = e.params()
            code = f"{a[0]}[{hi}]" if hi == lo else f"{a[0]}[{hi}:{lo}]"
            if cw == 1:
                code = a[0]
        elif k == z3.Z3_OP_CONCAT:
            code = "{" + ", ".join(a) + "}"
        elif k == z3.Z3_OP_ZERO_EXT:
            code = f"{{{{{e.params()[0]}{{1'b0}}}}, {a[0]}}}"
        elif k == z3.Z3_OP_SIGN_EXT:
            code = f"{{{{{e.params()[0]}{{{a[0]}[{cw - 1}]}}}}, {a[0]}}}"
        elif k == z3.Z3_OP_ITE:
            code = f"{a[0]} ? {a[1]} : {a[2]}"
        elif k == z3.Z3_OP_EQ:
            code = f"{a[0]} == {a[1]}"
        elif k == z3.Z3_OP_DISTINCT:
            code = f"{a[0]} != {a[1]}"
        elif k in (z3.Z3_OP_ULT, z3.Z3_OP_ULEQ, z3.Z3_OP_UGT, z3.Z3_OP_UGEQ):
            code = f"{a[0]} {dict([(z3.Z3_OP_ULT, '<'), (z3.Z3_OP_ULEQ, '<='), (z3.Z3_OP_UGT, '>'), (z3.Z3_OP_UGEQ, '>=')])[k]} {a[1]}"
        elif k in (z3.Z3_OP_SLT, z3.Z3_OP_SLEQ, z3.Z3_OP_SGT, z3.Z3_OP_SGEQ):
            op = dict([(z3.Z3_OP_SLT, "<"), (z3.Z3_OP_SLEQ, "<="), (z3.Z3_OP_SGT, ">"), (z3.Z3_OP_SGEQ, ">=")])[k]
            code = f"$signed({a[0]}) {op} $signed({a[1]})"
        elif k == z3.Z3_OP_BSHL:
            code = f"{a[0]} << {a[1]}"
        elif k == z3.Z3_OP_BLSHR:
            code = f"{a[0]} >> {a[1]}"
        elif k == z3.Z3_OP_BASHR:
            code = f"$signed({a[0]}) >>> {a[1]}"
        elif k in (z3.Z3_OP_ROTATE_LEFT, z3.Z3_OP_ROTATE_RIGHT):
            r = e.params()[0] % w
            if k == z3.Z3_OP_ROTATE_RIGHT:
                r = (w - r) % w
            code = a[0] if r == 0 else f"{{{a[0]}[{w - r - 1}:0], {a[0]}[{w - 1}:{w - r}]}}"
        else:
            raise NotImplementedError(f"operator {e.decl().name()}")
        if self.refs[e.get_id()] <= 1 and not need_name:
            return f"({code})"
        name = self.wire(e, code)
        self.memo[e.get_id()] = name
        return name


# ---- module ------------------------------------------------------------------------------------
data_pads = sorted({str(v).partition(":")[2] for rs in rules.values() for _, e in rs for v in z3_vars(e)
                    if str(v).startswith("input:")} |
                   {str(v).partition(":")[2] for port in outputs.values() for _, _, es in port for e in es for v in z3_vars(e)
                    if str(v).startswith("input:")}, key=lambda s: int(s[3:]))
# Protocol (non-data) pads that differ between the cycles of one rule stay inputs; the testbench drives them as the trace.
decls, body = [], []
cbits = max(1, (NC - 1).bit_length())
for wname in written:
    body.append("  case (cyc)")
    for j, (cyc_list, e) in enumerate(sorted(rules[wname])):
        code = VPrinter(f"u_{wname}_{j}", decls, e).emit(e)
        body.append(f"    {', '.join(map(str, cyc_list))}: w_{wname} <= {code};")
    body.append("    default: ;\n  endcase")
out_lines = []
for port, bits in sorted(outputs.items()):
    width = max(i for i, _, _ in bits) + 1
    out_lines.append(f"always @(*) begin {port} = {width}'h0;")
    for i, _, es in bits:
        groups = collections.OrderedDict()  # identical per-cycle functions share one rule
        for k, e in enumerate(es):
            groups.setdefault(e.sexpr(), (e, []))[1].append(k)
        if len(groups) == 1:
            e = next(iter(groups.values()))[0]
            out_lines.append(f"  {port}[{i}] = {VPrinter(f'o_{port}_{i}', decls, e).emit(e)};")
            continue
        out_lines.append("  case (cyc)")
        for j, (e, ks) in enumerate(groups.values()):
            out_lines.append(f"    {', '.join(map(str, ks))}: {port}[{i}] = {VPrinter(f'o_{port}_{i}_{j}', decls, e).emit(e)};")
        out_lines.append("    default: ;\n  endcase")
    out_lines.append("end")
ports = ["input clk"] + [f"input {p}" for p in data_pads] + [f"output reg [{max(i for i, _, _ in b)}:0] {p}" for p, b in sorted(outputs.items())]
module = [f"// Recovered folded RTL (c3/emit_rtl.py): {len(written)} word registers, cycle counter of {NC} cycles",
          f"// (certified schedule, graph period {P} events, origin {origin}); valid from the origin under the protocol.",
          "module recovered(" + ", ".join(ports) + ");",
          f"reg [{cbits - 1}:0] cyc;"]
module += [f"reg [{len(words[w]['bits']) - 1}:0] w_{w};" for w in written]
module += decls
module += out_lines
module += ["always @(posedge clk) begin", f"  cyc <= (cyc == {NC - 1}) ? 0 : cyc + 1;"] + body + ["end", "endmodule"]
open(f"{out}/recovered.v", "w").write("\n".join(module) + "\n")


# ---- validation with Icarus ---------------------------------------------------------------------
def validate(directory, tag):
    meta = json.load(open(f"{directory}/native_trace.json"))
    pos = {b: i for i, b in enumerate(meta["observed"])}
    rows = [line.split() for line in open(f"{directory}/native_trace.txt")]

    def bit(b, t, post=False):
        r = rows[t][1 if post else 0]
        c = r[len(r) - 1 - pos[b]]
        return int(c) if c in "01" else None

    def wordval(name, t, post):
        v = 0
        for i, b in enumerate(words[name]["bits"]):
            x = bit(int(b[1:]), t, post)
            if x is None:
                return None
            v |= x << i
        return v
    cycles = (len(rows) - 1 - origin) // 2
    w = f"{out}/{tag}"
    os.makedirs(w)
    init = {n: wordval(n, origin + par, False) for n in written}
    stim = []
    for k in range(cycles):
        e = origin + 2 * k + par
        stim.append("".join(str(bit(int(inputs[p]["bits"][0][1:]), e) or 0) for p in reversed(data_pads)) or "0")
    open(f"{w}/stim.txt", "w").write("\n".join(stim) + "\n")
    tb = ["`timescale 1ns/1ps", "module tb;", "reg clk = 0;"]
    tb += [f"reg {p} = 0;" for p in data_pads]
    tb += [f"wire [{max(i for i, _, _ in b)}:0] {p};" for p, b in sorted(outputs.items())]
    conn = ", ".join([".clk(clk)"] + [f".{p}({p})" for p in data_pads] + [f".{p}({p})" for p in sorted(outputs)])
    tb += [f"recovered dut({conn});", f"reg [{max(1, len(data_pads)) - 1}:0] s [0:{cycles - 1}];", "integer k, fd;", "initial begin",
           f'$readmemb("{w}/stim.txt", s); fd = $fopen("{w}/sim.txt", "w"); dut.cyc = 0;']
    tb += [f"dut.w_{n} = {len(words[n]['bits'])}'d{init[n] or 0};" for n in written]
    tb += [f"for (k = 0; k < {cycles}; k = k + 1) begin"]
    tb += [f"  {p} = s[k][{j}];" for j, p in enumerate(data_pads)]
    tb += ["  #1;", '  $fwrite(fd, "O' + "".join(f" %b" for _ in outputs) + '\\n"' + "".join(f", {p}" for p in sorted(outputs)) + ");",
           "  clk = 1; #1;", '  $fwrite(fd, "R' + "".join(" %b" for _ in written) + '\\n"' + "".join(f", dut.w_{n}" for n in written) + ");",
           "  clk = 0; #1;", "end", "$fclose(fd); $finish;", "end", "endmodule"]
    open(f"{w}/tb.v", "w").write("\n".join(tb) + "\n")
    r = subprocess.run(["iverilog", "-g2012", "-o", f"{w}/sim.vvp", f"{w}/tb.v", f"{out}/recovered.v"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    r = subprocess.run(["vvp", "-n", f"{w}/sim.vvp"], capture_output=True, text=True, timeout=3600)
    assert r.returncode == 0, r.stderr[-3000:]
    res = collections.Counter()
    first = None
    lines = open(f"{w}/sim.txt").read().split("\n")
    o_lines = [l.split()[1:] for l in lines if l.startswith("O")]
    r_lines = [l.split()[1:] for l in lines if l.startswith("R")]
    if any(x is None for x in init.values()):
        res["x_initial_words"] = sum(x is None for x in init.values())
    for k in range(cycles):
        e = origin + 2 * k + par
        for n, val in zip(written, r_lines[k]):
            exp = wordval(n, e, True)
            if exp is None or "x" in val or "z" in val:
                res["reg_unknown"] += 1
                continue
            res["reg_compared"] += 1
            if int(val, 2) != exp:
                res["reg_mismatch"] += 1
                first = first or {"cycle": k, "word": n, "rtl": int(val, 2), "trace": exp}
        for p, val in zip(sorted(outputs), o_lines[k]):
            for i, net, _ in outputs[p]:
                exp = bit(net, e)
                c = val[len(val) - 1 - i]
                if exp is None or c not in "01":
                    res["out_unknown"] += 1
                    continue
                res["out_compared"] += 1
                if int(c) != exp:
                    res["out_mismatch"] += 1
                    first = first or {"cycle": k, "port": p, "bit": i, "rtl": c, "trace": exp}
    return {"trace": directory, "cycles": cycles, "results": dict(res), "first_mismatch": first}


report = {"graph": graph_path, "period_events": P, "cycles_per_period": NC, "origin": origin,
          "word_registers": len(written), "data_inputs": len(data_pads), "outputs": {p: len(b) for p, b in outputs.items()},
          "verilog_lines": sum(1 for _ in open(f"{out}/recovered.v")),
          "validation": [validate(d, f"sim{i}") for i, d in enumerate(trace_dirs)]}
json.dump(report, open(f"{out}/report.json", "w"), indent=1)
print(json.dumps(report, indent=1))
