"""A12: a node with two rounding operations chained combinationally (two scaled multiplies in one cycle, or a scaled
product added to a scaled sum) is linear with two quantizers, not floor(sum c s + c0); the fit fails and the node is
labelled nonlinear, splitting a linear island. This is exactly what the paper's inlining remark produces when two
registered-folding nodes share a cycle. Design: P = 1, acc <- x + (((acc*3)>>2)*5)>>2 (pole 15/16 with two roundings)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'acc': 16}; IN = {'x': 16}; OUT = {'y': 16}
def sh(e, k): return '((_ extract %d %d) ((_ sign_extend %d) %s))' % (15 + k, k, k, e)
def step(T):
    inner = sh('(bvmul #x0003 %s)' % R('acc', T), 2)
    return {'acc': '(bvadd %s %s)' % (X('x', T), sh('(bvmul #x0005 %s)' % inner, 2))}, {'y': R('acc', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
F = recover(eqs, meta); r = report(F)
print('errors', r['errors'], 'nonlinear', F.report.get('nonlinear'), 'sections', len(r['sections']))
