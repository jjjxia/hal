"""B1: the new fit's 'quant' fallback accepts WRONG coefficients (and saturation) as 'linear part exact, bounded
error'. Candidates come only from power-of-two probes and -2^(w-1); when none reproduces the single-source map, the
fallback picks the best small-value candidate and the full-range check then only requires |error| <= #sources + 2.
Cases (16-bit x unless noted); 'want' is the true coefficient, the function determines it uniquely on the domain:
 (a) top half of a 16x16 product with an odd coefficient: y = (x * 23171) >> 16           want 23171/65536
 (b) a 3-bit source with coefficient 3/8 next to a 16-bit source: y = (x*16 + s*6) >> 4    want s: 3/8
 (c) 1-bit sources: y = (x + b) >> 1 with b a 1-bit signal                                 want b: 1/2
 (d) offsets other than 0, 1/2: y = (x + 1) >> 2 (round half down), y = (x + 3) >> 2 (ceil): exact floor forms with
     const 1/4 and 3/4 (the previous fit found them); now 'quant', and identify.execute() mismatches the hardware
 (e) saturation with small overshoot: y = sat8(a + (b >>> 6)), 8-bit a, b: clips, reported linear 'quant'"""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness2 import *

def sx(e, k): return '((_ sign_extend %d) %s)' % (k, e)
def run_case(name, W, IN, step, want, src_key):
    eqs, meta = build(W, IN, {'yo': 16}, lambda T: (step(T), {'yo': R('y', T)}), 40, range(10, 40))
    F = recover(eqs, meta); f = F.fits.get(('sig', 'y', 0))
    got = None if f is None else {str(k): str(v) for k, v in f['coeffs'].items()}
    rng = random.Random(4); xs = {p: [rng.getrandbits(w) if t >= 4 else 0 for t in range(40)] for p, w in IN.items()}
    try: bad, n = check_execute(F, W, IN, {'yo': 16}, lambda T: (step(T), {'yo': R('y', T)}), 40, xs, {r: 0 for r in W}, 4)
    except Exception as e: bad, n = ['execute failed: %r' % e], 0
    print('%-34s mode=%s bound=%s coeffs=%s want %s=%s | execute mismatches %d/%d' % (name, f and f['mode'], f and f.get('quant_bound'), got, src_key, want, len(bad), n))

# (a)
run_case('(a) (x*23171)>>16', {'y': 16}, {'x': 16}, lambda T: {'y': '((_ extract 31 16) (bvmul %s #x00005a83))' % sx(X('x', T), 16)}, '23171/65536', 'x')
# (b) s is a 3-bit input port
run_case('(b) (16x + 6s)>>4, s 3-bit', {'y': 16}, {'x': 16, 's': 3},
         lambda T: {'y': '((_ extract 19 4) (bvadd (bvmul %s #x00010) (bvmul %s #x00006)))' % (sx(X('x', T), 4), sx(X('s', T), 17))}, '3/8', 's')
# (c) b is a 1-bit input port
run_case('(c) (x + b)>>1, b 1-bit', {'y': 16}, {'x': 16, 'b': 1},
         lambda T: {'y': '((_ extract 16 1) (bvadd %s %s))' % (sx(X('x', T), 1), sx(X('b', T), 16))}, '1/2', 'b')
# (d)
run_case('(d1) (x + 1)>>2', {'y': 16}, {'x': 16}, lambda T: {'y': '((_ extract 17 2) (bvadd %s #b000000000000000001))' % sx(X('x', T), 2)}, '1/4 + const 1/4', 'x')
run_case('(d2) (x + 3)>>2', {'y': 16}, {'x': 16}, lambda T: {'y': '((_ extract 17 2) (bvadd %s #b000000000000000011))' % sx(X('x', T), 2)}, '1/4 + const 3/4', 'x')
# (e) 8-bit saturating add of a and b>>>6 (b>>>6 in {-2..1})
def sat8(e9):
    return '(ite (= ((_ extract 8 8) %s) ((_ extract 7 7) %s)) ((_ extract 7 0) %s) (ite (= ((_ extract 8 8) %s) #b1) #x80 #x7f))' % (e9, e9, e9, e9)
def step_e(T):
    s = '(bvadd %s ((_ sign_extend 7) ((_ extract 7 6) %s)))' % (sx(X('a', T), 1), X('b', T))
    return {'y': '((_ sign_extend 8) %s)' % sat8(s)}
run_case('(e) sat8(a + (b>>>6))', {'y': 16}, {'a': 8, 'b': 8}, step_e, 'nonlinear (saturates)', '-')
# (e') same, 8-bit register (no sign extension after the saturation)
def step_e2(T):
    s = '(bvadd %s ((_ sign_extend 7) ((_ extract 7 6) %s)))' % (sx(X('a', T), 1), X('b', T))
    return {'y': sat8(s)}
eqs, meta = build({'y': 8}, {'a': 8, 'b': 8}, {'yo': 8}, lambda T: (step_e2(T), {'yo': R('y', T)}), 40, range(10, 40))
F = recover(eqs, meta); f = F.fits.get(('sig', 'y', 0))
print("(e') sat8(a + (b>>>6)), 8-bit y      ", None if f is None else (f['mode'], f.get('quant_bound'), {str(k): str(v) for k, v in f['coeffs'].items()}))
# how often does the saturation fire on full-range inputs? (a >= 126 with b>>>6 = 1, or a <= -127 with b>>>6 = -2)
print('     saturation events per 3000 uniform samples ~ %.1f' % (3000 * (2/256 * 1/4 + 2/256 * 1/4 + 1/256 * 1/4)))
