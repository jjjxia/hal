"""A10: idle-slot writes (the datapath keeps computing with default mux selects when a slot is unused) are neither
algorithm nodes nor moves, so Theorem 7's registered-folding model excludes them, and IF contains dead nodes.
If the default operand is the input port, the dead node reads x in an extra phase:
 - the snapshot reports 'input x read in 2 phases (multi-rate input?)' for a single-rate design (error, wrong diagnosis);
 - under X1 (frame semantics), x becomes a 2-vector input with an all-zero column for every live output, so
   M_in = 2 != M_out = 1 and the pseudo-circulant test is never applied: a plain FIR is reported as a frame-level MIMO.
Design (P = 3): slot 0: acc <- 3x, d <- x; slot 1: acc <- acc + 5d; slot 2: acc <- acc + 7x (idle slot, dead: acc is
overwritten in slot 0 without being read), y <- acc. Algorithm: y[n] = 3x[n] + 5x[n-1]."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'acc': 16, 'd': 16, 'y': 16}; IN = {'x': 16}; OUT = {'yo': 16}
def step(T):
    p = T % 3
    if p == 0: wr = {'acc': '(bvmul #x0003 %s)' % X('x', T), 'd': X('x', T)}
    elif p == 1: wr = {'acc': '(bvadd %s (bvmul #x0005 %s))' % (R('acc', T), R('d', T))}
    else: wr = {'acc': '(bvadd %s (bvmul #x0007 %s))' % (R('acc', T), X('x', T)), 'y': R('acc', T)}
    return wr, {'yo': R('y', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
F = recover(eqs, meta); print(identify.summary(report(F)))
