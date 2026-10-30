"""B12 (survival check of the 'quant' mode on its intended case): two roundings in one cycle WITHOUT internal overflow
(the a12 of round 1 wrapped inside the 16-bit product, as the coordinator noted). acc <- x + (((acc*3)>>2)*5)>>2 computed
in 24 bits. Expected: coefficients 1 and 15/16, mode 'quant', small bound."""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness2 import *
def sh(e, k, w=24): return '((_ extract %d %d) %s)' % (w - 1 + k, k, '((_ sign_extend %d) %s)' % (k, e))
def step(T):
    a = '((_ sign_extend 8) %s)' % R('acc', T)
    inner = sh('(bvmul #x000003 %s)' % a, 2)
    tot = '(bvadd ((_ sign_extend 8) %s) %s)' % (X('x', T), sh('(bvmul #x000005 %s)' % inner, 2))
    return {'acc': '((_ extract 15 0) %s)' % tot}, {'y': R('acc', T)}
eqs, meta = build({'acc': 16}, {'x': 16}, {'y': 16}, step, 40, range(10, 40))
F = recover(eqs, meta); f = F.fits.get(('sig', 'acc', 0))
print('fit:', f and (f['mode'], f.get('quant_bound'), {str(k): str(v) for k, v in f['coeffs'].items()}))
