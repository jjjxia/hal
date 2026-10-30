"""B8: a DFT built with 3-multiplier complex multiplications (k1 = c(a+b), k2 = a(d-c), k3 = b(c+d); re = k1 - k3,
im = k1 + k2), the usual DSP-saving form, with the three constants c, d-c, c+d each rounded to the grid. The linear map
per entry is [[c^, c^ - e2^], [c^ + e1^, c^]]: complex-linear only if e2^ - e1^ = 2 c^, which rounding breaks. frame.block_dft
checks complex linearity with tolerance 1e-12 and reports 'real-linear block map (not complex-linear)': a correctly
built fixed-point DFT is never matched (contradicts the lemma's 'never rejects a correctly built fixed-point FFT').
Control: the same DFT with 4-multiplier products (c^, d^ rounded) is matched.
Design: direct 8-point DFT (single stage), Q8 constants, as in invfold/tests/frame_tests.py dft_case."""
import sys, os, math, cmath
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness2 import *
import frame
N, Q = 8, int(sys.argv[1]) if len(sys.argv) > 1 else 128; WX, WY = 16, 24
QB = Q.bit_length() - 1
def bvc(v, w): return '(_ bv%d %d)' % (v % (1 << w), w)
def case(three):
    # coefficient matrices: yr = sum A xr + B xi, yi = sum C xr + D xi
    A, B, C, D = [[[0] * N for _ in range(N)] for _ in range(4)]
    for k in range(N):
        for j in range(N):
            w = cmath.exp(-2j * math.pi * k * j / N); c, d = w.real, w.imag
            if three:
                ch, e1, e2 = round(Q * c), round(Q * (d - c)), round(Q * (c + d))
                A[k][j], B[k][j], C[k][j], D[k][j] = ch, ch - e2, ch + e1, ch
            else:
                ch, dh = round(Q * c), round(Q * d)
                A[k][j], B[k][j], C[k][j], D[k][j] = ch, -dh, dh, ch
    W = {}
    for j in range(N): W['XR%d' % j] = WX; W['XI%d' % j] = WX
    for k in range(N): W['YR%d' % k] = WY; W['YI%d' % k] = WY
    def step(T):
        phi = T % N; q = lambda r: R(r, T); wr = {'XR%d' % phi: X('xr', T), 'XI%d' % phi: X('xi', T)}
        if phi == 0:
            sx = lambda r: '((_ sign_extend 16) %s)' % q(r)
            for k in range(N):
                re_ = ' '.join('(bvmul %s %s) (bvmul %s %s)' % (bvc(A[k][j], 32), sx('XR%d' % j), bvc(B[k][j], 32), sx('XI%d' % j)) for j in range(N))
                im_ = ' '.join('(bvmul %s %s) (bvmul %s %s)' % (bvc(C[k][j], 32), sx('XR%d' % j), bvc(D[k][j], 32), sx('XI%d' % j)) for j in range(N))
                wr['YR%d' % k] = '((_ extract %d %d) (bvadd %s))' % (23 + QB, QB, re_); wr['YI%d' % k] = '((_ extract %d %d) (bvadd %s))' % (23 + QB, QB, im_)
        o = (phi - 1) % N
        return wr, {'yr': q('YR%d' % o), 'yi': q('YI%d' % o)}
    eqs, meta = build(W, {'xr': WX, 'xi': WX}, {'yr': WY, 'yi': WY}, step, 120, range(40, 120))
    F = invfold.run_big(recover, eqs, meta, 0)
    c = frame.classify_frame(F, [(('xr', 'xi'), ('yr', 'yi'))])
    return c.get('class'), c.get('dft', {}).get('match'), c.get('dft', {}).get('max_error'), c.get('dft', {}).get('bound')
print('Q =', Q); print('4-multiplier DFT8 :', case(False))
print('3-multiplier DFT8 :', case(True))
