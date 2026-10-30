"""X1 attack: "a constant H (no inter-frame memory) is a block transform" is not invariant under the origin (Thm 5).
2-point butterfly, P = 4: slot 0: c <- x; slot 1: u <- c + x, v <- c - x (x read in phases 0 and 1); output ports read
u and v. Frame-level H(z) from the recovered graph (the snapshot builds it despite the multi-phase error), for every
origin: H is constant only for origins at which both input phases fall in the same sample as the outputs."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'c': 16, 'u': 16, 'v': 16}; IN = {'x': 16}; OUT = {'yu': 16, 'yv': 16}
def step(T):
    p = T % 4; wr = {}
    if p == 0: wr['c'] = X('x', T)
    if p == 1: wr['u'] = '(bvadd %s %s)' % (R('c', T), X('x', T)); wr['v'] = '(bvsub %s %s)' % (R('c', T), X('x', T))
    return wr, {'yu': R('u', T), 'yv': R('v', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(8, 40))
def frameH(F):
    fits = {k: f for k, f in F.fits.items() if f is not None and k[0] == 'sig'}
    nodes = sorted(k[1:] for k in fits); ins = sorted({s[:3] for f in fits.values() for s in f['coeffs'] if s[0] == 'in'})
    eqsys = {}
    for k, f in fits.items():
        e = collections.defaultdict(lambda: identify.RF([0]))
        for s, cf in f['coeffs'].items():
            key = s[1:3] if s[0] == 'sig' else s[:3]
            e[key] = e[key] + identify.zk(s[3], cf)
        eqsys[k[1:]] = dict(e)
    H = identify.solve(nodes, eqsys, ins)
    show = lambda r: pretty(r)
    return {(o, i): show(H[o][i]) for o in nodes if o[0] in 'uv' for i in ins}
import collections
def poly(p):
    t = ['%s%s' % (c, '' if i == 0 else 'z^-%d' % i) for i, c in enumerate(p) if c != 0]
    return ' + '.join(t) or '0'
def pretty(r): return poly(r.n) if r.d == [1] else '(%s)/(%s)' % (poly(r.n), poly(r.d))
for o in range(4):
    F = recover(eqs, meta, origin=o)
    H = frameH(F)
    const = all(('[' + 'Fraction(0, 1), ' not in h) for h in H.values())
    print('origin', o, {('%s%d' % (k[0][0], k[0][1]), 'x%d' % k[1][2]): v for k, v in H.items()})
