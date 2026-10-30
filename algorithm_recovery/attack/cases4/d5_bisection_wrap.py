"""D5: class_by_bisection (9..12-bit sources) when the output wraps inside the enumerated range (output narrower than
c * range): y8 = (3 x) mod 256, y8 = floor(5x/8) mod 256, y10 = floor(1.25 x) mod 1024 with a 12-bit x; and a tie case
(the feasible interval has a dyadic endpoint). Each result is checked exhaustively modulo 2^w."""
import sys, os, random, math
from fractions import Fraction as Fr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot4'))
import invfold
src = ('in', 'x', 0, 0)
for wa, c, w in ((10, Fr(3), 8), (10, Fr(5, 8), 8), (12, Fr(5, 4), 10), (11, Fr(1, 2), 16), (12, Fr(4095, 4096), 12)):
    run = lambda vals, c=c, w=w: math.floor(c * vals[src]) & ((1 << w) - 1)
    f = invfold.fit_floor_linear(run, [src], {src: wa}, w, random.Random(1))
    if f is None: print('%2d-bit x, y%d = floor(%s x): NONLINEAR' % (wa, w, c)); continue
    c2, r2 = f['coeffs'].get(src, Fr(0)), f['const']
    bad = sum(1 for x in range(-(1 << (wa - 1)), 1 << (wa - 1)) if (math.floor(c2 * x + r2) - math.floor(c * x)) % (1 << w) != 0)
    print('%2d-bit x, y%d = floor(%s x): %s c=%s r=%s, wrong on %d values' % (wa, w, c, f['mode'], c2, r2, bad))
