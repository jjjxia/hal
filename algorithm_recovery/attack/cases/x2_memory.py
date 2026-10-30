"""X2 attacks (memory cells as virtual registers), on the 'virtual register' equation systems X2 prescribes.
(1) Circular-buffer delay line (the standard memory FIR/delay structure): write cell n mod K at slot 0, read cell
    (n - 3) mod K at slot 1, P = 2, K = 4. With cells as registers the equations are periodic only with period K*P = 8:
    the least period is not the sample period, IF_{P*} is the K-unfolding (K input phases), and Cor. 8's "most folded"
    graph is not the algorithm y[n] = x[n-3].
(2) Read-during-write: same address written and read in the same cycle. READ_FIRST returns the old cell value
    (last write strictly before t), WRITE_FIRST the new one (write at t), NO_CHANGE leaves rdata unchanged (rdata not
    written). "a read resolves to the last write of cell a_r" is one of three different graphs; shown on a trace.
(3) Synchronous read latency L: rdata during cycle t is cell[a_r(t - L)] as of cycle t - L; resolving with the address
    at the cycle rdata is used (a_r(t)) reads the wrong cell when the address changes every cycle."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
K, P = 4, 2
W = {'m%d' % a: 16 for a in range(K)}; W['rd'] = 16; IN = {'x': 16}; OUT = {'y': 16}
def step(T):
    n, p = T // P, T % P; wr = {}
    if p == 0: wr['m%d' % (n % K)] = X('x', T)
    else: wr['rd'] = R('m%d' % ((n - 3) % K), T)
    return wr, {'y': R('rd', T)}
eqs, meta = build(W, IN, OUT, step, 80, range(20, 80))
F = recover(eqs, meta); print('(1) circular buffer: period found', F.P, '; errors', F.report['errors'])

# (2) read-during-write on a one-cell memory, cycle t: we = 1, a_w = a_r = 0, wdata = x(t)
def rdw(mode, xs):
    cell, rdata, out = 0, 0, []
    for t, x in enumerate(xs):
        if mode == 'READ_FIRST': rdata = cell
        elif mode == 'WRITE_FIRST': rdata = x
        # NO_CHANGE: rdata keeps its value during a write
        cell = x; out.append(rdata)
    return out
xs = [10, 20, 30, 40]
for m in ('READ_FIRST', 'WRITE_FIRST', 'NO_CHANGE'): print('(2) %-11s rdata after each cycle: %s' % (m, rdw(m, xs)))
# (3) latency: address sequence a_r = t mod 2 over cells holding [100, 200]
cells = [100, 200]
print('(3) rdata(t) with latency 1 = cell[a_r(t-1)]:', [cells[(t - 1) % 2] for t in range(1, 5)], ' vs cell[a_r(t)]:', [cells[t % 2] for t in range(1, 5)])
