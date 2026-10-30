"""B10: frame.classify_frame applies the LTI (pseudo-circulant) test only when there is exactly one input port and one
output port. Any single-rate LTI design with two data ports (a complex filter on (re, im) ports, a two-input mixer
stage, stereo) falls through to 'periodically time-varying / multirate linear map' (or to the block-transform test).
Cases, all P = 1 (one sample per cycle per port, no time multiplexing at all):
 (i) y <- a + b + y/2 (two input ports, one output port);
 (ii) complex one-pole filter on (xr, xi) -> (yr, yi): y <- x + (1/2 + j/4) y."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness2 import *
import frame
def half(e, k=1): return '((_ extract %d %d) ((_ sign_extend %d) %s))' % (15 + k, k, k, e)
eqs, meta = build({'y': 16}, {'a': 16, 'b': 16}, {'yo': 16}, lambda T: ({'y': '(bvadd %s (bvadd %s %s))' % (X('a', T), X('b', T), half(R('y', T)))}, {'yo': R('y', T)}), 40, range(10, 40))
F = recover(eqs, meta); c = frame.classify_frame(F)
print('(i)  P=%d sections:' % F.P, [(s['inputs'], [(t['num'], t['den']) for t in s['transfer']]) for s in report(F)['sections']], '\n     frame class:', c.get('class'))
def step2(T):
    yr, yi = R('yr', T), R('yi', T)
    return {'yr': '(bvadd %s (bvsub %s %s))' % (X('xr', T), half(yr), half(yi, 2)), 'yi': '(bvadd %s (bvadd %s %s))' % (X('xi', T), half(yi), half(yr, 2))}, {'or': yr, 'oi': yi}
eqs, meta = build({'yr': 16, 'yi': 16}, {'xr': 16, 'xi': 16}, {'or': 16, 'oi': 16}, step2, 40, range(10, 40))
F = recover(eqs, meta); c = frame.classify_frame(F, [(('xr', 'xi'), ('or', 'oi'))])
print('(ii) P=%d frame class:' % F.P, c.get('class'), '| sections:', [(s['nodes'], s['recursive']) for s in report(F)['sections']])
