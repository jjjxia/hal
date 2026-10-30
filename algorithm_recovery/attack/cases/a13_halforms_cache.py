"""A13: halforms.compile_text caches parsed HAL expressions by id(text). CPython reuses the id of a freed string for
the next string of the same size, so in any process that loads a second set of equations (another design, a reloaded
forms file) a new equation can silently get the AST of an old, different one.
Demonstration: 'load' 1000 equations '(a_o_Q_k & b_o_Q_k)', compile them, drop the list (as when a second design or a
re-read forms file replaces the first), load 1000 equations '(a_o_Q_k | b_o_Q_k)' and evaluate them at a=1, b=0."""
import sys, os, gc
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot'))
import halforms
def load(op): return [{'text': '(a_o_Q_%d %s b_o_Q_%d)' % (k, op, k)} for k in range(1000)]
ALLW = {'%s_o_Q_%d' % (c, k): 1 for c in 'ab' for k in range(1000)}
ENV = {'a_o_Q_%d' % k: 1 for k in range(1000)}; ENV.update({'b_o_Q_%d' % k: 0 for k in range(1000)})
def compile_all(eqs):
    return [halforms.compile_text(e['text'], ALLW)[0](ENV) for e in eqs]   # widths: every word of the design
e1 = load('&'); r1 = compile_all(e1); del e1; gc.collect()
e2 = load('|'); r2 = compile_all(e2)
wrong = sum(1 for v in r2 if v != 1)
print('second load: %d/1000 OR-equations evaluated as AND (stale cached AST, no error)' % wrong)
