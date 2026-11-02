#!/usr/bin/env python3
"""T1.2 / E1.4 (protocol T12_NATIVE_FAMILIES_PROTOCOL.txt): F2 (GF(2) fitting + CEC under
training-constant bits) and F1 (Daikon-style word invariants) on the native graph, judged by the
certified node functions and confirmed on the variant trace.
usage: t12_native_families.py CASE_DIR GRAPH_JSON OUT_JSON   (run with the c3 venv python: z3)"""
import collections, json, sys
import z3

case_dir, graph_path, out_path = sys.argv[1:4]
G = json.load(open(graph_path))
P, origin = G["period"], G["origin"]


def load_trace(d):
    meta = json.load(open(f"{d}/native_trace.json"))
    pos = {b: i for i, b in enumerate(meta["observed"])}
    rows = [l.split() for l in open(f"{d}/native_trace.txt")]
    return meta, pos, rows


meta, pos, rows = load_trace(f"{case_dir}/trace")
vmeta, vpos, vrows = load_trace(f"{case_dir}/variant_trace")


def bit(rows_, pos_, b, t, post):
    r = rows_[t][1 if post else 0]
    c = r[len(r) - 1 - pos_[b]]
    return None if c not in "01" else int(c)


def phys(sym, k):
    kind, _, base = sym.partition(":")
    d = G["words"][base] if kind == "word" else G["inputs"][base]
    return int(d["bits"][k][1:])


def symbols(e):
    out, seen, st = {}, set(), [e]
    while st:
        x = st.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out[str(x)] = x
        st.extend(x.children())
    return out


def bit_support(e):
    out, seen, st = set(), set(), [e]
    while st:
        x = st.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_app_of(x, z3.Z3_OP_EXTRACT) and z3.is_const(x.arg(0)) and x.arg(0).decl().kind() == z3.Z3_OP_UNINTERPRETED:
            hi, lo = x.params(); out |= {(str(x.arg(0)), k) for k in range(lo, hi + 1)}; continue
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out |= {(str(x), k) for k in range(x.size())}; continue
        st.extend(x.children())
    return out


def specialise(e, phase, syms):
    subs = []
    for name, v in syms.items():
        kind, _, base = name.partition(":")
        known = (G["words"][base] if kind == "word" else G["inputs"][base])["certified_known_lsb_first"][phase]
        w = v.size()
        if "x" not in known:
            subs.append((v, z3.BitVecVal(int(known[::-1], 2), w)))
        elif known.strip("x"):
            m = z3.BitVecVal(int("".join("1" if c != "x" else "0" for c in known)[::-1], 2), w)
            val = z3.BitVecVal(int("".join(c if c != "x" else "0" for c in known)[::-1], 2), w)
            subs.append((v, (v & ~m) | val))
    return z3.simplify(z3.substitute(e, *subs)) if subs else z3.simplify(e)


def proved_equal(a, b, timeout=10000):
    s = z3.Solver(); s.set("timeout", timeout); s.add(a != b)
    r = s.check()
    return True if r == z3.unsat else (False if r == z3.sat else None)


win = meta["windows"].get("training") or next(iter(meta["windows"].values()))  # Index traces: one named profile
train = sorted(e for e in win if e >= origin)
var_events = list(range(origin, len(vrows) - 1))
stats = collections.defaultdict(collections.Counter)
accepted_smt2 = collections.defaultdict(list)
examples = collections.defaultdict(list)
for nd in G["nodes"]:
    if nd["width"] < 2:
        continue
    phase, W = nd["phase"], nd["name"]
    raw = z3.parse_smt2_string(nd["smt2"])[0].arg(1)
    syms = symbols(raw)
    cert = specialise(raw, phase, syms)
    tev = [e for e in train if (e - origin) % P == phase]
    vev = [e for e in var_events if (e - origin) % P == phase]
    if len(tev) < 4:
        continue
    wbits = [int(b[1:]) for b in G["words"][W]["bits"]]
    n = len(wbits)

    def sample(rows_, pos_, e):
        vals = {}
        for name, v in syms.items():
            x = 0
            for k in range(v.size()):
                b = bit(rows_, pos_, phys(name, k), e, False)
                if b is None:
                    return None
                x |= b << k
            vals[name] = x
        t = 0
        for k, b in enumerate(wbits):
            bb = bit(rows_, pos_, b, e, True)
            if bb is None:
                return None
            t |= bb << k
        return vals, t
    S = [s for s in (sample(rows, pos, e) for e in tev) if s]
    V = [s for s in (sample(vrows, vpos, e) for e in vev) if s]
    if len(S) < 4:
        continue
    stats["F2"]["targets"] += 1; stats["F1"]["targets"] += 1

    def judge(fam, cand_expr, predict, desc):
        stats[fam]["accepted"] += 1
        sv = z3.Solver(); sv.add(z3.BitVec("__out", n) == cand_expr)
        accepted_smt2[fam].append({"name": W, "phase": phase, "smt2": sv.to_smt2()})
        eq = proved_equal(cand_expr, cert)
        if eq is None:
            stats[fam]["undecided"] += 1; return
        if eq:
            return
        stats[fam]["wrong"] += 1
        bad = next((t for vals, t in V if predict(vals) != t), None)
        if bad is not None:
            stats[fam]["confirmed"] += 1
            if len(examples[fam]) < 5:
                examples[fam].append({"word": W, "phase": phase, "candidate": desc})
    # ---- F2: GF(2) fit per bit + CEC under training-constant support bits ----------------
    cand_bits, ok = [], True
    for i in range(n):
        bexp = z3.simplify(z3.Extract(i, i, raw))
        sup = sorted(bit_support(bexp))
        col = {sk: [(vals[sk[0]] >> sk[1]) & 1 for vals, _ in S] for sk in sup}
        const = {sk: c[0] for sk, c in col.items() if len(set(c)) == 1}
        free = [sk for sk in sup if sk not in const]
        y = [(t >> i) & 1 for _, t in S]
        # Gaussian elimination over GF(2): unknowns = free columns + constant term
        m = len(free) + 1
        rows_eq = []
        for j in range(len(S)):
            r_ = 0
            for q, sk in enumerate(free):
                r_ |= col[sk][j] << q
            r_ |= 1 << len(free)
            rows_eq.append((r_, y[j]))
        piv, sol, consistent = {}, [0] * m, True
        rr = []
        for r_, b_ in rows_eq:
            for q in sorted(piv, reverse=False):
                if (r_ >> q) & 1:
                    r_ ^= piv[q][0]; b_ ^= piv[q][1]
            if r_ == 0:
                if b_:
                    consistent = False; break
                continue
            q = (r_ & -r_).bit_length() - 1
            for k2 in list(piv):
                if (piv[k2][0] >> q) & 1:
                    piv[k2] = (piv[k2][0] ^ r_, piv[k2][1] ^ b_)
            piv[q] = (r_, b_)
        if not consistent:
            ok = False; break
        for q, (r_, b_) in piv.items():
            sol[q] = b_  # free variables 0 (particular solution)
        terms = [z3.Extract(sk[1], sk[1], syms[sk[0]]) for q, sk in enumerate(free) if sol[q]]
        cb = z3.BitVecVal(sol[len(free)], 1)
        for tm in terms:
            cb = cb ^ tm
        # CEC under training-constant support bits
        fixed = bexp
        subs = []
        for (nm, k), c in const.items():
            pass
        fixed_e = bexp
        bymask = collections.defaultdict(lambda: [0, 0])
        for (nm, k), c in const.items():
            bymask[nm][0] |= 1 << k; bymask[nm][1] |= c << k
        for nm, (mk, vl) in bymask.items():
            v = syms[nm]; w = v.size()
            subs.append((v, (v & z3.BitVecVal(((1 << w) - 1) ^ mk, w)) | z3.BitVecVal(vl, w)))
        if subs:
            fixed_e = z3.substitute(bexp, *subs)
        if proved_equal(cb, fixed_e) is not True:
            ok = False; break
        cand_bits.append((cb, [sk for q, sk in enumerate(free) if sol[q]], sol[len(free)]))
    if ok:
        cexpr = z3.Concat(*reversed([c for c, _, _ in cand_bits])) if n > 1 else cand_bits[0][0]

        def pred_f2(vals):
            out = 0
            for i, (_, sks, c0) in enumerate(cand_bits):
                b_ = c0
                for nm, k in sks:
                    b_ ^= (vals[nm] >> k) & 1
                out |= b_ << i
            return out
        judge("F2", cexpr, pred_f2, "GF(2) fit, CEC under training-constant bits")
    # ---- F1: Daikon-style word invariants -------------------------------------------------
    M = (1 << n) - 1
    words_same = sorted(nm for nm, v in syms.items() if v.size() == n)
    found = None
    ts = [t for _, t in S]
    if len(set(ts)) == 1:
        found = (z3.BitVecVal(ts[0], n), lambda vals, c=ts[0]: c, f"T == {ts[0]}")
    for a in words_same:
        if found:
            break
        if all(vals[a] == t for vals, t in S):
            found = (syms[a], lambda vals, a=a: vals[a], f"T == {a}")
    for i_, a in enumerate(words_same):
        for b in words_same[i_:]:
            if found:
                break
            for op, f, zf in (("^", lambda x, y: x ^ y, lambda x, y: x ^ y), ("+", lambda x, y: (x + y) & M, lambda x, y: x + y),
                              ("-", lambda x, y: (x - y) & M, lambda x, y: x - y)):
                if all(f(vals[a], vals[b]) == t for vals, t in S):
                    found = (zf(syms[a], syms[b]), lambda vals, a=a, b=b, f=f: f(vals[a], vals[b]), f"T == {a} {op} {b}"); break
    if found:
        judge("F1", found[0], found[1], found[2])
res = {"case": case_dir.rstrip("/").split("/")[-1], "families": {k: dict(v) for k, v in stats.items()},
       "confirmed_examples": dict(examples)}
json.dump(res, open(out_path, "w"), indent=1)
json.dump(dict(accepted_smt2), open(out_path.replace(".json", ".accepted.json"), "w"))
print(json.dumps(res))
