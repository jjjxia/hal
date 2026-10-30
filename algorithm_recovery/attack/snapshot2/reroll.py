"""Temporal rerolling (extension X3): find logical signals of one word that compute the same function g up to the
choice of input bits and constants, i.e. "for i in rounds: s = g(s, kappa_i)" with a parameter table kappa.
Method: syntactic anti-unification of the node expressions. Every state read is renamed to its source relative to
the node (word, slot difference, frame delay), every input-port bit slice index and every constant becomes a
placeholder. Nodes whose templates are identical strings compute the same g (a proof by syntactic identity); their
placeholder values form the binding table. Placeholders whose values agree in every round are part of g; the others
are parameters, grouped into classes with identical value sequences.
Also: affine-in-word test for Feistel-like structure (f(L, R, K) xor f(L', R, K) = f(L xor L', R, K) xor f(0, R, K)
with a linear part that is a bit permutation), checked on random inputs (a test, not a proof)."""
import re, collections, random

SLICE = re.compile(r'Slice\(([A-Za-z_][A-Za-z0-9_]*?)_(\d+), (\d+), (\d+)\)')
CONST = re.compile(r'\b0b[01]+\b')

def template(F, v):
    d = F.nodes[v]; text = F.eq[(d['T'], d['tgt'])]
    phi = v[1]; params = []
    def rel(sym):
        src = d['res'].get(sym)
        if src is None: return sym
        if src[0] == 'in': return 'IN[%s]' % src[1]          # input phase is a parameter too (recorded below)
        if src[0] == 'cat': return 'CAT[%s]' % src[1]
        return 'S[%s,%d]' % (src[1], (phi - src[2]) % F.P + F.P * src[3])
    def sl(m):
        sym = '%s_%s' % (m.group(1), m.group(2)); src = d['res'].get(sym)
        if src and src[0] == 'in':
            params.append(('in', src[1], int(m.group(3)))); assert m.group(3) == m.group(4)
            return 'Slice(%s, #, #)' % rel(sym)
        return 'Slice(%s, %s, %s)' % (rel(sym), m.group(3), m.group(4))
    t = SLICE.sub(sl, text)
    t = F.sym_re.sub(lambda m: rel('%s_%s' % (m.group(1), m.group(2))), t)
    def cst(m): params.append(('const', m.group(0))); return '#C%d' % (len(m.group(0)) - 2)
    t = CONST.sub(cst, t)
    phases = tuple(sorted(src[2] for src in d['res'].values() if src[0] == 'in'))
    return t, params, phases

def reroll(F):
    groups = collections.defaultdict(list)
    for v in sorted(F.nodes):
        t, params, phases = template(F, v)
        groups[(v[0], t)].append((v, params, phases))
    out = []
    for (w, t), members in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
        n = len(members[0][1])
        assert all(len(p) == n for _, p, _ in members)
        cols = [tuple(m[1][i] for m in members) for i in range(n)]
        fixed = [i for i, c in enumerate(cols) if len(set(c)) == 1]
        varying = [i for i, c in enumerate(cols) if len(set(c)) > 1]
        classes = collections.OrderedDict()
        for i in varying: classes.setdefault(cols[i], []).append(i)
        out.append({'word': w, 'slots': [m[0][1] for m in members], 'rounds': len(members), 'template_chars': len(t),
                    'placeholders': n, 'fixed': len(fixed), 'parameter_classes': len(classes),
                    'table': [[c[r] for c in classes] for r in range(len(members))]})
    return out

def feistel_test(F, v, state_word, other_word, trials=64, seed=3):
    """Is node v affine in the source word `other_word` (read at one source), with a linear part that is a bit
    permutation independent of the other inputs? Random-input test."""
    import invfold
    d = F.nodes[v]; widths = F.widths_of(d); fn, syms, w = F.compile(d, widths)
    rng = random.Random(seed)
    lsyms = [s for s in syms if d['res'][s][0] == 'sig' and d['res'][s][1] == other_word]
    if len(lsyms) != 1: return {'feistel': False, 'reason': 'word read %d times' % len(lsyms)}
    L = lsyms[0]; wl = widths[L]
    perm = None
    for _ in range(trials):
        env = {s: rng.getrandbits(widths[s]) for s in syms}
        base = dict(env, **{L: 0}); f0 = fn(base)
        a, b = rng.getrandbits(wl), rng.getrandbits(wl)
        fa, fb, fab = fn(dict(env, **{L: a})), fn(dict(env, **{L: b})), fn(dict(env, **{L: a ^ b}))
        if fa ^ fb != fab ^ f0: return {'feistel': False, 'reason': 'not affine in %s' % other_word}
        p = []
        for i in range(wl):
            dlt = fn(dict(env, **{L: 1 << i})) ^ f0
            if dlt == 0 or dlt & (dlt - 1): return {'feistel': False, 'reason': 'bit %d does not flip exactly one output bit' % i}
            p.append(dlt.bit_length() - 1)
        if perm is None: perm = p
        elif p != perm: return {'feistel': False, 'reason': 'linear part depends on the other inputs'}
    return {'feistel': True, 'trials': trials, 'tested': 'random inputs (not a proof)'}
