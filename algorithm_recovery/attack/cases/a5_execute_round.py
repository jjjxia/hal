"""A5: identify.execute ignores the rounding mode of output-port nodes (always floor), so a design whose output port
rounds combinationally, y = (acc + 2) >> 2, is executed with floor(acc/4): silent output mismatches although the fit
itself is correct (mode 'round'). execute() is the E1 held-out scorer (E1/score_e1.py S5)."""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'acc': 16}; IN = {'x': 16}; OUT = {'y': 14}
def step(T):
    return {'acc': '(bvadd %s %s)' % (X('x', T), '((_ extract 16 1) ((_ sign_extend 1) %s))' % R('acc', T))}, \
           {'y': '((_ extract 15 2) (bvadd %s #x0002))' % R('acc', T)}
eqs, meta = build(W, IN, OUT, step, 60, range(20, 60))
F = recover(eqs, meta)
print('output fit:', identify.fmt(F.fits[('out', 'y', 0)]))
rng = random.Random(1); xs = {'x': [rng.randrange(-3000, 3000) & 0xffff if t >= 4 else 0 for t in range(60)]}
bad, n = check_execute(F, W, IN, OUT, step, 60, xs, {'acc': 0}, 4)
print('execute() vs hardware: %d/%d output mismatches, e.g. %s' % (len(bad), n, bad[:3]))
bad2, n2 = check_run_graph(F, W, IN, OUT, step, 60, xs, {'acc': 0})
print('run_graph (raw expressions) node mismatches: %d/%d' % (len(bad2), n2))
