"""E2: for every output pair of the FFT64 frame matrix: the best single DFT bin f (relative to the window start m0) and
the largest set of input samples s with C[s] = A w^(f (s - m0)) for one common A; the remaining samples are outliers,
reported with their extra factor as a power of w = exp(-2 pi i/64) and their position relative to the window."""
import pickle, collections, cmath, math
from fractions import Fraction as Fr
d = pickle.load(open('/home/jiahao/hal/algorithm_recovery/E4_certified/out/fft_H.pkl', 'rb')); H = d['H']; P = 128; N = 64
w = cmath.exp(-2j * math.pi / N)
def parse_in(x): return x.split("'")[3], int(x.split(', ')[2].rstrip(')'))
rows = collections.defaultdict(dict)
for (r, x), (dl, c) in H.items():
    p, ph = parse_in(x); rows[r][(p, -dl * P + ph)] = Fr(c)
def resp(i):
    r, ri = "('DOR', %d)" % i, "('DOI', %d)" % i
    S = sorted({s for (_, s) in rows[r]} | {s for (_, s) in rows[ri]})
    return S, {s: complex(float(rows[r].get(('DR_ordered', s), 0)), float(rows[ri].get(('DR_ordered', s), 0))) for s in S}
def wpow(z):   # z / |z| as a power of w (rounded), and the rounding error
    e = -cmath.phase(z) / (2 * math.pi / N); return int(round(e)) % N, abs(e - round(e))
summary = collections.Counter(); detail = []
for i in range(128):
    S, C = resp(i); m0 = S[0]
    best = None
    for f in range(N):
        cl = collections.defaultdict(list)
        for s in S:
            q = C[s] / w ** (f * (s - m0)); k, err = wpow(q)
            cl[(k, round(abs(q), 5))].append(s)
        key, members = max(cl.items(), key=lambda kv: len(kv[1]))
        if best is None or len(members) > len(best[2]): best = (f, key, members, cl)
    f, key, members, cl = best
    outl = [(s, s - m0, (k - key[0]) % N) for (k, a), ss in cl.items() if (k, a) != key for s in ss]
    span = S[-1] - S[0] + 1
    summary[(len(S), span, len(members), tuple(sorted({o[2] for o in outl})))] += 1
    detail.append((i, m0 % P, span, f, len(members), outl[:4]))
print('(samples, window span, samples on one bin, outlier factors as powers of w) -> number of outputs:')
for k, v in sorted(summary.items(), key=lambda kv: -kv[1]): print('   ', k, v)
print('examples (output, window start mod 128, span, bin, on-bin samples, outliers (sample, offset in window, extra power of w)):')
for x in detail[:6] + [d_ for d_ in detail if d_[2] != 64][:6]: print('   ', x)
