"""E3: per-row time permutation of the FFT64 frame matrix. For an output pair with DFT bin f (f odd = a unit mod 64),
C[s] = A w^(f t(s)) determines t(s) up to an additive constant: t_raw(s) = f^-1 * log_w(C[s]) mod 64. Check that t_raw
is a bijection of the row's 64 samples onto Z_64, and compare the permutations of different rows after removing the
per-row constant (normalize so the window's first sample has t = 0). A common permutation with per-row constants means
D F Pi: a DFT up to a common time permutation and a PER-OUTPUT phase w^(f r_k), which a common-scale matcher rejects."""
import pickle, collections, cmath, math
from fractions import Fraction as Fr
d = pickle.load(open('/home/jiahao/hal/algorithm_recovery/E4_certified/out/fft_H.pkl', 'rb')); H = d['H']; P = 128; N = 64
def parse_in(x): return x.split("'")[3], int(x.split(', ')[2].rstrip(')'))
rows = collections.defaultdict(dict)
for (r, x), (dl, c) in H.items():
    p, ph = parse_in(x); rows[r][(p, -dl * P + ph)] = Fr(c)
def resp(i):
    r, ri = "('DOR', %d)" % i, "('DOI', %d)" % i
    S = sorted({s for (_, s) in rows[r]} | {s for (_, s) in rows[ri]})
    return S, {s: complex(float(rows[r].get(('DR_ordered', s), 0)), float(rows[ri].get(('DR_ordered', s), 0))) for s in S}
def lw(z): return -cmath.phase(z) / (2 * math.pi / N)
res = {}; perms = collections.Counter(); bins = {}
for i in range(128):
    S, C = resp(i)
    if len(S) != 64: continue
    found = None
    for f in range(1, N, 2):
        finv = pow(f, -1, N)
        e = [lw(C[s]) for s in S]
        if max(abs(x - round(x)) for x in e) > 0.05: break   # phases not on the 64-grid
        t = [(round(x) * finv) % N for x in e]
        if sorted(t) == list(range(N)):
            t0 = t[0]; tn = tuple((x - t0) % N for x in t)
            found = (f, tn, S[0]); break
    if found:
        f, tn, m0 = found; perms[(m0 % P, S[-1] - S[0] + 1, tn)] += 1; bins[i] = (f, m0 % P)
print('rows with an odd bin giving a bijective time map:', len(bins), 'of 128')
for (m0, span, tn), k in perms.most_common(6):
    ident = tn == tuple(range(N))
    print('window start mod 128 = %d, span %d: %d rows share t = %s%s' % (m0, span, k, list(tn[:20]), ' (identity)' if ident else ''))
print('distinct normalized permutations:', len(perms))
S, C = resp(0)
e = [lw(C[s]) for s in S]
print('row 0: magnitudes', sorted({round(abs(C[s]), 4) for s in S})[:6], '| phase / (2 pi/64) samples', [round(x, 3) for x in e[:12]])
print('       fractional parts (rounded to 1/8):', collections.Counter(round((x - math.floor(x)) * 8) / 8 for x in e))
for i in (0, 1, 64):
    S, C = resp(i); e = [round(lw(C[s])) % N for s in S]
    # best bin by projection over natural time
    f = max(range(N), key=lambda f: abs(sum(C[s] * cmath.exp(2j * math.pi * f * (s - S[0]) / N) for s in S)))
    dev = [(e[k] - f * k) % N for k in range(len(S))]
    print('row %d (window start %d, bin %d): exponent - bin*j per sample j (mod 64):' % (i, S[0], f))
    print('   ', dev)
    print('    distinct exponents mod 64: %d of 64' % len(set(e)))
