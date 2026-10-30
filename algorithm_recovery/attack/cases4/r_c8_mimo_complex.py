"""C8: frame.classify_frame (MIMO LTI branch) labels a 2x2 port system 'complex_filter' when the two diagonal
transfer functions agree, but never checks that the off-diagonal pair is (B, -B): 'check -B' is only printed.
Design (P = 1): yr <- xr + 2 xi, yi <- 3 xr + xi  (A = 1, B = 3, but the yr <- xi entry is 2, not -3): not a complex
multiplication (not even complex-linear), reported as complex_filter with real part 1 and imaginary part 3."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness4 import *
import frame
def step(T):
    return {'yr': '(bvadd %s (bvmul #x0002 %s))' % (X('xr', T), X('xi', T)), 'yi': '(bvadd (bvmul #x0003 %s) %s)' % (X('xr', T), X('xi', T))}, {'or': R('yr', T), 'oi': R('yi', T)}
eqs, meta = build({'yr': 16, 'yi': 16}, {'xr': 16, 'xi': 16}, {'or': 16, 'oi': 16}, step, 40, range(10, 40))
F = recover(eqs, meta); c = frame.classify_frame(F, [(('xr', 'xi'), ('or', 'oi'))])
print('class:', c.get('class'), '| complex_filter:', c.get('complex_filter'))
