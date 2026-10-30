"""C3: scan the new exact-class fit (snapshot3 invfold.fit_floor_linear) on single-source nodes y = floor(c x) for
random dyadic c with more fractional bits than the source (b = 10..20 bits, source width 9..16). For each fitted
coefficient c' check EVERY source value: c' is wrong if floor(c' x + r') != floor(c x) for some x. A wrong c' with
mode 'floor'/'exact' and no error is a silent wrong fit (the 3000-sample verification missed the differing points)."""
import sys, os, random, math
from fractions import Fraction as Fr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot3'))
import invfold
rng = random.Random(int(sys.argv[1]) if len(sys.argv) > 1 else 3)
stats = {'ok': 0, 'wrong_silent': 0, 'quant': 0, 'none': 0}; ex = []
for it in range(int(sys.argv[2]) if len(sys.argv) > 2 else 300):
    wa = rng.randint(9, 16); b = rng.randint(wa, wa + 8); p = rng.randrange(1, 1 << b) | 1
    c = Fr(p, 1 << b) * rng.choice([1, -1]); w = 24
    src = ('in', 'x', 0, 0)
    run = lambda vals, c=c: math.floor(c * vals[src]) & ((1 << w) - 1)
    f = invfold.fit_floor_linear(run, [src], {src: wa}, w, random.Random(it))
    if f is None: stats['none'] += 1; continue
    if f['mode'] == 'quant': stats['quant'] += 1; continue
    c2 = f['coeffs'].get(src, Fr(0)); r2 = f['const']
    bad = [x for x in range(-(1 << (wa - 1)), 1 << (wa - 1)) if math.floor(c2 * x + r2) != math.floor(c * x)]
    if bad:
        stats['wrong_silent'] += 1
        if len(ex) < 5: ex.append((wa, str(c), str(c2), str(r2), f['mode'], len(bad), bad[:4]))
    else: stats['ok'] += 1
print(stats)
for e in ex: print('WRONG source width %d true c %s fitted c %s const %s mode %s: differs on %d of the source values, e.g. %s' % e)
# examples of the misses: exact floor nodes reported as quant or nonlinear
rng = random.Random(7); shown = 0
for it in range(400):
    wa = rng.randint(9, 16); b = rng.randint(wa, wa + 8); p = rng.randrange(1, 1 << b) | 1
    c = Fr(p, 1 << b) * rng.choice([1, -1]); w = 24; src = ('in', 'x', 0, 0)
    run = lambda vals, c=c: math.floor(c * vals[src]) & ((1 << w) - 1)
    f = invfold.fit_floor_linear(run, [src], {src: wa}, w, random.Random(it))
    if f is None or f['mode'] == 'quant':
        print('MISS source width %d, y = floor(%s x) (%d fractional bits): %s' % (wa, c, b, 'nonlinear' if f is None else 'quant c=%s bound %s class %s' % (f['coeffs'].get(src), f['quant_bound'], f.get('coeff_class'))))
        shown += 1
        if shown >= 6: break
# realistic: 12-bit data (ADC) times Q15 coefficients, y = (x * C) >> 15
print('12-bit data x Q15 coefficient, y = (x*C) >> 15:')
for C in (23170, 30274, 12540, 32735, 16384 + 3, 1):
    c = Fr(C, 1 << 15); src = ('in', 'x', 0, 0)
    run = lambda vals, c=c: math.floor(c * vals[src]) & ((1 << 24) - 1)
    f = invfold.fit_floor_linear(run, [src], {src: 12}, 24, random.Random(C))
    print('   C = %5d: %s' % (C, 'nonlinear' if f is None else '%s c = %s%s' % (f['mode'], f['coeffs'].get(src), ' bound %s' % f.get('quant_bound') if f['mode'] == 'quant' else '')))
print('exhaustive check of the 12-bit fits:')
for C in (23170, 30274, 12540, 32735, 16387, 1):
    c = Fr(C, 1 << 15); src = ('in', 'x', 0, 0)
    run = lambda vals, c=c: math.floor(c * vals[src]) & ((1 << 24) - 1)
    f = invfold.fit_floor_linear(run, [src], {src: 12}, 24, random.Random(C))
    if f is None or f['mode'] == 'quant': continue
    c2, r2 = f['coeffs'].get(src, Fr(0)), f['const']
    bad = [x for x in range(-2048, 2048) if math.floor(c2 * x + r2) != math.floor(c * x)]
    print('   C = %5d fitted %s + %s: wrong on %d of 4096 values %s' % (C, c2, r2, len(bad), bad[:5]))
