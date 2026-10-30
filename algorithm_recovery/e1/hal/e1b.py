"""E1b: time-aware word recovery and period discovery on the gate-level (Xilinx 7-series) netlist, no names used.
1. simulate the gate netlist twice with different random data on x_in; flip-flops with identical value sequences in
   both runs are control (differential criterion); P = smallest period of the control vector after reset.
2. per phase: control flip-flops concretized to their trace values, rst = 0, data flip-flops and x_in symbolic (z3);
   a data flip-flop is written in the phase unless next == Q is proved; support = flip-flops/inputs it reads.
3. words by partition refinement: start from the set of phases in which a flip-flop is written; split classes
   until all members read the same classes in every phase (fixpoint).
Scoring only: FDRE Q-net names (ff_q.json) give the RTL register of each flip-flop.
Usage (in e1/hal): python3 e1b.py"""
import json, random, re, collections, sys
import z3

M = json.load(open('gate.json'))['modules']['tm_biquad']
cells = M['cells']
drv = {}
for cn, c in cells.items():
    for p, d in c['port_directions'].items():
        if d == 'output':
            for i, b in enumerate(c['connections'][p]): drv[b] = (cn, p, i)
port_bits = {p: v['bits'] for p, v in M['ports'].items()}
for p, v in M['ports'].items():
    if v['direction'] == 'input':
        for i, b in enumerate(v['bits']): drv[b] = ('IN', p, i)
FF = {cn: c for cn, c in cells.items() if c['type'] == 'FDRE'}
for cn, c in FF.items(): drv[c['connections']['Q'][0]] = ('FF', cn, 0)

def lut_init(c):
    v = c['parameters']['INIT']; return int(v, 2) if set(v) <= {'0', '1'} else int(v, 16)

def make_eval(ops):
    """ops: dict with and_/or_/xor_/not_/ite/const -> generic evaluator over a value domain."""
    def evaluate(get_ff, get_in):
        cache = {}
        def bit(b):
            if b == '0': return ops['const'](0)
            if b == '1': return ops['const'](1)
            if b in ('x', 'z'): return ops['const'](0)
            if b in cache: return cache[b]
            k = drv[b]
            if k[0] == 'FF': r = get_ff(k[1])
            elif k[0] == 'IN': r = get_in(k[1], k[2])
            else:
                cn, port, i = k; c = cells[cn]; t = c['type']; C = c['connections']
                if t.startswith('LUT'):
                    n = int(t[3]); init = lut_init(c); ins = [bit(C['I%d' % j][0]) for j in range(n)]
                    # mux tree over the inputs, MSB input last
                    vals = [ops['const']((init >> m) & 1) for m in range(1 << n)]
                    for j in range(n):
                        vals = [ops['ite'](ins[j], vals[2 * m + 1], vals[2 * m]) for m in range(len(vals) // 2)]
                    r = vals[0]
                elif t in ('MUXF7', 'MUXF8'): r = ops['ite'](bit(C['S'][0]), bit(C['I1'][0]), bit(C['I0'][0]))
                elif t in ('IBUF', 'OBUF', 'BUFG'): r = bit(C['I'][0])
                elif t == 'INV': r = ops['not_'](bit(C['I'][0]))
                elif t == 'CARRY4':
                    ci = ops['or_'](bit(C['CI'][0]), bit(C['CYINIT'][0])); co = []; o = []
                    for j in range(4):
                        s = bit(C['S'][j]); o.append(ops['xor_'](s, ci)); ci = ops['ite'](s, ci, bit(C['DI'][j])); co.append(ci)
                    r = (o if port == 'O' else co)[i]
                else: raise ValueError(t)
            cache[b] = r; return r
        def nxt(cn):
            C = FF[cn]['connections']
            return ops['ite'](bit(C['R'][0]), ops['const'](0), ops['ite'](bit(C['CE'][0]), bit(C['D'][0]), get_ff(cn)))
        return nxt, bit
    return evaluate

IntOps = {'const': lambda v: v, 'not_': lambda a: 1 - a, 'or_': lambda a, b: a | b, 'xor_': lambda a, b: a ^ b,
          'ite': lambda s, a, b: a if s else b}
def zc(v): return z3.BoolVal(bool(v))
Z3Ops = {'const': zc, 'not_': z3.Not, 'or_': lambda a, b: z3.simplify(z3.Or(a, b)), 'xor_': lambda a, b: z3.simplify(z3.Xor(a, b)),
         'ite': lambda s, a, b: (a if z3.is_true(s) else b if z3.is_false(s) else z3.simplify(z3.If(s, a, b)))}

# ---------- 1. simulation, control, period ----------
N = 200
def simulate(seed):
    rng = random.Random(seed); q = {cn: 0 for cn in FF}; trace = []
    ev = make_eval(IntOps)
    for t in range(N):
        x = rng.randrange(1 << 16); rst = 1 if t < 2 else 0
        nxt, _ = ev(lambda cn: q[cn], lambda p, i: (rst if p == 'rst' else (x >> i) & 1 if p == 'x_in' else 0))
        trace.append(dict(q)); q = {cn: nxt(cn) for cn in FF}
    return trace
T1, T2 = simulate(1), simulate(2)
ctrl = [cn for cn in FF if all(T1[t][cn] == T2[t][cn] for t in range(3, N))]
data = [cn for cn in FF if cn not in ctrl]
cv = [tuple(T1[t][cn] for cn in ctrl) for t in range(N)]
t0 = 3
P = next(p for p in range(1, N // 3) if all(cv[t] == cv[t + p] for t in range(t0, N - p)))
print('# control flip-flops (identical in two runs with different data): %d; data: %d; period P = %d' % (len(ctrl), len(data), P))

# ---------- 2. per-phase symbolic next state ----------
S = {cn: z3.Bool('q_' + cn) for cn in data}
X = [z3.Bool('x_%d' % i) for i in range(16)]
def free(e):
    out, st, seen = set(), [e], set()
    while st:
        y = st.pop()
        if y.get_id() in seen: continue
        seen.add(y.get_id())
        if z3.is_const(y) and y.decl().kind() == z3.Z3_OP_UNINTERPRETED: out.add(str(y))
        st.extend(y.children())
    return out
writes = collections.defaultdict(set); support = {}
base = t0 + P   # a cycle in steady state
for ph in range(P):
    t = base + ph; cvals = T1[t]
    ev = make_eval(Z3Ops)
    nxt, _ = ev(lambda cn: zc(cvals[cn]) if cn in ctrl else S[cn],
                lambda p, i: zc(0) if p == 'rst' else X[i] if p == 'x_in' else zc(0))
    for cn in data:
        e = nxt(cn)
        s = z3.Solver(); s.set('timeout', 20000); s.add(e != S[cn])
        if s.check() == z3.unsat: continue
        writes[cn].add(ph); support[(cn, ph)] = free(e)
print('# write phases found for %d of %d data flip-flops' % (len(writes), len(data)))

# ---------- 3. partition refinement ----------
# a phase whose next value is constant (empty support) neither counts as a write nor distinguishes flip-flops;
# reads of the flip-flop's own class (carry chains inside an arithmetic word) are ignored
live = {cn: frozenset(ph for ph in writes[cn] if support[(cn, ph)]) for cn in data}
cls = {cn: live[cn] for cn in data}
for it in range(50):
    def key(cn):
        sig = []
        for ph in sorted(live[cn]):
            rd = frozenset(cls.get(v[2:], 'IN') if v.startswith('q_') else 'IN' for v in support[(cn, ph)])
            sig.append((ph, rd - {cls[cn]}))
        return (cls[cn], tuple(sig))
    keys = {cn: key(cn) for cn in data}
    ids = {}
    new = {cn: ids.setdefault(keys[cn], len(ids)) for cn in data}
    if len(set(new.values())) == len(set(cls.values())): cls = new; break
    cls = new
# merge step for arithmetic words: flip-flops whose D input comes (through at most 3 LUT/MUX levels, no
# flip-flop) from the O outputs of one carry chain are bits of one adder result -> one word
chain = {cn: cn for cn, c in cells.items() if c['type'] == 'CARRY4'}
def cf(x):
    while chain[x] != x: chain[x] = chain[chain[x]]; x = chain[x]
    return x
for cn, c in cells.items():
    if c['type'] == 'CARRY4':
        k = drv.get(c['connections']['CI'][0])
        if k and k[0] in cells and cells[k[0]]['type'] == 'CARRY4': chain[cf(cn)] = cf(k[0])
def chains_of(b, depth=3):
    k = drv.get(b)
    if not k or k[0] in ('FF', 'IN'): return set()
    c = cells[k[0]]
    if c['type'] == 'CARRY4': return {cf(k[0])} if k[1] == 'O' else set()
    if depth == 0: return set()
    out = set()
    for p, d in c['port_directions'].items():
        if d == 'input':
            for bb in c['connections'][p]:
                if isinstance(bb, int): out |= chains_of(bb, depth - 1)
    return out
by_chain = collections.defaultdict(set)
for cn in data:
    for ch in chains_of(FF[cn]['connections']['D'][0]): by_chain[ch].add(cls[cn])
par = {k: k for k in set(cls.values())}
def pf(x):
    while par[x] != x: par[x] = par[par[x]]; x = par[x]
    return x
for ch, cs in by_chain.items():
    cs = sorted(cs)
    for a in cs[1:]: par[pf(a)] = pf(cs[0])
cls = {cn: pf(cls[cn]) for cn in data}
print('# carry-chain merge: %d chains feed flip-flops directly' % len(by_chain))
words = collections.defaultdict(list)
for cn in data: words[cls[cn]].append(cn)
print('# words after refinement: %d (iterations %d)' % (len(words), it + 1))

# ---------- scoring (white box) ----------
qn = json.load(open('ff_q.json'))
outmap = {}   # Q net bit -> output port bit, through OBUF
for cn, c in cells.items():
    if c['type'] == 'OBUF':
        ib, ob = c['connections']['I'][0], c['connections']['O'][0]
        for p, v in M['ports'].items():
            if v['direction'] == 'output' and ob in v['bits']: outmap[ib] = '%s[%d]' % (p, v['bits'].index(ob))
def reg(cn):
    qb = FF[cn]['connections']['Q'][0]
    return re.sub(r'\[\d+\]$', '', outmap.get(qb, qn.get(cn, '?')))
exact = 0; rows = []
by_reg = collections.defaultdict(set)
for cn in data: by_reg[reg(cn)].add(cn)
for w, mem in sorted(words.items(), key=lambda kv: -len(kv[1])):
    regs = collections.Counter(reg(cn) for cn in mem)
    rows.append((len(mem), dict(regs), sorted(writes[mem[0]])))
for r_, ffs in sorted(by_reg.items()):
    ok = any(set(m) == ffs for m in words.values())
    exact += ok
    print('  %-8s %2d bits -> %s' % (r_, len(ffs), 'ONE word' if ok else 'split/merged: %s' % [dict(collections.Counter(reg(c) for c in m)) for m in words.values() if set(m) & ffs]))
print('# registers recovered exactly as one word: %d of %d' % (exact, len(by_reg)))
print('# control flip-flops by RTL register:', dict(collections.Counter(reg(cn) for cn in ctrl)))
json.dump({'P': P, 'ctrl': ctrl, 'words': {str(k): v for k, v in words.items()}, 'writes': {k: sorted(v) for k, v in writes.items()}},
          open('e1b_words.json', 'w'), indent=1)

# ---------- debug: why acc splits ----------
if '--debug' in sys.argv:
    accw = [m for m in words.values() if any(reg(c) == 'acc' for c in m)]
    for m in accw:
        cn = m[0]
        print('acc class size %d, sample bit %s' % (len(m), qn[cn]), [(ph, sorted({str(cls.get(v[2:], 'IN')) if v.startswith('q_') else 'IN' for v in support[(cn, ph)]})) for ph in sorted(writes[cn])][:6])
