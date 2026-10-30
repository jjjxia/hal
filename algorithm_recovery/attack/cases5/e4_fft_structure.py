"""E4: test the decomposition C[k][j] = A_k * w^(f_k t(j)) * g_j on the FFT64 frame matrix, per window group:
common time map t and common per-INPUT phase g_j (powers of w), per-output bin f_k and per-output constant A_k.
t and g are read off one row (rows with an odd bin determine t); every other row is then checked over all f."""
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
E = {}; groups = collections.defaultdict(list)
for i in range(128):
    S, C = resp(i); E[i] = (S, [round(-cmath.phase(C[s]) / (2 * math.pi / N)) % N for s in S]); groups[tuple(S)].append(i)
for S, idx in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    # reference row with an odd bin: try every row/odd f until e = f t + g with t bijective is consistent for all rows
    solved = None
    for ref in idx:
        e0 = E[ref][1]
        for f0 in range(1, N, 2):
            # unknown t and g: fix g from a second reference later; first assume g = 0 to get a candidate t, then
            # derive g from other rows as the common residual
            t = [(x * pow(f0, -1, N)) % N for x in e0]
            if sorted(t) != list(range(N)): continue
            ok = True; fs = {}
            g = None
            for k in idx:
                ek = E[k][1]; best = None
                for fk in range(N):
                    res = [(ek[j] - fk * t[j]) % N for j in range(N)]
                    if g is None:   # first other row defines the column phases relative to the reference
                        best = (fk, res); break
                    dlt = [(res[j] - g[j]) % N for j in range(N)]
                    if len(set(dlt)) == 1: best = (fk, res); break
                if best is None: ok = False; break
                if g is None and k != ref: g = [(best[1][j] - best[1][0]) % N for j in range(N)]
                fs[k] = best[0]
            if ok: solved = (ref, f0, t, g, fs); break
        if solved: break
    span = S[-1] - S[0] + 1
    if not solved: print('window [%d..%d] span %d, %d rows: no common (t, g) decomposition found' % (S[0], S[-1], span, len(idx))); continue
    ref, f0, t, g, fs = solved
    print('window [%d..%d] span %d, %d rows: decomposition FOUND; nontrivial column phases at %d inputs; t identity: %s; bins distinct: %d' %
          (S[0], S[-1], span, len(idx), sum(1 for x in (g or []) if x), t == list(range(N)), len(set(fs.values()))))
