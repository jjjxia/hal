"""C4: edge cases of the new fit (snapshot3).
(i) hidden saturation: y = sext(sat8(a + (b >>> 6))) + e (10-bit y, 8-bit a, b, e). The saturation error depends on a,
    not on the size of the linear part a + b/64 + e, so it occurs in the 'central' range too; the quant guard
    (central error == full error) accepts it as a linear node with bounded error.
(ii) four 1-bit sources: y = (x + b1 + b2 + b3 + b4) >> 1. Exactly floor-linear (all coefficients 1/2), but the joint
    search for narrow sources only runs for <= 3 of them.
(iii) mixed signedness (b6 design): the low lane a = x of the concatenation {b, a} is fitted as UNSIGNED, while b = a+1
    is signed; identify.execute and the transfer functions treat every source as signed."""
import sys, os, random, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness4 import *
def sx(e, k): return '((_ sign_extend %d) %s)' % (k, e)
def sat8(e9):
    return '(ite (= ((_ extract 8 8) %s) ((_ extract 7 7) %s)) ((_ extract 7 0) %s) (ite (= ((_ extract 8 8) %s) #b1) #x80 #x7f))' % (e9, e9, e9, e9)
def step_i(T):
    s = '(bvadd %s ((_ sign_extend 7) ((_ extract 7 6) %s)))' % (sx(X('a', T), 1), X('b', T))
    return {'y': '(bvadd %s %s)' % (sx(sat8(s), 2), sx(X('e', T), 2))}, {'yo': R('y', T)}
eqs, meta = build({'y': 10}, {'a': 8, 'b': 8, 'e': 8}, {'yo': 10}, step_i, 40, range(10, 40))
F = recover(eqs, meta); f = F.fits.get(('sig', 'y', 0))
print('(i)  y = sat8(a + b>>>6) + e:', None if f is None else (f['mode'], f.get('quant_bound'), {str(k): str(v) for k, v in f['coeffs'].items()}))
def step_ii(T):
    s = sx(X('x', T), 1)
    for i in range(4): s = '(bvadd %s %s)' % (s, sx(X('b%d' % i, T), 16))
    return {'y': '((_ extract 16 1) %s)' % s}, {'yo': R('y', T)}
IN = {'x': 16, 'b0': 1, 'b1': 1, 'b2': 1, 'b3': 1}
eqs, meta = build({'y': 16}, IN, {'yo': 16}, step_ii, 40, range(10, 40))
F = recover(eqs, meta); f = F.fits.get(('sig', 'y', 0))
print('(ii) y = (x + b1 + b2 + b3 + b4) >> 1:', None if f is None else (f['mode'], f.get('quant_bound'), {str(k): str(v) for k, v in f['coeffs'].items()}, f.get('coeff_class')))
rng = random.Random(2); xs = {p: [rng.getrandbits(w) if t >= 4 else 0 for t in range(40)] for p, w in IN.items()}
bad, n = check_execute(F, {'y': 16}, IN, {'yo': 16}, step_ii, 40, xs, {'y': 0}, 4)
print('     identify.execute vs hardware: %d/%d mismatches' % (len(bad), n))
# (iii)
W = {'a': 8, 'b': 8, 'c': 16}; IN3 = {'x': 8}; OUT = {'yo': 16}
def step3(T):
    if T % 2 == 0: return {'a': X('x', T)}, {'yo': R('c', T)}
    return {'b': '(bvadd %s #x01)' % R('a', T), 'c': '(bvadd top_module_pq_%d #x0003)' % T}, {'yo': R('c', T)}
eqs, meta = build(W, IN3, OUT, step3, 60, range(10, 60))
meta['alias'] = {'top_module_pq': [('a', i) for i in range(8)] + [('b', i) for i in range(8)]}
F = recover(eqs, meta); f = F.fits.get(('sig', 'c', 1))
print('(iii) fit of c:', {str(k): str(v) for k, v in f['coeffs'].items()}, 'unsigned', f.get('unsigned_sources'))
# hardware: simulate with the alias = {b, a}
def step3_hw(T):
    if T % 2 == 0: return {'a': X('x', T)}, {'yo': R('c', T)}
    return {'b': '(bvadd %s #x01)' % R('a', T), 'c': '(bvadd (concat %s %s) #x0003)' % (R('b', T), R('a', T))}, {'yo': R('c', T)}
xs = {'x': [rng.getrandbits(8) if t >= 4 else 0 for t in range(60)]}
bad, n = check_execute(F, W, IN3, OUT, step3_hw, 60, xs, {r: 0 for r in W}, 4)
late = [b for b in bad if b[1] >= 12]
print('     identify.execute vs hardware after warm-up (T >= 12): %d mismatches, e.g. %s; all with x(T-1) < 0 (low lane read as signed): %s' % (len(late), late[:3], all(invfold.signed(xs['x'][b[1] - 1], 8) < 0 or invfold.signed(xs['x'][b[1] - 2], 8) < 0 for b in late)))
