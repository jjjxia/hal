"""Sections, canonical forms and library identification on the single-rate graph produced by invfold (PROPOSAL §3).
Nodes: ('sig', word, slot) with floor(sum c * src[n-k] + c0) mod 2^w. Sections: R1 recursive SCCs; R5 a feed-forward
node whose node sources all lie in one section joins it (output stage); R4 a remaining feed-forward node read by
exactly one feed-forward node joins its reader; otherwise its own section. Canonical form of a section: the exact
rational transfer function (in z^-1, rational coefficients, rounding treated as exact scaling) from each external
input to each node read outside the section, with the boundary pure delay z^-d stripped and listed separately.
Usage: identify.py EQUATIONS.json META.json [OUT.json] [--origin K]"""
import sys, json, collections
from fractions import Fraction as Fr
import numpy as np
import invfold

# ---- polynomials in z^-1 (list of Fractions, index = power of z^-1) and rational functions ----
def trim(p):
    p = list(p)
    while len(p) > 1 and p[-1] == 0: p.pop()
    return p or [Fr(0)]
def padd(a, b): n = max(len(a), len(b)); return trim([(a[i] if i < len(a) else 0) + (b[i] if i < len(b) else 0) for i in range(n)])
def pmul(a, b):
    r = [Fr(0)] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b): r[i + j] += x * y
    return trim(r)
def pneg(a): return [-x for x in a]
def iszero(a): return all(x == 0 for x in a)
def pdivmod(a, b):
    a = trim(a); b = trim(b); q = [Fr(0)] * max(len(a) - len(b) + 1, 1)
    while not iszero(a) and len(a) >= len(b):
        c = a[-1] / b[-1]; d = len(a) - len(b); q[d] = c
        a = trim(padd(a, pneg(pmul([Fr(0)] * d + [c], b))))
    return trim(q), a
def pgcd(a, b):
    a, b = trim(a), trim(b)
    while not iszero(b): a, b = b, pdivmod(a, b)[1]
    return [x / a[-1] for x in a]
class RF:
    def __init__(self, n, d=None):
        n = trim([Fr(x) for x in n]); d = trim([Fr(x) for x in (d or [1])])
        g = pgcd(n, d) if not iszero(n) else d
        n, d = pdivmod(n, g)[0], pdivmod(d, g)[0]
        k = next((x for x in d if x != 0)); self.n, self.d = [x / k for x in n], [x / k for x in d]
        if iszero(self.n): self.n, self.d = [Fr(0)], [Fr(1)]
    def __add__(s, o): return RF(padd(pmul(s.n, o.d), pmul(o.n, s.d)), pmul(s.d, o.d))
    def __sub__(s, o): return s + RF(pneg(o.n), o.d)
    def __mul__(s, o): return RF(pmul(s.n, o.n), pmul(s.d, o.d))
    def __truediv__(s, o): return RF(pmul(s.n, o.d), pmul(s.d, o.n))
    def zero(s): return iszero(s.n)
def zk(k, c=1): return RF([Fr(0)] * k + [Fr(c)])

def solve(nodes, eqs, inputs):
    """nodes: list; eqs[node] = {src: RF}; src is a node or an input. Returns H[node][input] (RF)."""
    idx = {v: i for i, v in enumerate(nodes)}; n = len(nodes)
    M = [[RF([1]) if i == j else RF([0]) for j in range(n)] + [RF([0]) for _ in inputs] for i in range(n)]
    for v in nodes:
        for s, c in eqs[v].items():
            if s in idx: M[idx[v]][idx[s]] = M[idx[v]][idx[s]] - c
            else: M[idx[v]][n + inputs.index(s)] = M[idx[v]][n + inputs.index(s)] + c
    for col in range(n):
        piv = next(r for r in range(col, n) if not M[r][col].zero())
        M[col], M[piv] = M[piv], M[col]
        p = M[col][col]; M[col] = [x / p for x in M[col]]
        for r in range(n):
            if r != col and not M[r][col].zero():
                f = M[r][col]; M[r] = [a - f * b for a, b in zip(M[r], M[col])]
    return {v: {u: M[idx[v]][n + j] for j, u in enumerate(inputs)} for v in nodes}

def analyze(F):
    fits = {k: f for k, f in F.fits.items() if f is not None}
    nodes = sorted(k for k in fits if k[0] == 'sig')
    nsrc = lambda f: {s[:3] for s in f['coeffs'] if s[0] == 'sig'}
    preds = {v: nsrc(fits[v]) for v in nodes}
    readers = collections.defaultdict(set)
    for v in nodes:
        for s in preds[v]: readers[s].add(v)
    # zero-delay cycle check (S2)
    zero = {v: {s[:3] for s in fits[v]['coeffs'] if s[0] == 'sig' and s[3] == 0} for v in nodes}
    state = {}
    def acyc(v):
        if state.get(v) == 1: return False
        if state.get(v) == 2: return True
        state[v] = 1; ok = all(acyc(u) for u in zero[v] if u in zero); state[v] = 2; return ok
    zero_delay_ok = all(acyc(v) for v in nodes)
    # R1: SCCs (Tarjan)
    index, low, st, on, comp, cnt = {}, {}, [], set(), {}, [0]
    def dfs(v):
        index[v] = low[v] = cnt[0]; cnt[0] += 1; st.append(v); on.add(v)
        for u in preds[v]:
            if u not in index: dfs(u); low[v] = min(low[v], low[u])
            elif u in on: low[v] = min(low[v], index[u])
        if low[v] == index[v]:
            c = []
            while True:
                u = st.pop(); on.discard(u); c.append(u)
                if u == v: break
            for u in c: comp[u] = tuple(sorted(c))
    for v in nodes:
        if v not in index: dfs(v)
    sec = {}
    for v in nodes:
        if len(comp[v]) > 1 or v in preds[v]: sec[v] = comp[v][0]
    # topological order of nodes (by SCC condensation)
    order, seen = [], set()
    def topo(v):
        if v in seen: return
        seen.add(v)
        for u in preds[v]: topo(u)
        order.append(v)
    for v in nodes: topo(v)
    # R5: feed-forward node whose node sources all lie in one section joins it
    for v in order:
        if v in sec: continue
        ss = {sec.get(u) for u in preds[v]}
        if len(ss) == 1 and None not in ss: sec[v] = ss.pop()
    # R4: remaining feed-forward node read by exactly one feed-forward node joins that reader
    changed = True
    while changed:
        changed = False
        for v in order:
            if v in sec: continue
            rd = readers[v]
            if len(rd) == 1:
                (r,) = rd
                if r in sec: sec[v] = sec[r]; changed = True
    for v in nodes:
        if v not in sec: sec[v] = v
    groups = collections.defaultdict(list)
    for v in nodes: groups[sec[v]].append(v)
    outs_read = collections.defaultdict(set)
    for k, f in fits.items():
        if k[0] == 'out':
            for s in f['coeffs']:
                if s[0] == 'sig': outs_read[s[:3]].add(k)
    sections = []
    for root, mem in sorted(groups.items(), key=lambda kv: min(order.index(v) for v in kv[1])):
        ms = set(mem)
        eqs = {}; ext = []
        for v in mem:
            e = collections.defaultdict(lambda: RF([0]))
            for s, c in fits[v]['coeffs'].items():
                if s[0] == 'sig' and s[:3] in ms: e[s[:3]] = e[s[:3]] + zk(s[3], c)
                else:
                    u = s[:3]
                    if u not in ext: ext.append(u)
                    e[u] = e[u] + zk(s[3], c)
            eqs[v] = dict(e)
        H = solve(mem, eqs, ext)
        outs = [v for v in mem if (readers[v] - ms) or outs_read.get(v)]
        tfs = []
        for o in outs:
            for u in ext:
                h = H[o][u]
                if h.zero(): continue
                d = next(i for i, x in enumerate(h.n) if x != 0)
                num = h.n[d:]; den = h.d
                tfs.append({'from': str(u), 'to': str(o), 'delay': d, 'num': [str(x) for x in num], 'den': [str(x) for x in den],
                            'poles': ['%.6f%+.6fj' % (p.real, p.imag) for p in sorted(np.roots([float(x) for x in den]), key=lambda p: (round(p.real, 9), round(p.imag, 9)))] if len(den) > 1 else [],
                            'class': classify(num, den)})
        rounding = [str(v) for v in mem if fits[v]['mode'] != 'exact']
        sections.append({'nodes': [str(v) for v in mem], 'recursive': any(len(comp[v]) > 1 or v in preds[v] for v in mem),
                         'inputs': [str(u) for u in ext], 'outputs': [str(o) for o in outs], 'transfer': tfs, 'rounding_sites': rounding})
    return {'zero_delay_acyclic': zero_delay_ok, 'sections': sections}

def classify(num, den):
    """Library (fixed hierarchy, most specific first)."""
    na, nb = len(den) - 1, len(num) - 1
    if na == 0:
        if len([x for x in num if x != 0]) == 2 and num[0] == 1 and num[-1] == -1: return 'comb(N=%d)' % nb
        return 'FIR(order %d)' % nb
    if na == 1: return 'first-order IIR'
    if na == 2 and nb <= 2: return 'biquad'
    return 'IIR(%d/%d)' % (nb, na)

def execute(F, x_by_cycle, first_cycle, ncyc):
    """Run the recovered single-rate graph; returns the output ports per cycle. Signals whose write cycle is before
    first_cycle are 0 (reset state, supplied by the scoring harness)."""
    P, T0 = F.P, F.win[0] + F.origin
    fits = {k: f for k, f in F.fits.items() if f is not None}
    nodes = [k for k in fits if k[0] == 'sig']
    phase_x = {p: v[0] for p, v in F.input_phases.items()}
    val = {}
    def cyc(phi, n): return T0 + n * P + phi
    def get(src, n):
        kind, w, phi, k = src; m = n - k
        if kind == 'in': c = cyc(phi, m); return x_by_cycle[w][c] if 0 <= c < ncyc else 0
        return value((w, phi), m)
    def value(v, n):
        if (v, n) in val: return val[(v, n)]
        if cyc(v[1], n) < first_cycle: return 0
        f = fits[('sig',) + v]
        acc = sum(c * get(s, n) for s, c in f['coeffs'].items()) + f['const']
        r = (int(acc // 1) if f['mode'] != 'round' else int((acc + Fr(1, 2)) // 1)) & ((1 << f['width']) - 1)
        r = invfold.signed(r, f['width']); val[(v, n)] = r; return r
    out = collections.defaultdict(dict)
    for T in range(first_cycle, ncyc):
        phi, n = (T - T0) % P, (T - T0) // P
        for k, f in fits.items():
            if k[0] == 'out' and k[2] == phi:
                acc = sum(c * get(s, n) for s, c in f['coeffs'].items()) + f['const']
                out[k[1]][T] = int(acc // 1) & ((1 << f['width']) - 1)
    return out

def main():
    a = sys.argv[1:]; origin = 0
    if '--origin' in a: i = a.index('--origin'); origin = int(a[i + 1]); del a[i:i + 2]
    eqs, meta = json.load(open(a[0])), json.load(open(a[1]))
    F = invfold.run_big(invfold.recover, eqs, meta, origin)
    res = {'period': F.P, 'origin': origin, 'errors': F.report['errors'], 'input_phases': F.input_phases,
           'copies': F.copies, 'nodes': {str(k): fmt(f) for k, f in F.fits.items()}}
    if F.P: res.update(analyze(F))
    if len(a) > 2: json.dump(res, open(a[2], 'w'), indent=1)
    print(summary(res))
    return F, res

def fmt(f):
    if f is None: return None
    return {'width': f['width'], 'mode': f['mode'], 'const': str(f['const']), 'coeffs': {str(s): str(c) for s, c in f['coeffs'].items()}}

def summary(r):
    L = ['period %s, origin %s, errors %d' % (r['period'], r['origin'], len(r['errors']))] + ['  ERROR ' + e for e in r['errors'][:10]]
    L.append('input phases %s, zero-delay acyclic %s' % (r.get('input_phases'), r.get('zero_delay_acyclic')))
    for i, s in enumerate(r.get('sections', [])):
        L.append('section %d: %s, %d nodes, in %s, out %s, %d rounding sites' % (i, 'recursive' if s['recursive'] else 'feed-forward', len(s['nodes']), s['inputs'], s['outputs'], len(s['rounding_sites'])))
        for t in s['transfer']:
            L.append('   %s -> %s: z^-%d (%s)/(%s)  [%s] poles %s' % (t['from'], t['to'], t['delay'], ', '.join(t['num']), ', '.join(t['den']), t['class'], t['poles']))
    return '\n'.join(L)

if __name__ == '__main__': main()
