"""X1 attacks on the pseudo-circulant criterion.
(1) survival: stream FIR y[k] = x[k] + 2x[k-1] + 3x[k-2], two samples per frame (P = 2): H is pseudo-circulant after
    alignment at every origin and G is recovered up to a pure delay.
(2) interleaved two-channel filter (stereo L/R on one port, same 2-tap FIR h = 1 + 2 z^-1 per channel, separate state):
    H = diag(h, h) is pseudo-circulant, so X1 reports ONE stream filter G(z) = h(z^2) = 1 + 2 z^-2 (a comb-like FIR with
    spectral images), not two channels with h. Both explain the equations: channel interleaving is an unobservable
    symmetry missing from G_obs.
(3) held input (environment holds x for the whole frame, sample period = frame): hardware reads x at slots 0 and 1 and
    computes y0 = 3x, y1 = 3x (same sample, ZOH upsampler by 2 with gain 3). X1 sees a 2-vector input and H = diag(3, 3):
    pseudo-circulant -> 'stream gain 3 at twice the rate'. The protocol (which reads are fresh samples) is not in
    the equations."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
from x1lib import *

def show(tag, eqs, meta, outw, P):
    for o in range(P):
        F = recover(eqs, meta, origin=o)
        outs = sorted([k for k in F.nodes if k[0] == outw], key=lambda k: k[1])
        H, ins = frameH(F, outs)
        res = pc_test(H)
        print('%s origin %d  H=%s  pc alignments=%s  G=%s' % (tag, o, [[pretty(h) for h in row] for row in H],
              [c for c, _ in res], [stream_G(E) for _, E in res]))

# (1) stream FIR, P = 2
W = {'a': 16, 'b': 16, 'y': 16}; IN = {'x': 16}; OUT = {'yo': 16}
def mac3(x, p, q): return '(bvadd %s (bvadd (bvmul #x0002 %s) (bvmul #x0003 %s)))' % (x, p, q)
def s1(T):
    if T % 2 == 0: return {'a': X('x', T), 'y': mac3(X('x', T), R('b', T), R('a', T))}, {'yo': R('y', T)}
    return {'b': X('x', T), 'y': mac3(X('x', T), R('a', T), R('b', T))}, {'yo': R('y', T)}
show('(1) stream FIR', *build(W, IN, OUT, s1, 40, range(10, 40)), 'y', 2)

# (2) interleaved 2-channel FIR h = 1 + 2z^-1: L at slot 0 (state a), R at slot 1 (state b)
def s2(T):
    if T % 2 == 0: return {'a': X('x', T), 'y': '(bvadd %s (bvmul #x0002 %s))' % (X('x', T), R('a', T))}, {'yo': R('y', T)}
    return {'b': X('x', T), 'y': '(bvadd %s (bvmul #x0002 %s))' % (X('x', T), R('b', T))}, {'yo': R('y', T)}
show('(2) two channels', *build(W, IN, OUT, s2, 40, range(10, 40)), 'y', 2)

# (3) held input read twice
def s3(T): return ({'y': '(bvmul #x0003 %s)' % X('x', T)} if T % 2 == 0 else {'y': '(bvmul #x0003 %s)' % X('x', T), 'a': R('y', T)}), {'yo': R('y', T)}
show('(3) held input', *build({'y': 16, 'a': 16}, IN, OUT, s3, 40, range(10, 40)), 'y', 2)
