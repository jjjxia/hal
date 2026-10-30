"""B1 check: the functions of b1_fit_quant (a) and (b) determine the coefficient: the set of c with
floor(c x) = f(x) for every x of the source's range is an interval; print it and the simplest dyadic inside.
If the fitted coefficient is outside, the fit is not 'up to an equivalence class' but wrong."""
from fractions import Fraction as Fr
import math
def interval(f, xs):
    """set of c with floor(c x) = f(x) for all x: returns (lo, lo_closed, hi, hi_closed)"""
    lo, loc, hi, hic = Fr(-10**9), True, Fr(10**9), True
    for x in xs:
        if x == 0: continue
        y = f(x)
        if x > 0: a, ac, b, bc = Fr(y, x), True, Fr(y + 1, x), False      # y/x <= c < (y+1)/x
        else: a, ac, b, bc = Fr(y + 1, x), False, Fr(y, x), True          # (y+1)/x < c <= y/x
        if a > lo or (a == lo and not ac): lo, loc = a, ac
        if b < hi or (b == hi and not bc): hi, hic = b, bc
    return lo, loc, hi, hic
def inside(c, I):
    lo, loc, hi, hic = I
    return (c > lo or (loc and c == lo)) and (c < hi or (hic and c == hi))
def simplest_dyadic(I):
    for e in range(0, 40):
        q = 1 << e
        for k in range(math.floor(I[0] * q), math.ceil(I[2] * q) + 1):
            if inside(Fr(k, q), I): return Fr(k, q)
for name, f, xs, fitted in [('(a)', lambda x: (x * 23171) >> 16, range(-32768, 32768), Fr(5793, 16384)),
                             ('(b) s alone', lambda s: (6 * s) >> 4, range(-4, 4), Fr(1, 2)),
                             ('(c) b alone, x even', lambda b: b >> 1, range(-1, 1), Fr(1))]:
    I = interval(f, xs)
    print('%-20s c in %s%s, %s%s (width %.3g); simplest dyadic %s; fitted %s inside: %s' % (name, '[' if I[1] else '(', I[0], I[2], ']' if I[3] else ')',
          float(I[2] - I[0]), simplest_dyadic(I), fitted, inside(fitted, I)))
# (c) needs the other source: floor(x/2 + c b) = floor((x + b)/2) for all 16-bit x, b in {-1, 0}
ok = [c for c in (Fr(1, 4), Fr(1, 2), Fr(3, 4), Fr(1)) if all(math.floor(Fr(x, 2) + c * b) == (x + b) >> 1 for x in range(-32768, 32768) for b in (-1, 0))]
print('(c) joint: coefficients c_b in {1/4, 1/2, 3/4, 1} that reproduce the node exactly: %s (fitted 1)' % ok)
