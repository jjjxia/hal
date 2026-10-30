"""E5: is each window group of the FFT64 frame matrix D_out * W * D_in with W[k][j] = w^(f_k t_j) (a DFT with a time
permutation t and per-output bins f_k) and DIAGONAL unit-modulus phase factors on inputs (D_in) and outputs (D_out)?
Exponents e_k[j] (C = |c| w^e). For two rows a, b with an odd bin difference delta, t = (e_a - e_b) / delta (mod 64)
up to a constant; then every row must satisfy e_k = e_a + f_k t + const_k for an integer f_k. A common-scale DFT
matcher (frame.match_dft, fft_blocks.match_rows) cannot see this structure when D_in is not constant."""
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
    S, C = resp(i); E[i] = [round(-cmath.phase(C[s]) / (2 * math.pi / N)) % N for s in S]; groups[tuple(S)].append(i)
allgroups = []
for S, idx in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    found = None
    for a in idx[:4]:
        for b in idx:
            if b == a: continue
            diff = [(E[a][j] - E[b][j]) % N for j in range(N)]
            for delta in range(1, N, 2):
                t = [(x * pow(delta, -1, N)) % N for x in diff]
                t = [(x - t[0]) % N for x in t]
                if sorted(t) != list(range(N)): continue
                fk = {}
                for k in idx:
                    sol = None
                    for f in range(N):
                        r = [(E[k][j] - E[a][j] - f * t[j]) % N for j in range(N)]
                        if len(set(r)) == 1: sol = f; break
                    if sol is None: break
                    fk[k] = sol
                if len(fk) == len(idx): found = (a, b, delta, t, fk); break
            if found: break
        if found: break
    span = S[-1] - S[0] + 1
    if not found: print('window [%d..%d] (span %d, %d rows): NOT of the form D_out W D_in' % (S[0], S[-1], span, len(idx))); continue
    a, b, delta, t, fk = found
    g = E[a]   # input phases relative to row a (row a's bin set to 0)
    gdev = [(g[j] - fk.get(a, 0) * t[j]) % N for j in range(N)]
    allgroups.append((S, t, fk))
    print('window [%d..%d] (span %d, %d rows): D_out W D_in holds; bins distinct %d; t = identity? %s; first t: %s' %
          (S[0], S[-1], span, len(idx), len(set(fk.values())), t == list(range(N)), t[:12]))
