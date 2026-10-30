"""A11: identify.analyze's R4 joins a feed-forward node only to a reader that ALREADY has a section, so in a graph
without recursive sections nothing ever merges: a folded FIR comes out as one single-node 'FIR(order 0)' section per
accumulator slot, and the FIR's transfer function is never computed. Paper R4 (a feed-forward node read by exactly one
feed-forward node joins its reader) gives one section {acc0..acc3} with H = 3 + 5z^-1 + 7z^-2 + 9z^-4 from x0 (d3 is read before its slot-0 shift).
Design: folded 4-tap FIR, P = 4, delay line d1..d3 shifted in slot 0, one MAC accumulator, x read in slot 0 only."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'acc': 16, 'd1': 16, 'd2': 16, 'd3': 16, 'y': 16, 'x0': 16}; IN = {'x': 16}; OUT = {'yo': 16}
def step(T):
    p = T % 4
    if p == 0: wr = {'x0': X('x', T), 'd1': R('x0', T), 'd2': R('d1', T), 'd3': R('d2', T), 'acc': '(bvmul #x0009 %s)' % R('d3', T), 'y': R('acc', T)}
    elif p == 1: wr = {'acc': '(bvadd %s (bvmul #x0007 %s))' % (R('acc', T), R('d2', T))}
    elif p == 2: wr = {'acc': '(bvadd %s (bvmul #x0005 %s))' % (R('acc', T), R('d1', T))}
    else: wr = {'acc': '(bvadd %s (bvmul #x0003 %s))' % (R('acc', T), R('x0', T))}
    return wr, {'yo': R('y', T)}
eqs, meta = build(W, IN, OUT, step, 60, range(20, 60))
F = recover(eqs, meta); print(identify.summary(report(F)))
