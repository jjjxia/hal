"""B5: frame.frame_matrix ignores the dead-node elimination of invfold.observe:
 (i) a dead linear idle-slot write that reads x in another phase: observe() drops that phase from F.input_phases, but
     frame_matrix still feeds every fitted node (dead ones included) to identify.solve with inputs built from the
     reduced phase list -> ValueError (inputs.index) instead of a classification;
 (ii) a dead NONLINEAR idle-slot write (default operands multiplied, acc <- acc * x): frame_matrix refuses with
     'nonlinear nodes present' although every observable node is linear.
Design (a10, corrected): P = 3, slot 0: acc <- 3x, d <- x; slot 1: acc <- acc + 5d; slot 2: idle write, y <- acc.
Algorithm: y = 8 x[n] (thanks to the coordinator for the correction of the a10 docstring: d is read in the same frame)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness3 import *
import frame
W = {'acc': 16, 'd': 16, 'y': 16}; IN = {'x': 16}; OUT = {'yo': 16}
for name, idle in [('(i) dead linear idle write acc <- acc + 7x', lambda T: '(bvadd %s (bvmul #x0007 %s))' % (R('acc', T), X('x', T))),
                   ('(ii) dead nonlinear idle write acc <- acc * x', lambda T: '(bvmul %s %s)' % (R('acc', T), X('x', T)))]:
    def step(T, idle=idle):
        p = T % 3
        if p == 0: wr = {'acc': '(bvmul #x0003 %s)' % X('x', T), 'd': X('x', T)}
        elif p == 1: wr = {'acc': '(bvadd %s (bvmul #x0005 %s))' % (R('acc', T), R('d', T))}
        else: wr = {'acc': idle(T), 'y': R('acc', T)}
        return wr, {'yo': R('y', T)}
    eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
    F = recover(eqs, meta)
    print(name, '| dead:', getattr(F, 'dead', None), '| input phases', F.input_phases, '| sections', [(s['nodes'], [(t['num'], t['den']) for t in s['transfer']]) for s in report(F)['sections']])
    try: print('   classify_frame ->', frame.classify_frame(F))
    except Exception as e: print('   classify_frame raised %s: %s' % (type(e).__name__, e))
