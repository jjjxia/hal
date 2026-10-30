"""B2: frame.match_dft takes the coefficient quantum q from the denominators of the ENTRIES of the linearized block
matrix C. In a multi-stage FFT each entry is a product of twiddles, so its denominator is q_tw^L (L nontrivial twiddle
stages on the path), and the acceptance bound log2(N) * q * max(1,|s|) is many orders below the real twiddle error
L * q_tw: a correctly built fixed-point FFT with >= 2 nontrivial twiddle stages (N >= 16) is rejected.
Design: radix-2 DIF FFT16, Q15 twiddles (trivial 1 and -j exact), one register stage per butterfly level (slots 0..3),
12-bit complex input one sample per cycle (P = 16), outputs in bit-reversed order on (yr, yi)."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness2 import *
import frame
N, L = 16, 4; WR = 24; WX = int(sys.argv[1]) if len(sys.argv) > 1 else 12   # input width
def bvc(v, w): return '(_ bv%d %d)' % (v % (1 << w), w)
def tw(k, span2):   # W_{span2}^k = exp(-2 pi i k / span2), Q15, exact for the trivial ones
    if k == 0: return 'one'
    if 4 * k == span2: return 'mj'
    a = -2 * math.pi * k / span2
    return (round(32768 * math.cos(a)), round(32768 * math.sin(a)))
def stage_exprs(s, q):
    """expressions of stage s outputs (re, im) from stage s-1 registers (or the inputs for s = 0)"""
    src = (lambda i, c: q('X%s%d' % (c, i))) if s == 0 else (lambda i, c: q('S%d%s%d' % (s - 1, c, i)))
    span = N >> (s + 1); out = {}
    sx = lambda e: '((_ sign_extend %d) %s)' % (48 - (WX if s == 0 else WR), e)
    for g in range(0, N, 2 * span):
        for k in range(span):
            i, j = g + k, g + k + span
            ar, ai, br, bi = sx(src(i, 'R')), sx(src(i, 'I')), sx(src(j, 'R')), sx(src(j, 'I'))
            out[(i, 'R')] = '((_ extract 23 0) (bvadd %s %s))' % (ar, br)
            out[(i, 'I')] = '((_ extract 23 0) (bvadd %s %s))' % (ai, bi)
            dr, di = '(bvsub %s %s)' % (ar, br), '(bvsub %s %s)' % (ai, bi)
            w = tw(k, 2 * span)
            if w == 'one': out[(j, 'R')], out[(j, 'I')] = '((_ extract 23 0) %s)' % dr, '((_ extract 23 0) %s)' % di
            elif w == 'mj': out[(j, 'R')], out[(j, 'I')] = '((_ extract 23 0) %s)' % di, '((_ extract 23 0) (bvneg %s))' % dr
            else:
                wr, wi = w
                out[(j, 'R')] = '((_ extract 38 15) (bvsub (bvmul %s %s) (bvmul %s %s)))' % (dr, bvc(wr, 48), di, bvc(wi, 48))
                out[(j, 'I')] = '((_ extract 38 15) (bvadd (bvmul %s %s) (bvmul %s %s)))' % (dr, bvc(wi, 48), di, bvc(wr, 48))
    return out
W = {}
for i in range(N): W['XR%d' % i] = WX; W['XI%d' % i] = WX
for s in range(L):
    for i in range(N): W['S%dR%d' % (s, i)] = WR; W['S%dI%d' % (s, i)] = WR
IN = {'xr': WX, 'xi': WX}; OUT = {'yr': WR, 'yi': WR}
def step(T):
    phi = T % N; q = lambda r: R(r, T); wr = {'XR%d' % phi: X('xr', T), 'XI%d' % phi: X('xi', T)}
    if phi < L:
        for (i, c), e in stage_exprs(phi, q).items(): wr['S%d%s%d' % (phi, c, i)] = e
    o = (phi - L) % N
    return wr, {'yr': q('S%dR%d' % (L - 1, o)), 'yi': q('S%dI%d' % (L - 1, o))}
eqs, meta = build(W, IN, OUT, step, 120, range(40, 120))
ORIGIN = int(sys.argv[2]) if len(sys.argv) > 2 else 0
F = invfold.run_big(recover, eqs, meta, ORIGIN)
print('origin', ORIGIN, 'input width', WX, 'errors', F.report['errors'], 'nonlinear', len(F.report.get('nonlinear', [])), 'modes', sorted({f['mode'] for f in F.fits.values() if f}))
c = frame.classify_frame(F, [(('xr', 'xi'), ('yr', 'yi'))])
d = c.get('dft', {})
print('class:', c.get('class'), '| match:', d.get('match'), '| max_error %.3g bound %.3g' % (d.get('max_error', -1), d.get('bound', -1)), '| output order', d.get('output_order'), '| input order', d.get('input_order'), '| scale', d.get('scale'))
# the bound the lemma actually justifies, with q = the twiddle quantum 2^-15 and L = 2 nontrivial stages
print('lemma bound with q = 2^-15: %.3g ; error is within it: %s' % (math.log2(N) * 2 ** -15, d.get('max_error', 9) <= math.log2(N) * 2 ** -15))
q = [f for f in F.fits.values() if f and f['mode'] == 'quant']
if q: print('quant nodes: %d, e.g. coefficients %s (true twiddles are k/32768 with k in {23170, 30274, 12540})' % (len(q), sorted({str(abs(v)) for v in q[0]['coeffs'].values()})))
print('reported output_perm', d.get('output_perm'), 'input_perm', d.get('input_perm'))
u = d.get('input_perm', [0, 1])[1]
print('input_perm is multiplication by %d mod 16; output_perm / bitrev =' % u, [(d['output_perm'][k] * pow(u, 1, 16)) % 16 for k in range(16)] if d else None,
      '(the DFT is invariant under f -> u f, t -> u^-1 t for every unit u, so the matcher can return any of the 8 equivalent labelings)')
br = frame.bit_reverse_perm(16)
# rerun the matcher with the lemma's quantum to see which labeling wins
fm, _ = frame.frame_matrix(F)
