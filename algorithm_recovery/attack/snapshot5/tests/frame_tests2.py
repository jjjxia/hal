"""X1 tests built from the attack round (attack/cases/x1_*): input permutations (DIT with bit-reversed input), inverse
DFT, one-sided I/Q swap, a block transform whose latency crosses frames at some origins, and channel interleaving."""
import sys, os, json, math, cmath
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import invfold, frame

def bv(v, w): return '(_ bv%d %d)' % (v % (1 << w), w)

def block_case(N, M, in_perm, out_perm, swap_out=False, origin=0, extra_delay=False):
    """complex block map y = M x (M: N x N complex, Q8 fixed point), inputs written in phase j = in_perm position,
    outputs shown in phases 1..N (next frame's phase 0 for the last); swap_out exchanges yr/yi ports."""
    Q = 256; WX, WY = 16, 24; win = list(range(40, 40 + 10 * N))
    cr = [[round(Q * M[k][j].real) for j in range(N)] for k in range(N)]
    ci = [[round(Q * M[k][j].imag) for j in range(N)] for k in range(N)]
    eqs = []; ce = {}
    words = {}
    for j in range(N): words['XR%d' % j] = (WX, (lambda j: lambda t: in_perm[t % N] == j)(j)); words['XI%d' % j] = (WX, (lambda j: lambda t: in_perm[t % N] == j)(j))
    for k in range(N): words['YR%d' % k] = (WY, lambda t: t % N == 0); words['YI%d' % k] = (WY, lambda t: t % N == 0)
    for T in win:
        phi = T % N; q = lambda r: '%s_o_Q_%d' % (r, T)
        j = in_perm[phi]   # the sample arriving in phase phi is time index j of the transform
        eqs += [{'T': T, 'target': 'XR%d - i_D' % j, 'smt2': 'xr_%d' % T}, {'T': T, 'target': 'XI%d - i_D' % j, 'smt2': 'xi_%d' % T}]
        if phi == 0:
            sx = lambda r: '((_ sign_extend 16) %s)' % q(r)
            for k in range(N):
                re_ = ' '.join('(bvmul %s %s) (bvmul %s %s)' % (bv(cr[k][jj], 32), sx('XR%d' % jj), bv(-ci[k][jj], 32), sx('XI%d' % jj)) for jj in range(N))
                im_ = ' '.join('(bvmul %s %s) (bvmul %s %s)' % (bv(ci[k][jj], 32), sx('XR%d' % jj), bv(cr[k][jj], 32), sx('XI%d' % jj)) for jj in range(N))
                eqs += [{'T': T, 'target': 'YR%d - i_D' % k, 'smt2': '((_ extract 31 8) (bvadd %s))' % re_},
                        {'T': T, 'target': 'YI%d - i_D' % k, 'smt2': '((_ extract 31 8) (bvadd %s))' % im_}]
        o = out_perm[(phi - 1) % N]
        pr, pi_ = ('yi', 'yr') if swap_out else ('yr', 'yi')
        eqs += [{'T': T, 'target': 'top_module - %s' % pr, 'smt2': q('YR%d' % o)}, {'T': T, 'target': 'top_module - %s' % pi_, 'smt2': q('YI%d' % o)}]
    n = win[-1] + 5
    meta = {'window': win, 'widths': {w: v[0] for w, v in words.items()}, 'ports': {'in': {'xr': WX, 'xi': WX}, 'out': {'yr': WY, 'yi': WY}},
            'write_enable': {w: [int(v[1](t)) for t in range(n)] for w, v in words.items()}}
    F = invfold.run_big(invfold.recover, eqs, meta, origin)
    assert not F.report['errors'], F.report['errors']
    return frame.classify_frame(F, [(('xr', 'xi'), ('yr', 'yi'))])

def dft(N, sign=-1): return [[cmath.exp(sign * 2j * math.pi * k * j / N) for j in range(N)] for k in range(N)]
N = 8; br = frame.bit_reverse_perm(N); nat = list(range(N))
R = {}
R['dit_bitrev_input'] = block_case(N, dft(N), br, nat)
R['inverse_dft'] = block_case(N, [[x / 2 for x in row] for row in dft(N, +1)], nat, nat)
R['one_sided_iq_swap'] = block_case(N, dft(N), nat, nat, swap_out=True)
R['origins'] = {o: block_case(4, dft(4), list(range(4)), list(range(4)), origin=o).get('class') for o in range(4)}
ok = {
 'DIT bit-reversed input -> input_order bit-reversed, match': R['dit_bitrev_input'].get('dft', {}).get('match') and R['dit_bitrev_input']['dft']['input_order'] == 'bit-reversed',
 'inverse DFT -> match, output order negated': R['inverse_dft'].get('dft', {}).get('match') and 'negated' in R['inverse_dft']['dft']['output_order'],
 'one-sided I/Q swap -> match, output orientation swapped': R['one_sided_iq_swap'].get('dft', {}).get('match') and R['one_sided_iq_swap']['orientation']['output (re, im)'] == ('yi', 'yr'),
 'block transform at every origin': all(v == 'block transform' for v in R['origins'].values()),
}
# interleaved two channels (attack x1_pc (2)): P = 2, y[t] = x[t] + 2 x[t-2] (two channels, each h = 1 + 2 z^-1)
W = 16; win = list(range(20, 60)); eqs = []
for T in win:
    q = lambda r: '%s_o_Q_%d' % (r, T)
    a, b = ('A', 'B') if T % 2 == 0 else ('B', 'A')
    eqs += [{'T': T, 'target': '%s - i_D' % a, 'smt2': 'x_%d' % T},
            {'T': T, 'target': 'Y - i_D', 'smt2': '(bvadd x_%d (bvmul %s %s))' % (T, bv(2, W), q(a))},
            {'T': T, 'target': 'top_module - y', 'smt2': q('Y')}]
meta = {'window': win, 'widths': {'A': W, 'B': W, 'Y': W}, 'ports': {'in': {'x': W}, 'out': {'y': W}},
        'write_enable': {'A': [int(t % 2 == 0) for t in range(70)], 'B': [int(t % 2 == 1) for t in range(70)], 'Y': [1] * 70}}
F = invfold.run_big(invfold.recover, eqs, meta, 0); c = frame.classify_frame(F); R['interleaved'] = c
ok['interleaved channels -> ambiguity reported'] = any('interleaved' in a for a in c.get('ambiguities', []))
print(json.dumps({k: (v if k != 'origins' else v) for k, v in R.items()}, indent=1, default=str)[:3000])
for k, v in ok.items(): print('PASS' if v else 'FAIL', k)
