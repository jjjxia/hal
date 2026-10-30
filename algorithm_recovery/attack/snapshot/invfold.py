"""Inverse folding (PROPOSAL.md §2): per-cycle word equations of a time-multiplexed design -> single-rate algorithm graph.
Input (no instance or RTL names): equations.json [{T, target, smt2}] with target '<word> - i_D' (D input of a cutpoint
word) or 'top_module - <port>' (output port), and meta.json {window, widths, ports, write_enable}.
Steps: (1) signature of every target on relative time, smallest period P; (2) logical signals = write events
(word, slot); (3) every read of word v at cycle t is the last write of v before t, at slot phi' and sample n' -> source
((v, phi'), k = n - n'); every input read at cycle t -> input phase (port, phi(t)) at delay k; (4) per signal a node,
canonicalized to floor(sum c_i * src_i + c0) mod 2^w (dyadic c_i, signed sources), verified on full-range random
values; pure copies become delays. The slot origin is the first window cycle (the free choice: a retiming)."""
import re, json, math, random, collections, sys, threading
from fractions import Fraction
import numpy as np
import smt2

def signed(v, w): v &= (1 << w) - 1; return v - (1 << w) if v >> (w - 1) else v

class Folded:
    def __init__(self, eqs, meta, origin=0):
        self.meta, self.W = meta, meta['widths']
        self.win = meta['window']; self.ce = meta['write_enable']; self.ports_in = meta['ports']['in']
        self.eq = {(e['T'], e['target']): e.get('smt2', e.get('text')) for e in eqs}
        self.fmt = 'smt2' if 'smt2' in eqs[0] else 'hal'
        self.targets = sorted({e['target'] for e in eqs})
        self.alias = meta.get('alias', {})   # symbol base (an output port pin group) -> [(word, bit)] per port bit
        pref = sorted([re.escape(w + '_o_Q') for w in self.W] + [re.escape(pp + p) for p in self.ports_in for pp in ('top_module_', '')] + [re.escape(a) for a in self.alias], key=len, reverse=True)
        self.sym_re = re.compile(r'\b(%s)_(\d+)\b' % '|'.join(pref))
        self.origin = origin
        self.report = {'errors': [], 'notes': []}

    # ---- (1) period ----
    def signature(self, T, tgt):
        w = tgt.split(' - ')[0]
        if ' - i_D' in tgt and not self.ce[w][T]: return 'HOLD'
        return self.sym_re.sub(lambda m: '%s@%d' % (m.group(1), T - int(m.group(2))), self.eq[(T, tgt)])
    def period(self, certified=None):
        if certified:   # period proved by the certificate (NetNotary schedule); the window may hold one period only
            self.P = certified; self.report['notes'].append('period %d from certificate' % certified); return certified
        sig = {(T, t): hash(self.signature(T, t)) for T in self.win for t in self.targets}
        L = len(self.win)
        for P in range(1, L // 3 + 1):
            if all(sig[(T, t)] == sig[(T + P, t)] for T in self.win[:-P] for t in self.targets):
                self.P = P; return P
        self.P = None; self.report['errors'].append('no period <= window/3 (data-dependent control?)'); return None

    # ---- (2)/(3) slots, write events, resolution ----
    def slot(self, t): d = t - self.win[0] - self.origin; return d % self.P, d // self.P
    def last_write(self, v, t):
        for tw in range(t - 1, -1, -1):
            if self.ce[v][tw]: return tw
        return None
    def resolve(self, T, tgt):
        """symbol -> source key; source keys: ('sig', word, phi, k) or ('in', port, phi, k)"""
        n = self.slot(T)[1]; out = {}
        for m in {(mm.group(1), int(mm.group(2))) for mm in self.sym_re.finditer(self.eq[(T, tgt)])}:
            base, t = m
            if base in self.alias:   # a port that is the Q bits of words: one source per word, assembled bitwise
                comps = []
                for w, b in self.alias[base]:
                    tw = self.last_write(w, t)
                    if tw is None: self.report['errors'].append('read of %s at %d resolves to no write' % (w, t)); comps.append(('init', w, t, 0, b)); continue
                    ph, nn = self.slot(tw); comps.append(('sig', w, ph, n - nn, b))
                out['%s_%d' % m] = ('cat', base, tuple(comps)); continue
            if not base.endswith('_o_Q'):
                p = base[len('top_module_'):] if base.startswith('top_module_') else base; ph, nn = self.slot(t); out['%s_%d' % m] = ('in', p, ph, n - nn)
            else:
                v = base[:-len('_o_Q')]; tw = self.last_write(v, t)
                if tw is None:   # value from the initial state, not a write
                    self.report['errors'].append('read of %s at %d resolves to no periodic write' % (v, t)); out['%s_%d' % m] = ('init', v, t, 0); continue
                ph, nn = self.slot(tw); out['%s_%d' % m] = ('sig', v, ph, n - nn)
        return out
    def build(self):
        P = self.P; self.nodes = {}; self.outputs = {}
        occ = collections.defaultdict(list)
        for T in self.win:
            for tgt in self.targets:
                w = tgt.split(' - ')[0]
                if ' - i_D' in tgt:
                    if not self.ce[w][T]: continue
                    key = ('sig', w, self.slot(T)[0])
                else: key = ('out', tgt.split(' - ')[1], self.slot(T)[0])
                r = self.resolve(T, tgt)
                rel = {self.sym_re.sub(lambda m: '%s@%d' % (m.group(1), T - int(m.group(2))), s): src for s, src in r.items()}
                occ[key].append((T, tgt, r, tuple(sorted(rel.items()))))
        for key, lst in occ.items():
            if len({x[3] for x in lst}) != 1:
                self.report['errors'].append('%s resolves differently in different periods' % (key,))
            T, tgt, r, _ = lst[0]
            (self.nodes if key[0] == 'sig' else self.outputs)[key[1:]] = {'T': T, 'tgt': tgt, 'res': r, 'count': len(lst)}
        # input phases
        ph = collections.defaultdict(set)
        for d in list(self.nodes.values()) + list(self.outputs.values()):
            for s in d['res'].values():
                if s[0] == 'in': ph[s[1]].add(s[2])
        self.input_phases = {p: sorted(v) for p, v in ph.items()}
        for p, v in ph.items():
            if len(v) != 1: self.report['errors'].append('input %s read in %d phases %s (multi-rate input?)' % (p, len(v), v))
        # write events never read and signals read but never written (in the window)
        written = {('sig',) + k for k in self.nodes}
        for d in list(self.nodes.values()) + list(self.outputs.values()):
            for s in [c for x in d['res'].values() for c in (x[2] if x[0] == 'cat' else (x,))]:
                if s[0] == 'sig' and s[:3] not in written: self.report['errors'].append('read of unwritten signal %s' % (s[:3],))

    # ---- (4) node canonicalization ----
    def fit(self, key, d, rng):
        widths = self.widths_of(d)
        fn, syms, w = self.compile(d, widths)
        srcs = sorted({d['res'][s] for s in syms}, key=str)
        sw = {}
        for s in syms: sw[d['res'][s]] = widths[s]
        def run(vals):   # vals: source -> signed value
            return fn({s: vals[d['res'][s]] & ((1 << widths[s]) - 1) for s in syms})
        if not looks_linear(lambda v: signed(run(v), w), srcs, sw, rng): return None
        return fit_floor_linear(run, srcs, sw, w, rng)
    def widths_of(self, d):
        widths = {}
        for sym in d['res']:
            base = sym.rsplit('_', 1)[0]
            widths[sym] = len(self.alias[base]) if base in self.alias else self.W[base[:-len('_o_Q')]] if base.endswith('_o_Q') else self.ports_in[base[len('top_module_'):] if base.startswith('top_module_') else base]
        return widths
    def compile(self, d, widths):
        text = self.eq[(d['T'], d['tgt'])]
        if self.fmt == 'smt2': return smt2.compile_term(text, widths)
        import halforms
        fn, syms, w = halforms.compile_text(text, widths)
        perm = self.meta.get('dq_perm', {}).get(d['tgt'].split(' - ')[0]) if ' - i_D' in d['tgt'] else None
        if perm and perm != list(range(len(perm))):   # D-group bit i is Q-group bit perm[i]
            g = fn; fn = lambda E: (lambda v: sum(((v >> i) & 1) << j for i, j in enumerate(perm)))(g(E))
        return fn, syms, w

def looks_linear(run, srcs, sw, rng, trials=6):
    """Cheap necessary test for floor-linearity on small values: f(a+b) - f(a) - f(b) + f(0) stays within the
    rounding error. Saves the full fit on Boolean-heavy nodes (ciphers)."""
    small = lambda k: rng.randrange(-(1 << min(6, sw[k] - 2)), 1 << min(6, sw[k] - 2)) if sw[k] > 2 else 0
    zero = {k: 0 for k in srcs}; f0 = run(zero)
    for _ in range(trials):
        a = {k: small(k) for k in srcs}; b = {k: small(k) for k in srcs}; ab = {k: a[k] + b[k] for k in srcs}
        if any(abs(v) >= (1 << (sw[k] - 1)) for k, v in ab.items()): continue
        if abs(run(ab) - run(a) - run(b) + f0) > len(srcs) + 2: return False
    return True

def fit_floor_linear(run, srcs, sw, w, rng):
    """floor(sum c*s + c0) mod 2^w over signed sources, dyadic c; None if no fit. Same method as eq2algo/lib/pipeline.py."""
    # least squares on small values; shrink the magnitude until the result does not wrap (residual below rounding)
    for e in range(max(list(sw.values()) + [4]) - 4, 0, -1):
        X, y = [], []
        for _ in range(400):
            vals = {k: rng.randrange(-(1 << min(e, sw[k] - 1)), 1 << min(e, sw[k] - 1)) for k in srcs}
            X.append([vals[k] for k in srcs] + [1]); y.append(signed(run(vals), w))
        A, b = np.array(X, float), np.array(y, float)
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        if np.max(np.abs(A @ sol - b)) < 2: break
    else: return None
    coeffs = {}
    for k, c in zip(srcs, sol[:-1]):
        f = None
        for e2 in range(0, 17):
            g = Fraction(round(c * (1 << e2)), 1 << e2)
            if abs(float(g) - c) < 2e-3: f = g; break
        if f is None: return None
        if f != 0: coeffs[k] = f
    den = max([c.denominator for c in coeffs.values()] + [1])
    consts = sorted({Fraction(round(sol[-1] * den + j), den) for j in (-1, 0, 1)} | {Fraction(0)}, key=abs)
    for mode in ('exact', 'floor', 'round'):
        if mode == 'exact' and den != 1: continue
        for c0 in consts:
            if mode == 'exact' and c0.denominator != 1: continue
            ok = True
            for _ in range(3000):
                vals = {k: signed(rng.getrandbits(sw[k]), sw[k]) for k in srcs}
                x = sum(c * vals[k] for k, c in coeffs.items()) + c0
                r = (math.floor(x) if mode != 'round' else math.floor(x + Fraction(1, 2))) & ((1 << w) - 1)
                if r != run(vals): ok = False; break
            if ok: return {'coeffs': coeffs, 'const': c0, 'mode': mode, 'width': w}
    return None

def recover(eqs, meta, origin=0, seed=1, period=None):
    F = Folded(eqs, meta, origin); rng = random.Random(seed)
    if F.period(period) is None: return F
    F.build()
    F.fits = {}
    for key, d in [(('sig',) + k, d) for k, d in F.nodes.items()] + [(('out',) + k, d) for k, d in F.outputs.items()]:
        f = F.fit(key, d, rng)
        if f is None: F.report.setdefault('nonlinear', []).append(str(key))   # kept as an opaque node function
        F.fits[key] = f
    collapse_copies(F)
    return F

def collapse_copies(F):
    """A node that is exactly one source with coefficient 1 (same width) is a delay: readers read its source."""
    alias = {}
    for key, f in F.fits.items():
        if key[0] == 'out' or f is None: continue
        if f['mode'] == 'exact' and f['const'] == 0 and len(f['coeffs']) == 1:
            (src, c), = f['coeffs'].items()
            if c == 1 and src[0] == 'sig' and F.W[src[1]] == f['width']: alias[key] = src
    def follow(src):
        k = 0
        while src[:3] in alias:
            a = alias[src[:3]]; k += src[3]; src = a
        return src[:3] + (src[3] + k,)
    F.copies = {str(k): str(v) for k, v in alias.items()}
    for key, f in F.fits.items():
        if f is None: continue
        new = collections.defaultdict(Fraction)
        for s, c in f['coeffs'].items(): new[follow(s) if s[0] == 'sig' else s] += c
        f['coeffs'] = {s: c for s, c in new.items() if c}
    for key in alias: del F.fits[key]

def run_big(fn, *a):
    sys.setrecursionlimit(1000000); threading.stack_size(1 << 28); out = {}
    def go():
        try: out['r'] = fn(*a)
        except BaseException as e: out['e'] = e
    t = threading.Thread(target=go); t.start(); t.join()
    if 'e' in out: raise out['e']
    return out['r']

def run_graph(F, inputs, init, n_from, n_to):
    """Execute the recovered graph with every node's own expression (linear or not) for samples n_from..n_to-1.
    inputs(port, cycle) -> value; init(word, cycle) -> value written at that cycle, used for samples < n_from (the
    trace supplies the state before the run, as in Theorem 1). Returns val[(word, phi, n)] (unsigned)."""
    P, T0 = F.P, F.win[0] + F.origin
    comp = {}
    for (w, phi), d in F.nodes.items():
        widths = F.widths_of(d); fn, syms, width = F.compile(d, widths); comp[(w, phi)] = (fn, d['res'], widths)
    val = {}
    order = sorted(F.nodes, key=lambda v: v[1])   # zero-delay edges go to later slots (Proposition: well-formedness)
    def src_value(src, n):
        if src[0] == 'cat':
            return sum(((src_value(c[:4], n) >> c[4]) & 1) << i for i, c in enumerate(src[2]))
        kind, w, phi, k = src; m = n - k; c = T0 + m * P + phi
        if kind == 'in': return inputs(w, c)
        if m < n_from: return init(w, c)
        return val[(w, phi, m)]
    for n in range(n_from, n_to):
        for v in order:
            fn, res, widths = comp[v]
            env = {s: src_value(src, n) & ((1 << widths[s]) - 1) for s, src in res.items()}
            val[v + (n,)] = fn(env)
    return val
