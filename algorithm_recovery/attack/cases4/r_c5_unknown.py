"""C5: lazy UNKNOWN_OPERATION semantics (halforms.compile_text) and 'incomplete' marking (Folded.fit).
(i) An UNKNOWN reachable only for x == 3 (a data-dependent branch, e.g. a special-case path through a gate HAL cannot
    model): Ite((x == 3), UNKNOWN_OPERATION, acc + x). The fit samples (powers of two, extremes, 48 random probes, 3000
    random values) never hit x == 3 for a 16-bit x: the node is fitted 'exact' linear and NOT marked incomplete.
    Real signals (small values) hit it constantly; run_graph then raises.
(ii) control: Ite((x == 0), ...) is caught (the zero probe).
(iii) evaluation itself: UNKNOWN in an untaken Ite branch is never evaluated; UNKNOWN & 0 stays UNKNOWN (conservative)."""
import sys, os, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot4'))
import invfold, halforms
def text(T, k): return 'Ite((x_ordered_%d == 0b%s), UNKNOWN_OPERATION, (w_o_Q_ordered_%d + x_ordered_%d))' % (T, format(k, '016b'), T, T)
for name, k in (('(i) UNKNOWN iff x == 3', 3), ('(ii) UNKNOWN iff x == 0', 0)):
    win = list(range(10, 40))
    eqs = [{'T': T, 'target': 'w - i_D', 'text': text(T, k)} for T in win]
    meta = {'window': win, 'widths': {'w': 16}, 'ports': {'in': {'x_ordered': 16}, 'out': {}}, 'write_enable': {'w': [1] * 50}}
    F = invfold.recover(eqs, meta)
    f = F.fits.get(('sig', 'w', 0))
    print(name, '-> fit', None if f is None else (f['mode'], {str(a): str(b) for a, b in f['coeffs'].items()}), '| incomplete:', F.report.get('incomplete'))
fn, syms, w = halforms.compile_text(text(5, 3), {'x_ordered_5': 16, 'w_o_Q_ordered_5': 16})
try: fn({'x_ordered_5': 3, 'w_o_Q_ordered_5': 1}); print('x == 3 evaluated without error (BAD)')
except halforms.Unknown: print('(iii) x == 3 raises Unknown when evaluated (good); x == 4 ->', fn({'x_ordered_5': 4, 'w_o_Q_ordered_5': 1}))
fn2, _, _ = halforms.compile_text('(w_o_Q_ordered_5 & (UNKNOWN_OPERATION & 0b0000000000000000))', {'w_o_Q_ordered_5': 16})
try: print('    w & (UNKNOWN & 0) =', fn2({'w_o_Q_ordered_5': 7}))
except halforms.Unknown: print('    w & (UNKNOWN & 0) raises Unknown (conservative: the value is 0)')
