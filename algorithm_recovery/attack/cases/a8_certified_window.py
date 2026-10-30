"""A8: with a certified period and a one-period window (E4 mode: recover(..., period=P)), the 'resolves differently
in different periods' check is vacuous, so a write-enable history that is not periodic before the window (one stalled
sample, the first sample after reset) silently changes delays. Design: integrator y <- x + y written in slot 0 of every
period (P = 2), except that the write of the period just before the window is stalled (CE = 0).
Truth (periodic regime): y[n] = x[n] + y[n-1]. Recovered: y[n] = x[n] + y[n-2], no error.
The same system with a 3-period window and no certified period reports the inconsistency."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'y': 16}; IN = {'x': 16}; OUT = {'yo': 16}
STALL = 18
def step(T):
    return ({'y': '(bvadd %s %s)' % (X('x', T), R('y', T))} if T % 2 == 0 and T != STALL else {}), {'yo': R('y', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(20, 22))
F = recover(eqs, meta, period=2)
print('certified P=2, window [20,21]: errors', F.report['errors'], 'fit', {str(k): identify.fmt(f)['coeffs'] for k, f in F.fits.items() if f})
eqs2, meta2 = build(W, IN, OUT, step, 40, range(20, 26))
F2 = recover(eqs2, meta2, period=2)
print('certified P=2, window [20,25]: errors', F2.report['errors'])
