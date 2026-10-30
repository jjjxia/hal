"""E1: offline look at E4_certified/out/fft_H.pkl (frame matrix of FFT64, (delay, coefficient) monomials). For each
output pair (DOR_i, DOI_i): its 64 input samples, and the ratio of each entry to the best single DFT bin s w^(f j).
Prints, for the first rows, which input samples deviate and by which factor."""
import pickle, collections, cmath, math, sys
from fractions import Fraction as Fr
d = pickle.load(open('/home/jiahao/hal/algorithm_recovery/E4_certified/out/fft_H.pkl', 'rb')); H = d['H']; P = 128
print('pickle keys', list(d.keys()), 'entries', len(H)); k0 = next(iter(H)); print('example key', k0, '->', H[k0])
def parse_in(x): return x.split("'")[3], int(x.split(', ')[2].rstrip(')'))
def parse_out(r): return r.split("'")[1], int(r.split(', ')[1].rstrip(')'))
rows = collections.defaultdict(dict)
for (r, x), (dl, c) in H.items():
    p, ph = parse_in(x); rows[r][(p, -dl * P + ph)] = Fr(c)
N = 64; w = cmath.exp(-2j * math.pi / N)
def crow(i):
    r, ri = "('DOR', %d)" % i, "('DOI', %d)" % i
    S = sorted({s for (_, s) in rows[r]} | {s for (_, s) in rows[ri]})
    vals = {}
    for s in S:
        a = float(rows[r].get(('DR_ordered', s), 0)); b = float(rows[ri].get(('DR_ordered', s), 0))
        c = float(rows[r].get(('DI_ordered', s), 0)); dd = float(rows[ri].get(('DI_ordered', s), 0))
        vals[s] = (complex(a, b), complex(-c, dd) if False else complex(dd, -c))   # response to re unit, and (d - j c) = response to im unit / j
    return S, vals
for i in list(range(0, 8)) + [64, 65]:
    S, vals = crow(i)
    m0 = S[0]; js = [s - m0 for s in S]
    resp_re = [vals[s][0] for s in S]; resp_im = [vals[s][1] for s in S]
    lin = max(abs(resp_re[k] - resp_im[k]) for k in range(len(S)))   # complex-linear iff the two agree
    s0 = resp_re[0]
    # best frequency for this row: f maximizing |sum C_j conj(w^(f j))|
    best = max(range(N), key=lambda f: abs(sum(resp_re[k] * w ** (-f * js[k]) for k in range(len(S)))))
    ratio = [resp_re[k] / (s0 * w ** (best * js[k])) for k in range(len(S))]
    dev = [(S[k], js[k], complex(round(ratio[k].real, 3), round(ratio[k].imag, 3))) for k in range(len(S)) if abs(ratio[k] - 1) > 1e-3]
    print('out %3d: %d samples, window [%d, %d] (start mod 128 = %d), complex-linear dev %.2g, |s| %.5f, best bin %d, deviating entries %d: %s' %
          (i, len(S), S[0], S[-1], S[0] % P, lin, abs(s0), best, len(dev), dev[:6]))
