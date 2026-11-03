"""Shared helpers for the T1.2 method-family experiments (copied from t12_native_families.py, which stays frozen):
trace access, symbol -> physical bits (words, inputs, RAM read latches), certified specialisation, and the
targets (word, phase) with their training samples (CASE_DIR/trace) and fresh samples (CASE_DIR/variant_trace)."""
import json
import z3


def load_trace(d):
    meta = json.load(open(f"{d}/native_trace.json"))
    pos = {b: i for i, b in enumerate(meta["observed"])}
    rows = [line.split() for line in open(f"{d}/native_trace.txt")]
    return meta, pos, rows


def bit(rows, pos, b, t, post):
    r = rows[t][1 if post else 0]
    c = r[len(r) - 1 - pos[b]]
    return None if c not in "01" else int(c)


def symbols(e):
    found, seen, stack = {}, set(), [e]
    while stack:
        x = stack.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            found[str(x)] = x
        stack.extend(x.children())
    return found


def proved_equal(a, b, timeout=10000):
    s = z3.Solver(); s.set("timeout", timeout); s.add(a != b)
    r = s.check()
    return True if r == z3.unsat else (False if r == z3.sat else None)


class Graph:
    def __init__(self, graph_path):
        self.G = json.load(open(graph_path))
        self.P, self.origin = self.G["period"], self.G["origin"]

    def phys(self, sym, k):
        kind, _, base = sym.partition(":")
        if kind == "ram":
            return int(self.G["memories"][base]["read_bits"][k][1:])
        d = self.G["words"][base] if kind == "word" else self.G["inputs"][base]
        return int(d["bits"][k][1:])

    def specialise(self, e, phase, syms):
        subs = []
        for name, v in syms.items():
            kind, _, base = name.partition(":")
            if kind == "ram":
                continue
            known = (self.G["words"][base] if kind == "word" else self.G["inputs"][base])["certified_known_lsb_first"][phase]
            w = v.size()
            if "x" not in known:
                subs.append((v, z3.BitVecVal(int(known[::-1], 2), w)))
            elif known.strip("x"):
                m = z3.BitVecVal(int("".join("1" if c != "x" else "0" for c in known)[::-1], 2), w)
                val = z3.BitVecVal(int("".join(c if c != "x" else "0" for c in known)[::-1], 2), w)
                subs.append((v, (v & ~m) | val))
        return z3.simplify(z3.substitute(e, *subs)) if subs else z3.simplify(e)

    def targets(self, case_dir, min_samples=4):
        """Yield dicts with word, phase, n, raw, cert, syms, S (training) and V (fresh) samples [(vals, next)]."""
        meta, pos, rows = load_trace(f"{case_dir}/trace")
        _, vpos, vrows = load_trace(f"{case_dir}/variant_trace")
        win = meta["windows"].get("training") or next(iter(meta["windows"].values()))
        train = sorted(e for e in win if e >= self.origin)
        var_events = list(range(self.origin, len(vrows) - 1))
        for nd in self.G["nodes"]:
            if nd["width"] < 2:
                continue
            phase, W = nd["phase"], nd["name"]
            raw = z3.parse_smt2_string(nd["smt2"])[0].arg(1)
            syms = symbols(raw)
            tev = [e for e in train if (e - self.origin) % self.P == phase]
            if len(tev) < min_samples:
                continue
            wbits = [int(b[1:]) for b in self.G["words"][W]["bits"]]

            def sample(rows_, pos_, e):
                vals = {}
                for name, v in syms.items():
                    x = 0
                    for k in range(v.size()):
                        b = bit(rows_, pos_, self.phys(name, k), e, False)
                        if b is None:
                            return None
                        x |= b << k
                    vals[name] = x
                t = cur = 0
                for k, b_ in enumerate(wbits):
                    bb, bc = bit(rows_, pos_, b_, e, True), bit(rows_, pos_, b_, e, False)
                    if bb is None or bc is None:
                        return None
                    t |= bb << k; cur |= bc << k
                return vals, t, cur
            S3 = [s for s in (sample(rows, pos, e) for e in tev) if s]
            if len(S3) < min_samples:
                continue
            vev = [e for e in var_events if (e - self.origin) % self.P == phase]
            V3 = [s for s in (sample(vrows, vpos, e) for e in vev) if s]
            yield {"word": W, "phase": phase, "n": len(wbits), "raw": raw, "syms": syms,
                   "cert": self.specialise(raw, phase, syms),
                   "S": [(v, t) for v, t, _ in S3], "V": [(v, t) for v, t, _ in V3],
                   "S_cur": [c for _, _, c in S3], "V_cur": [c for _, _, c in V3]}


def evaluate(expr, syms, vals):
    e = z3.substitute(expr, *[(syms[nm], z3.BitVecVal(v, syms[nm].size())) for nm, v in vals.items()]) if vals else expr
    return z3.simplify(e).as_long()
