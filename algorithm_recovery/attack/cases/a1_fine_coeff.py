"""A1: node fit cannot represent a coefficient with more than 8 fractional bits.
fit_floor_linear rounds each least-squares coefficient to the FIRST dyadic g = round(c 2^e)/2^e (e = 0..16) with
|g - c| < 2e-3; at e = 8 the rounding error is at most 2^-9 < 2e-3, so every coefficient is truncated to <= 8 fractional
bits, the full-range verification then fails, and a plainly linear node is reported as nonlinear (no error).
Designs (P = 1, 16-bit): (a) Q15 gain 1/sqrt2 = 23170/2^15; (b) DC blocker pole 0.999 = 32735/2^15;
(c) Q1.10 coefficient 0.7070 = 724/1024 (E1's own format); (d) control: coefficient 181/256 (8 fractional bits)."""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *

def mul_q(src, coef, frac, w=16):
    # ((sext src) * coef) >> frac, back to w bits
    return '((_ extract %d %d) (bvmul ((_ sign_extend %d) %s) %s))' % (frac + w - 1, frac, w, src, c(coef, 2 * w))

def design(coef, frac, recursive):
    W = {'acc': 16}; IN = {'x': 16}; OUT = {'y': 16}
    def step(T):
        if recursive: e = '(bvadd %s %s)' % (X('x', T), mul_q(R('acc', T), coef, frac))
        else: e = mul_q(X('x', T), coef, frac)
        return {'acc': e}, {'y': R('acc', T)}
    return W, IN, OUT, step

for name, coef, frac, rec in [('gain Q15 23170/2^15 (1/sqrt2)', 23170, 15, False), ('DC-blocker pole Q15 32735/2^15', 32735, 15, True),
                              ('Q1.10 723/1024', 723, 10, False), ('Q1.12 2897/4096 (0.7073)', 2897, 12, False), ('Q1.10 pole 1023/1024 (leaky integrator)', 1023, 10, True),
                              ('control 181/256', 181, 8, False), ('control pole 255/256', 255, 8, True)]:
    W, IN, OUT, step = design(coef, frac, rec)
    eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
    F = recover(eqs, meta)
    rep = report(F)
    fit = F.fits.get(('sig', 'acc', 0))
    rng = random.Random(5); xs = {'x': [rng.getrandbits(16) for _ in range(40)]}
    bad, n = check_run_graph(F, W, IN, OUT, step, 40, xs, {'acc': 0})
    print('%-42s errors=%d nonlinear=%s fit=%s  run_graph mismatches %d/%d' % (name, len(rep['errors']), F.report.get('nonlinear'), identify.fmt(fit), len(bad), n))

# plain bit slices: y = acc32[31:k] (an accumulator output stage), coefficient 2^-k
for k in (4, 8, 9, 12, 16):
    W = {'acc': 32, 'y': 32 - k}; IN = {'x': 32}; OUT = {'yo': 32 - k}
    def step(T, k=k): return {'acc': '(bvadd %s %s)' % (X('x', T), R('acc', T)), 'y': '((_ extract 31 %d) %s)' % (k, R('acc', T))}, {'yo': R('y', T)}
    eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
    F = recover(eqs, meta)
    print('slice acc[31:%d] (coefficient 2^-%d): fit %s, nonlinear %s' % (k, k, identify.fmt(F.fits.get(('sig', 'y', 0))), F.report.get('nonlinear')))
