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

Step 3 (roll-up): consecutive write steps whose nodes have the SAME raw (unspecialised)
equation and the same relative sources differ only in certified per-phase constants; they
are emitted once as a loop body whose varying constants come from an iteration table (or
an affine counter found over bit orders). Each iteration is SMT-checked against the flat
specialised node. Both the flat and the rolled program are validated on the trace.

usage: decompile.py GRAPH_JSON TRACE_DIR CONTRACT_JSON OBSERVE_JSON CASE OUT_DIR
Run with build/c3_venv/bin/python (z3-solver)."""
import collections, hashlib, itertools, json, os, sys
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
                                      "width": n["width"], "raw_size": len(n["smt2"]), "raw": e,
                                      "raw_sha256": hashlib.sha256(n["smt2"].encode()).hexdigest()}
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
         "def route(x, pairs):  # bit k of x -> bit o, for (o, k) in pairs",
         "    return sum(((x >> k) & 1) << o for o, k in pairs)",
         "def pins(*pairs):  # 1-bit values placed at bit o, for (o, v) in pairs",
         "    return sum((v & 1) << o for o, v in pairs)",
         "def bits(x, *idx):  # gather: bit i of the result = bit idx[i] of x (None: constant 0)",
         "    return sum(((x >> k) & 1) << i for i, k in enumerate(idx) if k is not None)",
         "def pick(*vk):  # gather across values: bit i = bit k_i of v_i, for v_i, k_i in pairs",
         "    return sum(((v >> k) & 1) << i for i, (v, k) in enumerate(zip(vk[::2], vk[1::2])) if k is not None)",
         "def place(y, *idx):  # scatter: bit i of y -> bit idx[i]",
         "    return sum(((y >> i) & 1) << k for i, k in enumerate(idx))",
         "def padword(pads, ph, names):  # word whose bit i is data pad names[i] at phase ph",
         "    return sum((pads.get((n, ph), 0) & 1) << i for i, n in enumerate(names))", "",
         "def transaction(pads, state):",
         "    \"\"\"pads[(pad, phase)] -> bit for data pads; state[(word, phase, delay)] -> loop-carried value.\"\"\"",
         "    v = {}"]
header = list(lines)


def source_code(node, sym, tolerant=False):
    s = node["sources"][sym]
    base = sym.partition(":")[2]
    if tolerant:
        return f"v.get({(base, s['phase'])!r}, 0)" if s["delay"] == 0 else f"state.get({(base, s['phase'], s['delay'])!r}, 0)"
    return f"v[{(base, s['phase'])!r}]" if s["delay"] == 0 else f"state[{(base, s['phase'], s['delay'])!r}]"


def emit_flat_node(key):
    name, ph = key
    node = nodes[key]

    def env(sym, ph=ph, node=node):
        kind, _, base = sym.partition(":")
        if kind == "word":
            return source_code(node, sym)
        if kind == "input":
            return f"pads[{(base, ph)!r}]"
        raise KeyError(sym)
    pr = Printer(env)
    code = pr.emit(node["expr"])
    return pr.lines + [f"    v[{key!r}] = {code}"]


for key in order:
    lines += emit_flat_node(key)
body_start = len(lines)
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
tail = lines[body_start:]
program_flat = "\n".join(lines) + "\n"
open(f"{out}/program_flat.py", "w").write(program_flat)

# ---- step 3: roll-up ------------------------------------------------------------------
steps = collections.OrderedDict()
for key in order:
    steps.setdefault(key[1], []).append(key[0])
step_phases = list(steps)


def known_pattern(sym, ph):
    """Certified known bits of a symbol at the (remapped) phase ph of a node, lsb first."""
    kind, _, base = sym.partition(":")
    table = words[base]["certified_known_lsb_first"] if kind == "word" else inputs[base]["certified_known_lsb_first"]
    return table[(ph + shift) % P]


def raw_symbols(name, ph):
    return sorted({str(v): v for v in z3_vars(nodes[(name, ph)]["raw"])}.items())


def data_symbol(sym, ph):
    return "x" in known_pattern(sym, ph)


def compatible(prev, cur, first):
    """Step `cur` repeats step `first`. A graph node of the loop's word set that dead-code
    elimination dropped at `cur` is recomputed (pure, harmless) if its data sources are
    available: carried words or live nodes."""
    if not set(steps[cur]) <= set(steps[first]):
        return False
    carried = set(steps[first])
    for name in steps[first]:
        if (name, cur) not in nodes:
            return False
        if (name, cur) not in live:
            for sym, _ in raw_symbols(name, cur):
                if sym.startswith("word:") and data_symbol(sym, cur):
                    src = nodes[(name, cur)]["sources"][sym]
                    base = sym.partition(":")[2]
                    if src["delay"] != 0 or (base not in carried and (src["name"], src["phase"]) not in live):
                        return False
        if nodes[(name, cur)]["raw_sha256"] != nodes[(name, first)]["raw_sha256"]:
            return False
    for name in steps[first]:
        for sym, _ in raw_symbols(name, cur):
            if not sym.startswith("word:"):
                continue
            if data_symbol(sym, cur) != data_symbol(sym, first):
                return False
            if not data_symbol(sym, cur):
                continue
            s = nodes[(name, cur)]["sources"][sym]
            base = sym.partition(":")[2]
            if base in carried:
                if (s["name"], s["phase"], s["delay"]) != (base, prev, 0):
                    return False
            else:
                f = nodes[(name, first)]["sources"][sym]
                if (s["name"], s["phase"], s["delay"]) != (f["name"], f["phase"], f["delay"]):
                    return False
    return True


def first_step_ok(first):
    """At the first iteration every node must read a carried word from one common source."""
    carried, seen = set(steps[first]), {}
    for name in steps[first]:
        for sym, _ in raw_symbols(name, first):
            base = sym.partition(":")[2]
            if sym.startswith("word:") and base in carried and data_symbol(sym, first):
                s = nodes[(name, first)]["sources"][sym]
                if seen.setdefault(base, (s["phase"], s["delay"])) != (s["phase"], s["delay"]):
                    return False
    return True


MIN_ITERATIONS = 3
runs, i = [], 0
while i < len(step_phases):
    j = i + 1
    if first_step_ok(step_phases[i]):
        while j < len(step_phases) and compatible(step_phases[j - 1], step_phases[j], step_phases[i]):
            j += 1
    if j - i >= MIN_ITERATIONS:
        runs.append(step_phases[i:j]); i = j
    else:
        i += 1


def pattern_subs(sym, var, pat):
    w = var.size()
    if "x" not in pat:
        return const_bv(pat, w)
    if pat.strip("x"):
        mask = const_bv("".join("1" if c != "x" else "0" for c in pat), w)
        val = const_bv("".join(c if c != "x" else "0" for c in pat), w)
        return (var & ~mask) | val
    return None


def find_counter(iter_vars, table):
    """Bit order (over the concatenated 1..8 bits of the varying constants) under which the
    iteration constants form an arithmetic sequence a + b*it; None if there is none."""
    bits = [(sym, k) for sym, w in iter_vars for k in range(w)]
    if not 1 <= len(bits) <= 8 or len(table) < 3:
        return None
    rows = [[(row[sym] >> k) & 1 for sym, k in bits] for row in table]
    W = len(bits)
    for perm in itertools.permutations(range(W)):
        seq = [sum(r[perm[q]] << q for q in range(W)) for r in rows]
        b = (seq[1] - seq[0]) % (1 << W)
        if b and all((seq[t] - seq[0] - b * t) % (1 << W) == 0 for t in range(len(seq))):
            # position q of the counter holds bit k of symbol sym
            return {"a": seq[0], "b": b, "width": W, "bits": [bits[perm[q]] for q in range(W)]}
    return None


def iteration_constants(run):
    """Fully known symbols whose certified values vary over the run, and their per-step values."""
    first = run[0]
    syms = {}
    for name in steps[first]:
        for sym, var in raw_symbols(name, first):
            syms[sym] = var
    out_ = []
    for sym, var in sorted(syms.items()):
        pats = [known_pattern(sym, ph) for ph in run]
        if any(pt != pats[0] for pt in pats) and all("x" not in pt for pt in pats):
            out_.append((sym, var.size()))
    return out_, [{sym: int(known_pattern(sym, ph)[::-1], 2) for sym, _ in out_} for ph in run]


def split_run(run):
    """Constants that are not an affine counter but piecewise constant (e.g. a round-range
    selector) split the run into sub-loops whose bodies have no varying constants."""
    iv, table = iteration_constants(run)
    if not iv or find_counter(iv, table) is not None:
        return [run]
    # blocks of steps whose specialised node functions are identical (hash-consed z3 terms)
    names_ = sorted(steps[run[0]])
    sig = [tuple(nodes[(nm, ph)]["expr"].get_id() for nm in names_) for ph in run]
    blocks, start = [], 0
    for k in range(1, len(run) + 1):
        if k == len(run) or sig[k] != sig[start]:
            blocks.append(run[start:k]); start = k
    if len(blocks) <= max(4, len(run) // 8):
        return [b for b in blocks if len(b) >= MIN_ITERATIONS]  # short blocks stay flat
    return [run]


runs = [sub for run in runs for sub in split_run(run)]
loops, roll_proofs = [], collections.Counter()
for li, run in enumerate(runs):
    first = run[0]
    names = sorted(steps[first])
    sym_vars = {}
    for name in names:
        for sym, var in raw_symbols(name, first):
            sym_vars[sym] = var
    invariant, iter_vars, partial = {}, [], []
    # all steps specialise to the same functions: the specialised function is the body
    uniform = len({tuple(nodes[(nm, ph)]["expr"].get_id() for nm in names) for ph in run}) == 1 and all((nm, ph) in nodes for nm in names for ph in run)
    for sym, var in ([] if uniform else sorted(sym_vars.items())):
        pats = [known_pattern(sym, ph) for ph in run]
        if all(pt == pats[0] for pt in pats):
            sub = pattern_subs(sym, var, pats[0])
            if sub is not None:
                invariant[sym] = sub
        elif all("x" not in pt for pt in pats):
            iter_vars.append((sym, var.size()))
        else:
            partial.append((sym, var.size()))
    table = [{sym: int(known_pattern(sym, ph)[::-1], 2) for sym, _ in iter_vars} for ph in run]
    ptable = [{sym: (int("".join("1" if c != "x" else "0" for c in known_pattern(sym, ph))[::-1], 2),
                     int("".join(c if c != "x" else "0" for c in known_pattern(sym, ph))[::-1], 2)) for sym, _ in partial} for ph in run]
    subs = list(invariant.items())
    iter_sym = {sym: z3.BitVec("iter:" + sym, w) for sym, w in iter_vars}
    for sym, w in iter_vars:
        subs.append((sym, iter_sym[sym]))
    for sym, w in partial:
        subs.append((sym, z3.BitVec("pv:" + sym, w)))  # pv:SYM = (SYM & ~imask) | ival, computed per iteration
    templates = {}
    for name in names:
        if uniform:
            templates[name] = nodes[(name, first)]["expr"]
            continue
        raw = nodes[(name, first)]["raw"]
        templates[name] = z3.simplify(z3.substitute(raw, *[(sym_vars[sym], e) for sym, e in subs]))
    # per-iteration check: template with this iteration's constants == flat specialised node
    for it, ph in enumerate(run):
        inst = [(iter_sym[sym], z3.BitVecVal(table[it][sym], w)) for sym, w in iter_vars]
        inst += [(z3.BitVec("pv:" + sym, w), (sym_vars[sym] & z3.BitVecVal(((1 << w) - 1) ^ ptable[it][sym][0], w)) | z3.BitVecVal(ptable[it][sym][1], w))
                 for sym, w in partial]
        for name in names:
            instance = z3.substitute(templates[name], *inst) if inst else templates[name]
            solver = z3.Solver(); solver.set("timeout", 120000)
            solver.add(instance != nodes[(name, ph)]["expr"])
            r = solver.check()
            roll_proofs[str(r)] += 1
            assert r == z3.unsat, f"roll-up iteration {it} of {name} not proven: {r}"
    stride = run[1] - run[0]
    regular = all(run[k + 1] - run[k] == stride for k in range(len(run) - 1))
    loops.append({"index": li, "phases": run, "names": names, "templates": templates, "iter_vars": iter_vars, "partial": partial,
                  "table": table, "ptable": ptable, "counter": find_counter(iter_vars, table), "regular": regular, "stride": stride})


def pyname(sym):
    return sym.replace(":", "_").replace("-", "_")


def emit_loop(L, lifted=False):
    first = L["phases"][0]
    carried = set(L["names"])
    out_lines = [f"    # loop {L['index']}: {len(L['phases'])} iterations writing {', '.join(L['names'])}"]
    n = len(L["phases"])
    if L["regular"]:
        phase_expr = f"{first} + {L['stride']} * it"
    else:
        out_lines.append(f"    L{L['index']}_PHASE = {L['phases']!r}")
        phase_expr = f"L{L['index']}_PHASE[it]"
    for sym, w in L["iter_vars"]:
        if L["counter"] is None:
            out_lines.append(f"    L{L['index']}_{pyname(sym)} = {[row[sym] for row in L['table']]!r}")
    for sym, w in L["partial"]:
        out_lines.append(f"    L{L['index']}_{pyname(sym)} = {[row[sym] for row in L['ptable']]!r}  # (known mask, known value)")
    # initial values of carried words: the common source at the first iteration
    init = {}
    for name in L["names"]:
        node = nodes[(name, first)]
        for sym in node["sources"]:
            base = sym.partition(":")[2]
            if sym.startswith("word:") and base in carried and data_symbol(sym, first) and base not in init:
                init[base] = source_code(node, sym, tolerant=True)
    for base in sorted(init):
        out_lines.append(f"    cur_{base} = {init[base]}")
    out_lines.append(f"    for it in range({n}):")
    out_lines.append(f"        ph = {phase_expr}")
    c = L["counter"]
    if c is not None:
        out_lines.append(f"        r = ({c['a']} + {c['b']} * it) & {hex((1 << c['width']) - 1)}  # iteration counter ({c['width']} bits)")
        per_sym = collections.defaultdict(list)
        for q, (sym, k) in enumerate(c["bits"]):
            per_sym[sym].append((k, q))
        for sym, w in L["iter_vars"]:
            parts = [f"(((r >> {q}) & 1) << {k})" if k else f"((r >> {q}) & 1)" for k, q in sorted(per_sym[sym])]
            out_lines.append(f"        {pyname('iter:' + sym)} = {' | '.join(parts)}")
    else:
        for sym, w in L["iter_vars"]:
            out_lines.append(f"        {pyname('iter:' + sym)} = L{L['index']}_{pyname(sym)}[it]")
    carried_l = set(L["names"])
    first_node = nodes[(L["names"][0], first)]
    for sym, w in L["partial"]:
        base_ = sym.partition(":")[2]
        mask_, val_ = pyname("imask:" + sym), pyname("ival:" + sym)
        out_lines.append(f"        {mask_}, {val_} = L{L['index']}_{pyname(sym)}[it]")
        if sym.startswith("word:"):
            holder = next(nodes[(nm, first)] for nm in L["names"] if sym in nodes[(nm, first)]["sources"])
            value_ = f"cur_{base_}" if base_ in carried_l else source_code(holder, sym, tolerant=True)
        else:
            value_ = f"pads.get(({base_!r}, ph), 0)"
        out_lines.append(f"        {pyname('pv:' + sym)} = ({value_} & ~{mask_}) | {val_}  # certified known bits of this iteration")
    new_names = []
    for name in L["names"]:
        node = nodes[(name, first)]

        def env(sym, node=node):
            # Reads use .get: every iteration is SMT-equal to its flat node, so a value the flat
            # node does not read (absent from the interface or dead) cannot affect the result.
            kind, _, base = sym.partition(":")
            if kind == "word":
                if base in carried:
                    return f"cur_{base}"
                return source_code(node, sym, tolerant=True)
            if kind == "input":
                return f"pads.get(({base!r}, ph), 0)"
            if kind in ("iter", "imask", "ival", "pv"):
                return pyname(sym)
            raise KeyError(sym)
        alias = {}
        if L["counter"] is not None:
            for q, (sym_, k) in enumerate(L["counter"]["bits"]):
                if dict(L["iter_vars"])[sym_] == 1:
                    alias["iter:" + sym_] = ("r", q)
        lifted_code = emit_lifted(L["templates"][name], env, 8, lambda s_: s_.partition(":")[0] in ("iter", "imask", "ival"),
                                  f"new_{name}", alias, node_id=[name, f"loop{L['index']}"], phase_code="ph", writes=name) if lifted else None
        pr = Printer(env)
        code = pr.emit(L["templates"][name])
        plain = ["    " + ln for ln in pr.lines] + [f"        new_{name} = {code}"]
        out_lines += lifted_code if lifted_code is not None and len(lifted_code) <= len(plain) else plain
        new_names.append(name)
    for name in new_names:
        out_lines.append(f"        v[({name!r}, ph)] = new_{name}")
    carried_written = [nm for nm in new_names if nm in init]
    if carried_written:
        out_lines.append("        " + ", ".join(f"cur_{nm}" for nm in carried_written) + " = " + ", ".join(f"new_{nm}" for nm in carried_written))
    return out_lines


# ---- step 4a: routing and table lifting --------------------------------------------
import lift
out_dir_proofs = f"{out}/proofs"


class Registry:
    """Module-level constants of the lifted program; tables are classified up to wire order."""

    def __init__(self):
        self.consts, self.names, self.classes, self.uses = {}, {}, collections.defaultdict(list), collections.Counter()

    def const(self, prefix, value):
        key = (prefix, repr(value))
        if key not in self.names:
            name = f"{prefix}{sum(1 for k in self.names if k[0] == prefix)}"
            self.names[key] = name; self.consts[name] = value
        return self.names[key]

    def classify(self, T, k, m):
        for name, S in self.classes[(k, m)]:
            c = lift.canonical([S, T], k, m)
            if len(c) == 1:
                _, in_order, out_order = c[0][1][1]
                self.uses[name] += 1
                return name, in_order, out_order
        name = f"TABLE{sum(len(v) for v in self.classes.values())}"
        self.classes[(k, m)].append((name, T)); self.consts[name] = list(T); self.uses[name] += 1
        return name, tuple(range(k)), tuple(range(m))

    def lines(self):
        return [f"{name} = {value!r}" for name, value in self.consts.items()]


registry = Registry()
lift_stats = collections.Counter()


import lift_arith, random as _random
arith_log = []
# numeric bit order of words, LSB first: from HAL's bit-order propagation where established,
# then from proven arithmetic (chain order of outputs and of whole-word operands)
word_order = {w: list(range(len(d["bits"]))) for w, d in words.items() if d.get("numeric_order_known")}
order_sources = {w: "HAL" for w in word_order}


def base_word(sym):
    parts = sym.split(":")
    return parts[-1] if "word" in parts[:-1] else None


def learn_order(word, order, how):
    if word and word not in word_order and len(order) == len(words[word]["bits"]) and sorted(order) == list(range(len(order))):
        word_order[word] = list(order); order_sources[word] = how
ARITH_PROOF_MS = {"linear": 60000, "bilinear": 20000, "arx": 120000}


def known_order_candidates(expr, variables, used, is_param, f, rng):
    rest = [i for i in range(expr.size()) if i not in used]
    if len(rest) < 4:
        return
    syms = set()
    for i in rest:
        syms |= {x for x in lift.symbol_bits(z3.simplify(z3.Extract(i, i, expr)))}
    syms = sorted(x for x in syms if not is_param(x))
    if not 1 <= len(syms) <= 3 or any(base_word(x) not in word_order for x in syms):
        return
    support = set()
    for i in rest:
        support |= {(x, k) for x, ks in lift.symbol_bits(z3.simplify(z3.Extract(i, i, expr))).items() for k in ks}
    operands = [[(x, k) if (x, k) in support else None for k in word_order[base_word(x)]] for x in syms]
    cand = lift_arith.fit_known(f, expr.size(), operands, rng, outputs=rest)
    if cand is not None:
        yield cand


def arith_part(expr, variables, node_id, phase_code, src, out, ind, writes=None, is_param=lambda s_: False):
    """Proven word-level arithmetic for expr: chains of nested supports, then known-order
    identification for the remaining bits. Returns (bits, code terms)."""
    if expr.size() < 4:
        return set(), []
    chains_, _ = lift_arith.chains(expr)
    f = lift_arith.compile_expr(expr, Printer)
    rng = _random.Random(20261106)
    used, terms = set(), []

    def candidates():
        for chain in chains_:
            cand = lift_arith.fit_chain(f, chain, None, rng, None) or lift_arith.fit_chain_arx(f, chain, rng)
            if cand is not None:
                yield cand
        yield from known_order_candidates(expr, variables, used, is_param, f, rng)
    for cand in candidates():
        if set(cand["outputs"]) & used:
            continue
        kind = "bilinear" if any(cand["bilinear"].values()) else ("arx" if cand.get("bitwise") else "linear")
        z = lift_arith.candidate_expr(cand, variables)
        solver = z3.Solver(); solver.set("timeout", ARITH_PROOF_MS[kind])
        solver.add(z3.Or(*[z3.Extract(o, o, expr) != z3.Extract(q, q, z) for q, o in enumerate(cand["outputs"])]))
        os.makedirs(f"{out_dir_proofs}", exist_ok=True)
        qfile = f"{out_dir_proofs}/arith_{len(arith_log)}.smt2"
        open(qfile, "w").write(solver.to_smt2())
        status = str(solver.check())
        entry = {"node": node_id, "kind": kind, "width": cand["width"], "operand_widths": [len(g) for g in cand["operands"]],
                 "signed": cand.get("signed"), "bitwise": (cand.get("bitwise") or {}).get("name") or (bool(cand.get("bitwise")) or None),
                 "const": cand["const"], "linear": cand["linear"], "bilinear": {f"{g},{h}": v for (g, h), v in cand["bilinear"].items()},
                 "random_tests": 64, "proof": status.upper(), "query": qfile, "sha256": hashlib.sha256(open(qfile, "rb").read()).hexdigest()}
        arith_log.append(entry)
        entry["method"] = cand.get("method", "nested_support_chain")
        if status != "unsat":
            continue  # tested candidate only: never used in the program
        entry["order_ambiguous"] = cand.get("order_ambiguous", False)
        if writes and len(cand["outputs"]) == expr.size() and not cand.get("order_ambiguous"):
            learn_order(writes, cand["outputs"], f"proven {kind} output")
        for grp in cand["operands"]:
            syms_ = {b[0] for b in grp if b is not None}
            if None not in grp and len(syms_) == 1 and base_word(next(iter(syms_))):
                learn_order(base_word(next(iter(syms_))), [k for _, k in grp], f"proven {kind} operand")
        m = cand["width"]
        names = []
        for gi, grp in enumerate(cand["operands"]):
            syms_ = {b[0] for b in grp if b is not None}
            nm = f"a{len(out)}_{gi}"
            if all(x.startswith("input:") and variables[x].size() == 1 for x in syms_) and None not in grp:
                out.append(f"{ind}{nm} = padword(pads, {phase_code}, {registry.const('PADS', tuple(x.partition(':')[2] for x, _ in grp))})")
            else:
                codes = [None if b is None else src(*b) for b in grp]
                bases = {c[0] for c in codes if c is not None}
                if len(bases) == 1:
                    out.append(f"{ind}{nm} = bits({next(iter(bases))}, *{registry.const('ORD', tuple(None if c is None else c[1] for c in codes))})")
                else:
                    out.append(f"{ind}{nm} = pick(" + ", ".join("0, None" if c is None else f"{c[0]}, {c[1]}" for c in codes) + ")")
            if cand.get("signed", [False] * len(cand["operands"]))[gi]:
                out.append(f"{ind}{nm} = sgn({nm}, {len(grp)})  # two's complement operand")
            names.append(nm)
        poly = [str(cand["const"])] if cand["const"] else []
        sc = lambda c_: c_ - (1 << m) if c_ >> (m - 1) else c_  # print coefficients as signed residues
        for g, cg in enumerate(cand["linear"]):
            if cg:
                poly.append(names[g] if sc(cg) == 1 else (f"-{names[g]}" if sc(cg) == -1 else f"({sc(cg)}) * {names[g]}"))
        for (g, h), dv in cand["bilinear"].items():
            if dv:
                poly.append(f"{names[g]} * {names[h]}" if sc(dv) == 1 else f"({sc(dv)}) * {names[g]} * {names[h]}")
        bw = cand.get("bitwise")
        if bw:
            rn = []
            for r, role in enumerate(bw["roles"]):
                codes = [src(*b) for b in role]
                bases = {c_[0] for c_ in codes}
                nm = f"w{len(out)}_{r}"
                if len(bases) == 1:
                    out.append(f"{ind}{nm} = bits({next(iter(bases))}, *{registry.const('ORD', tuple(c_[1] for c_ in codes))})")
                else:
                    out.append(f"{ind}{nm} = pick(" + ", ".join(f"{c_[0]}, {c_[1]}" for c_ in codes) + ")")
                rn.append(nm)
            M_ = hex((1 << m) - 1)
            if bw["name"] == "ch" and len(rn) == 3:
                fexpr = f"({rn[0]} & {rn[1]}) | (~{rn[0]} & {rn[2]})"
            elif bw["name"] == "parity" and len(rn) == 3:
                fexpr = f"{rn[0]} ^ {rn[1]} ^ {rn[2]}"
            elif bw["name"] == "maj" and len(rn) == 3:
                fexpr = f"({rn[0]} & {rn[1]}) | ({rn[0]} & {rn[2]}) | ({rn[1]} & {rn[2]})"
            else:
                terms_ = []
                for x, v in enumerate(bw["table"]):
                    if v:
                        terms_.append("(" + " & ".join(w if (x >> r) & 1 else f"~{w}" for r, w in enumerate(rn)) + ")")
                fexpr = " | ".join(terms_) or "0"
            fname = f"f{len(out)}"
            out.append(f"{ind}{fname} = ({fexpr}) & {M_}  # bitwise layer{' (' + bw['name'] + ')' if bw['name'] else ''}")
            poly.append(fname)
        zname = f"z{len(out)}"
        out.append(f"{ind}{zname} = ({' + '.join(poly) or '0'}) & {hex((1 << m) - 1)}  # proven: {kind} word arithmetic")
        terms.append(f"place({zname}, *{registry.const('OUT', tuple(cand['outputs']))})")
        used |= set(cand["outputs"])
        lift_stats[f"arith_{kind}_bits"] += m
    return used, terms


def emit_lifted(expr, env, indent, is_param, target, alias=None, node_id=None, phase_code="ph", writes=None):
    """Lines computing `target` = expr with proven arithmetic, routes, XOR layers and tables
    lifted; None if nothing lifts. alias maps a 1-bit parameter symbol to (code, bit) of a loop counter."""
    variables = {str(x): x for x in z3_vars(expr)}
    alias = alias or {}
    ind = " " * indent
    out, local = [], {}

    def src(sname, k):
        if sname in alias:
            return alias[sname]
        if sname not in local:
            local[sname] = f"s{len(local)}"
            out.append(f"{ind}{local[sname]} = {env(sname)}")
        return local[sname], k
    abits, aterms = arith_part(expr, variables, node_id, phase_code, src, out, ind, writes, is_param)
    rest = z3.simplify(expr & z3.BitVecVal(((1 << expr.size()) - 1) ^ sum(1 << i for i in abits), expr.size())) if abits else expr
    plan = lift.lift(rest, variables, is_param)
    if not (aterms or plan["route"] or plan["xor"] or plan["tables"] or plan["param_route"]):
        return None

    def gather(items):
        codes = [src(x, k) for x, k in items]
        if len({c for c, _ in codes}) == 1:
            return f"bits({codes[0][0]}, *{tuple(b for _, b in codes)})"
        return "pick(" + ", ".join(f"{c}, {b}" for c, b in codes) + ")"

    def routes(pairs_by_code, prefix="R"):
        terms = []
        for code, pairs in sorted(pairs_by_code.items()):
            terms.append(f"route({code}, {registry.const(prefix, tuple(sorted(pairs)))})")
        return terms
    parts, xor_terms = list(aterms), []
    inv = 0
    by_code = collections.defaultdict(list)
    pin_pairs = []
    for o, (sname, k), flag in plan["route"]:
        inv |= flag << o
        if variables[sname].size() == 1 and sname not in alias:
            pin_pairs.append((o, src(sname, k)[0]))
        else:
            code, b = src(sname, k); by_code[code].append((o, b))
    parts += routes(by_code)
    if pin_pairs:
        parts.append("pins(" + ", ".join(f"({o}, {c})" for o, c in sorted(pin_pairs)) + ")")
    lift_stats["route_bits"] += len(plan["route"])
    # XOR layers: the t-th term of every bit (ordered by source and rotation offset) forms layer t
    if plan["xor"]:
        layers = collections.defaultdict(lambda: collections.defaultdict(list))
        for o, terms, c0 in plan["xor"]:
            inv |= c0 << o
            keyed = sorted(terms, key=lambda xk: (xk[0], (xk[1] - o) % variables[xk[0]].size()))
            seen = collections.Counter()
            for x, k in keyed:
                code, b = src(x, k)
                layer = (code, seen[code]); seen[code] += 1
                layers[layer][code].append((o, b))
        xs = []
        for layer in sorted(layers):
            xs += routes(layers[layer], "X")
        parts.append("(" + " ^ ".join(xs) + ")")
        lift_stats["xor_bits"] += len(plan["xor"])
    # tables: XOR pre-whitening, then classification up to wire order
    loops_ = collections.defaultdict(list)
    for t in plan["tables"]:
        w = lift.whiten(t, is_param)
        if w:
            name, in_order, out_order = registry.classify(w["table"], len(w["a"]), len(t["outputs"]))
            a = [w["a"][q] for q in in_order]; b = [w["b"][q] for q in in_order]
            outs = tuple(t["outputs"][q] for q in out_order)
            asym, bsym = {x for x, _ in a}, {x for x, _ in b}
            if len(asym) == 1 and len(bsym) == 1 and not (asym | bsym) & set(alias):
                loops_[(name, "xor", asym.pop(), bsym.pop())].append((tuple(k for _, k in a), tuple(k for _, k in b), outs))
            else:
                parts.append(f"place({name}[{gather(a)} ^ {gather(b)}], *{outs})")
            lift_stats["xor_table_bits"] += len(t["outputs"])
        else:
            name, in_order, out_order = registry.classify(t["table"], len(t["inputs"]), len(t["outputs"]))
            ins = [t["inputs"][q] for q in in_order]
            outs = tuple(t["outputs"][q] for q in out_order)
            isym = {x for x, _ in ins}
            if len(isym) == 1 and not isym & set(alias):
                loops_[(name, "plain", isym.pop(), None)].append((tuple(k for _, k in ins), None, outs))
            else:
                parts.append(f"place({name}[{gather(ins)}], *{outs})")
            lift_stats["table_bits"] += len(t["outputs"])
        lift_stats["tables"] += 1
    for (name, kind, A, B), inst in loops_.items():
        ca = src(A, 0)[0]
        if len(inst) == 1:
            ia, ib, outs = inst[0]
            idx = f"bits({ca}, *{ia})" + (f" ^ bits({src(B, 0)[0]}, *{ib})" if kind == "xor" else "")
            parts.append(f"place({name}[{idx}], *{outs})")
            continue
        GA = registry.const("IN", tuple(i[0] for i in inst)); OUT = registry.const("OUT", tuple(i[2] for i in inst))
        idx = f"bits({ca}, *{GA}[j])"
        if kind == "xor":
            GB = registry.const("IN", tuple(i[1] for i in inst)); idx += f" ^ bits({src(B, 0)[0]}, *{GB}[j])"
        acc = f"u{len(out)}"
        out.append(f"{ind}{acc} = 0")
        out.append(f"{ind}for j in range({len(inst)}):  # {len(inst)} instances of {name}")
        out.append(f"{ind}    {acc} |= place({name}[{idx}], *{OUT}[j])")
        parts.append(acc)
    # parameter parts (XORed on top): single parameter bits are routes, the rest small tables
    pby = collections.defaultdict(list)
    for o, params, g in plan["param_route"]:
        if len(params) == 1 and g == [0, 1]:
            code, b = src(*params[0]); pby[code].append((o, b))
        else:
            xor_terms.append(f"({registry.const('P', tuple(g))}[{gather(params)}] << {o})")
        lift_stats["param_bits"] += 1
    xor_terms = routes(pby, "R") + xor_terms
    if plan["keep"]:
        mask = sum(1 << i for i in plan["keep"])
        kexpr = z3.simplify(rest & z3.BitVecVal(mask, expr.size()))
        pr = Printer(env)
        code = pr.emit(kexpr)
        out += [ind + ln[4:] for ln in pr.lines]
        parts.append(code)
        lift_stats["kept_bits"] += len(plan["keep"])
    value = " | ".join(parts) if parts else "0"
    const = plan["const"] ^ inv
    if const:
        value = f"({value}) ^ {hex(const)}"
    if xor_terms:
        value = f"({value}) ^ " + " ^ ".join(xor_terms)
    out.append(f"{ind}{target} = {value}")
    return out


def emit_flat_node_lifted(key):
    name, ph = key
    node = nodes[key]

    def env(sym_, ph=ph, node=node):
        kind, _, base = sym_.partition(":")
        if kind == "word":
            return source_code(node, sym_)
        if kind == "input":
            return f"pads[{(base, ph)!r}]"
        raise KeyError(sym_)
    lifted = emit_lifted(node["expr"], env, 4, lambda s_: False, f"v[{key!r}]", node_id=list(key), phase_code=str(ph), writes=name)
    plain = emit_flat_node(key)
    return lifted if lifted is not None and len(lifted) <= len(plain) else plain


rolled = list(header)
in_loop = {(name, ph): L for L in loops for ph in L["phases"] for name in L["names"]}
emitted = set()
for key in order:
    L = in_loop.get(key)
    if L is None:
        rolled += emit_flat_node(key)
    elif L["index"] not in emitted:
        emitted.add(L["index"]); rolled += emit_loop(L)
rolled += tail
program_rolled = "\n".join(rolled) + "\n"
open(f"{out}/program_rolled.py", "w").write(program_rolled)

LIFT = True
lifted_lines = []
emitted = set()
for key in order:
    L = in_loop.get(key)
    if L is None:
        lifted_lines += emit_flat_node_lifted(key)
    elif L["index"] not in emitted:
        emitted.add(L["index"]); lifted_lines += emit_loop(L, lifted=True)
tail_lifted = ["    outputs = {}"]
for ob in observations:
    def env(sym, ph=ob["phase"]):
        kind, _, base = sym.partition(":")
        if kind == "word":
            p2, d = last_write(base, ph)
            return f"v[{(base, p2)!r}]" if d == 0 else f"state[{(base, p2, d)!r}]"
        if kind == "input":
            return f"pads[{(base, ph)!r}]"
        raise KeyError(sym)
    target = f"outputs[{(ob['port'], ob['phase'])!r}]"
    code = emit_lifted(ob["expr"], env, 4, lambda s_: False, target, node_id=["output", ob["port"], ob["phase"]], phase_code=str(ob["phase"]))
    pr = Printer(env)
    plain = pr.lines + [f"    {target} = {pr.emit(ob['expr'])}"]
    tail_lifted += code if code is not None and len(code) <= len(plain) else plain
tail_lifted += tail[tail.index("    new_state = {}"):]
hdr = list(header)
cut = hdr.index("def transaction(pads, state):")
program = "\n".join(hdr[:cut] + registry.lines() + [""] + hdr[cut:] + lifted_lines + tail_lifted) + "\n"
open(f"{out}/program.py", "w").write(program)
json.dump({"pad_reads": sorted(pad_reads), "state_keys": state_keys,
           "observations": [(o["port"], o["phase"]) for o in observations]}, open(f"{out}/interface.json", "w"), indent=1)

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


training_end = contract["training_end_exclusive"]
theta = origin + shift


def validate(text, path):
    ns = {}
    exec(compile(text, path, "exec"), ns)
    results = collections.Counter()
    first_failure = None
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
    return results, first_failure


flat_results, flat_failure = validate(program_flat, f"{out}/program_flat.py")
rolled_results, rolled_failure = validate(program_rolled, f"{out}/program_rolled.py")
results, first_failure = validate(program, f"{out}/program.py")
ok = lambda r: bool(r["compared"]) and not r["mismatch"]
report = {"case": case, "status": "PASS" if ok(results) and ok(flat_results) and ok(rolled_results) else "FAIL",
          "rolled_validation": dict(rolled_results), "rolled_first_failure": rolled_failure,
          "rolled_program_lines": program_rolled.count("\n"),
          "lifting": dict(lift_stats), "arithmetic": arith_log, "word_orders": {w: order_sources[w] for w in sorted(word_order)}, "tables": {name: {"inputs": k, "outputs": m, "instances": registry.uses[name], "table": T}
                                                for (k, m), v in registry.classes.items() for name, T in v},
          "validation": dict(results), "first_failure": first_failure,
          "flat_validation": dict(flat_results), "flat_first_failure": flat_failure,
          "flat_program_lines": program_flat.count("\n"),
          "loops": [{"iterations": len(L["phases"]), "first_phase": L["phases"][0], "stride": L["stride"] if L["regular"] else None,
                     "words": L["names"], "iteration_constants": [s_ for s_, _ in L["iter_vars"]],
                     "partially_known_varying": [s_ for s_, _ in L["partial"]],
                     "counter": None if L["counter"] is None else {k: L["counter"][k] for k in ("a", "b", "width")}} for L in loops],
          "rollup_iteration_checks": dict(roll_proofs),
          "graph_nodes": len(nodes), "live_nodes": len(order), "state_values": len(state_keys),
          "data_pad_reads": len(pad_reads), "observations": [(o["port"], o["phase"]) for o in observations],
          "program_lines": program.count("\n"), "validated_on": "held-out transactions (start >= training_end_exclusive), state from the trace"}
json.dump(report, open(f"{out}/report.json", "w"), indent=1)
print(json.dumps(report, indent=1))
