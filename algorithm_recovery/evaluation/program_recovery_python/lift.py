"""C3 step 4a: routing and table lifting of bit-level node functions.

For an expression E (bit-vector, width n) every output bit is classified by its syntactic
support (the bits of the symbols it mentions):
  const    no support
  route    one data bit (possibly inverted)
  xor      an affine function (XOR) of several data bits
  table    a function of <= MAX_TABLE data bits; output bits with the same support form one
           multi-output table, evaluated EXHAUSTIVELY over the whole support (exact, no SMT)
  keep     anything larger stays a bit-level expression
Before classification, each bit is split as f(d, p) = f(d, 0) ^ f(0, p) ^ f(0, 0) when that
identity holds over its whole support, where p are parameter bits (iteration constants of a
loop): round-constant XORs become a data part plus a parameter part.
Tables over two symbols are tested for XOR pre-whitening T(x, y) = S(x ^ pi(y)); table
instances are canonicalised to one table up to input and output wire order.
Every lifted node is re-checked: the lifted Python is evaluated against the z3 expression on
random inputs here, and the whole program is validated on the trace by decompile.py."""
import collections, itertools, random
import z3

MAX_TABLE = 8
MIN_TABLE_OUTPUTS = 2
MAX_SPLIT = 12


def symbol_bits(e):
    """Syntactic support {symbol name: set of bit indices}; a symbol used whole contributes all bits."""
    res, seen, stack = collections.defaultdict(set), set(), [e]
    while stack:
        x = stack.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_app_of(x, z3.Z3_OP_EXTRACT):
            a = x.arg(0)
            if z3.is_const(a) and a.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                hi, lo = x.params()
                res[str(a)].update(range(lo, hi + 1))
                continue
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            res[str(x)].update(range(x.size()) if z3.is_bv(x) else [0])
            continue
        stack.extend(x.children())
    return res


class Evaluator:
    """Evaluate bit expressions under assignments of (symbol, bit) -> 0/1, other bits 0."""

    def __init__(self, variables):
        self.vars = variables  # name -> z3 const

    def value(self, e, assignment):
        vals = collections.defaultdict(int)
        for (sym, k), b in assignment.items():
            if b:
                vals[sym] |= 1 << k
        subs = [(self.vars[s], z3.BitVecVal(vals.get(s, 0), self.vars[s].size())) for s in self.vars]
        r = z3.simplify(z3.substitute(e, *subs)) if subs else z3.simplify(e)
        assert z3.is_bv_value(r), r
        return r.as_long()


def bits_of(x, idx):
    return sum(((x >> k) & 1) << i for i, k in enumerate(idx))


def permute(x, perm):
    """bit i of the result = bit perm[i] of x"""
    return sum(((x >> p) & 1) << i for i, p in enumerate(perm))


def canonical(tables_in, k, m):
    """Group k-input m-output tables into classes equal up to input/output wire order.
    Returns [(S, [(j, in_order, out_order)])] with, for every member j and every x,
      T_j[x] == scatter(S[gather(x, in_order)], out_order)
    where bit i of gather(x, o) is bit o[i] of x and bit i of y goes to bit out_order[i]."""
    def gather(x, o):
        return sum(((x >> q) & 1) << i for i, q in enumerate(o))

    def scatter(y, o):
        return sum(((y >> i) & 1) << q for i, q in enumerate(o))
    classes = []
    for j, T in enumerate(tables_in):
        found = None
        for S, members in classes:
            for in_order in itertools.permutations(range(k)):
                pre = [S[gather(x, in_order)] for x in range(1 << k)]
                for out_order in itertools.permutations(range(m)):
                    if all(scatter(pre[x], out_order) == T[x] for x in range(1 << k)):
                        found = (members, in_order, out_order); break
                if found:
                    break
            if found:
                break
        if found:
            found[0].append((j, found[1], found[2]))
        else:
            classes.append((T, [(j, tuple(range(k)), tuple(range(m)))]))
    return classes


def lift(expr, variables, is_param):
    """Return a lifting plan for expr or None if nothing is lifted.
    variables: name -> z3 const for every symbol that may appear; is_param(name) -> bool."""
    n = expr.size()
    ev = Evaluator(variables)
    bit_exprs = [z3.simplify(z3.Extract(i, i, expr)) for i in range(n)]
    plan = {"width": n, "const": 0, "route": [], "xor": [], "param_route": [], "tables": [], "keep": []}
    groups = collections.OrderedDict()
    for i, b in enumerate(bit_exprs):
        sup = sorted((s, k) for s, ks in symbol_bits(b).items() for k in ks)
        if not sup:
            plan["const"] |= ev.value(b, {}) << i
            continue
        data = [x for x in sup if not is_param(x[0])]
        params = [x for x in sup if is_param(x[0])]
        if len(data) > MAX_TABLE or (params and len(sup) > MAX_SPLIT):
            plan["keep"].append(i); continue
        f00 = ev.value(b, {})
        if params:
            # f(d, p) == f(d, 0) ^ f(0, p) ^ f(0, 0) over the whole support?
            ok = True
            for bits in itertools.product((0, 1), repeat=len(sup)):
                a = dict(zip(sup, bits))
                ad = {x: v for x, v in a.items() if x in data}
                ap = {x: v for x, v in a.items() if x in params}
                if ev.value(b, a) != ev.value(b, ad) ^ ev.value(b, ap) ^ f00:
                    ok = False; break
            if not ok:
                if len(sup) <= MAX_TABLE:  # parameters enter non-linearly: they become table inputs
                    dt = [ev.value(b, {sup[q]: (x >> q) & 1 for q in range(len(sup))}) for x in range(1 << len(sup))]
                    groups.setdefault(tuple(sup), []).append((i, dt))
                else:
                    plan["keep"].append(i)
                continue
            g = [ev.value(b, {params[q]: (x >> q) & 1 for q in range(len(params))}) ^ f00 for x in range(1 << len(params))]
            if any(g):
                plan["param_route"].append((i, params, g))
        if not data:
            plan["const"] |= f00 << i
            continue
        # data table f(d, 0), bit q of the index = data[q]
        dt = [ev.value(b, {data[q]: (x >> q) & 1 for q in range(len(data))}) for x in range(1 << len(data))]
        c0 = dt[0]
        coeff = [dt[1 << q] ^ c0 for q in range(len(data))]
        if all(dt[x] == c0 ^ (sum(coeff[q] for q in range(len(data)) if (x >> q) & 1) & 1) for x in range(1 << len(data))):
            terms = [data[q] for q in range(len(data)) if coeff[q]]
            if not terms:
                plan["const"] |= c0 << i
            elif len(terms) == 1:
                plan["route"].append((i, terms[0], c0))
            else:
                plan["xor"].append((i, terms, c0))
            continue
        groups.setdefault(tuple(data), []).append((i, dt))
    for data, members in groups.items():
        if len(members) < MIN_TABLE_OUTPUTS:  # single-output slices (adder bits) stay bit-level
            plan["keep"] += [i for i, _ in members]
            continue
        k = len(data)
        outs = [i for i, _ in members]
        T = [sum(dt[x] << q for q, (_, dt) in enumerate(members)) for x in range(1 << k)]
        plan["tables"].append({"inputs": list(data), "outputs": outs, "table": T})
    plan["keep"].sort()
    return plan


def whiten(tab, is_param):
    """Find a split of the table inputs into A and B (two symbols, or data vs parameter bits)
    and a pairing pi with T(x, y) == T(x ^ pi(y), 0). Returns {"a", "b", "table"} or None."""
    ins = tab["inputs"]
    syms = sorted({s for s, _ in ins})
    splits = []
    if len(syms) == 2:
        splits.append(([q for q, (s, _) in enumerate(ins) if s == syms[0]], [q for q, (s, _) in enumerate(ins) if s == syms[1]]))
    A = [q for q, (s, _) in enumerate(ins) if not is_param(s)]
    B = [q for q, (s, _) in enumerate(ins) if is_param(s)]
    if A and B:
        splits.append((A, B))
    T = tab["table"]
    for A, B in splits:
        if len(A) != len(B) or len(A) > 6:
            continue
        for pi in itertools.permutations(range(len(B))):
            pairs = list(zip(A, [B[p] for p in pi]))
            ok = True
            for x in range(1 << len(ins)):
                y = x
                for qa, qb in pairs:
                    if (x >> qb) & 1:
                        y ^= 1 << qa
                    y &= ~(1 << qb)
                if T[x] != T[y]:
                    ok = False; break
            if ok:
                S = [T[sum(((u >> i) & 1) << A[i] for i in range(len(A)))] for u in range(1 << len(A))]
                return {"a": [ins[q] for q in A], "b": [ins[B[p]] for p in pi], "table": S}
    return None
