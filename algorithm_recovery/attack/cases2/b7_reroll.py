"""B7: reroll.py.
(i) template() renames every input read to IN[port] and records the input phases, but reroll() never uses them: four
    rounds s <- s xor k that read DIFFERENT input phases (a round key streamed one per cycle) form one round group with
    zero parameters, i.e. 'the same g with no per-round parameter' - the per-round key selection is lost.
(ii) feistel_test checks that each bit of L flips exactly one output bit, but not that different bits flip different
    output bits: f = ((L ^ (L >> 1)) & 0x55) ^ F(R, K) is accepted as Feistel-like although its L-part is not a
    permutation (pairs of L bits collapse), so the round is not invertible.
(iii) the test looks at one node: the round (L, R) -> (L ^ R, L ^ F(R, K)) passes on BOTH nodes (each is affine in L
    with a bit-permutation linear part) but the round map is not a bijection."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness2 import *
import reroll
# (i)
W = {'s': 8}; IN = {'k': 8}; OUT = {'yo': 8}
eqs, meta = build(W, IN, OUT, lambda T: ({'s': '(bvxor %s %s)' % (R('s', T), X('k', T))} if T % 5 != 4 else {}, {'yo': R('s', T)}), 60, range(20, 60))
F = recover(eqs, meta)
print('(i) period', F.P, 'input phases', F.input_phases)
for g in reroll.reroll(F): print('    group', g['word'], 'slots', g['slots'], 'placeholders', g['placeholders'], 'parameter classes', g['parameter_classes'], 'table', g['table'])
# (ii), (iii): P = 1, 8-bit halves, a small nonlinear F(R, K)
SB = '(bvmul (bvxor %s %s) (bvor (bvxor %s %s) #x01))'   # nonlinear in R
def Ff(T): return SB % (R('R', T), X('k', T), R('R', T), X('k', T))
cases = {
 '(ii) R <- ((L^(L>>1))&0x55) ^ F(R,K), L <- R': lambda T: ({'R': '(bvxor (bvand (bvxor %s (bvlshr %s #x01)) #x55) %s)' % (R('L', T), R('L', T), Ff(T)), 'L': R('R', T)}, {'yo': R('R', T)}),
 '(iii) L <- L^R, R <- L ^ F(R,K)': lambda T: ({'L': '(bvxor %s %s)' % (R('L', T), R('R', T)), 'R': '(bvxor %s %s)' % (R('L', T), Ff(T))}, {'yo': R('R', T)}),
}
for name, step in cases.items():
    eqs, meta = build({'L': 8, 'R': 8}, {'k': 8}, {'yo': 8}, step, 30, range(10, 30))
    F = recover(eqs, meta)
    res = {v: reroll.feistel_test(F, v, v[0], 'L') for v in sorted(F.nodes)}
    # invertibility of the round map for a fixed key (k = 0x3c)
    import smt2 as S
    def ev(word, l, r):
        d = F.nodes[(word, 0)]; ws = F.widths_of(d); fn, syms, w = F.compile(d, ws)
        env = {}
        for sy in syms:
            src = d['res'][sy]
            env[sy] = {'L': l, 'R': r}[src[1]] if src[0] == 'sig' else 0x3c
        return fn(env)
    imgs = {(ev('L', l, r) if ('L', 0) in F.nodes else r, ev('R', l, r)) for l in range(256) for r in range(256)}
    print(name, '| feistel_test(.., other_word=L):', {str(v): r['feistel'] for v, r in res.items()}, '| round bijective:', len(imgs) == 65536, '(%d images)' % len(imgs))
