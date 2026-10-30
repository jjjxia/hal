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
import cmath, math, collections
from fractions import Fraction as Fr
from fractions import Fraction
from identify import RF, solve, zk, padd, pmul, trim

def frame_io(F):
    fits = {k: f for k, f in F.fits.items() if f is not None}
    ins = {p: sorted(v) for p, v in F.input_phases.items()}
    outs = {}
    for (kind, port, phi), f in sorted(fits.items(), key=lambda kv: str(kv[0])):
        if kind != 'out': continue
        sig = [s for s in f['coeffs'] if s[0] in ('sig', 'in')]   # outputs may read inputs directly (through memories)
        if not sig: continue
        kmin = min(s[3] for s in sig)
        key = (tuple(sorted((s[:3], s[3] - kmin, c) for s, c in f['coeffs'].items() if s[0] in ('sig', 'in'))), f['const'], f['mode'])
        tau = phi + F.P * kmin
        cur = outs.setdefault(port, {})
        if key not in cur or tau < cur[key]['tau']: cur[key] = {'tau': tau, 'coeffs': key[0], 'phase': phi}
    return ins, {p: sorted(c.values(), key=lambda x: x['tau']) for p, c in outs.items()}

def frame_matrix(F):
    """H[(out port, i)][(in port, j)] as RF in z^-1 (frame delays); None if a node needed is not linear."""
    fits = {k: f for k, f in F.fits.items() if f is not None}
    obs = getattr(F, 'observable', None)
    if any(f is None for k, f in F.fits.items() if k[0] == 'sig' and (obs is None or k in obs)): return None, 'nonlinear nodes present'
    nodes = sorted(k for k in fits if k[0] == 'sig' and k in getattr(F, 'observable', fits))
    if any(s[0] not in ('sig', 'in') for v in nodes for s in fits[v]['coeffs']) or \
       any(s[0] not in ('sig', 'in') for k, f in fits.items() if k[0] == 'out' for s in f['coeffs']):
        return None, 'partial-word sources (a memory lane or port alias covering part of a word)'
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
                    hux = (RF([1]) if u == x else RF([0])) if u[0] == 'in' else H[u][x]
                    row[x] = row.get(x, RF([0])) + zk(k, c) * hux
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
    """True and (row delays, column delays) if every nonzero entry is c z^-(a_i + b_j), else (False, None, None).
    BFS over the bipartite graph of nonzero entries (a greedy pass misses valid decompositions; adversarial review)."""
    rows = sorted(H); ent = {}
    for i, r in enumerate(rows):
        for j, x in enumerate(inputs):
            m = monomial(H[r][x])
            if m is None: return False, None, None
            if m[0] is not None: ent[(i, j)] = m[0]
    adj = collections.defaultdict(list)
    for (i, j), d in ent.items(): adj[('r', i)].append((('c', j), d)); adj[('c', j)].append((('r', i), d))
    val = {}
    for start in list(adj):
        if start in val: continue
        val[start] = 0; queue = [start]
        while queue:
            u = queue.pop()
            for v, d in adj[u]:
                want = d - val[u]          # a_i + b_j = d
                if v not in val: val[v] = want; queue.append(v)
                elif val[v] != want: return False, None, None
    return True, [val.get(('r', i), 0) for i in range(len(rows))], [val.get(('c', j), 0) for j in range(len(inputs))]

def bit_reverse_perm(N):
    b = N.bit_length() - 1
    return [int(format(i, '0%db' % b)[::-1], 2) for i in range(N)]

def match_dft(C, grid):
    """C: N x N complex (rows = outputs, cols = inputs, stream order). Find s, output permutation f and input permutation
    t with C[k][j] ~ s w^(f(k) t(j)), w = exp(-2 pi i/N). All solutions are collected and one canonical labeling is
    reported (input order natural > bit-reversed > other, then output order, then error): the DFT's symmetries
    (f, t) -> (-f, -t) and frame rotations otherwise make the label depend on the origin (adversarial review).
    Accept iff max error <= log2(N) * grid * |s| (grid = quantum of the node coefficients; the bound scales with s)."""
    N = len(C); w = cmath.exp(-2j * math.pi / N); br = bit_reverse_perm(N)
    def rnd(z): return int(round(-cmath.phase(z) / (2 * math.pi / N))) % N if abs(z) > 1e-12 else None
    def name(p):
        if p == list(range(N)): return 'natural'
        if p == br: return 'bit-reversed'
        if p == [(-x) % N for x in range(N)]: return 'negated (inverse direction)'
        for r in range(1, N):   # the frame origin rotates the stream: name rotations explicitly
            if p == [(x + r) % N for x in range(N)]: return 'natural rotated by %d' % r
            if p == [br[(x + r) % N] for x in range(N)] or p == [(br[x] + r) % N for x in range(N)]: return 'bit-reversed rotated by %d' % r
        return 'other'
    rank_of = lambda nm: 0 if nm == 'natural' else 1 if nm == 'bit-reversed' else 2 if nm.startswith('natural rotated') else 3 if nm.startswith('bit-reversed rotated') else 4 if nm.startswith('negated') else 5
    rank = collections.defaultdict(lambda: 5)
    sols = []
    for j0 in range(N):
        s0 = sum(C[k][j0] for k in range(N)) / N
        if abs(s0) < 1e-12: continue
        for j1 in range(N):
            if j1 == j0: continue
            f = [rnd(C[k][j1] / s0) for k in range(N)]
            if None in f or sorted(f) != list(range(N)): continue
            k1 = f.index(1)
            t = [rnd(C[k1][j] / s0) for j in range(N)]
            if None in t or sorted(t) != list(range(N)): continue
            err = max(abs(C[k][j] - s0 * w ** (f[k] * t[j])) for k in range(N) for j in range(N))
            sols.append(((rank_of(name(t)), rank_of(name(f)), err), f, t, s0, err))
    if not sols: return {'match': False, 'reason': 'no bijective time/frequency assignment'}
    sols.sort(key=lambda x: x[0]); _, f, t, s0, err = sols[0]
    bound = math.log2(N) * grid * abs(s0)
    return {'match': err <= bound, 'N': N, 'scale': '%.6g%+.6gj' % (s0.real, s0.imag), 'output_order': name(f), 'input_order': name(t),
            'output_perm': f, 'input_perm': t, 'max_error': err, 'bound': bound, 'equivalent_labelings': len(sols)}

def classify_frame(F, pairs=None):
    """pairs: optional [((in_re, in_im), (out_re, out_im))] port pairs to read as complex signals."""
    fm, why = frame_matrix(F)
    if fm is None: return {'class': 'nonlinear frame map', 'reason': why}
    dens = [c.denominator for k, f in F.fits.items() if f is not None and k in getattr(F, 'observable', F.fits) for c in f['coeffs'].values()]
    grid = Fraction(1, max(dens + [1]))   # quantum of the node coefficients (entries are products of several)
    H, ins, outs = fm['H'], fm['ins'], fm['outs']
    res = {'inputs_per_frame': {p: len(v) for p, v in ins.items()}, 'outputs_per_frame': outs}
    # block transform: every entry c z^-d with d = a_row + b_col (per-output and per-input delays, e.g. a pipelined
    # transform whose latency crosses frame boundaries at some origins; attack x1_block_origin)
    allc, rowd, cold = row_col_delays(H, [x for x in fm['inputs']])
    res['row_delays'] = rowd; res['col_delays'] = cold
    if (len(ins) > 1 or len(outs) > 1) and all(len(v) == M0 for v in ins.values() for M0 in [len(next(iter(ins.values())))]) and \
       all(n == len(next(iter(ins.values()))) for n in outs.values()):
        Mn = len(next(iter(ins.values()))); Gm = {}
        for po in outs:
            for pi in ins:
                Mx = [[H[(po, i)][('in', pi, phi)] for phi in ins[pi]] for i in range(Mn)]
                if all(x.zero() for row in Mx for x in row): continue
                pc = pseudo_circulant(Mx, Mn)
                if not pc: Gm = None; break
                G = pc[1]; d = next((i for i, x in enumerate(G.n) if x != 0), 0)
                Gm['%s <- %s' % (po, pi)] = {'delay': d, 'num': [str(x) for x in G.n[d:]], 'den': [str(x) for x in G.d]}
            if Gm is None: break
        if Gm:   # every port pair is LTI at the stream rate (adversarial review: multi-port designs were called time-varying)
            res.update({'class': 'MIMO LTI at %s rate' % ('stream' if Mn > 1 else 'frame'), 'block': Mn, 'G': Gm,
                        'note': 'each entry up to a pure delay (G_obs (i))'})
            if pairs:
                (ir, ii), (orr, oi) = pairs[0]
                g = lambda a, b: Gm.get('%s <- %s' % (a, b))
                if g(orr, ir) and g(oi, ii) and g(orr, ir)['num'] == g(oi, ii)['num'] and g(orr, ir)['den'] == g(oi, ii)['den']:
                    res['complex_filter'] = {'real part (A)': g(orr, ir), 'imaginary part (B)': g(oi, ir), 'check -B': g(orr, ii)}
            return res
    if len(ins) == 1 and len(outs) == 1:
        (pi, phs), (po, no) = list(ins.items())[0], list(outs.items())[0]
        if len(phs) == no:
            Mx = [[H[(po, i)][('in', pi, phi)] for phi in phs] for i in range(no)]
            pc = pseudo_circulant(Mx, no)
            if pc:
                s, G = pc
                d = next(i for i, x in enumerate(G.n) if x != 0)
                res.update({'class': 'LTI at stream rate' if no > 1 else 'LTI at frame rate', 'block': no, 'output_alignment': s,
                            'note': 'G is determined up to a pure delay (observation latency is a boundary delay, G_obs (i))',
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
                r = block_dft(H, ins, outs, ir, ii, orr, oi, grid)
                if r: tries.append(((not r.get('dft', {}).get('match', False), r.get('dft', {}).get('max_error', 9e9)), (ir, ii, orr, oi), r))
        if tries:
            tries.sort(key=lambda x: x[0]); _, orient, r = tries[0]
            res.update(r); res['orientation'] = {'input (re, im)': orient[:2], 'output (re, im)': orient[2:],
                                                 'equally good orientations': sum(1 for t in tries if t[0] == tries[0][0])}
            return res
    if allc:   # a constant 0/1 matrix with one 1 per row and column is a reorder buffer: report the permutation
        rows = sorted(H); cols = fm['inputs']
        Mv = [[monomial(H[r][x])[1] for x in cols] for r in rows]
        if all(sorted(row) == [0] * (len(row) - 1) + [1] for row in Mv) and len(rows) == len(cols) and \
           all(sum(Mv[i][j] for i in range(len(rows))) == 1 for j in range(len(cols))):
            perm = [row.index(1) for row in Mv]
            n = len(perm)
            # smallest sub-block b with perm[i] = (i // b) * b + p[i % b] (e.g. ping-pong buffers: two blocks per frame)
            b = next(b for b in range(1, n + 1) if n % b == 0 and all(perm[i] // b == i // b and perm[i] % b == perm[i % b] for i in range(n)))
            p = perm[:b]; br = bit_reverse_perm(b) if b & (b - 1) == 0 else None
            res.update({'class': 'block permutation (reorder buffer)', 'perm': perm, 'sub_block': b, 'blocks_per_frame': n // b,
                        'order': 'identity' if p == list(range(b)) else 'bit-reversed' if p == br else 'other'})
            return res
    res.update({'class': 'block linear map' if allc else 'periodically time-varying / multirate linear map'})
    return res

def block_dft(H, ins, outs, ir, ii, orr, oi, grid=None):
    if True:
        N = len(ins[ir])
        if len(ins[ii]) == N and outs.get(orr) == N and outs.get(oi) == N:
            val = lambda o, i, p, phi: float(monomial(H[(o, i)][('in', p, phi)])[1])
            C = []; antilinear = False
            amax = max(abs(val(o, k, p, phi)) for o in (orr, oi) for k in range(N) for p in (ir, ii) for phi in ins[p]) or 1
            tol = 2 * math.log2(N) * grid * amax   # separately rounded constants (3-multiplier products) break exact symmetry
            for k in range(N):
                row = []
                for j, (pr, pim) in enumerate(zip(ins[ir], ins[ii])):
                    a, b = val(orr, k, ir, pr), val(oi, k, ir, pr)        # response to a real unit input
                    c, d = val(orr, k, ii, pim), val(oi, k, ii, pim)      # response to an imaginary unit input
                    if abs(c + b) > tol or abs(d - a) > tol: antilinear = True
                    row.append(complex((a + d) / 2, (b - c) / 2))
                C.append(row)
            if antilinear: return {'class': 'real-linear block map (not complex-linear)'}
            return {'class': 'block transform', 'dft': match_dft(C, grid), 'complex_linear_tolerance': tol}
    return None
