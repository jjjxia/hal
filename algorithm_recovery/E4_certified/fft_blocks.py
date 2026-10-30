"""Block identification on the FFT64 frame matrix (out/fft_H.pkl from fft_run.py): every output row depends on a set
of input samples (absolute index relative to the row's frame: phase - delay * P). Rows are grouped by block
(min(set) mod P; two blocks per frame differ by 64) with block-internal time j = s - min(set), and each block's
64 x 64 complex matrix (from the DR/DI -> DOR/DOI 2x2 pattern) is matched against the DFT with free input and output
permutations (frame.match_dft). Exploratory."""
import pickle, collections, sys, json
from fractions import Fraction as Fr
sys.path.insert(0, '../invfold'); import frame
d = pickle.load(open('out/fft_H.pkl', 'rb')); H = d['H']; P = 128
def parse_in(x): return x.split("'")[3], int(x.split(', ')[2].rstrip(')'))
def parse_out(r): return r.split("'")[1], int(r.split(', ')[1].rstrip(')'))
rows = collections.defaultdict(dict)
for (r, x), (dl, c) in H.items():
    p, ph = parse_in(x); rows[r][(p, -dl * P + ph)] = Fr(c)
blocks = collections.defaultdict(lambda: collections.defaultdict(dict))   # key -> port -> output index -> {(port, j): c}
for r, ent in rows.items():
    S = {s for (_, s) in ent}; m0 = min(S); key = (m0 % P, len(S))
    po, i = parse_out(r)
    blocks[key][po][i] = {(p, s - m0): c for (p, s), c in ent.items()}
out = {}
for key, ports in sorted(blocks.items()):
    nR, nI = len(ports.get('DOR', {})), len(ports.get('DOI', {}))
    common = sorted(set(ports.get('DOR', {})) & set(ports.get('DOI', {})))
    N = key[1]
    if len(common) != N: out[str(key)] = {'rows DOR/DOI': (nR, nI), 'paired': len(common), 'note': 'not N outputs'}; print(key, out[str(key)]); continue
    C = []
    for i in common:
        rr, ri = ports['DOR'][i], ports['DOI'][i]; row = []
        for j in range(N):
            a = float(rr.get(('DR_ordered', j), 0)); b = float(ri.get(('DR_ordered', j), 0))
            c = float(rr.get(('DI_ordered', j), 0)); dd = float(ri.get(('DI_ordered', j), 0))
            row.append(complex((a + dd) / 2, (b - c) / 2))
        C.append(row)
    dens = [c.denominator for ent in rows.values() for c in ent.values()]
    m = frame.match_dft(C, Fr(1, 1 << 15))
    out[str(key)] = {k: v for k, v in m.items() if k not in ('output_perm', 'input_perm')}
    out[str(key)]['output_perm_head'] = m.get('output_perm', [])[:16]; out[str(key)]['input_perm_head'] = m.get('input_perm', [])[:16]
    print(key, {k: (round(v, 7) if isinstance(v, float) else v) for k, v in out[str(key)].items()})
json.dump(out, open('out/fft_blocks.json', 'w'), indent=1, default=str)

# per-output identification: each (DOR_i, DOI_i) pair against one DFT bin on its own 64-sample window
import cmath, math
N = 64; w = cmath.exp(-2j * math.pi / N); bound_q = 2 ** -15
per = []
for r in rows:
    po, i = parse_out(r)
    if po != 'DOR': continue
    ri = "('DOI', %d)" % i
    if ri not in rows: continue
    S = sorted({s for (_, s) in rows[r]} | {s for (_, s) in rows[ri]}); m0 = S[0]
    if len(S) != N or S[-1] - m0 != N - 1: per.append((i, None, 'window not 64 consecutive', len(S))); continue
    C = []
    for j in range(N):
        a = float(rows[r].get(('DR_ordered', m0 + j), 0)); b = float(rows[ri].get(('DR_ordered', m0 + j), 0))
        c = float(rows[r].get(('DI_ordered', m0 + j), 0)); dd = float(rows[ri].get(('DI_ordered', m0 + j), 0))
        C.append(complex((a + dd) / 2, (b - c) / 2))
    s0 = C[0]
    f = int(round(-cmath.phase(C[1] / s0) / (2 * math.pi / N))) % N if abs(s0) > 1e-12 else None
    err = max(abs(C[j] - s0 * w ** (f * j)) for j in range(N)) if f is not None else 9
    per.append((i, f, err <= math.log2(N) * bound_q * abs(s0), (m0 % P), abs(s0), err))
ok = [p for p in per if p[1] is not None and p[2] is True]
print('outputs that are one DFT bin of their own 64-sample window: %d of %d' % (len(ok), len(per)))
print('scales |s|:', sorted({round(p[4], 6) for p in ok})[:4], ' max error %.3g' % max(p[5] for p in ok) if ok else '')
by_win = collections.defaultdict(list)
for p in ok: by_win[p[3]].append((p[0], p[1]))
for wkey, lst in sorted(by_win.items()):
    lst.sort(); fs = [f for _, f in lst]
    print('window start mod 128 = %d: %d outputs, bins (in output order, first 16) %s, distinct bins %d' % (wkey, len(lst), fs[:16], len(set(fs))))
json.dump({'per_output': per}, open('out/fft_bins.json', 'w'), default=str)

# merged blocks: rows whose windows start at m0 or m0 + 1 belong to one transform; time slot j = (s - m0) mod 64,
# i.e. the 8 shifted rows read sample m0 + 64 (the next block's first sample) in the slot of sample m0
print('--- merged blocks (window start m0 or m0 + 1), time slot j = (s - m0) mod 64 ---')
merged = collections.defaultdict(dict)
for r, ent in rows.items():
    po, i = parse_out(r); S = {s for (_, s) in ent}; m0 = min(S)
    base = m0 - 1 if (m0 - 1) % P in (8, 72) and (m0 % P) in (9, 73) else m0
    merged[base % P].setdefault(po, {})[i] = {(p, (s - base) % N): c for (p, s), c in ent.items()}
    merged[base % P].setdefault('shifted', set())
    if base != m0: merged[base % P]['shifted'].add(i)
summary = {}
for key, ports in sorted(merged.items()):
    common = sorted(set(ports.get('DOR', {})) & set(ports.get('DOI', {})))
    C = []
    for i in common:
        rr, ri = ports['DOR'][i], ports['DOI'][i]; row = []
        for j in range(N):
            a = float(rr.get(('DR_ordered', j), 0)); b = float(ri.get(('DR_ordered', j), 0))
            c = float(rr.get(('DI_ordered', j), 0)); dd = float(ri.get(('DI_ordered', j), 0))
            row.append(complex((a + dd) / 2, (b - c) / 2))
        C.append(row)
    m = frame.match_dft(C, Fr(1, 1 << 15)) if len(C) == N else {'match': False, 'reason': '%d rows' % len(C)}
    summary[key] = {'rows': len(C), 'rows_reading_next_block_sample': len(ports['shifted']), **{k: v for k, v in m.items() if k not in ('output_perm', 'input_perm')}}
    print(key, {k: (round(v, 7) if isinstance(v, float) else v) for k, v in summary[key].items()})
    if m.get('output_perm'): print('   output perm (first 16):', m['output_perm'][:16], ' input perm (first 16):', m['input_perm'][:16])
json.dump(summary, open('out/fft_blocks_merged.json', 'w'), indent=1, default=str)

def match_rows(C, N, grid):
    """M x N rows (M <= N) against DFT bins with one common time permutation t and per-row frequencies f (injective),
    scale s: C[k][j] ~ s w^(f(k) t(j)). Uses a column with t = 0 (constant over rows) and a row whose frequency is a
    unit mod N to read t. Returns (ok, f, t, s, err)."""
    M = len(C); best = None
    def rnd(z): return int(round(-cmath.phase(z) / (2 * math.pi / N))) % N if abs(z) > 1e-12 else None
    for j0 in range(N):
        s0 = sum(C[k][j0] for k in range(M)) / M
        if abs(s0) < 1e-12 or max(abs(C[k][j0] - s0) for k in range(M)) > 1e-3 * abs(s0): continue
        for k1 in range(M):
            # candidate t from row k1 assuming its frequency is u (a unit): t(j) = rnd(C[k1][j]/s0) * u^-1
            e = [rnd(C[k1][j] / s0) for j in range(N)]
            if None in e: continue
            for u in (1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37, 39, 41, 43, 45, 47, 49, 51, 53, 55, 57, 59, 61, 63):
                uinv = pow(u, -1, N); t = [(x * uinv) % N for x in e]
                if sorted(t) != list(range(N)): continue
                j1 = t.index(1)
                f = [rnd(C[k][j1] / s0) for k in range(M)]
                if None in f or len(set(f)) != M: continue
                err = max(abs(C[k][j] - s0 * w ** (f[k] * t[j])) for k in range(M) for j in range(N))
                if best is None or err < best[4]: best = (err <= math.log2(N) * grid * abs(s0), f, t, s0, err)
            if best and best[0]: return best
    return best or (False, None, None, None, None)
print('--- per window: one common time permutation, per-row DFT bins ---')
groups2 = collections.defaultdict(list)
for r in rows:
    po, i = parse_out(r)
    if po != 'DOR' or "('DOI', %d)" % i not in rows: continue
    ri = "('DOI', %d)" % i; S = sorted({s for (_, s) in rows[r]} | {s for (_, s) in rows[ri]})
    groups2[S[0]].append(i)
res2 = {}
for m0, idx in sorted(groups2.items()):
    C = []
    for i in sorted(idx):
        r, ri = "('DOR', %d)" % i, "('DOI', %d)" % i; row = []
        for j in range(N):
            a = float(rows[r].get(('DR_ordered', m0 + j), 0)); b = float(rows[ri].get(('DR_ordered', m0 + j), 0))
            c = float(rows[r].get(('DI_ordered', m0 + j), 0)); dd = float(rows[ri].get(('DI_ordered', m0 + j), 0))
            row.append(complex((a + dd) / 2, (b - c) / 2))
        C.append(row)
    ok, f, t, s0, err = match_rows(C, N, 2 ** -15)
    br = frame.bit_reverse_perm(N)
    tname = None if t is None else ('natural' if t == list(range(N)) else 'bit-reversed' if t == br else 'other')
    res2[m0] = {'outputs': len(idx), 'match': ok, 'scale': s0 and '%.6g%+.6gj' % (s0.real, s0.imag), 'max_error': err, 'time_order': tname, 't_head': t and t[:16], 'bins_head': f and f[:16]}
    print('window start %d (mod 128: %d): %s' % (m0, m0 % P, res2[m0]))
json.dump({str(k): v for k, v in res2.items()}, open('out/fft_windows.json', 'w'), indent=1, default=str)
