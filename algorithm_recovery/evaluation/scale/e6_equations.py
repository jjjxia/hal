#!/usr/bin/env python3
"""E6 harness: per-cycle word equations of a design, from its RTL only (protocol E6_SCALING_PROTOCOL.txt).

usage: e6_equations.py RTL_JSON TOP OUT_JSON
RTL_JSON: Yosys `proc; flatten; opt_clean; write_json` of the RTL. The generator's truth file is not read.
Data registers: registers whose next-state cone depends, through registers, on a data input (every input except clk
and rst). Control registers are simulated concretely from reset (rst = 1 for 2 cycles); data registers and data
inputs are time-normalized cut symbols |word:R| and |input:X|. One event per clock cycle. Per cycle, each data
register's next state is evaluated with the concrete control substituted (memoised per control state): identical to
the register's own symbol = hold, otherwise a full write with that equation. The window starts at the first cycle
after reset release (its control state is the reset-release state) and has 5 F cycles, where F is the number of
cycles after which the control state first repeats (measured, not supplied).
Output: {"start", "length", "words": {R: {"width", "writes", "eq"}}, "inputs": {X: width}, "equations": [smt2]}.
"""
import collections, json, sys, time

t0 = time.time()
rtl, top, out = sys.argv[1:4]
mod = json.load(open(rtl))["modules"][top]
cells = mod["cells"]
ports = mod["ports"]
NON_DATA = {"clk", "rst"}

# ---- bit-level structure ----------------------------------------------------------------------------------------
driver = {}            # bit id -> (cell name, port, index)
for cn, c in cells.items():
    for p, d in c["port_directions"].items():
        if d == "output":
            for i, b in enumerate(c["connections"][p]):
                if isinstance(b, int):
                    driver[b] = (cn, p, i)
port_bit = {}          # bit id -> (port, index) for input ports
for pn, p in ports.items():
    if p["direction"] == "input":
        for i, b in enumerate(p["bits"]):
            port_bit[b] = (pn, i)
ffs = {cn: c for cn, c in cells.items() if c["type"] == "$dff"}
assert all(c["type"] != "$dff" or c["parameters"].get("CLK_POLARITY") in ("1", 1) for c in cells.values())
unsupported = {c["type"] for c in cells.values()} - {"$dff", "$mux", "$eq", "$ne", "$logic_not", "$logic_and",
                                                     "$logic_or", "$not", "$and", "$or", "$xor", "$add", "$sub", "$mul",
                                                     "$reduce_or", "$reduce_and", "$reduce_bool", "$pmux"}
assert not unsupported, f"unsupported cells {unsupported}"
# register name: the netname whose bits are exactly the Q bits
qname = {}
bits_name = {}         # Q bits -> a register name; output ports that alias a register are not register names
for nm, n in sorted(mod["netnames"].items()):
    if not n.get("hide_name") and (tuple(n["bits"]) not in bits_name or nm not in ports):
        if tuple(n["bits"]) in bits_name and bits_name[tuple(n["bits"])] not in ports:
            continue
        bits_name[tuple(n["bits"])] = nm
for cn, c in ffs.items():
    q = tuple(c["connections"]["Q"])
    qname[cn] = bits_name.get(q, cn)
q_of_bit = {b: (cn, i) for cn, c in ffs.items() for i, b in enumerate(c["connections"]["Q"])}


def cone(bits):
    """Registers and input ports in the combinational cone of the given bits."""
    regs, ins, seen, stack = set(), set(), set(), [b for b in bits if isinstance(b, int)]
    while stack:
        b = stack.pop()
        if b in seen:
            continue
        seen.add(b)
        if b in q_of_bit:
            regs.add(q_of_bit[b][0]); continue
        if b in port_bit:
            ins.add(port_bit[b][0]); continue
        cn, _, _ = driver[b]
        c = cells[cn]
        for p, d in c["port_directions"].items():
            if d == "input":
                stack += [x for x in c["connections"][p] if isinstance(x, int)]
    return regs, ins


deps = {cn: cone(c["connections"]["D"]) for cn, c in ffs.items()}
data = {cn for cn, (r, i) in deps.items() if i - NON_DATA}
changed = True
while changed:
    changed = False
    for cn, (r, i) in deps.items():
        if cn not in data and r & data:
            data.add(cn); changed = True
control = sorted(set(ffs) - data)
data_inputs = sorted(pn for pn, p in ports.items() if p["direction"] == "input" and pn not in NON_DATA)

# ---- evaluation (bit values: 0/1 or (expr id, bit)) -------------------------------------------------------------
exprs = []             # (smt2 text, width)
memo_expr = {}


def new_expr(text, w):
    key = (text, w)
    if key not in memo_expr:
        memo_expr[key] = len(exprs)
        exprs.append(key)
    return memo_expr[key]


sym = {}
for cn in sorted(data):
    sym[cn] = new_expr(f"|word:{qname[cn]}|", len(ffs[cn]["connections"]["Q"]))
for pn in data_inputs:
    sym[("in", pn)] = new_expr(f"|input:{pn}|", len(ports[pn]["bits"]))


def word(bits):
    """('c', value, w) or ('e', text, w) from a list of bit values."""
    w = len(bits)
    if all(isinstance(b, int) for b in bits):
        return ("c", sum(b << i for i, b in enumerate(bits)), w)
    e0 = bits[0]
    if not isinstance(e0, int) and e0[1] == 0 and exprs[e0[0]][1] == w and \
            all(not isinstance(b, int) and b[0] == e0[0] and b[1] == i for i, b in enumerate(bits)):
        return ("e", exprs[e0[0]][0], w)
    segs, i = [], 0
    while i < w:                      # runs, LSB first
        j = i
        if isinstance(bits[i], int):
            while j + 1 < w and isinstance(bits[j + 1], int):
                j += 1
            segs.append("#b" + "".join(str(bits[k]) for k in range(j, i - 1, -1)))
        else:
            e, lo = bits[i]
            while j + 1 < w and not isinstance(bits[j + 1], int) and bits[j + 1][0] == e and bits[j + 1][1] == lo + (j + 1 - i):
                j += 1
            hi = lo + (j - i)
            t, ew = exprs[e]
            segs.append(t if (lo == 0 and hi == ew - 1) else f"((_ extract {hi} {lo}) {t})")
        i = j + 1
    return ("e", segs[0] if len(segs) == 1 else "(concat " + " ".join(reversed(segs)) + ")", w)


def ext(v, w, signed):
    k, x, vw = v
    if vw == w:
        return v
    if vw > w:
        return ("c", x & ((1 << w) - 1), w) if k == "c" else ("e", f"((_ extract {w-1} 0) {x})", w)
    if k == "c":
        if signed and x >> (vw - 1) & 1:
            x |= ((1 << w) - 1) ^ ((1 << vw) - 1)
        return ("c", x, w)
    return ("e", f"((_ {'sign' if signed else 'zero'}_extend {w - vw}) {x})", w)


def lit(v):
    k, x, w = v
    return x if k == "e" else f"(_ bv{x} {w})"


def to_bits(v):
    k, x, w = v
    if k == "c":
        return [(x >> i) & 1 for i in range(w)]
    e = new_expr(x, w)
    return [(e, i) for i in range(w)]


def P(c, n):
    return int(c["parameters"][n], 2) if isinstance(c["parameters"][n], str) else int(c["parameters"][n])


BIN = {"$and": ("bvand", lambda a, b: a & b), "$or": ("bvor", lambda a, b: a | b), "$xor": ("bvxor", lambda a, b: a ^ b),
       "$add": ("bvadd", lambda a, b: a + b), "$sub": ("bvsub", lambda a, b: a - b), "$mul": ("bvmul", lambda a, b: a * b)}

# topological order of combinational cells
comb = [cn for cn, c in cells.items() if c["type"] != "$dff"]
need = {cn: {driver[b][0] for p, d in cells[cn]["port_directions"].items() if d == "input"
             for b in cells[cn]["connections"][p] if isinstance(b, int) and b in driver and cells[driver[b][0]]["type"] != "$dff"}
        for cn in comb}
users = collections.defaultdict(list)
for cn, ns in need.items():
    for m in ns:
        users[m].append(cn)
indeg = {cn: len(ns) for cn, ns in need.items()}
topo = [cn for cn in comb if indeg[cn] == 0]
for cn in topo:
    for u in users[cn]:
        indeg[u] -= 1
        if indeg[u] == 0:
            topo.append(u)
assert len(topo) == len(comb), "combinational loop"


def evaluate(ctrl_vals, rst):
    """Next-state words of every register for one control state."""
    val = {}

    def bit(b):
        if b in ("0", "x", "z"):
            return 0
        if b == "1":
            return 1
        if b in val:
            return val[b]
        if b in q_of_bit:
            cn, i = q_of_bit[b]
            if cn in data:
                return (sym[cn], i)
            return (ctrl_vals[cn] >> i) & 1
        pn, i = port_bit[b]
        if pn == "rst":
            return rst
        if pn == "clk":
            return 0
        return (sym[("in", pn)], i)

    for cn in topo:
        c = cells[cn]; t = c["type"]; cc = c["connections"]
        A = word([bit(b) for b in cc["A"]]) if "A" in cc else None
        Bv = word([bit(b) for b in cc["B"]]) if "B" in cc else None
        if t == "$mux":
            s = word([bit(b) for b in cc["S"]])
            assert s[0] == "c", f"symbolic mux select in {cn}"
            y = Bv if s[1] else A
        elif t == "$pmux":
            s = word([bit(b) for b in cc["S"]])
            assert s[0] == "c", f"symbolic pmux select in {cn}"
            w = P(c, "WIDTH"); y = A
            for k in range(s[2]):
                if s[1] >> k & 1:
                    bb = [bit(b) for b in cc["B"][k * w:(k + 1) * w]]
                    y = word(bb); break
        else:
            yw = P(c, "Y_WIDTH")
            sa = P(c, "A_SIGNED") if "A_SIGNED" in c["parameters"] else 0
            sb = P(c, "B_SIGNED") if "B_SIGNED" in c["parameters"] else 0
            if t in BIN:
                a, b = ext(A, yw, sa and sb), ext(Bv, yw, sa and sb)
                if a[0] == "c" and b[0] == "c":
                    y = ("c", BIN[t][1](a[1], b[1]) & ((1 << yw) - 1), yw)
                else:
                    y = ("e", f"({BIN[t][0]} {lit(a)} {lit(b)})", yw)
            elif t in ("$eq", "$ne"):
                w = max(A[2], Bv[2]); a, b = ext(A, w, sa and sb), ext(Bv, w, sa and sb)
                assert a[0] == "c" and b[0] == "c", f"symbolic comparison in {cn}"
                y = ("c", int((a[1] == b[1]) == (t == "$eq")), yw)
            elif t == "$not":
                a = ext(A, yw, sa)
                y = ("c", ~a[1] & ((1 << yw) - 1), yw) if a[0] == "c" else ("e", f"(bvnot {a[1]})", yw)
            elif t in ("$logic_not", "$reduce_or", "$reduce_bool", "$reduce_and", "$logic_and", "$logic_or"):
                assert A[0] == "c" and (Bv is None or Bv[0] == "c"), f"symbolic logic op in {cn}"
                a = A[1] != 0
                r = {"$logic_not": not a, "$reduce_or": a, "$reduce_bool": a, "$reduce_and": A[1] == (1 << A[2]) - 1,
                     "$logic_and": a and Bv and Bv[1] != 0, "$logic_or": a or (Bv and Bv[1] != 0)}[t]
                y = ("c", int(bool(r)), yw)
            else:
                raise AssertionError(t)
        for b, v in zip(cc["Y"], to_bits(y)):
            if isinstance(b, int):
                val[b] = v
    return {cn: word([bit(b) for b in ffs[cn]["connections"]["D"]]) for cn in ffs}


# ---- run: reset, then find the control period -------------------------------------------------------------------
ctrl = {cn: 0 for cn in control}          # power-up value is irrelevant: rst = 1 sets the control registers
cache = {}


def step(ctrl, rst):
    key = (tuple(ctrl[cn] for cn in control), rst)
    if key not in cache:
        cache[key] = evaluate(ctrl, rst)
    nxt = cache[key]
    for cn in control:
        assert nxt[cn][0] == "c", f"control register {cn} became symbolic"
    return key, {cn: nxt[cn][1] for cn in control}


for _ in range(2):
    _, ctrl = step(ctrl, 1)
start_state = tuple(ctrl[cn] for cn in control)
states, c = [], dict(ctrl)
while True:
    states.append(tuple(c[cn] for cn in control))
    _, c = step(c, 0)
    if tuple(c[cn] for cn in control) == start_state:
        break
    assert len(states) < 1 << 20, "no control period"
factor = len(states)
L = 5 * factor
t_ctrl = time.time()
words = {cn: {"width": len(ffs[cn]["connections"]["Q"]), "writes": [], "eq": []} for cn in sorted(data)}
eq_index = {}
equations = []
c = dict(ctrl)
for t in range(L):
    key, c2 = step(c, 0)
    nxt = cache[key]
    for cn in sorted(data):
        k, x, w = nxt[cn]
        own = exprs[sym[cn]][0]
        if k == "e" and x == own:
            words[cn]["writes"].append("H"); words[cn]["eq"].append(-1)
        else:
            text = lit(nxt[cn])
            if text not in eq_index:
                eq_index[text] = len(equations); equations.append(text)
            words[cn]["writes"].append("F"); words[cn]["eq"].append(eq_index[text])
    c = c2
res = {"start": 0, "length": L, "control_period": factor, "control_registers": [qname[cn] for cn in control],
       "words": {qname[cn]: {"width": v["width"], "writes": "".join(v["writes"]), "eq": v["eq"]} for cn, v in words.items()},
       "inputs": {pn: len(ports[pn]["bits"]) for pn in data_inputs}, "equations": equations,
       "stats": {"cells": len(cells), "registers": len(ffs), "data_registers": len(data), "control_states": len(cache),
                 "distinct_equations": len(equations), "seconds": round(time.time() - t0, 2),
                 "seconds_control": round(t_ctrl - t0, 2)}}
json.dump(res, open(out, "w"))
print(json.dumps(res["stats"]))
