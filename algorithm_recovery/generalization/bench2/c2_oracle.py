#!/usr/bin/env python3
"""E2.1 structural oracle and scorer (evaluation side; protocol E21_ORACLE_PROTOCOL.txt).

usage: c2_oracle.py CASE_DIR RTL_WORDLEVEL_JSON TOP SYNTH_JSON GRAPH_JSON OUT_JSON [TRANSACTIONS]

The RTL word-level netlist (Yosys proc/flatten/memory) is simulated with the case's own pad
stimulus; truth write events and producers come from this simulation alone. The recovered event
graph is mapped to RTL registers through the synthesized netlist's names and scored."""
import collections, json, random, sys

case_dir, rtl_path, top, synth_path, graph_path, out_path = sys.argv[1:7]
NT = int(sys.argv[7]) if len(sys.argv) > 7 else 6
TRIALS = 6
rtl = json.load(open(rtl_path))["modules"][top]
contract = json.load(open(f"{case_dir}/input/evaluation_contract.json"))
stim = json.load(open(f"{case_dir}/input/stimulus.json"))
G = json.load(open(graph_path))


def P(cell, k, d=0):
    v = cell.get("parameters", {}).get(k, d)
    return int(v, 2) if isinstance(v, str) else int(v)


# ---- word-level evaluator ---------------------------------------------------------------
cells = rtl["cells"]
ports = rtl["ports"]
SEQ = {"$dff", "$adff"}
for name, c in cells.items():
    assert c["type"] in SEQ or c["type"].startswith("$"), f"unsupported cell {c['type']}"
driver = {}
for name, c in cells.items():
    for pn, bits in c["connections"].items():
        if c["port_directions"][pn] == "output":
            for b in bits:
                if isinstance(b, int):
                    driver[b] = name
comb = [n for n, c in cells.items() if c["type"] not in SEQ]
deps = {n: {driver[b] for pn, bits in cells[n]["connections"].items() if cells[n]["port_directions"][pn] == "input"
            for b in bits if isinstance(b, int) and b in driver and cells[driver[b]]["type"] not in SEQ} for n in comb}
order, state, stack = [], {}, []
for n in comb:
    if n in state:
        continue
    stack.append((n, iter(sorted(deps[n]))))
    state[n] = 1
    while stack:
        cur, it = stack[-1]
        nxt = next(it, None)
        if nxt is None:
            stack.pop(); state[cur] = 2; order.append(cur)
        elif nxt not in state:
            state[nxt] = 1; stack.append((nxt, iter(sorted(deps[nxt]))))
        else:
            assert state[nxt] == 2, "combinational loop in RTL netlist"


def word(vals, bits):
    v = 0
    for i, b in enumerate(bits):
        if b == "1" or (isinstance(b, int) and vals.get(b, 0)):
            v |= 1 << i
    return v


def put(vals, bits, v):
    for i, b in enumerate(bits):
        if isinstance(b, int):
            vals[b] = (v >> i) & 1


def sx(v, w, signed, to):
    v &= (1 << w) - 1 if w else 0
    if signed and w and (v >> (w - 1)) & 1:
        v -= 1 << w
    return v & ((1 << to) - 1)


def sval(v, w, signed):
    v &= (1 << w) - 1
    return v - (1 << w) if signed and w and (v >> (w - 1)) & 1 else v


def eval_cell(c, vals):
    t, cn = c["type"], c["connections"]
    A = word(vals, cn["A"]) if "A" in cn else 0
    B = word(vals, cn["B"]) if "B" in cn else 0
    aw, bw = len(cn.get("A", [])), len(cn.get("B", []))
    yw = len(cn["Y"])
    asg, bsg = P(c, "A_SIGNED"), P(c, "B_SIGNED")
    M = (1 << yw) - 1
    if t == "$mux":
        S = word(vals, cn["S"]); y = B if S else A
    elif t == "$pmux":
        S = word(vals, cn["S"]); y = A
        for k in range(len(cn["S"])):
            if (S >> k) & 1:
                y = (B >> (k * yw)) & M; break
    elif t in ("$add", "$sub", "$mul", "$and", "$or", "$xor", "$xnor"):
        a, b = sx(A, aw, asg, yw), sx(B, bw, bsg, yw)
        y = {"$add": a + b, "$sub": a - b, "$mul": a * b, "$and": a & b, "$or": a | b, "$xor": a ^ b, "$xnor": ~(a ^ b)}[t]
    elif t in ("$div", "$mod"):
        a, b = sval(A, aw, asg), sval(B, bw, bsg)
        if b == 0:
            y = 0
        else:
            q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
            y = q if t == "$div" else a - q * b
    elif t == "$not":
        y = ~sx(A, aw, asg, yw)
    elif t == "$neg":
        y = -sx(A, aw, asg, yw)
    elif t == "$pos":
        y = sx(A, aw, asg, yw)
    elif t in ("$eq", "$ne", "$lt", "$le", "$gt", "$ge"):
        sg = asg and bsg
        a, b = (sval(A, aw, True), sval(B, bw, True)) if sg else (A, B)
        y = int({"$eq": a == b, "$ne": a != b, "$lt": a < b, "$le": a <= b, "$gt": a > b, "$ge": a >= b}[t])
    elif t == "$logic_not":
        y = int(A == 0)
    elif t == "$logic_and":
        y = int(A != 0 and B != 0)
    elif t == "$logic_or":
        y = int(A != 0 or B != 0)
    elif t in ("$reduce_or", "$reduce_bool"):
        y = int(A != 0)
    elif t == "$reduce_and":
        y = int(A == (1 << aw) - 1)
    elif t in ("$reduce_xor", "$reduce_xnor"):
        y = bin(A).count("1") & 1
        y = y ^ 1 if t == "$reduce_xnor" else y
    elif t in ("$shl", "$sshl"):
        y = sx(A, aw, asg, yw) << B if B < 4096 else 0
    elif t == "$shr":
        y = (A & ((1 << aw) - 1)) >> B if B < 4096 else 0
    elif t == "$sshr":
        y = sval(A, aw, asg) >> min(B, 4096)
    elif t in ("$shift", "$shiftx"):
        b = sval(B, bw, bsg)
        a = sx(A, aw, asg, max(aw, 1)) if t == "$shift" else A
        y = (a >> b) if b >= 0 else (a << -b)
        if t == "$shiftx" and b >= aw:
            y = 0
    else:
        raise NotImplementedError(t)
    put(vals, cn["Y"], y & M)


def evaluate(vals, cellset=None):
    for n in order if cellset is None else cellset:
        eval_cell(cells[n], vals)


import z3


def z3_cone(n, vals, sym_leaves, cset):
    """D of register n as a z3 bit-vector: leaves in sym_leaves are variables, other nets take vals."""
    var, memo = {}, {}
    for leaf in sym_leaves:
        bits_ = [leaf[1]] if isinstance(leaf, tuple) else [b for b in regs[leaf]["connections"]["Q"] if isinstance(b, int)]
        name = f"port{leaf[1]}" if isinstance(leaf, tuple) else f"reg_{leaf}"
        v = z3.BitVec(name, len(bits_))
        var[leaf] = v
        for i, b in enumerate(bits_):
            memo[b] = z3.Extract(i, i, v)

    def bitexpr(b):
        if b == "1":
            return z3.BitVecVal(1, 1)
        if not isinstance(b, int):
            return z3.BitVecVal(0, 1)
        if b in memo:
            return memo[b]
        return z3.BitVecVal(vals.get(b, 0), 1)

    def vec(bits_):
        es = [bitexpr(b) for b in bits_]
        return z3.Concat(*reversed(es)) if len(es) > 1 else es[0]

    def ext(x, w, signed, to):
        if to <= w:
            return z3.Extract(to - 1, 0, x) if to < w else x
        return z3.SignExt(to - w, x) if signed else z3.ZeroExt(to - w, x)

    for cn_name in cset:
        c = cells[cn_name]; t, cn = c["type"], c["connections"]
        yw = len(cn["Y"])
        A = vec(cn["A"]) if cn.get("A") else None
        B = vec(cn["B"]) if cn.get("B") else None
        aw, bw = len(cn.get("A", [])), len(cn.get("B", []))
        asg, bsg = P(c, "A_SIGNED"), P(c, "B_SIGNED")
        one, zero = z3.BitVecVal(1, yw), z3.BitVecVal(0, yw)
        b1 = lambda cond: z3.If(cond, z3.BitVecVal(1, yw), z3.BitVecVal(0, yw))
        if t == "$mux":
            y = z3.If(vec(cn["S"]) != 0, B, A)
        elif t == "$pmux":
            S = vec(cn["S"]); y = A
            for k in reversed(range(len(cn["S"]))):
                y = z3.If(z3.Extract(k, k, S) == 1, z3.Extract((k + 1) * yw - 1, k * yw, B), y)
        elif t in ("$add", "$sub", "$mul", "$and", "$or", "$xor", "$xnor"):
            a, b = ext(A, aw, asg, yw), ext(B, bw, bsg, yw)
            y = {"$add": lambda: a + b, "$sub": lambda: a - b, "$mul": lambda: a * b, "$and": lambda: a & b,
                 "$or": lambda: a | b, "$xor": lambda: a ^ b, "$xnor": lambda: ~(a ^ b)}[t]()
        elif t in ("$div", "$mod"):
            w = max(aw, bw, yw); a, b = ext(A, aw, asg, w), ext(B, bw, bsg, w)
            if asg and bsg:
                y = z3.If(b == 0, z3.BitVecVal(0, w), a / b if t == "$div" else z3.SRem(a, b))
            else:
                y = z3.If(b == 0, z3.BitVecVal(0, w), z3.UDiv(a, b) if t == "$div" else z3.URem(a, b))
            y = ext(y, w, False, yw)
        elif t == "$not":
            y = ~ext(A, aw, asg, yw)
        elif t == "$neg":
            y = -ext(A, aw, asg, yw)
        elif t == "$pos":
            y = ext(A, aw, asg, yw)
        elif t in ("$eq", "$ne", "$lt", "$le", "$gt", "$ge"):
            sg = asg and bsg; w = max(aw, bw)
            a, b = ext(A, aw, sg, w), ext(B, bw, sg, w)
            if sg:
                cond = {"$eq": a == b, "$ne": a != b, "$lt": a < b, "$le": a <= b, "$gt": a > b, "$ge": a >= b}[t]
            else:
                cond = {"$eq": a == b, "$ne": a != b, "$lt": z3.ULT(a, b), "$le": z3.ULE(a, b), "$gt": z3.UGT(a, b), "$ge": z3.UGE(a, b)}[t]
            y = b1(cond)
        elif t == "$logic_not":
            y = b1(A == 0)
        elif t == "$logic_and":
            y = b1(z3.And(A != 0, B != 0))
        elif t == "$logic_or":
            y = b1(z3.Or(A != 0, B != 0))
        elif t in ("$reduce_or", "$reduce_bool"):
            y = b1(A != 0)
        elif t == "$reduce_and":
            y = b1(A == (1 << aw) - 1)
        elif t in ("$reduce_xor", "$reduce_xnor"):
            r_ = z3.Extract(0, 0, A)
            for i in range(1, aw):
                r_ = r_ ^ z3.Extract(i, i, A)
            r_ = ~r_ if t == "$reduce_xnor" else r_
            y = z3.ZeroExt(yw - 1, r_) if yw > 1 else r_
        elif t in ("$shl", "$sshl", "$shr", "$sshr", "$shift", "$shiftx"):
            w = max(aw, yw, 1) + 1; sw = max(w, bw) + 1
            a = ext(A, aw, asg and t in ("$sshr", "$shift", "$shl", "$sshl"), sw)
            bb = ext(B, bw, bsg and t in ("$shift", "$shiftx"), sw)
            if t in ("$shl", "$sshl"):
                y = a << bb
            elif t == "$shr":
                y = z3.LShR(ext(A, aw, False, sw), bb)
            elif t == "$sshr":
                y = a >> bb
            else:
                y = z3.If(bb < 0 if bsg else z3.BoolVal(False), a << (-bb), (a >> bb) if t == "$shift" and asg else z3.LShR(ext(A, aw, False, sw), bb))
                if t == "$shiftx":
                    y = z3.If(z3.UGE(bb, aw) if not bsg else bb >= aw, z3.BitVecVal(0, sw), y)
            y = ext(y, sw, False, yw)
        else:
            raise NotImplementedError(t)
        y = ext(y, y.size(), False, yw) if y.size() != yw else y
        for i, b in enumerate(cn["Y"]):
            if isinstance(b, int):
                memo[b] = z3.Extract(i, i, y)
    return vec(regs[n]["connections"]["D"]), var


z3_stats = collections.Counter()


def sat(cond):
    s_ = z3.Solver(); s_.set("timeout", 5000); s_.add(cond)
    r_ = s_.check(); z3_stats[str(r_)] += 1
    return r_


regs = {n: c for n, c in cells.items() if c["type"] in SEQ}
names = {}
for nm, d in rtl["netnames"].items():
    for i, b in enumerate(d["bits"]):
        if isinstance(b, int) and (b not in names or nm.count("$") < names[b][0].count("$") or (nm.count("$") == names[b][0].count("$") and len(nm) < len(names[b][0]))):
            names[b] = (nm, i)
reg_name = {}
for n, c in regs.items():
    q = [b for b in c["connections"]["Q"] if isinstance(b, int)]
    reg_name[n] = names[q[0]][0] if q else n


def cone(reg):
    """Combinational cells feeding D of reg, in evaluation order, and the registers/ports read."""
    need, seen = [], set()
    st = [driver[b] for b in regs[reg]["connections"]["D"] if isinstance(b, int) and b in driver]
    leaves = set()
    while st:
        n = st.pop()
        if n in seen:
            continue
        seen.add(n)
        if cells[n]["type"] in SEQ:
            leaves.add(n); continue
        for pn, bits in cells[n]["connections"].items():
            if cells[n]["port_directions"][pn] == "input":
                for b in bits:
                    if isinstance(b, int):
                        if b in driver:
                            st.append(driver[b])
                        else:
                            leaves.add(("port", b))
    for b in regs[reg]["connections"]["D"]:
        if isinstance(b, int) and b not in driver:
            leaves.add(("port", b))
    pos = {n: i for i, n in enumerate(order)}
    return sorted((n for n in seen if n in pos), key=pos.get), leaves


# ---- stimulus -> RTL ports ----------------------------------------------------------------
phys = {p["physical_bit"]: (p["name"], p["offset"]) for p in contract["port_bits"]}
in_index = {b: i for i, b in enumerate(stim["input_bits"])}
port_bit = {}
for pname, pd in ports.items():
    if pd["direction"] == "input":
        for i, b in enumerate(pd["bits"]):
            port_bit[(pname, i)] = b
clock = contract["non_data_ports"][0]
clk_phys = next(p["physical_bit"] for p in contract["port_bits"] if p["name"] == clock)
ev = stim["events"]
rising = [e for e in range(1, len(ev)) if ev[e][in_index[clk_phys]] and not ev[e - 1][in_index[clk_phys]]]
edge_of_event = {e: k for k, e in enumerate(rising)}
data_ports = set(contract["data_ports"])
port_of_bit = {b: pn for (pn, i), b in port_bit.items()}


def inputs_at(e, vals):
    for pb, (pname, off) in phys.items():
        if pb in in_index and (pname, off) in port_bit:
            vals[port_bit[(pname, off)]] = ev[e][in_index[pb]]


# ---- simulate ----------------------------------------------------------------------------
period_ev = 2 * contract["period_cycles"]
first = contract["first_event"]
held = [t for t in contract["transactions"] if t["split"] != "training"][:NT]
last_event = held[-1]["first_event"] + period_ev + 2
vals = {}
cone_cache = {}
rng0 = random.Random(20261111)
rng = random.Random(20261110)
for n, c in regs.items():
    put(vals, c["connections"]["Q"], 0)
for n in regs:
    cone_cache[n] = cone(n)
data_port_bits = {b for (pn, i), b in port_bit.items() if pn in data_ports}
taint = {n: False for n in regs}
truth_write = collections.defaultdict(set)      # (reg, rel_event) -> transactions where written
truth_prod = collections.defaultdict(dict)       # (reg, rel_event) -> producer -> count
tx_of_event = {}
for ti, t in enumerate(held):
    for r in range(period_ev):
        tx_of_event[t["first_event"] + r] = (ti, r)
TR = 12
z3_write, z3_prod, z3_taint = {}, {}, {}


def tainted_leaves(leaves):
    out = []
    for leaf in leaves:
        if isinstance(leaf, tuple):
            if leaf[1] in data_port_bits:
                out.append(leaf)
        elif taint[leaf]:
            out.append(leaf)
    return out


def leaf_bits(leaf):
    return [leaf[1]] if isinstance(leaf, tuple) else [b for b in regs[leaf]["connections"]["Q"] if isinstance(b, int)]


def randomise(v, leaves):
    for leaf in leaves:
        for b in leaf_bits(leaf):
            v[b] = rng.getrandbits(1)


for e in rising:
    if e > last_event:
        break
    inputs_at(e, vals)
    evaluate(vals)
    nxt, new_taint = {}, {}
    rec = e in tx_of_event
    for n, c in regs.items():
        cn = c["connections"]
        if c["type"] == "$adff" and word(vals, cn["ARST"]) == P(c, "ARST_POLARITY", 1):
            nxt[n] = P(c, "ARST_VALUE"); new_taint[n] = False; continue
        nxt[n] = word(vals, cn["D"])
        cset, leaves = cone_cache[n]
        tl = tainted_leaves(leaves)
        d_actual = nxt[n]
        # taint: does re-randomising the tainted leaves change the next value?
        tnt = False
        for _ in range(4 if tl else 0):
            v2 = dict(vals); randomise(v2, tl); evaluate(v2, cset)
            if word(v2, cn["D"]) != d_actual:
                tnt = True; break
        if not tnt and tl:
            key_t = (n, e - first) if e >= first else (n, e)
            rel_e = (e - first) % period_ev if e >= first else None
            ck = (n, rel_e, tuple(sorted(str(x) for x in tl)))
            if ck not in z3_taint:
                D_, var_ = z3_cone(n, vals, tl, cset)
                fresh = [(v, z3.FreshConst(v.sort())) for v in var_.values()]
                z3_taint[ck] = sat(D_ != z3.substitute(D_, *fresh)) == z3.sat
            tnt = z3_taint[ck]
        new_taint[n] = tnt
        if not rec:
            continue
        ti, r = tx_of_event[e]
        q = cn["Q"]
        own = [] if n in tl else [n]
        written = d_actual != word(vals, q)
        for _ in range(0 if written else TR):
            v2 = dict(vals); randomise(v2, tl + own); evaluate(v2, cset)
            if word(v2, cn["D"]) != word(v2, q):
                written = True; break
        if not written and (n, r) not in z3_write:
            D_, var_ = z3_cone(n, vals, tl + own, cset)
            qv = var_[n] if n in var_ else None
            z3_write[(n, r)] = qv is not None and sat(D_ != qv) == z3.sat
        if not written and z3_write.get((n, r)):
            written = True
        if not written:
            continue
        truth_write[(n, r)].add(ti)
        prods = truth_prod[(n, r)]
        for x in tl:
            key = ("port", port_of_bit.get(x[1], "?")) if isinstance(x, tuple) else x
            if key in prods:
                continue
            for _ in range(TR):
                v2 = dict(vals); randomise(v2, tl); evaluate(v2, cset); d1 = word(v2, cn["D"])
                randomise(v2, [x]); evaluate(v2, cset)
                if word(v2, cn["D"]) != d1:
                    prods[key] = 1; break
            if key not in prods and (n, r, key) not in z3_prod:
                D_, var_ = z3_cone(n, vals, tl, cset)
                x1 = var_[x]
                x2 = z3.FreshConst(x1.sort())
                z3_prod[(n, r, key)] = sat(D_ != z3.substitute(D_, (x1, x2))) == z3.sat
            if key not in prods and z3_prod.get((n, r, key)):
                prods[key] = 1
    for n, c in regs.items():
        put(vals, c["connections"]["Q"], nxt[n])
    taint.update(new_taint)

writes_by_reg = collections.defaultdict(set)
for (n, r) in truth_write:
    writes_by_reg[n].add(r)
truth = {}
for (n, r) in truth_write:
    out = {}
    for x in truth_prod[(n, r)]:
        if isinstance(x, tuple):
            out[("port", x[1])] = 0
        else:
            out[(x, None)] = 0 if any(w < r for w in writes_by_reg.get(x, ())) else 1
    truth[(n, r)] = out

# ---- recovered graph -> RTL registers ----------------------------------------------------
syn = json.load(open(synth_path))
stop = next(m for m in syn["modules"].values() if str(m.get("attributes", {}).get("top", "0")).strip("0") != "")
sname = {}
for nm, d in stop["netnames"].items():
    for i, b in enumerate(d["bits"]):
        if isinstance(b, int) and (b not in sname or nm.count("$") < sname[b][0].count("$") or (nm.count("$") == sname[b][0].count("$") and len(nm) < len(sname[b][0]))):
            sname[b] = (nm, i)
rtl_reg_by_name = collections.defaultdict(set)
for n in regs:
    rtl_reg_by_name[reg_name[n]].add(n)
word_regs = {}
for w, d in G["words"].items():
    rs = set()
    for b in d["bits"]:
        nm = sname.get(int(b[1:]))
        if nm and nm[0] in rtl_reg_by_name:
            rs |= rtl_reg_by_name[nm[0]]
    word_regs[w] = rs
Pg, origin = G["period"], G["origin"]
assert Pg == period_ev or period_ev % Pg == 0, "graph period vs transaction"
shift = (first - origin) % Pg
rel = lambda ph: (ph - shift) % Pg
rho = lambda ph: -1 if ph < shift else 0
import z3


def bit_support(e):
    """(symbol, bit) pairs read by a 1-bit expression (extract of a symbol, or a whole symbol)."""
    out, seen, stack = set(), set(), [e]
    while stack:
        x = stack.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_app_of(x, z3.Z3_OP_EXTRACT) and z3.is_const(x.arg(0)) and x.arg(0).decl().kind() == z3.Z3_OP_UNINTERPRETED:
            hi, lo = x.params(); out |= {(str(x.arg(0)), k) for k in range(lo, hi + 1)}; continue
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out |= {(str(x), k) for k in range(x.size() if z3.is_bv(x) else 1)}; continue
        stack.extend(x.children())
    return out


def specialised_symbols(nd):
    """Symbols of the node function after substituting the certificate's known bits at its phase
    (the same rule as c3/decompile.py): the operand edges that matter at this phase."""
    e = z3.parse_smt2_string(nd["smt2"])[0].arg(1)
    subs = []
    seen, stack, vs = set(), [e], {}
    while stack:
        x = stack.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            vs[str(x)] = x
        stack.extend(x.children())
    for name, v in vs.items():
        kind, _, base = name.partition(":")
        table = G["words"][base]["certified_known_lsb_first"] if kind == "word" else G["inputs"][base]["certified_known_lsb_first"]
        known = table[nd["phase"]]
        w = v.size()
        if "x" not in known:
            subs.append((v, z3.BitVecVal(int(known[::-1], 2), w)))
        elif known.strip("x"):
            mask = z3.BitVecVal(int("".join("1" if c != "x" else "0" for c in known)[::-1], 2), w)
            val = z3.BitVecVal(int("".join(c if c != "x" else "0" for c in known)[::-1], 2), w)
            subs.append((v, (v & ~mask) | val))
    e = z3.simplify(z3.substitute(e, *subs)) if subs else z3.simplify(e)
    SPEC_EXPR[0] = e
    out, seen, stack = set(), set(), [e]
    while stack:
        x = stack.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out.add(str(x))
        stack.extend(x.children())
    return out


SPEC_EXPR = [None]
bit_reg = {}  # (word, bit index) -> RTL register cell
for w, d in G["words"].items():
    for i, b in enumerate(d["bits"]):
        nm = sname.get(int(b[1:]))
        if nm and nm[0] in rtl_reg_by_name:
            regs_ = rtl_reg_by_name[nm[0]]
            # a name may denote several cells only for memories; take the cell whose Q has this bit index
            bit_reg[(w, i)] = sorted(regs_)[0]
pred = collections.defaultdict(dict)  # (reg, rel_event) -> producers
pred_written = set()
unmapped_pred = set()
for nd in G["nodes"]:
    r = rel(nd["phase"])
    prods = {}
    live_syms = specialised_symbols(nd)
    for s in nd["sources"]:
        if s["symbol"] not in live_syms:
            continue
        if s["kind"] == "signal":
            d = s["delay"] + rho(nd["phase"]) - rho(s["phase"])
            for x in word_regs.get(s["name"], ()):
                prods[(x, None)] = min(d, 1)
        else:
            prods[("port", s["name"])] = 0
    e = SPEC_EXPR[0]
    srcs = {s_["symbol"]: s_ for s_ in nd["sources"]}
    own_var = z3.BitVec("word:" + nd["name"], len(G["words"][nd["name"]]["bits"]))
    own_src = srcs.get("word:" + nd["name"])
    for reg in word_regs.get(nd["name"], ()):
        mine = [i for i in range(len(G["words"][nd["name"]]["bits"])) if bit_reg.get((nd["name"], i)) == reg]
        # identity update of these bits (reading the same word's previous write): a hold, not a write
        if own_src is not None and all(z3.eq(z3.simplify(z3.Extract(i, i, e)), z3.simplify(z3.Extract(i, i, own_var))) for i in mine):
            continue
        sup = set()
        for i in mine:
            sup |= bit_support(z3.simplify(z3.Extract(i, i, e)))
        pr = {}
        for sym, k in sup:
            s_ = srcs.get(sym)
            if s_ is None:
                continue
            if s_["kind"] == "signal":
                x = bit_reg.get((s_["name"], k))
                if x is not None:
                    d = s_["delay"] + rho(nd["phase"]) - rho(s_["phase"])
                    pr[(x, None)] = min(d, 1)
                else:
                    unmapped_pred.add((reg, r))
            else:
                pr[("port", s_["name"])] = 0
        pred[(reg, r)].update(pr)
        pred_written.add((reg, r))
mapped = set().union(*word_regs.values()) if word_regs else set()
# a recovered write at event r is the clock edge at r; truth uses the same rising events
T_ev = {(n, r) for (n, r) in truth if n in mapped}
P_ev = {k for k in pred_written if k[0] in mapped}
hit_all = T_ev & P_ev
hit = hit_all - unmapped_pred
exact = jac = delay_ok = delay_n = 0
for k in hit:
    tp = {x for x in truth[k] if x[0] != "port"}
    pp = {x for x in pred[k] if x[0] != "port"}
    tports = any(x[0] == "port" for x in truth[k]); pports = any(x[0] == "port" for x in pred[k])
    exact += int(tp == pp and tports == pports)
    jac += len(tp & pp) / len(tp | pp) if tp | pp else 1.0
    for x in tp & pp:
        delay_n += 1; delay_ok += int(truth[k][x] == pred[k][x])
splits = collections.Counter()
for w, rs in word_regs.items():
    for x in rs:
        splits[x] += 1
res = {"case": contract["case"], "z3_confirmations": dict(z3_stats), "rtl_registers": len(regs), "mapped_rtl_registers": len(mapped),
       "unmapped_rtl_registers": sorted(reg_name[n] for n in regs if n not in mapped),
       "transactions_simulated": len(held), "truth_write_events": len(T_ev), "recovered_write_events": len(P_ev),
       "write_recall": len(hit_all) / len(T_ev) if T_ev else None, "write_precision": len(hit_all) / len(P_ev) if P_ev else None,
       "producer_scored_events": len(hit), "producer_unmapped_events": len(hit_all) - len(hit),
       "producer_exact_match": exact / len(hit) if hit else None, "producer_jaccard": jac / len(hit) if hit else None,
       "producer_delay_accuracy": delay_ok / delay_n if delay_n else None,
       "rtl_registers_split_over_words": sum(1 for v in splits.values() if v >= 2),
       "words_merging_registers": sum(1 for rs in word_regs.values() if len(rs) >= 2),
       "missed_by_register": dict(collections.Counter(reg_name[n] for n, r in T_ev - P_ev)),
       "truth_writes_by_register": dict(collections.Counter(reg_name[n] for n, r in T_ev)),
       "missed_examples": sorted((reg_name[n], r) for n, r in T_ev - P_ev)[:12],
       "spurious_examples": sorted((reg_name[n], r) for n, r in P_ev - T_ev)[:12],
       "producer_mismatch_examples": [
           {"event": (reg_name[k[0]], k[1]), "truth": sorted(str(reg_name.get(x[0], x)) for x in truth[k]),
            "recovered": sorted(str(reg_name.get(x[0], x)) for x in pred[k])}
           for k in sorted(hit) if {x for x in truth[k] if x[0] != "port"} != {x for x in pred[k] if x[0] != "port"}][:6]}
json.dump(res, open(out_path, "w"), indent=1)
print(json.dumps({k: v for k, v in res.items() if not k.endswith("examples") and k != "unmapped_rtl_registers"}))
