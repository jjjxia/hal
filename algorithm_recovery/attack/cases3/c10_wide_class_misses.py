"""C10: consequence of C3 in a realistic node: a 10-bit sample times a Q15 coefficient with a 2^-16 output scaling,
y = (x * 23170) >> 16 (10-bit x, the coefficient has 16 fractional bits after the shift). Exactly floor-linear; the
probe class for sources of more than 8 bits is wider than the true class and the fit picks a simpler dyadic that the
3000-sample check refutes. Reported: the fitted result (expected: floor, c = 11585/32768 or any member of its class)."""
import sys, os, random, math
from fractions import Fraction as Fr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot3'))
import invfold
src = ('in', 'x', 0, 0)
for wa, C, sh in ((10, 23170, 16), (9, 1625, 15), (11, 30274, 17), (12, 12540, 18)):
    c = Fr(C, 1 << sh)
    run = lambda vals, c=c: math.floor(c * vals[src]) & ((1 << 24) - 1)
    f = invfold.fit_floor_linear(run, [src], {src: wa}, 24, random.Random(1))
    print('%2d-bit x, y = (x*%d) >> %d: %s' % (wa, C, sh, 'NONLINEAR' if f is None else '%s c=%s%s' % (f['mode'], f['coeffs'].get(src), ' (quant, bound %s)' % f['quant_bound'] if f['mode'] == 'quant' else '')))
