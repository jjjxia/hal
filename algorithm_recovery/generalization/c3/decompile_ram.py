#!/usr/bin/env python3
"""C3 for RAM-backed event graphs (Index firmware): certified, certificate-aligned event graph -> period program.

One call computes one graph period: P events from a start s = origin (mod P). The state is every word value read
with a delay (as in decompile.py) plus the contents and read register of every RAM. Nodes are specialised with
the certificate's per-phase constants, which requires the graph period to equal the certificate period
(hal-recover graph --align-certificate 1; Theorem "period multiples are unfoldings"). Within a phase, as in the
graph semantics (folding.cpp run_frame): observations read the frame before the edge, word nodes are computed,
then all RAM ports are evaluated before any RAM changes, and each RAM steps (read before write) at the phases
where the certified clock pattern has a rising edge for it. Observations are all output pad bits at every phase
(tristate pads: data and enable). Words excluded from the graph are cut inputs supplied from the trace and are
counted in the report. Validation against original-gate traces: per period (state taken from the trace) and
chained (state carried by the program from the first period on); RAM contents come from ice40_ram.replay.
usage: decompile_ram.py [--reuse] GRAPH_JSON RECOVERED_JSON OUT_DIR TRACE_DIR [TRACE_DIR ...]   (c3 venv python: z3)
--reuse validates an existing OUT_DIR/program.py with its program_meta.json instead of regenerating it. The program
is compiled one phase function at a time (a single compile of the whole module exceeds the memory cap)."""
import collections, json, os, re, sys, time
import z3
import ice40_ram
from pyexpr import Printer

reuse = sys.argv[1] == "--reuse"
args = sys.argv[2:] if reuse else sys.argv[1:]
graph_path, recovered, out = args[0:3]
trace_dirs = args[3:]
if not reuse:
    os.makedirs(out, exist_ok=False)
t_start = time.time()
G = json.load(open(graph_path))
cert = G["certificate"]
assert G["available"] and cert["certified"] and cert["phases_aligned"], \
    "needs an available graph aligned with a certified schedule (hal-recover graph --align-certificate 1)"
P, origin = G["period"], G["origin"]
words, inputs, memories = G["words"], G["inputs"], G["memories"]


def parse(smt2):
    eq = z3.parse_smt2_string(smt2)[0]
    assert eq.decl().kind() == z3.Z3_OP_EQ and str(eq.arg(0)) == "__out"
    return eq.arg(1)


def z3_vars(expr):
    seen, found, stack = set(), [], [expr]
    while stack:
        e = stack.pop()
        if e.get_id() in seen:
            continue
        seen.add(e.get_id())
        if z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            found.append(e)
        stack.extend(e.children())
    return found


def const_bv(bits_lsb, w):
    return z3.BitVecVal(int(bits_lsb[::-1], 2), w)


def specialise(expr, phase):
    subs = []
    for v in z3_vars(expr):
        kind, _, base = str(v).partition(":")
        if kind not in ("word", "input"):
            continue
        known = (words if kind == "word" else inputs)[base]["certified_known_lsb_first"][phase]
        w = v.size()
        if "x" not in known:
            subs.append((v, const_bv(known, w)))
        elif known.strip("x"):
            mask = const_bv("".join("1" if c != "x" else "0" for c in known), w)
            val = const_bv("".join(c if c != "x" else "0" for c in known), w)
            subs.append((v, (v & ~mask) | val))
    return z3.simplify(z3.substitute(expr, *subs)) if subs else z3.simplify(expr)


if reuse:
    meta_saved = json.load(open(f"{out}/program_meta.json"))
    pad_keys = {tuple(k) for k in meta_saved["pad_keys"]}
    state_keys = {tuple(k) for k in meta_saved["state_keys"]}
    cell_of = meta_saved["cell_of"]
    rams = ice40_ram.load(recovered)
    program = open(f"{out}/program.py").read()
    report_gen = meta_saved["report_gen"]
else:
    # ---- symbols -------------------------------------------------------------------------
    bit_owner = {}
    for name, w in words.items():
        for i, b in enumerate(w["bits"]):
            bit_owner[b] = ("word", name, i)
    for name, inp in inputs.items():
        for i, b in enumerate(inp["bits"]):
            bit_owner[b] = ("input", name, i)
    for name, mem in memories.items():
        for i, b in enumerate(mem["read_bits"]):
            bit_owner[b] = ("ram", name, i)
    excluded = set(G["excluded"])

    # ---- nodes and writes -------------------------------------------------------------------
    nodes = {}
    for n in G["nodes"]:
        nodes[(n["name"], n["phase"])] = {"expr": specialise(parse(n["smt2"]), n["phase"]),
                                          "sources": {s["symbol"]: s for s in n["sources"]}}
    writes = collections.defaultdict(list)
    for (name, ph) in nodes:
        writes[name].append(ph)
    for name in writes:
        writes[name].sort()


    def last_write(name, phase):
        before = [p for p in writes[name] if p < phase]
        return (before[-1], 0) if before else (writes[name][-1], 1)


    # ---- RAMs: netlist cells, certified clock edges, port nodes ------------------------------
    rams = ice40_ram.load(recovered)
    cell_of = {}
    for g, mem in memories.items():
        rb = [int(b[1:]) for b in mem["read_bits"]]
        match = [c for c, r in rams.items() if r["pins"]["RDATA"] == rb]
        assert len(match) == 1, f"RAM {g}: no unique netlist cell with these RDATA bits"
        cell_of[g] = match[0]


    def clock_pattern(net):
        name = f"pad{net}"
        assert name in inputs, f"clock net {net} is not a primary pad"
        rows = inputs[name]["certified_known_lsb_first"]
        assert all(r in ("0", "1") for r in rows), f"clock pad {name} is not certified at every phase"
        return [int(r) for r in rows]


    edge = {}
    for g, c in cell_of.items():
        for kind, pin in (("read", "RCLK"), ("write", "WCLK")):
            k = clock_pattern(rams[c]["pins"][pin][0])
            edge[(g, kind)] = [k[ph] == 0 and k[(ph + 1) % P] == 1 for ph in range(P)]
    READ_PORTS, WRITE_PORTS = ("RADDR", "RE", "RCLKE"), ("WADDR", "WDATA", "MASK", "WE", "WCLKE")
    ports = {}
    for n in G["ram_nodes"]:
        g, port, ph = n["memory"], n["port"], n["phase"]
        if (port in READ_PORTS and edge[(g, "read")][ph]) or (port in WRITE_PORTS and edge[(g, "write")][ph]):
            ports[(g, port, ph)] = {"expr": specialise(parse(n["smt2"]), ph), "sources": {s["symbol"]: s for s in n["sources"]}}

    # ---- observations: every output pad bit at every phase --------------------------------------
    def leaf_substitute(expr, phase):
        subs = []
        for v in z3_vars(expr):
            kind, name, i = bit_owner[str(v)]
            width = {"word": lambda: len(words[name]["bits"]), "input": lambda: len(inputs[name]["bits"]), "ram": lambda: 16}[kind]()
            subs.append((v, z3.Extract(i, i, z3.BitVec(f"{kind}:{name}", width))))
        return specialise(z3.substitute(expr, *subs), phase)


    observations = []
    for port, entries in G["outputs"].items():
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            parts = [("data", e["data"]), ("enable", e["enable"])] if e.get("tristate") else [("value", e)]
            for kind, spec in parts:
                if not isinstance(spec, dict):
                    observations += [((port, i, kind), ph, z3.BitVecVal(1 if spec == -2 else 0, 1)) for ph in range(P)]
                    continue
                b = parse(spec["smt2"])
                as_bv = z3.If(b, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)) if z3.is_bool(b) else b
                observations += [((port, i, kind), ph, leaf_substitute(as_bv, ph)) for ph in range(P)]

    # ---- emission ------------------------------------------------------------------------------
    pad_keys, state_keys = set(), set()


    def env_for(sources, ph):
        def env(sym):
            kind, _, base = sym.partition(":")
            s = sources.get(sym) if sources is not None else None
            if kind == "ram":
                return f"mem[{base!r}][1]"
            if kind == "input" or (kind == "word" and s is not None and s["kind"] == "input") or (kind == "word" and base in excluded):
                key = (s["name"], s["phase"]) if s is not None and s["kind"] == "input" else \
                      (f"cut_{base}" if kind == "word" else base, ph)
                pad_keys.add(key)
                return f"pads[{key!r}]"
            if s is not None:
                p2, d = s["phase"], s["delay"]
            else:
                p2, d = last_write(base, ph)
            if d == 0:
                return f"v[{(base, p2)!r}]"
            state_keys.add((base, p2, d))
            return f"state[{(base, p2, d)!r}]"
        return env


    obs_by_phase = collections.defaultdict(list)
    for key, ph, expr in observations:
        obs_by_phase[ph].append((key, expr))
    nodes_by_phase = collections.defaultdict(list)
    for (name, ph) in sorted(nodes):
        nodes_by_phase[ph].append(name)
    ports_by_phase = collections.defaultdict(list)
    for (g, port, ph) in sorted(ports):
        ports_by_phase[ph].append((g, port))

    lines = ["# Recovered period program for a RAM-backed event graph (generated by c3/decompile_ram.py; do not edit).",
             f"# graph period={P} events, origin={origin}; certificate origin={cert['origin']}, period={cert['period']}",
             "def sgn(x, w):", "    return x - (1 << w) if (x >> (w - 1)) & 1 else x",
             "def sra(x, s, w):", "    return (sgn(x, w) >> min(s, w)) & ((1 << w) - 1)", ""]
    for ph in range(P):
        body = []
        for key, expr in obs_by_phase[ph]:
            pr = Printer(env_for(None, ph))
            code = pr.emit(expr)
            body += pr.lines + [f"    out[{(key, ph)!r}] = {code}"]
        for name in nodes_by_phase[ph]:
            node = nodes[(name, ph)]
            pr = Printer(env_for(node["sources"], ph))
            code = pr.emit(node["expr"])
            body += pr.lines + [f"    v[{(name, ph)!r}] = {code}"]
        rams_here = sorted({g for g, _ in ports_by_phase[ph]})
        for g, port in ports_by_phase[ph]:
            node = ports[(g, port, ph)]
            pr = Printer(env_for(node["sources"], ph))
            code = pr.emit(node["expr"])
            body += pr.lines + [f"    p_{g}_{port} = {code}"]
        for g in rams_here:  # every port above was read before any RAM changes
            if edge[(g, "read")][ph]:
                body.append(f"    if p_{g}_RE and p_{g}_RCLKE: mem[{g!r}][1] = mem[{g!r}][0][p_{g}_RADDR & 255]")
            if edge[(g, "write")][ph]:
                body.append(f"    if p_{g}_WE and p_{g}_WCLKE:")
                body.append(f"        a = p_{g}_WADDR & 255; m = mem[{g!r}][0]")
                body.append(f"        m[a] = (m[a] & p_{g}_MASK) | (p_{g}_WDATA & ~p_{g}_MASK & 0xFFFF)")
        lines += [f"def phase_{ph}(pads, state, mem, v, out):"] + (body or ["    pass"]) + [""]
    lines += ["def period(pads, state, mem):",
              "    \"\"\"pads[(pad, phase)]: data pads and cut words; state[(word, phase, delay)]; mem[ram] = [contents, read]\"\"\"",
              "    v, out = {}, {}"]
    lines += [f"    phase_{ph}(pads, state, mem, v, out)" for ph in range(P)]
    lines.append("    new_state = {}")
    for (name, ph, d) in sorted(state_keys):
        lines.append(f"    new_state[{(name, ph, d)!r}] = " + (f"v[{(name, ph)!r}]" if d == 1 else f"state[{(name, ph, d - 1)!r}]"))
    lines.append("    return out, v, new_state")
    program = "\n".join(lines) + "\n"
    open(f"{out}/program.py", "w").write(program)
    t_emit = time.time()

    report_gen = {"word_nodes": len(nodes), "ram_port_expressions": len(ports), "rams": len(memories),
                  "read_edges_per_period": {g: sum(edge[(g, "read")]) for g in memories},
                  "write_edges_per_period": {g: sum(edge[(g, "write")]) for g in memories},
                  "observations": len(observations), "program_lines": program.count("\n"),
                  "seconds_emit": round(t_emit - t_start, 1)}
    json.dump({"pad_keys": sorted(pad_keys), "state_keys": sorted(state_keys), "cell_of": cell_of, "report_gen": report_gen},
              open(f"{out}/program_meta.json", "w"))
    del nodes, ports, observations, obs_by_phase, nodes_by_phase, ports_by_phase  # free z3 terms before compiling
G["nodes"] = G["ram_nodes"] = None
import gc
gc.collect()

# ---- validation ----------------------------------------------------------------------------
ns = {}
for part in re.split(r"(?m)^(?=def phase_|def period)", program):  # one phase function at a time
    exec(compile(part, f"{out}/program.py", "exec"), ns)
del program
max_delay = max([d for _, _, d in state_keys] + [1])


def validate(directory):
    tr = ice40_ram.Trace(directory)
    n_ev = len(tr.rows)

    def wordval(name, t, post):
        v = 0
        for i, b in enumerate(words[name]["bits"]):
            x = tr.bit(int(b[1:]), t, post)
            if x is None:
                return None
            v |= x << i
        return v

    starts = [s for s in range(origin % P, n_ev - P + 1, P) if s >= cert["origin"] and s - max_delay * P >= 0]
    snaps, ram_stats = ice40_ram.replay(rams, tr, starts)

    def inputs_at(s):
        pads = {}
        for (name, ph) in pad_keys:
            pads[(name, ph)] = wordval(name[4:], s + ph, False) if name.startswith("cut_") else \
                tr.bit(int(inputs[name]["bits"][0][1:]), s + ph)
        return pads

    def mem_at(s, res):
        # Two-state program, four-state simulation: an unknown (x) RAM word or read register is given the value 0,
        # as hal-recover's replay does (unknown_operand_choice 0); comparisons use only known trace values.
        m = {}
        for g, c in cell_of.items():
            contents, read = snaps[s][c]
            if read is None:
                read = ice40_ram._pin_value(tr, rams[c]["pins"]["RDATA"], s)
            res["x_ram_words_as_0"] += sum(x is None for x in contents)
            res["x_ram_reads_as_0"] += read is None
            m[g] = [[0 if x is None else x for x in contents], 0 if read is None else read]
        return m

    def known_or_zero(d, res, tag):
        for k, x in d.items():
            if x is None:
                res[tag] += 1; d[k] = 0
        return d

    def compare(s, outs, vals, res):
        for (name, ph), val in vals.items():
            exp = wordval(name, s + ph, True)
            if exp is None:
                res["word_unknown"] += 1
            else:
                res["word_compared"] += 1
                if val != exp:
                    res["word_mismatch"] += 1
                    res.setdefault("first_word_mismatch", [s, name, ph, val, exp])
        pads = collections.defaultdict(dict)
        for ((port, i, kind), ph), val in outs.items():
            pads[(port, i, ph)][kind] = val
        for (port, i, ph), d in pads.items():
            entry = G["outputs"][port][i]
            # Plain pads: the pad net. Tristate pads: their data and enable nets (observed); the testbench drives a
            # released pad (enable 0) with stimulus, so the pad net then shows the external value, not z.
            checks = [("value", entry["bit"])] if "value" in d else \
                     [(k, entry[k]["bit"]) for k in ("data", "enable") if isinstance(entry[k], dict)]
            for kind, net in checks:
                c = tr.char(net, s + ph)
                if c not in "01":
                    res["pad_unknown"] += 1
                    continue
                res[f"pad_{kind}_compared"] += 1
                if int(c) != d[kind]:
                    res[f"pad_{kind}_mismatch"] += 1
                    res.setdefault("first_pad_mismatch", [s, port, i, ph, kind, d[kind], c])
            if "enable" in d and d["enable"] == 1:  # driven pad: the pad net must carry the data
                c = tr.char(entry["bit"], s + ph)
                if c in "01":
                    res["pad_driven_compared"] += 1
                    res["pad_driven_mismatch"] += int(c) != d["data"]

    per, chained = collections.Counter(), collections.Counter()
    for s in starts:
        pads, mem = known_or_zero(inputs_at(s), per, "x_inputs_as_0"), mem_at(s, per)
        state = known_or_zero({(n, ph, d): wordval(n, s + ph - d * P, True) for (n, ph, d) in state_keys}, per, "x_state_as_0")
        outs, vals, _ = ns["period"](pads, state, mem)
        compare(s, outs, vals, per)
        per["periods"] += 1
    state = mem = None
    for s in starts:
        pads = known_or_zero(inputs_at(s), chained, "x_inputs_as_0")
        if state is None:
            mem = mem_at(s, chained)
            state = known_or_zero({(n, ph, d): wordval(n, s + ph - d * P, True) for (n, ph, d) in state_keys}, chained, "x_state_as_0")
            chained["start"] = s
        outs, vals, state = ns["period"](pads, state, mem)
        compare(s, outs, vals, chained)
        chained["periods"] += 1
    return {"trace": directory, "events": n_ev, "period_starts": len(starts), "ram_replay": ram_stats,
            "per_period": dict(per), "chained": dict(chained)}


validations = [validate(d) for d in trace_dirs]
report = dict({"graph": graph_path, "period": P, "origin": origin, "certificate": cert}, **report_gen,
              state_values=len(state_keys), pad_reads=len(pad_keys),
              cut_inputs=sorted({k for k, _ in pad_keys if k.startswith("cut_")}),
              cut_input_reads=sum(1 for k, _ in pad_keys if k.startswith("cut_")),
              seconds_total=round(time.time() - t_start, 1), validation=validations)
json.dump(report, open(f"{out}/report.json", "w"), indent=1)
print(json.dumps({k: v for k, v in report.items() if k not in ("read_edges_per_period", "write_edges_per_period")}, indent=1))
