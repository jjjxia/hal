"""D3: the new quant guard requires the error to be as large in the centre (all sources within half their range, linear
part within a quarter of its span) as on the full range. That rejects saturation (errors at the extremes) but accepts
nonlinearities whose errors sit IN the centre: a dead zone / coring stage y = (|x| < 7) ? 0 : x >> 1 (10-bit x,
max error 3 = the bound for one source) is reported as a linear 'quant' node, c = 1/2.
Also a 'small-signal' clipper is the same shape: y = x >> 1 except a flat step at 0."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness5 import *
def step(T):
    x = X('x', T); half = '((_ extract 9 1) ((_ sign_extend 1) %s))' % x
    small = '(and (bvslt %s #b0000000111) (bvsgt %s #b1111111001))' % (x, x)   # -7 < x < 7
    return {'y': '(ite %s #b000000000 %s)' % (small, half)}, {'yo': R('y', T)}
eqs, meta = build({'y': 9}, {'x': 10}, {'yo': 9}, step, 40, range(10, 40))
F = recover(eqs, meta); f = F.fits.get(('sig', 'y', 0))
print('dead zone (|x| < 7 -> 0, else x >> 1):', None if f is None else (f['mode'], f.get('quant_bound'), {str(k): str(v) for k, v in f['coeffs'].items()}))
