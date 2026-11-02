#!/usr/bin/env python3
"""C3 program recovery: certified event graph -> untimed transaction program.

Steps (NetNotary C3):
 1. specialise every node at its phase with the certificate's per-phase constants
    (untainted FF bits and non-data pads); the schedule FSM disappears;
 2. interface: data pads read at each phase are parameters; declared observations are
    results; delayed sources are loop-carried state;
 3. keep only nodes that reach an observation (dead-code elimination);
 4. emit a Python function (one call = one transaction) and validate it against the
    original-gate trace on transactions disjoint from the extraction window.

usage: decompile.py GRAPH_JSON TRACE_DIR CONTRACT_JSON OBSERVE_JSON CASE OUT_DIR
Run with build/c3_venv/bin/python (z3-solver)."""
import collections, json, os, sys
import z3

graph_path, trace_dir, contract_path, observe_path, case, out = sys.argv[1:7]
os.makedirs(out, exist_ok=False)
G = json.load(open(graph_path))
contract = json.load(open(contract_path))
observe = json.load(open(observe_path))[case]
P, origin = G["period"], G["origin"]
assert G["available"], "no event graph"
# A word never written in the window is admissible only if the certificate fixes all of
# its bits at every phase (a configuration constant); otherwise the graph is not closed.
for name, reason in G["excluded"].items():
    assert reason == "NO_WRITES_IN_WINDOW" and all("x" not in row for row in G["words"][name]["certified_known_lsb_first"]), \
        f"program recovery needs a closed graph: {name} {reason}"
assert G["certificate"]["certified"] and G["certificate"]["phases_aligned"], "needs a certificate aligned with the graph period"


def parse(smt2):
    a = z3.parse_smt2_string(smt2)
    eq = a[0]
    assert eq.decl().kind() == z3.Z3_OP_EQ and str(eq.arg(0)) == "__out"
    return eq.arg(1)


# ---- symbols ------------------------------------------------------------------
words = G["words"]
inputs = G["inputs"]
width = {name: len(w["bits"]) for name, w in words.items()}
bit_owner = {}
for name, w in words.items():
    for i, b in enumerate(w["bits"]):
        bit_owner[b] = ("word", name, i)
for name, inp in inputs.items():
    for i, b in enumerate(inp["bits"]):
        bit_owner[b] = ("input", name, i)
for name, mem in G["memories"].items():
    for i, b in enumerate(mem["read_bits"]):
        bit_owner[b] = ("ram", name, i)
assert not G["memories"], "RAM-backed graphs are not handled by this first version"


def const_bv(bits_lsb, w):
    return z3.BitVecVal(int(bits_lsb[::-1], 2), w)


def specialise(expr, phase):
    """Substitute certified per-phase constants for word bits and non-data pads."""
    subs = []
    for v in z3_vars(expr):
        name = str(v)
        kind, _, base = name.partition(":")
        if kind == "word":
            known = words[base]["certified_known_lsb_first"][phase]
        elif kind == "input":
            known = inputs[base]["certified_known_lsb_first"][phase]
        else:
            continue
        w = v.size()
        if "x" not in known:
            subs.append((v, const_bv(known, w)))
        elif known.strip("x"):
            mask = const_bv("".join("1" if c != "x" else "0" for c in known), w)
            val = const_bv("".join(c if c != "x" else "0" for c in known), w)
            subs.append((v, (v & ~mask) | val))
    return z3.simplify(z3.substitute(expr, *subs)) if subs else z3.simplify(expr)


def z3_vars(expr):
    seen, out, stack = set(), [], [expr]
    while stack:
        e = stack.pop()
        if e.get_id() in seen:
            continue
        seen.add(e.get_id())
        if z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out.append(e)
        stack.extend(e.children())
    return out


# ---- nodes -------------------------------------------------------------------------
nodes = {}
for n in G["nodes"]:
    e = parse(n["smt2"])
    nodes[(n["name"], n["phase"])] = {"expr": specialise(e, n["phase"]), "sources": {s["symbol"]: s for s in n["sources"]},
                                      "width": n["width"], "raw_size": len(n["smt2"])}
# Re-index sample coordinates so that one program call is one protocol transaction
# (Theorem 4: changing the origin is a retiming of the event graph).
first = contract["transactions"][0]["first_event"]
period_events = 2 * contract["period_cycles"]
assert period_events == P, f"graph period {P} differs from the transaction length {period_events}"
shift = (first - origin) % P
rho = lambda phase: -1 if phase < shift else 0
remapped = {}
for (name, phase), node in nodes.items():
    new_sources = {}
    for sym, src in node["sources"].items():
        if src["kind"] == "signal":
            delay = src["delay"] + rho(phase) - rho(src["phase"])
            assert delay >= 0
            new_sources[sym] = dict(src, phase=(src["phase"] - shift) % P, delay=delay)
        else:
            new_sources[sym] = dict(src, phase=(src["phase"] - shift) % P)
    remapped[(name, (phase - shift) % P)] = dict(node, sources=new_sources, old_phase=phase)
nodes = remapped
known_at = lambda table, psi: table[(psi + shift) % P]
writes = collections.defaultdict(list)
for (name, phase) in nodes:
    writes[name].append(phase)
for name in writes:
    writes[name].sort()


def last_write(name, phase):
    """(phase', delay) of the last write of `name` strictly before `phase` in sample coordinates."""
    before = [p for p in writes[name] if p < phase]
    return (before[-1], 0) if before else (writes[name][-1], 1)


# ---- observations ------------------------------------------------------------------
ports = collections.defaultdict(list)
for p in contract["port_bits"]:
    if p["direction"] == "output":
        ports[p["name"]].append(p)
for v in ports.values():
    v.sort(key=lambda p: p["offset"])


def output_expr(port_name):
    bits = []
    for p in ports[port_name]:
        spec = G["outputs"][p["anonymous_port"]][0]
        if isinstance(spec, int):
            bits.append(z3.BitVecVal(1 if spec == -2 else 0, 1))
        else:
            b = parse(spec["smt2"])
            bits.append(z3.If(b, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)))
    return z3.simplify(z3.Concat(*reversed(bits)) if len(bits) > 1 else bits[0])


def leaf_substitute(expr, phase):
    """Rewrite b<id> leaves of an output function into word/input symbols at program offset `phase` (0..P)."""
    subs = []
    for v in z3_vars(expr):
        owner = bit_owner[str(v)]
        if owner[0] == "word":
            subs.append((v, z3.Extract(owner[2], owner[2], z3.BitVec("word:" + owner[1], width[owner[1]]))))
        elif owner[0] == "input":
            subs.append((v, z3.Extract(owner[2], owner[2], z3.BitVec("input:" + owner[1], len(inputs[owner[1]]["bits"])))))
    return specialise(z3.substitute(expr, *subs), (phase + shift) % P)


observations = []
for ob in observe:
    if "cycle" in ob:
        offset = 2 * ob["cycle"]
        assert 0 <= offset <= P, "observation must lie within the transaction or at its end"
        phases = [offset]
    else:
        phases = []
        for ph in range(P):
            s = leaf_substitute(output_expr(ob["strobe"]), ph)
            assert z3.is_bv_value(s), f"strobe {ob['strobe']} is not certified constant at phase {ph}"
            if s.as_long() == 1:
                phases.append(ph)
    for ph in phases:
        observations.append({"port": ob["port"], "phase": ph, "expr": leaf_substitute(output_expr(ob["port"]), ph)})
assert observations, "no observation phases"

# ---- liveness from observations ----------------------------------------------------
live, state_reads, pad_reads = set(), set(), set()


def visit_symbols(expr, phase, sources=None):
    for v in z3_vars(expr):
        kind, _, base = str(v).partition(":")
        if kind == "word":
            src = sources[str(v)] if sources else None
            ph, d = (src["phase"], src["delay"]) if src else last_write(base, phase)
            if d == 0:
                if (base, ph) not in live:
                    live.add((base, ph)); stack.append((base, ph))
            else:
                state_reads.add((base, ph, d))
                if (base, ph) not in live:
                    live.add((base, ph)); stack.append((base, ph))
        elif kind == "input":
            pad_reads.add((base, phase))


stack = []
for ob in observations:
    visit_symbols(ob["expr"], ob["phase"])
while stack:
    key = stack.pop()
    node = nodes[key]
    visit_symbols(node["expr"], key[1], node["sources"])
order = sorted(live, key=lambda k: (k[1], k[0]))

# ---- Python emission ----------------------------------------------------------------
class Printer:
    def __init__(self, env):
        self.env, self.lines, self.memo, self.count = env, [], {}, 0

    def mask(self, w):
        return hex((1 << w) - 1)

    def emit(self, e):
        key = e.get_id()
        if key in self.memo:
            return self.memo[key]
        code = self.build(e)
        if e.num_args() > 0 and len(code) > 40:
            self.count += 1
            name = f"t{self.count}"
            self.lines.append(f"    {name} = {code}")
            code = name
        self.memo[key] = code
        return code

    def build(self, e):
        k = e.decl().kind()
        a = [self.emit(c) for c in e.children()]
        w = e.size() if z3.is_bv(e) else 1
        m = self.mask(w)
        if z3.is_bv_value(e):
            return hex(e.as_long())
        if z3.is_true(e):
            return "True"
        if z3.is_false(e):
            return "False"
        if z3.is_const(e):
            return self.env(str(e))
        if k == z3.Z3_OP_EXTRACT:
            hi, lo = e.params()
            return f"(({a[0]} >> {lo}) & {self.mask(hi - lo + 1)})"
        if k == z3.Z3_OP_CONCAT:
            parts, shift = [], 0
            for c, code in reversed(list(zip(e.children(), a))):
                parts.append(f"({code} << {shift})" if shift else code); shift += c.size()
            return "(" + " | ".join(parts) + ")"
        if k == z3.Z3_OP_BNOT:
            return f"({a[0]} ^ {m})"
        if k in (z3.Z3_OP_BAND, z3.Z3_OP_BOR, z3.Z3_OP_BXOR):
            op = {z3.Z3_OP_BAND: " & ", z3.Z3_OP_BOR: " | ", z3.Z3_OP_BXOR: " ^ "}[k]
            return "(" + op.join(a) + ")"
        if k == z3.Z3_OP_BADD:
            return f"(({' + '.join(a)}) & {m})"
        if k == z3.Z3_OP_BSUB:
            return f"(({a[0]} - {a[1]}) & {m})"
        if k == z3.Z3_OP_BMUL:
            return f"(({' * '.join(a)}) & {m})"
        if k == z3.Z3_OP_BNEG:
            return f"((-{a[0]}) & {m})"
        if k == z3.Z3_OP_BSHL:
            return f"(({a[0]} << {a[1]}) & {m} if {a[1]} < {w} else 0)"
        if k == z3.Z3_OP_BLSHR:
            return f"(({a[0]} >> {a[1]}) if {a[1]} < {w} else 0)"
        if k == z3.Z3_OP_BASHR:
            return f"(sra({a[0]}, {a[1]}, {w}))"
        if k == z3.Z3_OP_ITE:
            return f"({a[1]} if {a[0]} else {a[2]})"
        if k == z3.Z3_OP_EQ:
            return f"({a[0]} == {a[1]})"
        if k == z3.Z3_OP_NOT:
            return f"(not {a[0]})"
        if k == z3.Z3_OP_AND:
            return "(" + " and ".join(a) + ")"
        if k == z3.Z3_OP_OR:
            return "(" + " or ".join(a) + ")"
        if k == z3.Z3_OP_ULEQ:
            return f"({a[0]} <= {a[1]})"
        if k == z3.Z3_OP_ULT:
            return f"({a[0]} < {a[1]})"
        if k in (z3.Z3_OP_SLEQ, z3.Z3_OP_SLT):
            sw = e.children()[0].size()
            op = "<=" if k == z3.Z3_OP_SLEQ else "<"
            return f"(sgn({a[0]}, {sw}) {op} sgn({a[1]}, {sw}))"
        if k == z3.Z3_OP_SIGN_EXT:
            ext = e.params()[0]; sw = e.children()[0].size()
            return f"(sgn({a[0]}, {sw}) & {self.mask(sw + ext)})"
        if k == z3.Z3_OP_ZERO_EXT:
            return a[0]
        raise NotImplementedError(f"operator {e.decl().name()}")


lines = ["# Recovered untimed transaction program (generated; do not edit).",
         f"# case={case}; graph period={P} events; certified origin={G['certificate']['origin']}; one call = one protocol transaction",
         "def sgn(x, w):", "    return x - (1 << w) if (x >> (w - 1)) & 1 else x",
         "def sra(x, s, w):", "    return (sgn(x, w) >> min(s, w)) & ((1 << w) - 1)", "",
         "def transaction(pads, state):",
         "    \"\"\"pads[(pad, phase)] -> bit for data pads; state[(word, phase, delay)] -> loop-carried value.\"\"\"",
         "    v = {}"]
for key in order:
    name, ph = key
    node = nodes[key]

    def env(sym, ph=ph, node=node):
        kind, _, base = sym.partition(":")
        if kind == "word":
            s = node["sources"][sym]
            return f"v[{(base, s['phase'])!r}]" if s["delay"] == 0 else f"state[{(base, s['phase'], s['delay'])!r}]"
        if kind == "input":
            return f"pads[{(base, ph)!r}]"
        raise KeyError(sym)
    pr = Printer(env)
    code = pr.emit(node["expr"])
    lines += pr.lines
    lines.append(f"    v[{key!r}] = {code}")
lines.append("    outputs = {}")
for ob in observations:
    def env(sym, ph=ob["phase"]):
        kind, _, base = sym.partition(":")
        if kind == "word":
            p2, d = last_write(base, ph)
            return f"v[{(base, p2)!r}]" if d == 0 else f"state[{(base, p2, d)!r}]"
        if kind == "input":
            return f"pads[{(base, ph)!r}]"
        raise KeyError(sym)
    pr = Printer(env)
    code = pr.emit(ob["expr"])
    lines += pr.lines
    lines.append(f"    outputs[{(ob['port'], ob['phase'])!r}] = {code}")
state_keys = sorted(state_reads)
lines.append("    new_state = {}")
for (name, ph, d) in state_keys:
    src = f"v[{(name, ph)!r}]" if d == 1 else f"state[{(name, ph, d - 1)!r}]"
    lines.append(f"    new_state[{(name, ph, d)!r}] = {src}")
lines.append("    return outputs, new_state")
program = "\n".join(lines) + "\n"
open(f"{out}/program.py", "w").write(program)

# ---- validation against the original-gate trace -------------------------------------
meta = json.load(open(f"{trace_dir}/native_trace.json"))
pos = {b: i for i, b in enumerate(meta["observed"])}
rows = [l.split() for l in open(f"{trace_dir}/native_trace.txt")]


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


ns = {}
exec(compile(program, f"{out}/program.py", "exec"), ns)
training_end = contract["training_end_exclusive"]
results = collections.Counter()
first_failure = None
theta = origin + shift
for start in range(theta, len(rows) - 2 * P, P):
    if start < training_end:
        continue
    pads = {(base, ph): bitval(int(inputs[base]["bits"][0][1:]), start + ph) for (base, ph) in pad_reads}
    state = {}
    ok_state = True
    for (name, ph, d) in state_keys:
        val = wordval(name, start + ph - d * P, True)
        if val is None:
            ok_state = False
        state[(name, ph, d)] = val
    if not ok_state or any(v is None for v in pads.values()):
        results["skipped_unknown"] += 1
        continue
    outs, _ = ns["transaction"](pads, state)
    for (port, ph), value in outs.items():
        expected = 0
        for i, p in enumerate(ports[port]):
            x = bitval(p["physical_bit"], start + ph)
            if x is None:
                expected = None; break
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
report = {"case": case, "status": "PASS" if results["compared"] and not results["mismatch"] else "FAIL",
          "validation": dict(results), "first_failure": first_failure,
          "graph_nodes": len(nodes), "live_nodes": len(order), "state_values": len(state_keys),
          "data_pad_reads": len(pad_reads), "observations": [(o["port"], o["phase"]) for o in observations],
          "program_lines": program.count("\n"), "validated_on": "held-out transactions (start >= training_end_exclusive), state from the trace"}
json.dump(report, open(f"{out}/report.json", "w"), indent=1)
print(json.dumps(report, indent=1))
