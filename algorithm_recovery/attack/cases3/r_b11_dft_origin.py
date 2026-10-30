"""B11: the permutations reported by the DFT matcher depend on the slot origin (cf. Prop. 9 for kappa): the same
natural-order DFT (frame_tests2.block_case, N = 4 and 8) is reported as natural/natural at the aligned origin and as
'other' orders at other origins, because the frame boundary rotates the input and output stream order and the DFT's
symmetries (f -> u f, t -> u^-1 t, and absorbing a rotation by N/2 into an affine relabeling) make several labelings fit.
Also printed: the reported scale (a rotation of the input order by r multiplies rows by w^(f r))."""
import sys, os, json, math, cmath
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot3'))
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

for N in (4, 8):
    nat = list(range(N)); F_ = dft(N)
    for o in range(N):
        r = block_case(N, F_, nat, nat, origin=o); d = r.get('dft', {})
        print('N=%d origin %d: class %s match %s output %s input %s scale %s' % (N, o, r.get('class'), d.get('match'), d.get('output_order'), d.get('input_order'), d.get('scale')))
