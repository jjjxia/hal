"""A7: Corollary 8's closing claim "P* is the most folded algorithm consistent with the equations, every other
explanation is an unfolding of it" is false once the register binding may vary between samples (ping-pong / double
buffering / register rotation / circular buffers, i.e. the binding is periodic with period 2N instead of fixed).
Design: y[n] = x[n] + x[n-1], one sample per cycle (N = 1), registers A and B capture x alternately:
  even cycles: A <- x, Y <- x + B;   odd cycles: B <- x, Y <- x + A.
The algorithm is 1-folded (one adder, one sample per cycle), but the equations have least period P* = 2, so
IF_{P*} is the 2-unfolding of the algorithm (two adders, two input phases). The implementation reports the input as
multi-rate. The pseudo-circulant test of the planned X1 does re-roll this example (H = [[1, z^-1],[1, 1]], E0 = E1 = 1)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'a': 16, 'b': 16, 'y': 16}; IN = {'x': 16}; OUT = {'yo': 16}
def step(T):
    if T % 2 == 0: wr = {'a': X('x', T), 'y': '(bvadd %s %s)' % (X('x', T), R('b', T))}
    else: wr = {'b': X('x', T), 'y': '(bvadd %s %s)' % (X('x', T), R('a', T))}
    return wr, {'yo': R('y', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
F = recover(eqs, meta); rep = report(F)
print(identify.summary(rep))
for k, f in F.fits.items(): print('  ', k, identify.fmt(f))
