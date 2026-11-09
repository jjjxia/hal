"""C3 step 4b: word-level arithmetic lifting with inferred bit orders.

Netlist words carry no bit order. For an output chain whose bit supports are strictly nested
(S_0 < S_1 < ... as in adders and multipliers), the order of the outputs is the chain order and
the bits new at level i are bit i of the operands. Bits new at one level are assigned to
operands by second differences z(u+v) - z(u) - z(v) + z(0): two bits of the same operand do not
interact in a bilinear function. A polynomial of degree <= 2 in the operands is fitted from
evaluations at unit points, tested on random points, and must then be PROVEN equal to the
node function (z3; ABC cec as a second engine). Unproven candidates are not used."""
import collections, random
import z3

import lift


def compile_expr(expr, printer_cls):
    """Python function f(values: dict sym -> int) -> int for a z3 expression."""
    pr = printer_cls(lambda sym: f"X[{sym!r}]")
    code = pr.emit(expr)
    src = "def f(X):\n" + "\n".join(pr.lines) + f"\n    return {code}\n"
    ns = {"sgn": lambda x, w: x - (1 << w) if (x >> (w - 1)) & 1 else x}
    ns["sra"] = lambda x, s, w: (ns["sgn"](x, w) >> min(s, w)) & ((1 << w) - 1)
    exec(src, ns)
    return ns["f"]


def chains(expr):
    """Partition the output bits into chains of strictly nested supports.
    Returns (list of chains [(bit, support)], leftover bits)."""
    n = expr.size()
    sup = {}
    for i in range(n):
        sb = lift.symbol_bits(z3.simplify(z3.Extract(i, i, expr)))
        sup[i] = frozenset((s, k) for s, ks in sb.items() for k in ks)
    # components of the support-overlap graph
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    owner = {}
    for i in range(n):
        if len(sup[i]) <= 1:
            continue
        for b in sup[i]:
            if b in owner:
                parent[find(i)] = find(owner[b])
            else:
                owner[b] = i
    comps = collections.defaultdict(list)
    for i in range(n):
        if len(sup[i]) > 1:  # single-bit supports are routes, lifted elsewhere
            comps[find(i)].append(i)
    result, leftover = [], [i for i in range(n) if len(sup[i]) <= 1]
    for members in comps.values():
        members.sort(key=lambda i: (len(sup[i]), i))
        # split a component into chains: each bit extends the unique chain whose last support it contains
        cs, ok = [], True
        for i in members:
            same = [c for c in cs if sup[c[-1]] == sup[i]]   # sign/carry extension: no new bits
            fits = same or [c for c in cs if sup[c[-1]] < sup[i]]
            if len(fits) > 1:
                ok = False; break
            if fits:
                fits[0].append(i)
            else:
                cs.append([i])
        if ok and all(len(c) >= 4 for c in cs):
            result += [[(i, sup[i]) for i in c] for c in cs]
        else:
            leftover += members
    return result, sorted(leftover)


def fit_chain(f, chain, width_of, rng, mask_bits):
    """Infer operands and a degree-2 polynomial for one chain. Returns a candidate or None."""
    levels, prev = [], frozenset()
    for i, s in chain:
        new = sorted(s - prev)
        levels.append(new); prev = s
    r = len(levels[0])
    if r < 1 or r > 3 or any(len(l) > r for l in levels):
        return None
    outs = [i for i, _ in chain]
    m = len(outs)
    M = (1 << m) - 1

    def z_of(assign):
        vals = collections.defaultdict(int)
        for (s, k), b in assign.items():
            if b:
                vals[s] |= 1 << k
        y = f(vals)
        return sum(((y >> o) & 1) << q for q, o in enumerate(outs))

    def inter(u, v):
        return (z_of({u: 1, v: 1}) - z_of({u: 1}) - z_of({v: 1}) + z_of({})) & M
    base = levels[0]
    # pattern of operand g: which level-0 bits its bits interact with (never its own operand)
    P = [tuple(False if h == g else bool(inter(base[g], base[h])) for h in range(r)) for g in range(r)]
    groups = [[u] for u in base]
    for lvl in levels[1:]:
        used = set()
        for v in lvl:
            sv = tuple(bool(inter(v, w)) for w in base)
            cand = [g for g in range(r) if g not in used and P[g] == sv]
            if not cand:
                return None
            used.add(cand[0]); groups[cand[0]].append(v)
    # operands A_g = sum 2^i bit(groups[g][i])
    def point(vals_by_group):
        a = {}
        for g, val in enumerate(vals_by_group):
            for i, b in enumerate(groups[g]):
                a[b] = (val >> i) & 1
        return z_of(a)
    zero = point([0] * r)
    c = [(point([1 if h == g else 0 for h in range(r)]) - zero) & M for g in range(r)]
    d = {}
    for g in range(r):
        for h in range(g + 1, r):
            d[(g, h)] = (point([1 if q in (g, h) else 0 for q in range(r)]) - zero - c[g] - c[h]) & M
    tests = [[rng.getrandbits(len(groups[g])) for g in range(r)] for _ in range(64)]
    observed = [point(A) for A in tests]
    for signed in __import__("itertools").product((False, True), repeat=r):
        def val(A, g):
            w = len(groups[g])
            return A[g] - (1 << w) * ((A[g] >> (w - 1)) & 1) if signed[g] and w > 1 else A[g]
        predict = lambda A: (zero + sum(c[g] * val(A, g) for g in range(r)) + sum(dv * val(A, g) * val(A, h) for (g, h), dv in d.items())) & M
        if all(predict(A) == y for A, y in zip(tests, observed)):
            return {"outputs": outs, "operands": groups, "const": zero, "linear": c, "bilinear": d, "width": m,
                    "signed": [bool(x) and len(groups[g]) > 1 for g, x in enumerate(signed)],
                    "order_ambiguous": any(not l for l in levels)}  # equal supports: those outputs may be permuted
    return None


def candidate_expr(cand, variables):
    """z3 expression of the candidate in the node's output bit positions (other bits 0)."""
    m = cand["width"]
    ops = []
    for g in cand["operands"]:
        bits_ = [z3.BitVecVal(0, 1) if b is None else z3.Extract(b[1], b[1], variables[b[0]]) for b in g]
        word = z3.Concat(*reversed(bits_)) if len(bits_) > 1 else bits_[0]
        signed = cand.get("signed", [False] * len(cand["operands"]))[len(ops)]
        ext = m - len(bits_)
        ops.append(word if ext == 0 else (z3.SignExt(ext, word) if signed else z3.ZeroExt(ext, word)) if ext > 0 else z3.Extract(m - 1, 0, word))
    z = z3.BitVecVal(cand["const"], m)
    for g, cg in enumerate(cand["linear"]):
        if cg:
            z = z + z3.BitVecVal(cg, m) * ops[g]
    for (g, h), dv in cand["bilinear"].items():
        if dv:
            z = z + z3.BitVecVal(dv, m) * ops[g] * ops[h]
    bw = cand.get("bitwise")
    if bw:
        words_ = []
        for role in bw["roles"]:
            bits_ = [z3.Extract(k, k, variables[s_]) for s_, k in role]
            words_.append(z3.Concat(*reversed(bits_)) if len(bits_) > 1 else bits_[0])
        F = z3.BitVecVal(0, m)
        for x, v in enumerate(bw["table"]):
            if v:
                term = z3.BitVecVal((1 << m) - 1, m)
                for r, w in enumerate(words_):
                    term = term & (w if (x >> r) & 1 else ~w)
                F = F | term
        z = z + F
    return z


def prove(expr, cand, variables, timeout_ms=300000):
    z = candidate_expr(cand, variables)
    s = z3.Solver(); s.set("timeout", timeout_ms)
    terms = [z3.Extract(o, o, expr) != z3.Extract(q, q, z) for q, o in enumerate(cand["outputs"])]
    s.add(z3.Or(*terms))
    r = s.check()
    return str(r)


def fit_known(f, n_out, operands, rng, outputs=None, samples=64):
    """Operands with KNOWN bit order (lists of (symbol, bit), LSB first), output order unknown.
    Identify z = P(A) mod 2^m, P of degree <= 2, bit by bit from the LSB: at position k choose the
    k-th coefficient bits and the output bit whose values on random samples match. Returns a
    candidate (same shape as fit_chain) or None."""
    import itertools
    r = len(operands)
    if not 1 <= r <= 3:
        return None
    outputs = list(range(n_out)) if outputs is None else list(outputs)
    m = len(outputs)
    pairs = [(g, h) for g in range(r) for h in range(g + 1, r)]
    tests = [[rng.getrandbits(len(op)) for op in operands] for _ in range(samples)]

    # positions that are None are bits the function does not read (certified constants): they
    # are 0 in every test and the fitted constant absorbs their value
    present = [sum(1 << i for i, b in enumerate(op) if b is not None) for op in operands]
    tests = [[a & present[g] for g, a in enumerate(A)] for A in tests]

    def evaluate(A):
        vals = collections.defaultdict(int)
        for op, a in zip(operands, A):
            for i, b in enumerate(op):
                if b is not None and (a >> i) & 1:
                    vals[b[0]] |= 1 << b[1]
        return f(vals)
    ys = [evaluate(A) for A in tests]
    col = {o: tuple((y >> o) & 1 for y in ys) for o in outputs}
    for signed in itertools.product((False, True), repeat=r):
        val = [[a - (1 << len(op)) * ((a >> (len(op) - 1)) & 1) if signed[g] and len(op) > 1 else a
                for g, (a, op) in enumerate(zip(A, operands))] for A in tests]
        const, lin, bil = 0, [0] * r, {p: 0 for p in pairs}
        order, free = [], set(outputs)
        ok, ambiguous = True, False
        # coefficient bit j of operand g first matters at output bit j + t_g (t_g: lowest bit it
        # really has), so it is decided there; likewise j + t_g + t_h for a product of two operands
        t = [min([i for i, b in enumerate(op) if b is not None], default=m) for op in operands]
        for k in range(m):
            match = None
            for bits in itertools.product((0, 1), repeat=1 + r + len(pairs)):
                c_ = const | (bits[0] << k)
                l_ = [lin[g] | ((bits[1 + g] << (k - t[g])) if k >= t[g] else 0) for g in range(r)]
                b_ = {p: bil[p] | ((bits[1 + r + q] << (k - t[p[0]] - t[p[1]])) if k >= t[p[0]] + t[p[1]] else 0)
                      for q, p in enumerate(pairs)}
                pred = tuple(((c_ + sum(l_[g] * V[g] for g in range(r)) + sum(b_[(g, h)] * V[g] * V[h] for g, h in pairs)) >> k) & 1 for V in val)
                hits = sorted(o for o in free if col[o] == pred)
                if hits:  # equal columns (e.g. sign extension) are interchangeable; the proof decides
                    match = (hits[0], c_, l_, b_); ambiguous |= len(hits) > 1; break
            if match is None:
                ok = False; break
            o, const, lin, bil = match
            order.append(o); free.discard(o)
        if ok:
            groups = [list(op) for op in operands]
            return {"outputs": order, "operands": groups, "const": const, "linear": lin, "bilinear": bil, "width": m,
                    "signed": [bool(x) and len(groups[g]) > 1 for g, x in enumerate(signed)], "method": "known_input_order",
                    "order_ambiguous": ambiguous}
    return None


BITWISE_NAMES = {}


def _name_tables():
    import itertools
    f = {"ch": lambda b, c, d: (b & c) | ((1 - b) & d), "parity": lambda b, c, d: b ^ c ^ d,
         "maj": lambda b, c, d: (b & c) | (b & d) | (c & d)}
    for nm, fn in f.items():
        BITWISE_NAMES[tuple(fn(x & 1, (x >> 1) & 1, (x >> 2) & 1) for x in range(8))] = nm


_name_tables()


def fit_chain_arx(f, chain, rng, max_linear=6, why=None):
    """Chain whose levels mix linear bits with ONE same-level interacting group of 2..3 bits
    (a bitwise function layer): z = c0 + sum_g c_g L_g + F (mod 2^m), F_i = T(group bits at level i)
    with the same table T at every level (roles may be permuted per level). None if not this shape."""
    import itertools
    levels, prev = [], frozenset()
    for i, s_ in chain:
        levels.append(sorted(s_ - prev)); prev = s_
    outs = [i for i, _ in chain]
    m = len(outs)
    M = (1 << m) - 1
    if any(not l for l in levels) or len(levels[0]) > max_linear + 3:
        return None

    def z_of(assign):
        vals = collections.defaultdict(int)
        for (s, k), b in assign.items():
            if b:
                vals[s] |= 1 << k
        y = f(vals)
        return sum(((y >> o) & 1) << q for q, o in enumerate(outs))
    z0 = z_of({})

    def inter(u, w):
        return (z_of({u: 1, w: 1}) - z_of({u: 1}) - z_of({w: 1}) + z0) & M
    lin_ops, roles, table = None, None, None
    for i, lvl in enumerate(levels):
        # same-level interaction graph: one nonlinear group, the rest linear
        nl = sorted({u for u in lvl for w in lvl if u < w and inter(u, w)} | {w for u in lvl for w in lvl if u < w and inter(u, w)})
        lin = [u for u in lvl if u not in nl]
        if not nl and table is not None and roles is not None:
            # interactions vanish mod 2^m (top bits). If the table is affine mod 2 there, every
            # assignment of this level's bits gives the same function: assign deterministically.
            k = len(roles)
            t0 = table[0] & 1
            affine2 = all((table[x] & 1) == t0 ^ (sum(((table[1 << r] & 1) ^ t0) for r in range(k) if (x >> r) & 1) & 1) for x in range(1 << k))
            if affine2 and len(lvl) == k + len(lin_ops):
                ordered = sorted(lvl)
                for r in range(k):
                    roles[r].append(ordered[r])
                for g, u in enumerate(ordered[k:]):
                    lin_ops[g].append(u)
                continue
        if not 2 <= len(nl) <= 3:
            why is not None and why.append(("group size", i, len(nl), len(lin)))
            return None
        k = len(nl)
        # table of the group at this level, as differences scaled by 2^i
        diff = {}
        for x in range(1 << k):
            a = {nl[q]: (x >> q) & 1 for q in range(k)}
            diff[x] = (z_of(a) - z0) & M
        found = None
        for perm in itertools.permutations(range(k)):
            # role r of the table is bit nl[perm[r]]
            def idx(x):
                return sum(((x >> perm[r]) & 1) << r for r in range(k))
            T = [None] * (1 << k)
            for x in range(1 << k):
                T[idx(x)] = diff[x]
            if table is None:
                # T(p) - T(0) = d / 2^i in {-1, 0, 1}; T(0) = 1 when some difference is -1
                unit = 1 << i
                ok, t = True, []
                for v in T:
                    if v == 0:
                        t.append(0)
                    elif v == unit & M:
                        t.append(1)
                    elif v == (-unit) & M:
                        t.append(-1)
                    else:
                        ok = False; break
                if not ok:
                    continue
                base = 1 if -1 in t else 0
                cand_table = [base + d for d in t]
                found = (perm, cand_table); break
            else:
                unit = 1 << i
                if all(((table[idx_] - table[0]) * unit - T[idx_]) & M == 0 for idx_ in range(1 << k)):
                    found = (perm, table); break
        if found is None:
            why is not None and why.append(("table mismatch", i, [hex(diff[x]) for x in range(1 << k)]))
            return None
        perm, T = found
        if table is None:
            table = T
            roles = [[] for _ in range(k)]
            lin_ops = [[] for _ in range(len(lin))]
        if len(lin) > len(lin_ops):
            why is not None and why.append(("more linear bits", i, len(lin)))
            return None
        for r in range(k):
            roles[r].append(nl[perm[r]])
        for g, u in enumerate(sorted(lin)):
            lin_ops[g].append(u)
    # name the bitwise layer up to a permutation of its roles (Ch's selector first, etc.)
    k_ = len(roles)
    for perm in itertools.permutations(range(k_)):
        # new role r is old role perm[r]
        Tn = [table[sum(((x >> r) & 1) << perm[r] for r in range(k_))] for x in range(1 << k_)]
        if tuple(Tn) in BITWISE_NAMES:
            table, roles = Tn, [roles[perm[r]] for r in range(k_)]
            break
    # linear coefficients from unit points of each linear operand's level-0 bit
    c = [(z_of({op[0]: 1}) - z0) & M for op in lin_ops]
    F0 = sum(table[0] << i for i in range(m)) & M
    const = (z0 - F0) & M

    def predict(assign):
        tot = const
        for g, op in enumerate(lin_ops):
            tot += c[g] * sum(assign.get(b, 0) << i for i, b in enumerate(op))
        for i in range(m):
            x = sum(assign.get(roles[r][i], 0) << r for r in range(len(roles)) if i < len(roles[r]))
            tot += table[x] << i
        return tot & M
    for _ in range(64):
        assign = {b: rng.getrandbits(1) for lvl in levels for b in lvl}
        if z_of(assign) != predict(assign):
            why is not None and why.append(("random test", hex(z_of(assign)), hex(predict(assign))))
            return None
    return {"outputs": outs, "operands": lin_ops, "const": const, "linear": c, "bilinear": {}, "width": m,
            "signed": [False] * len(lin_ops), "bitwise": {"table": table, "roles": roles, "name": BITWISE_NAMES.get(tuple(table))},
            "method": "arx_chain", "order_ambiguous": False}
