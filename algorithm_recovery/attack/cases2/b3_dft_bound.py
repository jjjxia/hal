"""B3: the DFT acceptance bound eps <= log2(N) * q * max(1,|s|) (ext.tex, frame.match_dft) does not scale with |s|.
For a scaled transform (|s| < 1, e.g. the usual 1/2 per radix-2 stage, s = 1/N) the absolute tolerance log2(N) q can
exceed the size of the entries |s|, and then almost anything is accepted.
Test: N = 16, s = 1/16, q = 2^-6 (6-bit twiddles; the lemma's quantum). Matrices: (i) s F16 with only column 0, column 1
and row 1 kept, every other entry ZERO; (ii) s F16 with every entry outside those lines rotated by 60 degrees.
Both are accepted as DFT16. Also (iii): with q = 2^-15 and s = 1/1024 (a 10-stage scaled FFT1024 has s = 2^-10),
log2(N) q = 3e-4 while |s| = 1e-3: a 1024-point matrix with every entry off by 0.3|s| would pass (shown for N = 16
with the same ratio log2(N) q / |s| = 0.3)."""
import sys, os, cmath, math
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot2'))
import frame
def F(N): return [[cmath.exp(-2j * math.pi * k * n / N) for n in range(N)] for k in range(N)]
N, s, q = 16, 1 / 16, 2 ** -6
keep = lambda k, j: j in (0, 1) or k == 1
C1 = [[s * F(N)[k][j] if keep(k, j) else 0 for j in range(N)] for k in range(N)]
r = frame.match_dft(C1, q); print('(i)  mostly-zero matrix: match', r['match'], 'max_error %.4f bound %.4f |s| %.4f' % (r['max_error'], r['bound'], s))
rot = cmath.exp(1j * math.pi / 3)
C2 = [[s * F(N)[k][j] * (1 if keep(k, j) else rot) for j in range(N)] for k in range(N)]
r = frame.match_dft(C2, q); print('(ii) 60-degree rotated entries: match', r['match'], 'max_error %.4f bound %.4f' % (r['max_error'], r['bound']))
s3 = math.log2(N) * 2 ** -15 / 0.3
C3 = [[s3 * F(N)[k][j] * (1 if keep(k, j) else (1 + 0.29)) for j in range(N)] for k in range(N)]
r = frame.match_dft(C3, 2 ** -15); print('(iii) entries 29%% too large, q = 2^-15, |s| = %.2g: match' % s3, r['match'], 'max_error %.3g bound %.3g' % (r['max_error'], r['bound']))
