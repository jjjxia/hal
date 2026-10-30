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
        self.q_reorder = dict(meta.get('q_reorder', {}))   # word -> perm: raw Q bit j sits at canonical position perm[j]
        # memories (extension X2): read-output symbol bases resolve through the address-specific last write
        self.mem = {}
        for M, m in meta.get('memories', {}).items():
            if m.get('kind') == 'srl':   # shift register (SRL16E): output = data of the (depth)-th most recent shift
                sh = sorted(int(t) for t in m['shifts'])
                self.mem[M] = {'kind': 'srl', 'depth': m['depth'], 'shifts': sh, 'data': [tuple(b) for b in m['data']], 'outputs': m['outputs']}
                continue
            self.mem[M] = {'latency': m['latency'], 'mode': m.get('mode', 'read_first'),
                           'writes': {int(t): (a, [tuple(b) for b in bits]) for t, (a, bits) in m['writes'].items()},
                           'raddr': {int(t): a for t, a in m['raddr'].items()}, 'outputs': m['outputs']}
        self.mem_out = {base: M for M, m in self.mem.items() for base in m['outputs']}
        self.alias_w = {a: len(v) for a, v in self.alias.items()}
        for base, M in self.mem_out.items(): self.alias_w[base] = len(self.mem[M]['outputs'][base])
        # word read symbols: <word>_o_Q_<t> or <word>_o_Q_ordered_<t> (bit-order-propagated pin group)
        self.word_of = {w + sfx: w for w in self.W for sfx in ('_o_Q', '_o_Q_ordered')}
        pref = sorted([re.escape(b) for b in self.word_of] + [re.escape(pp + p) for p in self.ports_in for pp in ('top_module_', '')] + [re.escape(a) for a in list(self.alias) + list(self.mem_out)], key=len, reverse=True)
        self.sym_re = re.compile(r'\b(%s)_(\d+)\b' % '|'.join(pref))
        self.origin = origin; self._resolved_writes = []
        self.report = {'errors': [], 'notes': []}

    # ---- (1) period ----
    def signature(self, T, tgt):
        w = tgt.split(' - ')[0]
        if ' - i_D' in tgt and not self.ce[w][T]: return 'HOLD'
        return self.sym_re.sub(lambda m: '%s@%d' % (m.group(1), T - int(m.group(2))), self.eq[(T, tgt)])
    def period(self, certified=None):
        if certified:   # period proved by the certificate (NetNotary schedule); the window may hold one period only
            self.P = certified; self.report['notes'].append('period %d from certificate' % certified)
            if len(self.win) < 2 * certified:
                self.report['notes'].append('window holds fewer than two periods: cross-period consistency of expressions is not checked')
            self.certified = True
            return certified
        sig = {(T, t): hash(self.signature(T, t)) for T in self.win for t in self.targets}
        L = len(self.win)
        for P in range(1, L // 3 + 1):
            if all(sig[(T, t)] == sig[(T + P, t)] for T in self.win[:-P] for t in self.targets):
                self.P = P; return P
        self.P = None; self.report['errors'].append('no period <= window/3 (data-dependent control?)'); return None

    # ---- (2)/(3) slots, write events, resolution ----
    def slot(self, t): d = t - self.win[0] - self.origin; return d % self.P, d // self.P
    def sched(self, t):
        """cycle whose control (write enables, memory schedules) applies at cycle t: t itself, or with
        meta['periodic_extension'] the periodic image inside the window for t before it (steady-state resolution: the
        certified schedule is periodic; the trace before the window may still be in its start-up transient)"""
        w0 = self.win[0]
        if self.meta.get('periodic_extension') and t < w0: return w0 + (t - w0) % self.P
        return t
    def last_write(self, v, t):
        for tw in range(t - 1, -1 if not self.meta.get('periodic_extension') else t - 64 * self.P, -1):
            if self.ce[v][self.sched(tw)]: return tw
        return None
    def resolve(self, T, tgt):
        """symbol -> source key; source keys: ('sig', word, phi, k) or ('in', port, phi, k)"""
        n = self.slot(T)[1]; out = {}
        for m in {(mm.group(1), int(mm.group(2))) for mm in self.sym_re.finditer(self.eq[(T, tgt)])}:
            base, t = m
            if base in self.mem_out:
                comps = self.mem_bits(base, t, n)
                if comps is None: out['%s_%d' % m] = ('init', self.mem_out[base], t, 0); continue
                out['%s_%d' % m] = simplify_cat(('cat', base, tuple(comps)), self); continue
            if base in self.alias:   # a port that is the Q bits of words: one source per word, assembled bitwise
                comps = []
                for w, b in self.alias[base]:
                    tw = self.last_write(w, t)
                    if tw is None: self.report['errors'].append('read of %s at %d resolves to no write' % (w, t)); comps.append(('init', w, t, 0, b)); continue
                    ph, nn = self.slot(tw); comps.append(('sig', w, ph, n - nn, b))
                out['%s_%d' % m] = simplify_cat(('cat', base, tuple(comps)), self); continue
            if base not in self.word_of:
                p = base[len('top_module_'):] if base.startswith('top_module_') else base; ph, nn = self.slot(t); out['%s_%d' % m] = ('in', p, ph, n - nn)
            else:
                v = self.word_of[base]; tw = self.last_write(v, t)
                if tw is None:   # value from the initial state, not a write
                    self.report['errors'].append('read of %s at %d resolves to no periodic write' % (v, t)); out['%s_%d' % m] = ('init', v, t, 0); continue
                ph, nn = self.slot(tw); out['%s_%d' % m] = ('sig', v, ph, n - nn); self._resolved_writes.append(tw)
        return out
    def mem_bits(self, base, t, n, depth=0):
        """per-bit sources of memory read symbol `base` at cycle t, relative to the reader's sample n. Block RAM: read
        cycle t - latency, last write to that address before it (READ_FIRST) or at it (WRITE_FIRST). Shift register:
        the (depth)-th most recent shift before t. Write data may be word bits, port bits, constants, or bits of
        another memory's read symbol at the write cycle (an SRL fed by a RAM output), resolved recursively."""
        M = self.mem_out[base]; mm = self.mem[M]
        if depth > 16: self.report['errors'].append('memory chain too deep at %s' % base); return None
        lo = -1 if not self.meta.get('periodic_extension') else t - 64 * self.P
        if mm.get('kind') == 'srl':
            if 'shiftset' not in mm: mm['shiftset'] = set(mm['shifts'])
            cnt, tw = 0, None
            for x in range(t - 1, lo, -1):
                if self.sched(x) in mm['shiftset']:
                    cnt += 1
                    if cnt == mm['depth']: tw = x; break
            data = mm['data']; a = None
        else:
            tr = t - mm['latency']; a = mm['raddr'].get(self.sched(tr))
            strict = mm['mode'] == 'read_first'
            wr = mm['writes']
            tw = next((x for x in range(tr - (1 if strict else 0), lo, -1) if self.sched(x) in wr and wr[self.sched(x)][0] == a), None) if a is not None else None
            data = wr[self.sched(tw)][1] if tw is not None else None
        if tw is None:
            self.report['errors'].append('memory %s read at %d (address %s) resolves to no write' % (M, t, a)); return None
        comps = []; sub = {}
        for mb in mm['outputs'][base]:
            src = data[mb]; kind = src[0]
            if kind == 'port':
                ph, nn = self.slot(tw); comps.append(('in', src[1], ph, n - nn, src[2]))
            elif kind == 'word':   # the word's value during cycle tw: its last write before tw
                tw2 = self.last_write(src[1], tw)
                if tw2 is None: self.report['errors'].append('memory data %s at %d resolves to no write' % (src[1], tw)); comps.append(('const', 0, 0, 0, 0)); continue
                ph, nn = self.slot(tw2); comps.append(('sig', src[1], ph, n - nn, src[2])); self._resolved_writes.append(tw2)
            elif kind == 'memout':   # bit src[2] of memory read symbol src[1] at cycle tw
                if src[1] not in sub: sub[src[1]] = self.mem_bits(src[1], tw, n, depth + 1)
                r = sub[src[1]]
                comps.append(r[src[2]] if r is not None else ('const', 0, 0, 0, 0))
            else: comps.append(('const', src[2], 0, 0, 0))
        return comps

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
        if self.meta.get('periodic_extension'):
            # the certificate fixes the period of the FORMS; control comes from the trace and must be checked to repeat
            # with the same period after the window (adversarial review round 3: a register written every other frame
            # or a circular buffer otherwise resolves silently wrong)
            w0, P = self.win[0], self.P
            bad = []
            for v, ce in self.ce.items():
                if any(ce[t] != ce[t - P] for t in range(w0 + P, len(ce))): bad.append(v)
            for M, mm in self.mem.items():
                if mm.get('kind') == 'srl':
                    sh = set(mm['shifts']); hi = max(sh) if sh else 0
                    if any((t in sh) != ((t - P) in sh) for t in range(w0 + P, hi + 1)): bad.append(M)
                else:
                    hi = max(list(mm['writes']) + list(mm['raddr']) + [0])
                    for t in range(w0 + P, hi + 1):
                        a1, a0 = mm['writes'].get(t), mm['writes'].get(t - P)
                        if (a1 is None) != (a0 is None) or (a1 and (a1[0] != a0[0] or a1[1] != a0[1])) or mm['raddr'].get(t) != mm['raddr'].get(t - P):
                            bad.append(M); break   # address AND written-data mapping must repeat (round 4)
            if bad: self.report['errors'].append('control not periodic with the certified period after the window: %s' % bad[:8])
            if any(len(ce) < w0 + 2 * P for ce in self.ce.values()):
                self.report['errors'].append('trace ends less than one period after the window: periodicity of control unchecked')
            self.report['notes'].append('steady-state resolution: schedules before the window are the periodic extension of the window (checked periodic on the trace after it)')
        elif getattr(self, 'certified', False):   # attack a8: resolution before the window must follow the periodic schedule
            t_lo = min([src_t for src_t in self._resolved_writes] + [self.win[0]])
            for v in self.W:
                ce = self.ce.get(v)
                if ce is None: continue
                for t in range(max(t_lo, 0), self.win[0]):
                    if t + self.P < len(ce) and ce[t] != ce[t + self.P]:
                        self.report['errors'].append('write pattern of %s at cycle %d (before the window) differs from the periodic schedule' % (v, t)); break
        # input phases
        ph = collections.defaultdict(set)
        for d in list(self.nodes.values()) + list(self.outputs.values()):
            for s in d['res'].values():
                if s[0] == 'in': ph[s[1]].add(s[2])
        self.input_phases = {p: sorted(v) for p, v in ph.items()}
        for p, v in ph.items():   # frame semantics: an input read in m phases is an m-vector per frame (not an error)
            if len(v) != 1: self.report['notes'].append('input %s is a %d-vector per frame (phases %s)' % (p, len(v), v))
        # write events never read and signals read but never written (in the window)
        written = {('sig',) + k for k in self.nodes}
        for d in list(self.nodes.values()) + list(self.outputs.values()):
            for s in [c for x in d['res'].values() for c in (x[2] if x[0] == 'cat' else (x,))]:
                if s[0] == 'sig' and s[:3] not in written: self.report['errors'].append('read of unwritten signal %s' % (s[:3],))

    # ---- (4) node canonicalization ----
    def fit(self, key, d, rng):
        try: return self._fit(key, d, rng)
        except Exception as e:
            if type(e).__name__ == 'Unknown':   # UNKNOWN_OPERATION reached for some inputs: the equation is incomplete
                self.report.setdefault('incomplete', []).append(str(key)); return None
            raise
    def _fit(self, key, d, rng):
        if any(src[0] == 'init' or (src[0] == 'cat' and any(c[0] == 'init' for c in src[2])) for src in d['res'].values()):
            self.report.setdefault('unresolved', []).append(str(key)); return None
        widths = self.widths_of(d)
        fn, syms, w = self.compile(d, widths)
        # a symbol whose source is a concatenation (memory word, aliased port) is split into lanes: maximal runs of bits
        # from one source with consecutive bit indices. A lane covering a whole source is that source (so linear
        # fits see words, not their concatenation); a partial lane is an independent variable ('slice', ...)
        lanes = {s: lanes_of(d['res'][s], widths[s], self) for s in syms}
        sw = {}
        for s in syms:
            for key, off, wl, lo in lanes[s]:
                if key[0] in ('sig', 'in'): sw[key] = self.src_width(key)
        srcs = sorted(sw, key=str)
        def lane_val(key, v):
            if key[0] == 'sig' and key[1] in self.q_reorder: return self.to_raw(key[1], v & ((1 << self.W[key[1]]) - 1))
            return v
        def run(vals):   # vals: source -> signed value (canonical bit order); expressions see raw bit order
            return fn({s: sum((((lane_val(key, vals[key]) >> lo) & ((1 << wl) - 1)) if key[0] != 'const' else key[1] * ((1 << wl) - 1)) << off for key, off, wl, lo in lanes[s]) for s in syms})
        text = self.eq[(d['T'], d['tgt'])]
        if 'UNKNOWN' in text and self.fmt == 'hal':   # constructive check of the guards on the path to UNKNOWN (round 4)
            import halforms
            for env in halforms.unknown_witnesses(text, widths):
                fn(env)   # raises Unknown if this assignment reaches an UNKNOWN branch
        if 'UNKNOWN' in text:   # targeted samples: every source set to each constant of the expression (conditions such as
            consts = {int(c_[2:], 2) for c_ in re.findall(r'0b[01]+', text)}   # x == 3 guard UNKNOWN; random sampling misses them)
            for k in srcs:
                for cv in list(consts)[:256]:
                    run({**{kk: 0 for kk in srcs}, k: cv})   # raises Unknown if a guarded branch is reachable
        r = fit_floor_linear(run, srcs, sw, w, rng) if looks_linear(lambda v: signed(run(v), w), srcs, sw, rng) else None
        if r is None:   # sources that appear as low lanes of a concatenation are unsigned there (adversarial review b6)
            low = {key for s_ in syms for key, off, wl, lo in lanes[s_] if key[0] in ('sig', 'in') and off + wl < widths[s_]}
            if low:
                r = fit_floor_linear(run, srcs, sw, w, rng, unsigned=low)
                if r is not None: r['unsigned_sources'] = sorted(str(k) for k in low); r['unsigned_keys'] = set(low)
        return r
    def to_raw(self, w, v):
        p = self.q_reorder.get(w)
        return v if p is None else sum(((v >> c) & 1) << j for j, c in enumerate(p))
    def to_can(self, w, v):
        p = self.q_reorder.get(w)
        return v if p is None else sum(((v >> j) & 1) << c for j, c in enumerate(p))
    def src_width(self, key):
        return self.W[key[1]] if key[0] == 'sig' else self.ports_in[key[1]]
    def widths_of(self, d):
        widths = {}
        for sym in d['res']:
            base = sym.rsplit('_', 1)[0]
            widths[sym] = self.alias_w[base] if base in self.alias_w else self.W[self.word_of[base]] if base in self.word_of else self.ports_in[base[len('top_module_'):] if base.startswith('top_module_') else base]
        return widths
    def compile(self, d, widths):
        text = self.eq[(d['T'], d['tgt'])]
        if self.fmt == 'smt2': return smt2.compile_term(text, widths)
        import halforms
        fn, syms, w = halforms.compile_text(text, widths)
        wd = d['tgt'].split(' - ')[0]
        perm = self.meta.get('dq_perm', {}).get(wd) if ' - i_D' in d['tgt'] else None
        if perm and perm != list(range(len(perm))):   # D-group bit i is Q-group bit perm[i]
            g = fn; fn = lambda E: (lambda v: sum(((v >> i) & 1) << j for i, j in enumerate(perm)))(g(E))
        if ' - i_D' in d['tgt'] and wd in self.q_reorder and not getattr(self, '_raw_compile', False):
            g2 = fn; fn = lambda E: self.to_can(wd, g2(E))
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

def simplify_cat(src, F):
    """a concatenation whose bits are bits 0..w-1 of one source, in order, is that source"""
    comps = src[2]
    keys = {c[:4] for c in comps}
    if len(keys) == 1:
        (k,) = keys
        if k[0] in ('sig', 'in') and [c[4] for c in comps] == list(range(len(comps))) and F.src_width(k) == len(comps): return k
    return src

def lanes_of(src, width, F):
    """[(lane source key, bit offset in the symbol, lane width, low bit in the source)]"""
    if src[0] != 'cat': return [(src, 0, width, 0)]
    out = []; comps = src[2]; j = 0
    while j < len(comps):
        c = comps[j]; k = j
        while k + 1 < len(comps) and comps[k + 1][:4] == c[:4] and comps[k + 1][4] == comps[k][4] + 1: k += 1
        n = k - j + 1
        # the variable is always the whole source; a lane takes bits c[4] .. c[4]+n-1 of it. A word split over two
        # read symbols (an 18-bit value in a 16 + 2 bit memory layout) is thus one variable, and the node is linear
        # in it iff it uses the whole word
        if c[0] in ('sig', 'in'): out.append((c[:4], j, n, c[4]))
        else: out.append((('const', c[1]), j, n, 0))
        j = k + 1
    return out

def simplest_dyadic(lo, hi, lo_closed=True, hi_closed=False):
    """the dyadic rational with the smallest denominator in the interval (ties: smallest absolute value), or None"""
    if lo > hi or (lo == hi and not (lo_closed and hi_closed)): return None
    for m in range(0, 64):
        q = 1 << m
        a = math.ceil(lo * q); b = math.floor(hi * q)
        if not lo_closed and Fraction(a, q) == lo: a += 1
        if not hi_closed and Fraction(b, q) == hi: b -= 1
        if a <= b:
            if a <= 0 <= b: return Fraction(0)
            return Fraction(a, q) if abs(a) <= abs(b) else Fraction(b, q)
    return None

def coefficient_class(xs, ds, E=0):
    """Feasible c with some r in [0, 1) such that floor(c x + r) = d(x) for all probes (x, d), d = f(x e_a) - f(0)
    (the constant is f(0) + r; floor and round modes differ only in r). Fourier-Motzkin elimination of r over
    c x + r >= d, c x + r < d + 1, 0 <= r < 1. Returns (lo, lo_closed, hi, hi_closed); empty if lo > hi."""
    lo, hi, lc, hc = Fraction(-10**30), Fraction(10**30), True, True
    def up(v, closed):
        nonlocal hi, hc
        if v < hi or (v == hi and not closed): hi, hc = v, closed
    def down(v, closed):
        nonlocal lo, lc
        if v > lo or (v == lo and not closed): lo, lc = v, closed
    L = [(x, d) for x, d in zip(xs, ds)] + [(0, 0)]
    # r >= d_i - c x_i (closed), r >= 0 (closed);  r < d_j + 1 - c x_j (open), r < 1 (open)
    lows = [(-x, Fraction(d - E)) for x, d in L] + [(0, Fraction(0))]          # r >= A c + B with (A, B)
    highs = [(-x, Fraction(d + 1 + E)) for x, d in L] + [(0, Fraction(1))]     # r <  A c + B   (E: tolerated error)
    for a1, b1 in lows:
        for a2, b2 in highs:      # a1 c + b1 < a2 c + b2  <=>  (a1 - a2) c < b2 - b1
            k = a1 - a2; v = b2 - b1
            if k > 0: up(v / k, False)
            elif k < 0: down(v / k, False)
            elif not v > 0: return (Fraction(1), True, Fraction(0), True)   # infeasible
    return (lo, lc, hi, hc)

def class_by_bisection(xs, ds, E=0, guess=None):
    """Exact coefficient class for many probes in O(n) per test: for fixed c the feasible r form the interval
    [max(0, max(d - E - c x)), min(1, min(d + 1 + E - c x))); its length is concave in c, so the feasible c form an
    interval. Find a feasible point (from the least-squares slope), then bisect both ends to 2^-48 with exact
    rationals and return the simplest dyadic strictly inside, or None."""
    def feas(c):
        rl, rh = Fraction(0), Fraction(1)
        for x, d in zip(xs, ds):
            a = d - E - c * x; b = d + 1 + E - c * x
            if a > rl: rl = a
            if b < rh: rh = b
            if rl >= rh: return False
        return True
    # a feasible point: the probe with the largest |x| confines c to [(d - 1 - E)/x, (d + 1 + E)/x]; the r-interval
    # length is concave in c, so a float ternary search inside that bracket finds its maximum, then exact checks
    i = max(range(len(xs)), key=lambda k: abs(xs[k]))
    xm, dm = xs[i], ds[i]
    lo_b, hi_b = sorted([Fraction(dm - 1 - E, xm), Fraction(dm + 1 + E, xm)])
    fx = [float(x) for x in xs]; fd = [float(d) for d in ds]
    def g(c):
        rl = max([0.0] + [d - E - c * x for x, d in zip(fx, fd)]); rh = min([1.0] + [d + 1 + E - c * x for x, d in zip(fx, fd)])
        return rh - rl
    a_, b_ = float(lo_b), float(hi_b)
    for _ in range(100):
        m1, m2 = a_ + (b_ - a_) / 3, b_ - (b_ - a_) / 3
        if g(m1) < g(m2): a_ = m1
        else: b_ = m2
    c0 = None
    for k in range(20, 56, 4):   # exact rational near the float optimum
        c = Fraction(round((a_ + b_) / 2 * (1 << k)), 1 << k)
        if feas(c): c0 = c; break
    if c0 is None: return None
    def edge(sign):
        lo, hi = c0, c0 + sign
        while feas(hi): hi = c0 + 2 * (hi - c0)
        for _ in range(48):
            mid = (lo + hi) / 2
            if feas(mid): lo = mid
            else: hi = mid
        return lo
    a, b = edge(-1), edge(1)
    return simplest_dyadic(a, b, True, True) if feas(simplest_dyadic(a, b, True, True) or c0) else c0

def fit_floor_linear(run, srcs, sw, w, rng, unsigned=()):
    """floor(sum c*s + c0) mod 2^w over signed sources; None if no fit.
    Per source the coefficient CLASS (all c that reproduce the probes, round 2 of the adversarial review) is computed
    exactly by Fourier-Motzkin on the floor constraints over probes: powers of two, extremes, and all values for
    sources of up to 8 bits; the simplest dyadic in the class is chosen. The joint constant r in [0, 1) is the
    simplest dyadic consistent with 3000 full-range samples. If no constant fits, the node can still be 'quant'
    (exact linear part, bounded error) but only if the error is as large in the central range as on the full range
    (rounding errors occur everywhere; saturation errors only near the extremes)."""
    f = lambda vals: signed(run(vals), w)
    zero = {k: 0 for k in srcs}; f0 = f(zero); m = 1 << w
    val = lambda k, raw: raw & ((1 << sw[k]) - 1) if k in unsigned else signed(raw, sw[k])   # source value interpretation
    def at(a, v): d = dict(zero); d[a] = v; return d
    coeffs, classes = {}, {}
    for a in srcs:
        wa = sw[a]
        if 8 < wa <= 12:   # enumerate every value; exact class by bisection (Fourier-Motzkin would be quadratic)
            xs = sorted({val(a, x) for x in range(1 << wa)} - {0}); ds = [f(at(a, x)) - f0 for x in xs]
            c = None
            for E in (0, 1, 2, 3):
                c = class_by_bisection(xs, ds, E)
                if c is not None: break
            if c is not None:
                if c: coeffs[a] = c
                classes[a] = (c, True, c, True)
                continue
        if wa <= 8: xs = sorted({val(a, x) for x in range(1 << wa)} - {0})
        else:
            xs = sorted({val(a, s_ * (1 << k)) for k in range(wa - 1) for s_ in (1, -1)} | {val(a, 1 << (wa - 1)), val(a, (1 << (wa - 1)) - 1)} |
                        {val(a, rng.getrandbits(wa)) for _ in range(48)} - {0})
        ds = []
        for x in xs:   # difference, unwrapped relative to the previous probe estimate is not possible in general: keep small
            ds.append(f(at(a, x)) - f0)
        lo, lc, hi, hc = coefficient_class(xs, ds)
        c = simplest_dyadic(lo, hi, lc, hc)
        if c is None:   # several roundings: widen by a tolerated error E (such a coefficient can only make a 'quant' node)
            for E in (1, 2, 3):
                lo, lc, hi, hc = coefficient_class(xs, ds, E); c = simplest_dyadic(lo, hi, lc, hc)
                if c is not None: break
        if c is None:   # the probes wrap: retry on probes whose output stays inside the output range
            xs2 = [x for x in xs if abs(x) < (1 << max(wa - 1, 0)) and abs(f(at(a, x)) - f0) < (1 << max(w - 2, 0))]
            xs2 = [x for x in xs2 if abs(x) <= min(abs(y) for y in xs if abs(f(at(a, y)) - f0) >= (1 << max(w - 2, 0)))] if \
                  any(abs(f(at(a, y)) - f0) >= (1 << max(w - 2, 0)) for y in xs) else xs2
            if not xs2: return None
            ds2 = [f(at(a, x)) - f0 for x in xs2]
            for E in (0, 1, 2, 3):   # several roundings may also remain after removing the wrapping probes
                lo, lc, hi, hc = coefficient_class(xs2, ds2, E); c = simplest_dyadic(lo, hi, lc, hc)
                if c is not None: break
            if c is None: return None
        if 12 < wa <= 16 and c is not None and len(srcs) <= 4:   # exhaustive single-source check (CEGIS; round 4); nodes
            # with more sources use the sampled refinement below (cost: 2^16 evaluations per source)
            allx = [val(a, x) for x in range(1 << wa)]; ally = None
            for _ in range(16):
                if ally is None: ally = [f(at(a, x)) - f0 for x in allx]
                rl, rh = Fraction(0), Fraction(1); bad = []
                for x, d_ in zip(allx, ally):   # modulo 2^w: u = (d - c x) reduced into (-1, m - 1]
                    u = (Fraction(d_) - c * x) % m
                    if u > m - 1: u -= m
                    lo_r, hi_r = u, u + 1
                    if lo_r > rl: rl = lo_r
                    if hi_r < rh: rh = hi_r
                    if rl >= rh:
                        if abs(d_) < (1 << max(w - 2, 0)): bad.append((x, d_))   # only non-wrapping probes refine the class
                        rl, rh = max(Fraction(0), lo_r), min(Fraction(1), hi_r)
                    if len(bad) >= 16: break
                if not bad: break
                xs = xs + [x for x, _ in bad]; ds = ds + [d_ for _, d_ in bad]
                xs_nw = [(x, d_) for x, d_ in zip(xs, ds) if abs(d_) < (1 << max(w - 2, 0))]
                c2 = class_by_bisection([x for x, _ in xs_nw], [d_ for _, d_ in xs_nw], 0)
                if c2 is None: c = None; break
                c = c2
            if c is None: return None
        if wa > 8 and c is not None:   # the probe set may leave the class too wide: refine with counterexamples
            for _ in range(12):
                extra = [val(a, rng.getrandbits(wa)) for _ in range(400)]
                xr = [x for x in extra if x]; dr = [f(at(a, x)) - f0 for x in xr]
                # consistency of c with some r: r in [d - c x, d + 1 - c x) for all probes, r in [0, 1)
                rl, rh = Fraction(0), Fraction(1)
                for x, d_ in zip(xs + xr, ds + dr):
                    rl = max(rl, d_ - c * x); rh = min(rh, d_ + 1 - c * x)
                if rl < rh: break
                viol = [(x, d_) for x, d_ in zip(xr, dr) if not (d_ - c * x < 1 and d_ + 1 - c * x > 0)][:24] or list(zip(xr, dr))[:24]
                xs = xs + [x for x, _ in viol]; ds = ds + [d_ for _, d_ in viol]
                lo, lc, hi, hc = coefficient_class(xs, ds); c2 = simplest_dyadic(lo, hi, lc, hc)
                if c2 is None: break   # several roundings or wrap: keep the previous candidate (quant check decides)
                c = c2
        if c: coeffs[a] = c
        classes[a] = (lo, lc, hi, hc)
    samples = [{k: val(k, rng.getrandbits(sw[k])) for k in srcs} for _ in range(3000)]
    outs = [run(v) for v in samples]
    def joint_r(cf):
        # joint constant: r in [max(0, u), min(1, u + 1)) for every sample, u = (out - f0 - lin) reduced to (-1, m - 1]
        rlo, rhi = Fraction(0), Fraction(1)
        for v, o in zip(samples, outs):
            u = (Fraction(o) - f0 - sum(c * v[k] for k, c in cf.items())) % m
            if u > m - 1: u -= m
            rlo = max(rlo, u); rhi = min(rhi, u + 1)
            if rlo >= rhi: return None
        return simplest_dyadic(rlo, rhi, True, False)
    r = joint_r(coeffs)
    narrow = [a for a in srcs if sw[a] <= 2]
    if r is None and narrow and len(narrow) <= 6:   # 1-2 bit sources: their own probes leave a wide class; search jointly
        D = max([c.denominator for k, c in coeffs.items() if k not in narrow] + [2])
        cand = {}
        for a in narrow:
            lo, lc, hi, hc = classes[a]
            lo2, hi2 = max(lo, Fraction(-4)), min(hi, Fraction(4))
            cand[a] = [Fraction(k, D) for k in range(math.ceil(lo2 * D), math.floor(hi2 * D) + 1)
                       if (Fraction(k, D) > lo or (lc and Fraction(k, D) == lo)) and (Fraction(k, D) < hi or (hc and Fraction(k, D) == hi))]
        import itertools
        for combo in itertools.product(*[cand[a] for a in narrow]):
            cf = dict(coeffs); cf.update({a: c for a, c in zip(narrow, combo) if c}); [cf.pop(a) for a, c in zip(narrow, combo) if not c and a in cf]
            r2 = joint_r(cf)
            if r2 is not None: coeffs, r = cf, r2; break
    if r is not None:
        den = max([c.denominator for c in coeffs.values()] + [1])
        mode = 'exact' if den == 1 and r == 0 else 'floor'
        return {'coeffs': coeffs, 'const': Fraction(f0) + r, 'mode': mode, 'width': w}
    lin = [sum(c * v[k] for k, c in coeffs.items()) for v in samples]
    errs = [((o - math.floor(x + f0)) + m // 2) % m - m // 2 for x, o in zip(lin, outs)]
    err = max(abs(e) for e in errs)
    span = max(abs(x) for x in lin) or 1
    # a band away from both the centre and the extremes: every source within half its range, the linear part between
    # 1/16 and 1/4 of its span. Rounding errors occur everywhere; saturation errs at the extremes, a dead zone (coring)
    # at the centre (adversarial review rounds 3 and 4); both are rejected
    central = [abs(e) for e, x, v in zip(errs, lin, samples) if span / 16 < abs(x) < span / 4 and all(abs(v[k]) < (1 << max(sw[k] - 2, 0)) for k in srcs)]
    if err <= len(srcs) + 2 and central and max(central) == err:
        return {'coeffs': coeffs, 'const': Fraction(f0), 'mode': 'quant', 'width': w, 'quant_bound': err,
                'coeff_class': {str(a): [str(classes[a][0]), str(classes[a][2])] for a in coeffs}}   # several roundings: c known only to this interval
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
    observe(F)
    return F

def collapse_copies(F):
    """A node that is exactly one source with coefficient 1 and the same width is a delay: readers read its source,
    delays add. Sources may be signals or inputs (an input capture register r <- x is a copy too; attack a12/minor).
    Copies can form cycles (a register held by D = Q with CE = 1, a rotating ring; attack a2): such a cycle is a pure
    storage loop y[n] = y[n-k]; one node per cycle (the smallest) is kept as a real node, so following terminates."""
    alias = {}
    for key, f in F.fits.items():
        if key[0] == 'out' or f is None or f['mode'] != 'exact' or f['const'] != 0 or len(f['coeffs']) != 1: continue
        (src, c), = f['coeffs'].items()
        if c != 1: continue
        if src[0] == 'sig' and F.W[src[1]] == f['width']: alias[key] = src
        if src[0] == 'in' and F.ports_in.get(src[1]) == f['width']: alias[key] = src
    # break copy cycles
    state = {}
    for start in list(alias):
        path, v = [], start
        while v in alias and state.get(v) is None:
            state[v] = start; path.append(v); v = alias[v][:3]
        if v in alias and state.get(v) == start:   # found a new cycle through v
            cyc = path[path.index(v):]
            del alias[min(cyc)]
        for u in path: state[u] = 'done'
    def follow(src):
        k = 0
        while src[0] == 'sig' and src[:3] in alias:
            a = alias[src[:3]]; k += src[3]; src = a
        return src[:3] + (src[3] + k,) + tuple(src[4:])
    F.copies = {str(k): str(v) for k, v in alias.items()}; F.follow = follow
    for key, f in F.fits.items():
        if f is None: continue
        new = collections.defaultdict(Fraction)
        for s, c in f['coeffs'].items(): new[follow(s) if s[0] == 'sig' else s] += c
        f['coeffs'] = {s: c for s, c in new.items() if c}
        if f.get('unsigned_keys'): f['unsigned_keys'] = {follow(s) if s[0] == 'sig' else s for s in f['unsigned_keys']}
    for key in alias: del F.fits[key]

def node_sources(F, v):
    """signal sources (with delays) of node v: from its fit, or (nonlinear node) from its resolved reads"""
    f = F.fits.get(v)
    if f is not None: return [s for s in f['coeffs'] if s[0] == 'sig']
    d = F.nodes[v[1:]] if v[0] == 'sig' else F.outputs[v[1:]]
    out = []
    for src in d['res'].values():
        for c in (src[2] if src[0] == 'cat' else (src,)):
            if c[0] == 'sig': out.append(F.follow(c[:4]) if hasattr(F, 'follow') else c[:4])
    return out

def input_sources(F, v):
    f = F.fits.get(v)
    if f is not None: return [s for s in f['coeffs'] if s[0] == 'in']
    d = F.nodes[v[1:]] if v[0] == 'sig' else F.outputs[v[1:]]
    return [c for src in d['res'].values() for c in (src[2] if src[0] == 'cat' else (src,)) if c[0] == 'in']

def observe(F):
    """Dead-node elimination (attack a10): nodes not in the backward cone of an output observation are dead writes
    (idle-slot defaults, garbage). Input phases are recounted on the observable part only."""
    outs = [k for k in F.fits if k[0] == 'out']
    if not outs: F.observable = {k for k in F.fits if k[0] == 'sig'}; return
    seen, work = set(), list(outs)
    while work:
        v = work.pop()
        for s in node_sources(F, v):
            u = s[:3]
            if u in F.fits and u not in seen: seen.add(u); work.append(u)
    F.observable = seen
    F.dead = sorted(k for k in F.fits if k[0] == 'sig' and k not in seen)
    ph = collections.defaultdict(set)
    for v in list(seen) + outs:
        for c in input_sources(F, v): ph[c[1]].add(c[2])
    F.input_phases_all = F.input_phases
    F.input_phases = {p: sorted(x) for p, x in ph.items()}
    if F.dead: F.report['notes'].append('%d dead nodes (not observable at any output): %s' % (len(F.dead), [str(x) for x in F.dead[:6]]))

def infer_bit_orders(F, words):
    """Bit order of words whose pin groups carry none (bit-order propagation failed): if in every write event the
    word's D is pure routing of bits of one source word (each written bit = one source bit), its canonical order is
    the source bit order. Probed by setting single source bits. Returns {word: perm} and applies it to F."""
    found = {}
    for w in words:
        maps = []
        for (ww, phi), d in F.nodes.items():
            if ww != w: continue
            widths = F.widths_of(d); F._raw_compile = True
            try: fn, syms, wd = F.compile(d, widths)
            finally: F._raw_compile = False
            srcs = {d['res'][s_] for s_ in syms}
            if len(syms) != 1 or len(srcs) != 1 or next(iter(srcs))[0] != 'sig': maps = None; break
            (s_,) = syms; m = {}
            if fn({s_: 0}) != 0: maps = None; break
            for b in range(widths[s_]):
                o = fn({s_: F.to_raw(d['res'][s_][1], 1 << b)})
                if o == 0: continue
                if o & (o - 1): maps = None; break
                m[o.bit_length() - 1] = b
            if maps is None: break
            maps.append(m)
        if not maps or any(mm != maps[0] for mm in maps): continue
        m = maps[0]; W = F.W[w]
        order = sorted(range(W), key=lambda j: (m.get(j, 10**9), j))   # raw bits by source bit; unmapped bits last
        perm = [0] * W
        for pos, j in enumerate(order): perm[j] = pos
        found[w] = perm; F.q_reorder[w] = perm
    F.report['notes'].append('bit order inferred by routing for %s' % sorted(found))
    return found

def run_big(fn, *a):
    sys.setrecursionlimit(1000000); threading.stack_size(1 << 28); out = {}
    def go():
        try: out['r'] = fn(*a)
        except BaseException as e: out['e'] = e
    t = threading.Thread(target=go); t.start(); t.join()
    if 'e' in out: raise out['e']
    return out['r']

def run_graph(F, inputs, init, n_from, n_to, outputs=False):
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
            return sum((((F.to_raw(c[1], src_value(c[:4], n)) >> c[4]) & 1) if c[0] != 'const' else c[1]) << i for i, c in enumerate(src[2]))
        kind, w, phi, k = src; m = n - k; c = T0 + m * P + phi
        if kind == 'in': return inputs(w, c)
        if m < n_from: return init(w, c)
        return val[(w, phi, m)]
    ocomp = {}
    if outputs:
        for k, d in F.outputs.items():
            widths = F.widths_of(d); fn, syms, width = F.compile(d, widths); ocomp[k] = (fn, d['res'], widths)
    outv = {}
    for n in range(n_from, n_to):
        for v in order:
            fn, res, widths = comp[v]
            env = {s: (F.to_raw(src[1], src_value(src, n) & ((1 << widths[s]) - 1)) if src[0] == 'sig' else src_value(src, n) & ((1 << widths[s]) - 1)) for s, src in res.items()}
            val[v + (n,)] = fn(env)
        for (port, phi), (fn, res, widths) in ocomp.items():
            env = {s: (F.to_raw(src[1], src_value(src, n) & ((1 << widths[s]) - 1)) if src[0] == 'sig' else src_value(src, n) & ((1 << widths[s]) - 1)) for s, src in res.items()}
            outv[(port, T0 + n * P + phi)] = fn(env)
    return (val, outv) if outputs else val
