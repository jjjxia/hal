"""A6 (survival test): Theorem 5 / Proposition 9 (kappa and partition independent of the origin) and Theorem 6
(period multiple = unfolding) on the implementation. Design: worked example extended with a feed-forward output stage
and idle slots, P = 5. For every origin 0..P-1: partition, kappa per transfer function, loop delays. Then forced
period 2P (certified) vs the 2-unfolding."""
import sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'acc': 16, 's1': 16, 's2': 16, 'o': 16}; IN = {'x': 16}; OUT = {'y': 16}
def step(T):
    p = T % 5; wr = {}
    if p == 0: wr['acc'] = '(bvadd %s (bvmul #x0003 %s))' % (X('x', T), R('s1', T)); wr['s2'] = R('acc', T)
    if p == 2: wr['acc'] = '(bvadd %s (bvmul #x0005 %s))' % (R('acc', T), R('s2', T)); wr['s1'] = R('acc', T)
    if p == 3: wr['o'] = '(bvsub %s (bvshl %s #x0001))' % (R('acc', T), R('o', T) if False else R('s1', T))
    return wr, {'y': R('o', T)}
eqs, meta = build(W, IN, OUT, step, 80, range(20, 80))
kap = {}
for o in range(5):
    F = recover(eqs, meta, origin=o); rep = report(F)
    k = sorted((len(s['nodes']), s['recursive'], tuple((tuple(t['num']), tuple(t['den'])) for t in s['transfer'])) for s in rep['sections'])
    d = [(t['from'], t['to'], t['delay']) for s in rep['sections'] for t in s['transfer']]
    kap[o] = k; print('origin', o, 'errors', len(rep['errors']), 'kappa', k, 'delays', d)
print('kappa identical over origins:', len({str(v) for v in kap.values()}) == 1)
F2 = recover(eqs, meta, origin=0, period=10); rep2 = report(F2)
print('forced period 10: errors', rep2['errors'], 'nodes', sorted(F2.nodes), 'input phases', F2.input_phases)
