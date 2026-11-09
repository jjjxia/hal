#!/usr/bin/env python3
"""End-to-end translation validation of a recovered program against the certified periodic equations.

Reference (independent of inverse folding): the certified word equations of the event graph, specialised with the
certificate's per-phase constants, executed with register semantics over one protocol transaction -- every write
reads the register file before its edge, unwritten words hold, observations read the register file at their phase.
This is the equation system that the certificate proves on the original netlist (W and O obligations); the graph's
producer resolution ("sources") is NOT used.

Program: the generated Python text itself (flat, rolled or lifted), executed with symbolic bit-vectors: every data
pad read and every loop-carried state value is a fresh symbol; every write v[(word, phase)] is recorded and then
replaced by a cut symbol c_(word,phase), which the reference also uses for the register value after that write.

Checks, each proved by z3 for all values of its symbols:
  node     each recorded write v[(w, ph)] equals the reference update of w at ph, with the reference register file
           holding the cut symbols of the last writes before ph (or the state symbol s_w at the transaction start);
  output   each observation equals the reference observation;
  state    each new_state[(w, ph, 1)] is the last write of w in the transaction, and each state read is (w, last, 1).
By induction over the writes in transaction order, all checks together give: for every assignment of data pads and
loop-carried state, the program's outputs and next state equal those of the certified equations over one
transaction. Trusted: the netlist semantics and certificate (as before), Python's semantics of the printed text, and
this checker.
usage: close_chain.py GRAPH_JSON CONTRACT_JSON OBSERVE_JSON CASE OUT_JSON PROGRAM_PY [PROGRAM_PY ...]
Run with the c3 venv python (z3)."""
import ast, builtins, collections, json, sys, time
import z3

graph_path, contract_path, observe_path, case, out_path = sys.argv[1:6]
programs = sys.argv[6:]
G = json.load(open(graph_path))
contract = json.load(open(contract_path))
observe = json.load(open(observe_path))[case]
P, origin = G["period"], G["origin"]
assert G["available"] and G["certificate"]["certified"] and G["certificate"]["phases_aligned"]
assert not G["memories"], "RAM-backed graphs are not handled"
words, inputs = G["words"], G["inputs"]
width = {n: len(w["bits"]) for n, w in words.items()}
in_width = {n: len(i["bits"]) for n, i in inputs.items()}
bit_owner = {}
for n, w in words.items():
    for i, b in enumerate(w["bits"]):
        bit_owner[b] = ("word", n, i)
for n, inp in inputs.items():
    for i, b in enumerate(inp["bits"]):
        bit_owner[b] = ("input", n, i)


# ---- reference: certified equations with register semantics ------------------------------------------------------
def parse(smt2):
    eq = z3.parse_smt2_string(smt2)[0]
    assert eq.decl().kind() == z3.Z3_OP_EQ and str(eq.arg(0)) == "__out"
    return eq.arg(1)


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


def const_bv(bits_lsb, w):
    return z3.BitVecVal(int(bits_lsb[::-1], 2), w)


def specialise(expr, phase):
    """Certified per-phase constants for word bits and non-data pads (the certificate's relation R_phase)."""
    subs = []
    for v in z3_vars(expr):
        kind, _, base = str(v).partition(":")
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


first = contract["transactions"][0]["first_event"]
assert 2 * contract["period_cycles"] == P, "graph period differs from the transaction length"
shift = (first - origin) % P
upd = collections.defaultdict(dict)          # program phase -> word -> specialised equation
for n in G["nodes"]:
    upd[(n["phase"] - shift) % P][n["name"]] = specialise(parse(n["smt2"]), n["phase"])
written = collections.defaultdict(list)
for ph in sorted(upd):
    for w in upd[ph]:
        written[w].append(ph)
last_phase = {w: phs[-1] for w, phs in written.items()}

cut = lambda w, ph: z3.BitVec(f"c|{w}|{ph}", width[w])
pad = lambda n, ph: z3.BitVec(f"p|{n}|{ph}", in_width[n])
st = lambda w: z3.BitVec(f"s|{w}", width[w])

reg = {w: st(w) for w in words}               # register file at the transaction start
reg_at, ref_node = {}, {}
for ph in range(P + 1):
    reg_at[ph] = dict(reg)
    if ph == P:
        break
    for w, e in upd.get(ph, {}).items():
        subs = []
        for v in z3_vars(e):
            kind, _, base = str(v).partition(":")
            subs.append((v, reg[base] if kind == "word" else pad(base, ph)))
        ref_node[(w, ph)] = z3.substitute(e, *subs) if subs else e
    for w in upd.get(ph, {}):
        reg[w] = cut(w, ph)

ports = collections.defaultdict(list)
for p in contract["port_bits"]:
    if p["direction"] == "output":
        ports[p["name"]].append(p)
for v in ports.values():
    v.sort(key=lambda p: p["offset"])


def observation(port_name, ph):
    bits = []
    for p in ports[port_name]:
        spec = G["outputs"][p["anonymous_port"]][0]
        if isinstance(spec, int):
            bits.append(z3.BitVecVal(1 if spec == -2 else 0, 1))
            continue
        b = parse(spec["smt2"])
        subs = []
        for v in z3_vars(b):
            kind, base, i = bit_owner[str(v)]
            sym = z3.BitVec(f"{kind}:{base}", width[base] if kind == "word" else in_width[base])
            subs.append((v, z3.Extract(i, i, sym)))
        b = specialise(z3.substitute(b, *subs), (ph + shift) % P)
        subs = [(v, reg_at[ph][str(v).partition(":")[2]] if str(v).startswith("word:") else pad(str(v).partition(":")[2], ph))
                for v in z3_vars(b)]
        b = z3.substitute(b, *subs) if subs else b
        bits.append(z3.If(b, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1)) if z3.is_bool(b) else b)
    return z3.simplify(z3.Concat(*reversed(bits)) if len(bits) > 1 else bits[0])


ref_out = {}
for ob in observe:
    if "cycle" in ob:
        phases = [2 * ob["cycle"]]
    else:
        phases = []
        for ph in range(P):
            s = observation(ob["strobe"], ph)
            assert z3.is_bv_value(s), f"strobe {ob['strobe']} not certified constant at phase {ph}"
            if s.as_long() == 1:
                phases.append(ph)
    for ph in phases:
        ref_out[(ob["port"], ph)] = observation(ob["port"], ph)


# ---- symbolic execution of the program text ------------------------------------------------------------------------
class Sym:
    W = 0
    __slots__ = ("t",)

    def __init__(self, t):
        self.t = t


def bv(x):
    if isinstance(x, Sym):
        return x.t
    if isinstance(x, SymBool):  # Python bools are the integers 0 and 1
        return z3.If(x.t, z3.BitVecVal(1, Sym.W), z3.BitVecVal(0, Sym.W))
    if isinstance(x, bool):
        return z3.BitVecVal(int(x), Sym.W)
    if isinstance(x, int):
        return z3.BitVecVal(x % (1 << Sym.W), Sym.W)
    raise TypeError(type(x))


def wrap(t):
    t = z3.simplify(t)
    return t.as_signed_long() if z3.is_bv_value(t) else Sym(t)


def _binop(f):
    def op(a, b):
        return wrap(f(bv(a), bv(b)))
    return op


def _shl(a, b):
    if isinstance(b, int):
        return wrap(bv(a) << b) if b < Sym.W else 0
    return wrap(z3.If(z3.ULT(bv(b), Sym.W), bv(a) << bv(b), 0))


def _shr(a, b):
    if isinstance(b, int):
        return wrap(bv(a) >> min(b, Sym.W - 1))
    return wrap(bv(a) >> bv(b))


class SymBool:
    __slots__ = ("t",)

    def __init__(self, t):
        self.t = t

    def __bool__(self):
        raise TypeError("symbolic condition used as a Python bool")


def bl(x):
    if isinstance(x, SymBool):
        return x.t
    if isinstance(x, bool):
        return z3.BoolVal(x)
    raise TypeError(type(x))


def wrapb(t):
    t = z3.simplify(t)
    return bool(z3.is_true(t)) if (z3.is_true(t) or z3.is_false(t)) else SymBool(t)


def _booleq(a, b, neg=False):
    """Python ==/!= with a symbolic bool operand: bool against bool is iff; otherwise integer comparison (0/1)."""
    if isinstance(b, (SymBool, bool)) and isinstance(a, (SymBool, bool)):
        r = bl(a) == bl(b)
    else:
        r = bv(a) == bv(b)
    return wrapb(z3.Not(r) if neg else r)


SymBool.__eq__ = lambda a, b: _booleq(a, b)
SymBool.__ne__ = lambda a, b: _booleq(a, b, True)
SymBool.__hash__ = None
for _name in ("and", "or", "xor"):  # bool op bool stays bool; with an int it is integer arithmetic
    def _bop(a, b, _name=_name):
        if isinstance(b, (SymBool, bool)):
            f = {"and": z3.And, "or": z3.Or, "xor": z3.Xor}[_name]
            return wrapb(f(bl(a), bl(b)))
        return getattr(Sym(bv(a)), f"__{_name}__")(b)
    setattr(SymBool, f"__{_name}__", _bop)
    setattr(SymBool, f"__r{_name}__", lambda a, b, _bop=_bop: _bop(a, b))
for _name in ("add", "sub", "mul", "lshift", "rshift"):
    setattr(SymBool, f"__{_name}__", lambda a, b, _name=_name: getattr(Sym(bv(a)), f"__{_name}__")(b))
    setattr(SymBool, f"__r{_name}__", lambda a, b, _name=_name: getattr(Sym(bv(a)), f"__r{_name}__")(b))
SymBool.__invert__ = lambda a: Sym(bv(a)).__invert__()
SymBool.__neg__ = lambda a: Sym(bv(a)).__neg__()


for name, f in {"and": lambda a, b: a & b, "or": lambda a, b: a | b, "xor": lambda a, b: a ^ b,
                "add": lambda a, b: a + b, "sub": lambda a, b: a - b, "mul": lambda a, b: a * b}.items():
    setattr(Sym, f"__{name}__", _binop(f))
    setattr(Sym, f"__r{name}__", (lambda g: lambda a, b: _binop(g)(b, a))(f))
Sym.__lshift__ = _shl
Sym.__rlshift__ = lambda a, b: _shl(b, a)
Sym.__rshift__ = _shr
Sym.__rrshift__ = lambda a, b: _shr(b, a)
Sym.__invert__ = lambda a: wrap(~a.t)
Sym.__neg__ = lambda a: wrap(-a.t)
Sym.__lt__ = lambda a, b: wrapb(bv(a) < bv(b))
Sym.__le__ = lambda a, b: wrapb(bv(a) <= bv(b))
Sym.__gt__ = lambda a, b: wrapb(bv(a) > bv(b))
Sym.__ge__ = lambda a, b: wrapb(bv(a) >= bv(b))
Sym.__eq__ = lambda a, b: wrapb(bv(a) == bv(b))
Sym.__ne__ = lambda a, b: wrapb(bv(a) != bv(b))
Sym.__hash__ = None
Sym.__bool__ = SymBool.__bool__
def _no_index(a):
    raise TypeError("symbolic value used as a Python index")


Sym.__index__ = _no_index


def _ite(c, a, b):
    if isinstance(c, (bool, int)) and not isinstance(c, Sym):
        return a if c else b
    c = bl(c) if isinstance(c, SymBool) else (bv(c) != 0)
    if isinstance(a, SymBool) or isinstance(b, SymBool) or (isinstance(a, bool) and isinstance(b, bool)):
        return wrapb(z3.If(c, bl(a), bl(b)))
    return wrap(z3.If(c, bv(a), bv(b)))


def _not(a):
    if isinstance(a, SymBool):
        return wrapb(z3.Not(a.t))
    if isinstance(a, Sym):
        return wrapb(a.t == 0)
    return not a


def _and(*xs):
    if all(not isinstance(x, (Sym, SymBool)) for x in xs):
        r = True
        for x in xs:
            r = r and x
        return r
    return wrapb(z3.And(*[bl(x) if isinstance(x, (SymBool, bool)) else bv(x) != 0 for x in xs]))


def _or(*xs):
    if all(not isinstance(x, (Sym, SymBool)) for x in xs):
        r = False
        for x in xs:
            r = r or x
        return r
    return wrapb(z3.Or(*[bl(x) if isinstance(x, (SymBool, bool)) else bv(x) != 0 for x in xs]))


def _min(*xs):
    if len(xs) == 1:
        xs = tuple(xs[0])
    if all(isinstance(x, int) and not isinstance(x, Sym) for x in xs):
        return builtins.min(xs)
    r = xs[0]
    for x in xs[1:]:
        r = _ite(x < r, x, r)
    return r


class SymSeq:
    """A module-level table; a symbolic index selects with an if-then-else chain over the entries."""
    def __init__(self, items):
        self.items = items

    def __getitem__(self, i):
        if isinstance(i, Sym):
            r = self.items[-1]
            for k in range(len(self.items) - 2, -1, -1):
                r = _ite(i == k, self.items[k], r)
            return r
        return self.items[i]

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


class Rewrite(ast.NodeTransformer):
    def visit_IfExp(self, n):
        self.generic_visit(n)
        return ast.Call(ast.Name("_ite", ast.Load()), [n.test, n.body, n.orelse], [])

    def visit_BoolOp(self, n):
        self.generic_visit(n)
        return ast.Call(ast.Name("_and" if isinstance(n.op, ast.And) else "_or", ast.Load()), n.values, [])

    def visit_UnaryOp(self, n):
        self.generic_visit(n)
        if isinstance(n.op, ast.Not):
            return ast.Call(ast.Name("_not", ast.Load()), [n.operand], [])
        return n

    def visit_If(self, n):
        raise NotImplementedError("statement-level if is not supported")

    def visit_While(self, n):
        raise NotImplementedError("while is not supported")


class Pads(dict):
    def __missing__(self, key):
        name, ph = key
        val = Sym(z3.ZeroExt(Sym.W - in_width[name], pad(name, ph)))
        self[key] = val
        return val

    def get(self, key, default=None):
        return self[key]


class State(dict):
    reads = set()

    def __missing__(self, key):
        w, ph, d = key
        State.reads.add(key)
        return Sym(z3.ZeroExt(Sym.W - width[w], st(w)))

    def get(self, key, default=None):
        return self[key]


class Values(dict):
    """v[(word, phase)]: record each write, then continue with the cut symbol c_(word, phase)."""
    def __init__(self):
        super().__init__()
        self.rec = {}

    def __setitem__(self, key, val):
        w, ph = key
        assert key not in self.rec, f"double write {key}"
        self.rec[key] = val.t if isinstance(val, Sym) else bv(val)
        c = Sym(z3.ZeroExt(Sym.W - width[w], cut(w, ph)))
        if isinstance(val, Sym):
            val.t = c.t
        super().__setitem__(key, c)

    def get(self, key, default=None):
        return super().get(key, default)


cut_def = {str(cut(w, ph)): (cut(w, ph), e) for (w, ph), e in ref_node.items()}


def cut_symbols(terms):
    return {str(v) for t in terms for v in z3_vars(t) if str(v).startswith("c|")}


def prove(lhs, rhs, timeout_ms, max_depth=4):
    """Prove lhs == rhs; if needed, assume the reference definitions c_k = ref(k) of the cut symbols involved
    (true under the interpretation of every cut symbol as its reference value), one level at a time."""
    eq = z3.simplify(lhs == rhs)
    if z3.is_true(eq):
        return "proved", 0
    assumed, frontier = set(), cut_symbols([eq])
    for depth in range(max_depth + 1):
        s = z3.SolverFor("QF_BV")
        s.set("timeout", timeout_ms)
        for name in assumed:
            c, e = cut_def[name]
            s.add(c == e)
        s.add(z3.Not(eq))
        r = s.check()
        if r == z3.unsat:
            return "proved", depth
        if r == z3.unknown:
            return "unknown", s.reason_unknown()
        new = {n for n in frontier if n in cut_def} - assumed
        if not new:
            return "refuted", str(s.model())[:400]
        assumed |= new
        frontier = cut_symbols([cut_def[n][1] for n in new])
    return "refuted_at_depth", max_depth


def check_program(path, timeout_ms=120000):
    src = open(path).read()
    tree = ast.parse(src)
    lits = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, int)]
    maxw = max([1] + list(width.values()) + list(in_width.values()) + [abs(x).bit_length() for x in lits])
    Sym.W = 2 * maxw + 8
    tree = ast.fix_missing_locations(Rewrite().visit(tree))
    ns = {"_ite": _ite, "_and": _and, "_or": _or, "_not": _not, "min": _min, "__name__": "recovered"}
    t0 = time.time()
    exec(compile(tree, path, "exec"), ns)
    for k, val in list(ns.items()):
        if isinstance(val, (list, tuple)) and not k.startswith("_"):
            ns[k] = SymSeq(val)
    vals, pads, state = Values(), Pads(), State()
    State.reads = set()

    def run():
        # the program creates its own `v = {}`; pass the recording dict instead
        g = dict(ns)
        g["__builtins__"] = builtins
        src_tx = ast.parse(src)
        fn = next(n for n in src_tx.body if isinstance(n, ast.FunctionDef) and n.name == "transaction")
        fn.body = [s for s in fn.body if not (isinstance(s, ast.Assign) and isinstance(s.value, ast.Dict)
                                               and any(isinstance(t, ast.Name) and t.id == "v" for t in s.targets))]
        fn.args.args.append(ast.arg("v"))
        mod = ast.fix_missing_locations(Rewrite().visit(ast.Module([fn], [])))
        exec(compile(mod, path, "exec"), g)
        return g["transaction"](pads, state, vals)
    outputs, new_state = run()
    t_exec = time.time() - t0
    res = collections.Counter()
    fails = []
    # nodes
    extra = [k for k in vals.rec if k not in ref_node]
    for k in extra:
        res["node_not_in_reference"] += 1; fails.append(("node_not_in_reference", k, None))
    t1 = time.time()
    for k, term in vals.rec.items():
        if k not in ref_node:
            continue
        w = width[k[0]]
        r, why = prove(term, z3.ZeroExt(Sym.W - w, ref_node[k]), timeout_ms)
        res[f"node_{r}"] += 1
        if r == "proved":
            res[f"node_depth_{why}"] += 1
        else:
            fails.append((f"node_{r}", k, why))
    # outputs
    for k, ref in ref_out.items():
        if k not in outputs:
            res["output_missing"] += 1; fails.append(("output_missing", k, None)); continue
        r, why = prove(bv(outputs[k]), z3.ZeroExt(Sym.W - ref.size(), ref), timeout_ms)
        res[f"output_{r}"] += 1
        if r != "proved":
            fails.append((f"output_{r}", k, why))
    for k in outputs:
        if k not in ref_out:
            res["output_not_in_reference"] += 1; fails.append(("output_not_in_reference", k, None))
    # loop-carried state
    for (w, ph, d) in new_state:
        ok = d == 1 and last_phase.get(w) == ph and (w, ph) in vals.rec
        if ok:
            r, why = prove(bv(new_state[(w, ph, d)]), z3.ZeroExt(Sym.W - width[w], cut(w, ph)), timeout_ms)
            ok = r == "proved"
        res["state_out_ok" if ok else "state_out_bad"] += 1
        if not ok:
            fails.append(("state_out_bad", (w, ph, d), None))
    for (w, ph, d) in State.reads:
        ok = d == 1 and last_phase.get(w) == ph
        res["state_in_ok" if ok else "state_in_bad"] += 1
        if not ok:
            fails.append(("state_in_bad", (w, ph, d), None))
    in_state = {(w, ph, d) for (w, ph, d) in State.reads}
    out_state = set(new_state)
    if in_state != out_state:
        res["state_interface_mismatch"] += 1
        fails.append(("state_interface_mismatch", sorted(in_state ^ out_state)[:5], None))
    return {"program": path, "width": Sym.W, "exec_s": round(t_exec, 1), "prove_s": round(time.time() - t1, 1),
            "writes": len(vals.rec), "reference_nodes": len(ref_node), "outputs": len(ref_out),
            "results": dict(res), "failures": [[a, list(b) if isinstance(b, tuple) else b, c] for a, b, c in fails[:20]],
            "closed": not fails}


report = {"case": case, "graph": graph_path, "period_events": P, "transaction_shift": shift, "programs": []}
for path in programs:
    r = check_program(path)
    report["programs"].append(r)
    print(json.dumps({k: r[k] for k in ("program", "writes", "outputs", "results", "closed", "exec_s", "prove_s")}), flush=True)
json.dump(report, open(out_path, "w"), indent=1)
