"""A2: copy collapse on a cycle of copies never terminates.
A register held by a feedback mux (CE tied 1, D = Q), e.g. a coefficient/config register loaded before the window, is
written in every slot with E = q_{r,T}. Every slot node is an exact copy of the previous slot's node, the copies form a
cycle (total delay 1 per period), and collapse_copies.follow() loops forever. Theory: Corollary 8's norm() and the
paper's copy collapse are undefined on copy cycles (the algorithm graph y[n] = y[n-1] is causal and valid).
Design: P = 2. coef register held (D = Q every cycle); acc <- x + coef*acc in slot 0, acc <- acc in slot 1 (mux hold)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'acc': 16, 'k': 16}; IN = {'x': 16}; OUT = {'y': 16}
def step(T):
    wr = {'k': R('k', T)}   # hold through the mux, CE = 1
    wr['acc'] = '(bvadd %s (bvmul %s %s))' % (X('x', T), R('k', T), R('acc', T)) if T % 2 == 0 else R('acc', T)
    return wr, {'y': R('acc', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
try:
    F = with_timeout(20, recover, eqs, meta)
    print('returned', F.P, F.report)
except Timeout:
    print('HANG: recover() did not return within 20 s (collapse_copies.follow loops on the copy cycle k0 -> k1 -> k0)')

# Variant: rotating coefficient ring of a folded FIR (c0 <- c1 <- c2 <- c0 every cycle, CE = 1): a copy cycle across
# registers. P = 3; acc <- x*c0 (slot 0), acc <- acc + d*c0 (slots 1, 2), d <- x (slot 0), y <- acc (slot 0).
W = {'c0': 16, 'c1': 16, 'c2': 16, 'acc': 16, 'd': 16, 'y': 16}; IN = {'x': 16}; OUT = {'yo': 16}
def step2(T):
    p = T % 3
    wr = {'c0': R('c1', T), 'c1': R('c2', T), 'c2': R('c0', T)}
    if p == 0: wr.update({'acc': '(bvmul %s %s)' % (X('x', T), R('c0', T)), 'd': X('x', T), 'y': R('acc', T)})
    else: wr['acc'] = '(bvadd %s (bvmul %s %s))' % (R('acc', T), R('d', T), R('c0', T))
    return wr, {'yo': R('y', T)}
eqs, meta = build(W, IN, OUT, step2, 40, range(10, 40))
try:
    F = with_timeout(20, recover, eqs, meta)
    print('ring: returned', F.P, F.report)
except Timeout:
    print('ring: HANG: recover() did not return within 20 s (copy cycle c0 -> c1 -> c2 -> c0)')
