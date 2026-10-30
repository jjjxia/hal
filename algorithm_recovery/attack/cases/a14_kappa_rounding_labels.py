"""A14: kappa contains 'rounding sites', which identify.analyze reports as node labels (word, slot); slot labels are
relabelled by the origin (Theorem 5's beta), so kappa compared literally is NOT origin-invariant (Prop. 9 holds only for
B/B(0), A, B(0)). Design: worked example with a rounded coefficient (slot 0 exact, slot 1 acc <- acc + floor(acc/2))."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'acc': 16, 's1': 16}; IN = {'x': 16}; OUT = {'y': 16}
def half(e): return '((_ extract 16 1) ((_ sign_extend 1) %s))' % e
def step(T):
    if T % 2 == 0: return {'acc': '(bvadd %s %s)' % (X('x', T), R('s1', T))}, {'y': R('acc', T)}
    return {'acc': '(bvadd %s %s)' % (R('acc', T), half(R('acc', T))), 's1': R('acc', T)}, {'y': R('acc', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
for o in range(2):
    r = report(recover(eqs, meta, origin=o))
    print('origin', o, [(s['rounding_sites'], [(t['num'], t['den']) for t in s['transfer']]) for s in r['sections']])
