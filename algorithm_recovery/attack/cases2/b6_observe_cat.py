"""B6: invfold.observe/node_sources ignore 'cat' sources (an output port whose bits are the Q bits of several words,
meta['alias'], as built by halforms.netlist_meta for XTEA's 'result') in LINEAR fits. Words read only through such a
port become 'dead' and vanish from sections and from the input-phase count, although they feed an observed output.
Design (P = 2): a <- x (slot 0), b <- a + 1 (slot 1); port 'pq' = {a, b} bits (alias, 16 bits = a[7:0], b[7:0]);
c <- pq + 3 (slot 1, reads the alias), y port shows c."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness2 import *
W = {'a': 8, 'b': 8, 'c': 16}; IN = {'x': 8}; OUT = {'yo': 16}
def step(T):
    if T % 2 == 0: return {'a': X('x', T)}, {'yo': R('c', T)}
    return {'b': '(bvadd %s #x01)' % R('a', T), 'c': '(bvadd top_module_pq_%d #x0003)' % T}, {'yo': R('c', T)}
eqs, meta = build(W, IN, OUT, step, 40, range(10, 40))
meta['alias'] = {'top_module_pq': [('a', i) for i in range(8)] + [('b', i) for i in range(8)]}
F = recover(eqs, meta)
print('errors', F.report['errors'], '| dead', getattr(F, 'dead', None), '| input phases', F.input_phases)
print('fit of c:', identify.fmt(F.fits.get(('sig', 'c', 1))))
print(identify.summary(report(F)))
