"""E1a: inverse folding on a time-multiplexed design, equations from the RTL (word-level guided symbolic step).
1. per-slot next-state equations: yosys word-level netlist evaluated with z3, slot register concrete, rst = 0;
   a register 'holds' in a slot if next == current is proved.
2. inverse folding (THEORY.md §4): write events (reg, slot); every read resolved to the last write before it,
   delay k in samples; copies eliminated.
3. Theorem 1 check: simulate the recovered graph sample by sample (exact node expressions) on the testbench input
   and compare y1/y2 with the RTL simulation (Icarus).
4. identification: inline zero-delay single-consumer nodes, fit each remaining node to floor(sum c_i * s_i) with
   rational c_i (exact, verified on random points), recurrences -> poles.
Usage: python3 e1a.py   (needs tm_biquad.json from yosys and sim.txt from the testbench)"""
import json, re, random, collections, itertools
from fractions import Fraction as Fr
import z3

M = json.load(open('tm_biquad.json'))['modules']['tm_biquad']
names = {}
for n, w in M['netnames'].items():
    for i, b in enumerate(w['bits']):
        if isinstance(b, int): names.setdefault(b, (n, i))
drv = {}
for cn, c in M['cells'].items():
    for p, d in c['port_directions'].items():
        if d == 'output':
            for i, b in enumerate(c['connections'][p]): drv[b] = (cn, p, i)
regs = {}   # Q bits -> register name
for cn, c in M['cells'].items():
    if c['type'] == '$dff':
        q = c['connections']['Q']; rn = names[q[0]][0]
        regs[rn] = c
        for i, b in enumerate(q): drv[b] = ('REG', rn, i)
inputs = {p: v['bits'] for p, v in M['ports'].items() if v['direction'] == 'input'}
for p, bits in inputs.items():
    for i, b in enumerate(bits): drv[b] = ('IN', p, i)
W = {rn: len(c['connections']['Q']) for rn, c in regs.items()}
Q = {rn: z3.BitVec('q_' + rn, W[rn]) for rn in regs}
X = {p: z3.BitVec('in_' + p, len(b)) for p, b in inputs.items()}

def build(slot):
    cache = {}
    def bit(b):
        if b in ('0', 'x', 'z'): return z3.BitVecVal(0, 1)
        if b == '1': return z3.BitVecVal(1, 1)
        k = drv[b]
        if k[0] == 'REG':
            if k[1] == 'slot': return z3.BitVecVal((slot >> k[2]) & 1, 1)
            return z3.Extract(k[2], k[2], Q[k[1]])
        if k[0] == 'IN':
            if k[1] == 'rst': return z3.BitVecVal(0, 1)
            return z3.Extract(k[2], k[2], X[k[1]])
        return z3.Extract(k[2], k[2], cell(k[0]))
    def sig(bits):
        e = bit(bits[-1])
        for b in reversed(bits[:-1]): e = z3.Concat(e, bit(b))
        return z3.simplify(e)
    def ext(e, w, signed):
        d = w - e.size()
        if d > 0: return z3.SignExt(d, e) if signed else z3.ZeroExt(d, e)
        if d < 0: return z3.Extract(w - 1, 0, e)
        return e
    def cell(cn):
        if cn in cache: return cache[cn]
        c = M['cells'][cn]; t = c['type']; P = c['parameters']; C = c['connections']
        yw = len(C['Y'])
        if t == '$mux': r = z3.If(sig(C['S']) == 1, sig(C['B']), sig(C['A']))
        elif t == '$pmux':
            A, B, S = sig(C['A']), sig(C['B']), sig(C['S']); n = S.size(); r = A
            for i in reversed(range(n)): r = z3.If(z3.Extract(i, i, S) == 1, z3.Extract((i + 1) * yw - 1, i * yw, B), r)
        else:
            sa = int(P.get('A_SIGNED', '0'), 2) == 1 if isinstance(P.get('A_SIGNED', 0), str) else bool(P.get('A_SIGNED', 0))
            sb = int(P.get('B_SIGNED', '0'), 2) == 1 if isinstance(P.get('B_SIGNED', 0), str) else bool(P.get('B_SIGNED', 0))
            if t in ('$add', '$mul', '$sub'):
                a, b = ext(sig(C['A']), yw, sa), ext(sig(C['B']), yw, sb)
                r = a + b if t == '$add' else a * b if t == '$mul' else a - b
            elif t == '$eq':
                a, b = sig(C['A']), sig(C['B']); w = max(a.size(), b.size())
                r = z3.If(ext(a, w, sa) == ext(b, w, sb), z3.BitVecVal(1, yw), z3.BitVecVal(0, yw))
            elif t == '$reduce_or': r = z3.If(sig(C['A']) != 0, z3.BitVecVal(1, yw), z3.BitVecVal(0, yw))
            elif t == '$logic_not': r = z3.If(sig(C['A']) == 0, z3.BitVecVal(1, yw), z3.BitVecVal(0, yw))
            else: raise ValueError(t)
        cache[cn] = r = z3.simplify(r); return r
    eqs = {}
    for rn, c in regs.items():
        if rn == 'slot': continue
        nxt = sig(c['connections']['D'])
        s = z3.Solver(); s.add(nxt != Q[rn])
        if s.check() == z3.unsat: continue   # holds in this slot
        eqs[rn] = nxt
    return eqs

P = 16
EQ = {s: build(s) for s in range(P)}
print('# per-slot writes:', {s: sorted(e) for s, e in EQ.items()})

# ---------------- inverse folding ----------------
writes = collections.defaultdict(list)   # reg -> sorted slots
for s in range(P):
    for r in EQ[s]: writes[r].append(s)
def last_write(r, s):   # (slot', k) of the last write of r strictly before cycle P*n + s
    before = [x for x in writes[r] if x < s]
    if before: return max(before), 0
    return max(writes[r]), 1
def reads(e):   # register / input symbols in an expression
    out = set(); st = [e]; seen = set()
    while st:
        x = st.pop()
        if x.get_id() in seen: continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED: out.add(str(x))
        st.extend(x.children())
    return out
nodes = {(r, s): EQ[s][r] for s in range(P) for r in EQ[s]}
def is_copy(node):
    e = nodes[node]; return z3.is_const(e) and str(e).startswith('q_')
def resolve(sym, s):   # -> (node or input, delay)
    if sym.startswith('in_'): return (sym[3:], 'input'), 0
    r = sym[2:]; s2, k = last_write(r, s)
    if is_copy((r, s2)):   # copy elimination: follow to the copied register's last write before the copy
        src = str(nodes[(r, s2)]); (n2, k2) = resolve(src, s2); return n2, k + k2
    return (r, s2), k
graph = {}
for node, e in nodes.items():
    if is_copy(node): continue
    graph[node] = {sym: resolve(sym, node[1]) for sym in reads(e)}
print('# algorithm nodes (write events, copies removed):', len(graph))
for node in sorted(graph, key=lambda x: x[1]):
    print('  %-12s %s' % ('%s@%d' % node, {k: ('%s@%d' % v[0] if v[0][1] != 'input' else v[0][0], v[1]) for k, v in graph[node].items()}))
neg = [(n, v) for n, d in graph.items() for v in d.values() if v[1] < 0]
print('# Lemma 1 check: negative delays:', neg)

# ---------------- Theorem 1 check: simulate the recovered graph ----------------
sim = [l.split()[1:] for l in open('sim.txt') if l.startswith('S ')]
xs = [int(a[0]) for a in sim]
order = sorted(graph, key=lambda x: x[1])   # within a sample, slots in increasing order (zero-delay edges go forward)
hist = collections.defaultdict(dict)   # node -> {n: value}
def val(ref, n):
    node, k = ref
    if node[1] == 'input': return xs[n] & 0xffff
    m = n - k
    return hist[node].get(m, 0) if m >= 0 else 0   # reset state 0
out = {}
for n in range(len(xs)):
    for node in order:
        e = nodes[node]; sub = []
        for sym, ref in graph[node].items():
            w = (W[sym[2:]] if sym.startswith('q_') else X[sym[3:]].size())
            sub.append((z3.BitVec(sym, w), z3.BitVecVal(val(ref, n), w)))
        hist[node][n] = z3.simplify(z3.substitute(e, *sub)).as_long()
def s16(v): v &= 0xffff; return v - 65536 if v >> 15 else v
y1n = [k for k in graph if k[0] == 'y1'][0]; y2n = [k for k in graph if k[0] == 'y2'][0]
# the testbench prints y1/y2 at slot 0 of sample n, i.e. the values written in sample n-1
mis = [(n, s16(hist[y1n].get(n - 1, 0)), int(sim[n][1]), s16(hist[y2n].get(n - 1, 0)), int(sim[n][2])) for n in range(1, len(xs))
       if s16(hist[y1n].get(n - 1, 0)) != int(sim[n][1]) or s16(hist[y2n].get(n - 1, 0)) != int(sim[n][2])]
print('# Theorem 1 check: %d samples, %d mismatches' % (len(xs) - 1, len(mis)), mis[:3])
json.dump({'%s@%d' % k: {s: ['%s@%d' % v[0] if v[0][1] != 'input' else v[0][0], v[1]] for s, v in d.items()} for k, d in graph.items()},
          open('e1a_graph.json', 'w'), indent=1)

# ---------------- 4. inline, exact fit, grouping, identification ----------------
consumers = collections.defaultdict(list)
for node, d in graph.items():
    for sym, (src, k) in d.items():
        if src[1] != 'input': consumers[src].append((node, k))
outregs = {names[b][0] for p, v in M['ports'].items() if v['direction'] == 'output' for b in v['bits'] if b in names}
# inline a node with a single zero-delay consumer, unless its register drives a module output (observable)
inline = {n for n in graph if len(consumers[n]) == 1 and consumers[n][0][1] == 0 and n[0] not in outregs}
keep = [n for n in graph if n not in inline]
def composed(node):   # z3 expression of node over (kept node, delay) symbols and inputs
    e = nodes[node]; sub = []
    for sym, (src, k) in graph[node].items():
        w = W[sym[2:]] if sym.startswith('q_') else X[sym[3:]].size()
        if src[1] == 'input': sub.append((z3.BitVec(sym, w), z3.BitVec('x', w)))
        elif src in inline and k == 0: sub.append((z3.BitVec(sym, w), ext_to(composed(src), w)))
        else: sub.append((z3.BitVec(sym, w), z3.BitVec('%s@%d^%d' % (src[0], src[1], k), w)))
    return z3.simplify(z3.substitute(e, *sub))
def ext_to(e, w): return e if e.size() == w else (z3.Extract(w - 1, 0, e) if e.size() > w else z3.SignExt(w - e.size(), e))
rng = random.Random(1)
def fit(node):
    e = composed(node); vs = sorted(reads(e)); wd = e.size()
    def f(vals):
        sub = [(z3.BitVec(v, z3.BitVec(v, 1).size() if False else None or 16), z3.BitVecVal(vals[v] & 0xffff, 16)) for v in vs]
        r = z3.simplify(z3.substitute(e, *sub)).as_long(); return r - (1 << wd) if r >> (wd - 1) else r
    # exact rational fit floor(sum c_i v_i): coefficients from large unit probes, verified on random points
    D = 1 << 12; c = {}
    for v in vs:
        base = {u: 0 for u in vs}; base[v] = D; hi = f(base); base[v] = -D; lo = f(base)
        c[v] = Fr(hi - lo, 2 * D).limit_denominator(1 << 16)
    ok = all(f(p) == math.floor(sum(c[v] * p[v] for v in vs)) for p in ({v: rng.randint(-2000, 2000) for v in vs} for _ in range(300)))
    return c, ok
import math
print('# kept nodes after inlining:', sorted('%s@%d' % n for n in keep))
fits = {}
for n in keep:
    c, ok = fit(n); fits[n] = c
    print('  %-8s exact fit %-5s %s' % ('%s@%d' % n, ok, {k: str(v) for k, v in c.items()}))
# grouping: a node whose fit reads itself with delay > 0 is recursive (R1); a node reading only one recursive node
# (any delays) is that section's output stage (R5)
import numpy as np
for n in keep:
    me = '%s@%d' % n; c = fits[n]
    selfc = {int(k.split('^')[1]): v for k, v in c.items() if k.split('^')[0] == me}
    if selfc:
        den = [1] + [-float(selfc.get(d, 0)) for d in range(1, max(selfc) + 1)]
        poles = np.roots(den)
        print('# section %s: recurrence, poles %s (|p| = %s)' % (me, np.round(poles, 4), np.round(abs(poles), 4)))
        outs = [m for m in keep if m != n and all(k.split('^')[0] == me for k in fits[m])]
        for o in outs:
            num = [float(fits[o].get('%s^%d' % (me, d), 0)) for d in range(0, 3)]
            z = np.roots(num)
            kind = 'low-pass (zeros at z=-1)' if np.allclose(z, -1, atol=1e-6) else 'high-pass (zeros at z=+1)' if np.allclose(z, 1, atol=1e-6) else 'zeros %s' % np.round(z, 4)
            print('    output stage %s: numerator %s -> biquad, %s' % ('%s@%d' % o, [str(fits[o].get('%s^%d' % (me, d), 0)) for d in range(3)], kind))
