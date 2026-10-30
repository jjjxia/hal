"""Frame semantics and polyphase classification (extension X1).
The recovered graph is single-rate over frames (P cycles). An input port read in M phases is an M-vector per frame;
an output port shows one component per distinct value it observes. For the linear part we compute the frame-level
polyphase matrix H(z) (z = one frame) from input samples to output samples, both in stream order, and classify:
- pseudo-circulant after some output alignment s  =>  LTI at the stream rate (one sample per input phase);
  the stream filter is G(z) = sum_r z^-r E_r(z^M) with E_r = H_r0 (Vaidyanathan's blocking theorem, inverted);
- constant H (no memory across frames)            =>  block transform; matched against DFT_N up to output
  permutation (reported), common scale and direction, within a fixed error bound from the coefficient grid;
- otherwise                                       =>  periodically time-varying / multirate (reported, not named).
Stream order: inputs by phase; output components by observation time tau = phase + P * k (k = frames between
the observed write and the observation), first observation of each component."""
import cmath, math
from fractions import Fraction as Fr
from identify import RF, solve, zk, padd, pmul, trim

def frame_io(F):
    fits = {k: f for k, f in F.fits.items() if f is not None}
    ins = {p: sorted(v) for p, v in F.input_phases.items()}
    outs = {}
    for (kind, port, phi), f in sorted(fits.items(), key=lambda kv: str(kv[0])):
        if kind != 'out': continue
        sig = [s for s in f['coeffs'] if s[0] == 'sig']
        if not sig: continue
        kmin = min(s[3] for s in sig)
        key = (tuple(sorted((s[:3], s[3] - kmin, c) for s, c in f['coeffs'].items() if s[0] == 'sig')), f['const'], f['mode'])
        tau = phi + F.P * kmin
        cur = outs.setdefault(port, {})
        if key not in cur or tau < cur[key]['tau']: cur[key] = {'tau': tau, 'coeffs': key[0], 'phase': phi}
    return ins, {p: sorted(c.values(), key=lambda x: x['tau']) for p, c in outs.items()}

def frame_matrix(F):
    """H[(out port, i)][(in port, j)] as RF in z^-1 (frame delays); None if a node needed is not linear."""
    fits = {k: f for k, f in F.fits.items() if f is not None}
    if any(f is None for k, f in F.fits.items() if k[0] == 'sig'): return None, 'nonlinear nodes present'
    nodes = sorted(k for k in fits if k[0] == 'sig')
    ins, outs = frame_io(F)
    inputs = [('in', p, phi) for p in sorted(ins) for phi in ins[p]]
    eqs = {}
    for v in nodes:
        e = {}
        for s, c in fits[v]['coeffs'].items():
            u = s[:3]; e[u] = e.get(u, RF([0])) + zk(s[3], c)
        eqs[v] = e
    H = solve(nodes, eqs, inputs)
    M = {}
    for p, comps in outs.items():
        for i, comp in enumerate(comps):
            row = {}
            for u, k, c in comp['coeffs']:
                for x in inputs:
                    row[x] = row.get(x, RF([0])) + zk(k, c) * H[u][x]
            M[(p, i)] = row
    return {'H': M, 'ins': ins, 'outs': {p: len(c) for p, c in outs.items()}, 'inputs': inputs}, None

def upsample(r, M):
    up = lambda p: trim(sum(([x] + [Fr(0)] * (M - 1) for x in p), [])) if M > 1 else p
    return RF(up(r.n), up(r.d))

def pseudo_circulant(Hm, M):
    """Hm: M x M list of RF (rows = output stream index, cols = input stream index). Returns (s, G) for the smallest
    output alignment s that makes it pseudo-circulant, else None."""
    for s in range(M):
        Hs = [Hm[i - s] if i - s >= 0 else [zk(1) * h for h in Hm[i - s + M]] for i in range(M)]
        E = [Hs[r][0] for r in range(M)]
        ok = all((Hs[i][j] - (E[i - j] if i >= j else zk(1) * E[M - (j - i)])).zero() for i in range(M) for j in range(M))
        if ok:
            G = RF([0])
            for r in range(M): G = G + zk(r) * upsample(E[r], M)
            return s, G
    return None

def interleave_factor(num, den):
    """largest m > 1 such that num and den only have nonzero coefficients at multiples of m (else 1)"""
    idx = [i for i, x in enumerate(num) if x != 0] + [i for i, x in enumerate(den) if x != 0]
    g = 0
    for i in idx: g = math.gcd(g, i)
    return g if g > 1 else 1

def monomial(r):
    """(d, c) if r = c z^-d, else None"""
    if len(r.d) != 1: return None
    nz = [(i, x) for i, x in enumerate(r.n) if x != 0]
    if len(nz) > 1: return None
    return nz[0] if nz else (None, Fr(0))

def row_col_delays(H, inputs):
    """True and (row delays, column delays) if every nonzero entry is c z^-(a_i + b_j), else (False, None, None)."""
    rows = sorted(H); ent = {}
    for i, r in enumerate(rows):
        for j, x in enumerate(inputs):
            m = monomial(H[r][x])
            if m is None: return False, None, None
            if m[0] is not None: ent[(i, j)] = m[0]
    a, b = {}, {}
    for (i, j), d in sorted(ent.items()):
        if i not in a and j not in b: a[i] = 0; b[j] = d
        elif i in a and j not in b: b[j] = d - a[i]
        elif j in b and i not in a: a[i] = d - b[j]
        elif a[i] + b[j] != d: return False, None, None
    return True, [a.get(i, 0) for i in range(len(rows))], [b.get(j, 0) for j in range(len(inputs))]

def bit_reverse_perm(N):
    b = N.bit_length() - 1
    return [int(format(i, '0%db' % b)[::-1], 2) for i in range(N)]

def match_dft(C, grid):
    """C: N x N complex (rows = outputs, cols = inputs, both in stream order). Find s, output permutation (row k ->
    frequency f(k)) and input permutation (column j -> time t(j)) with C[k][j] ~ s w^(f(k) t(j)), w = exp(-2 pi i/N),
    error bound log2(N) * grid * max(1, |s|). Inverse transforms appear as the frequency permutation f -> -f (the two
    differ only by that permutation and the scale, so the direction is reported as a property of the permutation)."""
    N = len(C); w = cmath.exp(-2j * math.pi / N)
    def rnd(z): return int(round(-cmath.phase(z) / (2 * math.pi / N))) % N if abs(z) > 1e-9 else None   # z = w^e, w = exp(-2 pi i/N)
    best = None
    for j0 in range(N):                       # column with t = 0: all rows equal s
        s0 = sum(C[k][j0] for k in range(N)) / N
        if abs(s0) < 1e-9: continue
        for j1 in range(N):                   # column with t = 1 gives f(k)
            if j1 == j0: continue
            f = [rnd(C[k][j1] / s0) for k in range(N)]
            if None in f or sorted(f) != list(range(N)): continue
            k1 = f.index(1)
            t = [rnd(C[k1][j] / s0) for j in range(N)]
            if None in t or sorted(t) != list(range(N)): continue
            err = max(abs(C[k][j] - s0 * w ** (f[k] * t[j])) for k in range(N) for j in range(N))
            if best is None or err < best[0]: best = (err, f, t, s0)
    if best is None: return {'match': False, 'reason': 'no bijective time/frequency assignment'}
    err, f, t, s0 = best
    bound = math.log2(N) * grid * max(1, abs(s0))
    br = bit_reverse_perm(N)
    name = lambda p: 'natural' if p == list(range(N)) else 'bit-reversed' if p == br else 'negated (inverse direction)' if p == [(-x) % N for x in range(N)] else 'other'
    return {'match': err <= bound, 'N': N, 'scale': '%.6g%+.6gj' % (s0.real, s0.imag), 'output_order': name(f), 'input_order': name(t),
            'output_perm': f, 'input_perm': t, 'max_error': err, 'bound': bound}

def classify_frame(F, pairs=None):
    """pairs: optional [((in_re, in_im), (out_re, out_im))] port pairs to read as complex signals."""
    fm, why = frame_matrix(F)
    if fm is None: return {'class': 'nonlinear frame map', 'reason': why}
    H, ins, outs = fm['H'], fm['ins'], fm['outs']
    res = {'inputs_per_frame': {p: len(v) for p, v in ins.items()}, 'outputs_per_frame': outs}
    # block transform: every entry c z^-d with d = a_row + b_col (per-output and per-input delays, e.g. a pipelined
    # transform whose latency crosses frame boundaries at some origins; attack x1_block_origin)
    allc, rowd, cold = row_col_delays(H, [x for x in fm['inputs']])
    res['row_delays'] = rowd; res['col_delays'] = cold
    if len(ins) == 1 and len(outs) == 1:
        (pi, phs), (po, no) = list(ins.items())[0], list(outs.items())[0]
        if len(phs) == no:
            Mx = [[H[(po, i)][('in', pi, phi)] for phi in phs] for i in range(no)]
            pc = pseudo_circulant(Mx, no)
            if pc:
                s, G = pc
                d = next(i for i, x in enumerate(G.n) if x != 0)
                res.update({'class': 'LTI at stream rate' if no > 1 else 'LTI at frame rate', 'block': no, 'output_alignment': s,
                            'G_delay': d, 'G_num': [str(x) for x in G.n[d:]], 'G_den': [str(x) for x in G.d]})
                if no > 1:
                    m = interleave_factor(G.n[d:], G.d)
                    res['ambiguities'] = ['stream order of the %d input phases assumed; if the protocol holds one input sample over several phases the stream filter differs' % no]
                    if m > 1: res['ambiguities'].append('G is a polynomial in z^-%d: equally explained as %d interleaved channels sharing H(z) with z^-%d -> z^-1' % (m, m, m))
                return res
    if allc and pairs:
        tries = []
        for (ir, ii) in (pairs[0][0], pairs[0][0][::-1]):
            for (orr, oi) in (pairs[0][1], pairs[0][1][::-1]):
                r = block_dft(H, ins, outs, ir, ii, orr, oi)
                if r: tries.append(((not r.get('dft', {}).get('match', False), r.get('dft', {}).get('max_error', 9e9)), (ir, ii, orr, oi), r))
        if tries:
            tries.sort(key=lambda x: x[0]); _, orient, r = tries[0]
            res.update(r); res['orientation'] = {'input (re, im)': orient[:2], 'output (re, im)': orient[2:],
                                                 'equally good orientations': sum(1 for t in tries if t[0] == tries[0][0])}
            return res
    res.update({'class': 'block linear map' if allc else 'periodically time-varying / multirate linear map'})
    return res

def block_dft(H, ins, outs, ir, ii, orr, oi):
    if True:
        N = len(ins[ir])
        if len(ins[ii]) == N and outs.get(orr) == N and outs.get(oi) == N:
            val = lambda o, i, p, phi: float(monomial(H[(o, i)][('in', p, phi)])[1])
            C = []; antilinear = False
            for k in range(N):
                row = []
                for j, (pr, pim) in enumerate(zip(ins[ir], ins[ii])):
                    a, b = val(orr, k, ir, pr), val(oi, k, ir, pr)        # response to a real unit input
                    c, d = val(orr, k, ii, pim), val(oi, k, ii, pim)      # response to an imaginary unit input
                    if abs(c + b) > 1e-12 or abs(d - a) > 1e-12: antilinear = True
                    row.append(complex(a, b))
                C.append(row)
            if antilinear: return {'class': 'real-linear block map (not complex-linear)'}
            dens = [monomial(h)[1].denominator for row in H.values() for h in row.values()]
            grid = 1 / max(dens)
            return {'class': 'block transform', 'dft': match_dft(C, grid)}
    return None
