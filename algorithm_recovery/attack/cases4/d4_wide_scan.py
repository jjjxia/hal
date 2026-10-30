"""D4: wide sources (13..16 bits) use probes + counterexample refinement (12 rounds x 400 single-source samples); the
fitted coefficient is then checked EXHAUSTIVELY over the source range with integer arithmetic. 'wrong' = mode
floor/exact but floor(c' x + r') != floor(c x) for some x (silent); also counted: quant and nonlinear (misses)."""
import sys, os, random, math
from fractions import Fraction as Fr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot4'))
import invfold
seed = int(sys.argv[1]) if len(sys.argv) > 1 else 5; n = int(sys.argv[2]) if len(sys.argv) > 2 else 120
rng = random.Random(seed); st = {'ok': 0, 'wrong': 0, 'quant': 0, 'none': 0}; ex = []
for it in range(n):
    wa = rng.randint(13, 16); b = rng.randint(wa + 1, 2 * wa); p = rng.randrange(1, 1 << b) | 1
    c = Fr(p, 1 << b) * rng.choice([1, -1]); w = 32; src = ('in', 'x', 0, 0)
    run = lambda vals, c=c: math.floor(c * vals[src]) & ((1 << w) - 1)
    f = invfold.fit_floor_linear(run, [src], {src: wa}, w, random.Random(it))
    if f is None: st['none'] += 1; continue
    if f['mode'] == 'quant': st['quant'] += 1; continue
    c2 = f['coeffs'].get(src, Fr(0)); r2 = f['const']
    D = max(c.denominator, c2.denominator, r2.denominator)
    P1, P2, R2 = int(c * D), int(c2 * D), int(r2 * D)
    bad = [x for x in range(-(1 << (wa - 1)), 1 << (wa - 1)) if (P1 * x) // D != (P2 * x + R2) // D]
    if bad:
        st['wrong'] += 1
        if len(ex) < 4: ex.append((wa, str(c), str(c2), str(r2), len(bad), bad[:3]))
    else: st['ok'] += 1
print(st)
for e in ex: print('WRONG %d-bit source, true c %s, fitted %s + %s: differs on %d values, e.g. %s' % e)
# realistic: 16-bit samples times Q23 coefficients (24-bit coefficient words), y = (x * C) >> 23
rng = random.Random(seed + 100); st2 = {'ok': 0, 'wrong': 0, 'quant': 0, 'none': 0}
for it in range(40):
    C = rng.randrange(1, 1 << 23) * rng.choice([1, -1]); c = Fr(C, 1 << 23); src = ('in', 'x', 0, 0); wa = 16
    run = lambda vals, c=c: math.floor(c * vals[src]) & ((1 << 32) - 1)
    f = invfold.fit_floor_linear(run, [src], {src: wa}, 32, random.Random(it))
    if f is None: st2['none'] += 1; continue
    if f['mode'] == 'quant': st2['quant'] += 1; continue
    c2, r2 = f['coeffs'].get(src, Fr(0)), f['const']; D = max(c.denominator, c2.denominator, r2.denominator)
    bad = sum(1 for x in range(-32768, 32768) if (int(c * D) * x) // D != (int(c2 * D) * x + int(r2 * D)) // D)
    st2['wrong' if bad else 'ok'] += 1
print('16-bit x times Q23 coefficient:', st2)
