"""A4: section rules in identify.analyze vs the paper, and lost section outputs.
(a) R4 as implemented joins a feed-forward node to ANY single reader that already has a section (also a recursive
    one); the paper's R4 joins only when the reader is feed-forward. FIR stage f = 2x + c[n-1] (c = captured x) read by
    a first-order IIR a = f + a[n-1]/2: paper -> sections {f} (FIR) and {a} (IIR); code -> one section with
    (2 + z^-1)/(1 - z^-1/2), classified 'first-order IIR'.
(b) A linear section whose nodes are read only by a nonlinear node (output saturation, comparator) has no outputs:
    `readers` is built from fitted nodes only, so the section reports out [] and no transfer function at all.
(c) Input capture registers (r <- x) are not collapsed (collapse requires a 'sig' source; the paper's copy definition
    includes inputs), so every registered input becomes an extra 'FIR(order 0)' section."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *

def sat16(e):   # signed saturation of a 17-bit value to 16 bits: nonlinear
    return '(ite (= ((_ extract 16 16) %s) ((_ extract 15 15) %s)) ((_ extract 15 0) %s) (ite (= ((_ extract 16 16) %s) #b1) #x8000 #x7fff))' % (e, e, e, e)
W = {'cx': 16, 'f': 16, 'a': 16, 's': 16}; IN = {'x': 16}; OUT = {'y': 16}
def step(T):
    if T % 2 == 0:
        wr = {'cx': X('x', T), 'f': '(bvadd (bvshl %s #x0001) %s)' % (X('x', T), R('cx', T))}
    else:
        half = '((_ extract 16 1) ((_ sign_extend 1) %s))' % R('a', T)
        wr = {'a': '(bvadd %s %s)' % (R('f', T), half),
              's': sat16('(bvadd ((_ sign_extend 1) %s) ((_ sign_extend 1) %s))' % (R('a', T), R('a', T)))}
    return wr, {'y': R('s', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
F = recover(eqs, meta); rep = report(F)
print(identify.summary(rep)); print('nonlinear:', F.report.get('nonlinear'))
for k, f in F.fits.items(): print('  ', k, identify.fmt(f))
