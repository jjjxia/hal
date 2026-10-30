"""SMT-LIB2 bit-vector terms (z3 printing, with let-sharing) -> straight-line Python functions.
Leaves are free symbols; their widths come from the caller. compile_term returns (fn, symbols, width) where
fn(env) evaluates the term on unsigned integer values env[symbol]."""
import re

TOK = re.compile(r'\s*(\(|\)|\|[^|]*\||[^\s()]+)')

def parse(s):
    toks = TOK.findall(s); pos = 0
    def rd():
        nonlocal pos
        t = toks[pos]; pos += 1
        if t == '(':
            out = []
            while toks[pos] != ')': out.append(rd())
            pos += 1
            return out
        return t
    e = rd()
    assert pos == len(toks), 'trailing tokens'
    return e

class Compiler:
    def __init__(self, widths):
        self.widths = widths   # symbol -> width
        self.lines, self.n, self.syms = [], 0, set()
    def tmp(self, code):
        v = 't%d' % self.n; self.n += 1; self.lines.append('%s = %s' % (v, code)); return v
    def term(self, e, env):
        """returns (python variable or literal, width); width 0 means Boolean"""
        if isinstance(e, str):
            if e in env: return env[e]
            if e == 'true': return '1', 0
            if e == 'false': return '0', 0
            if e.startswith('#b'): return str(int(e[2:], 2)), len(e) - 2
            if e.startswith('#x'): return str(int(e[2:], 16)), 4 * (len(e) - 2)
            name = e.strip('|')
            if name not in self.widths: raise KeyError('unknown symbol %s' % name)
            self.syms.add(name); return 'E[%r]' % name, self.widths[name]
        h = e[0]
        if h == '_' and e[1].startswith('bv'): return str(int(e[1][2:])), int(e[2])   # literal (_ bvN w)
        if h == 'let':
            env2 = dict(env)
            for nm, val in e[1]: env2[nm] = self.term(val, env)
            return self.term(e[2], env2)
        if isinstance(h, list):   # indexed operators
            op = h[1]
            if h[0] == '_' and op.startswith('bv'): return str(int(op[2:])), int(h[2])
            a, w = self.term(e[1], env)
            if op == 'extract':
                hi, lo = int(h[2]), int(h[3])
                return self.tmp('(%s >> %d) & %d' % (a, lo, (1 << (hi - lo + 1)) - 1)), hi - lo + 1
            if op == 'zero_extend': return a, w + int(h[2])
            if op in ('rotate_left', 'rotate_right'):
                k = int(h[2]) % w; k = k if op == 'rotate_left' else (w - k) % w
                return self.tmp('((%s << %d) | (%s >> %d)) & %d' % (a, k, a, w - k, (1 << w) - 1)), w
            if op == 'repeat':
                r = int(h[2]); return self.tmp(' | '.join('(%s << %d)' % (a, i * w) for i in range(r))), w * r
            if op == 'sign_extend':
                k = int(h[2])
                return self.tmp('%s | (%d if %s >> %d else 0)' % (a, ((1 << k) - 1) << w, a, w - 1)), w + k
            raise ValueError('indexed op %s' % h)
        args = [self.term(x, env) for x in e[1:]]
        ws = [w for _, w in args]; vs = [v for v, _ in args]; w = max(ws) if ws else 0; m = (1 << w) - 1
        if h == 'bvnot': return self.tmp('%s ^ %d' % (vs[0], m)), w
        if h in ('bvand', 'bvor', 'bvxor'):
            return self.tmp((' %s ' % {'bvand': '&', 'bvor': '|', 'bvxor': '^'}[h]).join(vs)), w
        if h == 'bvadd': return self.tmp('(%s) & %d' % (' + '.join(vs), m)), w
        if h == 'bvmul':
            return self.tmp('(%s) & %d' % (' * '.join(vs), m)), w
        if h == 'bvsub': return self.tmp('(%s - %s) & %d' % (vs[0], vs[1], m)), w
        if h == 'bvneg': return self.tmp('(-%s) & %d' % (vs[0], m)), w
        if h == 'bvshl': return self.tmp('(%s << %s) & %d' % (vs[0], vs[1], m)), w
        if h == 'bvlshr': return self.tmp('%s >> %s' % (vs[0], vs[1])), w
        if h == 'concat':
            code, sh = [], 0
            for v, wi in reversed(args): code.append('(%s << %d)' % (v, sh)); sh += wi
            return self.tmp(' | '.join(code)), sh
        if h == 'ite': return self.tmp('%s if %s else %s' % (vs[1], vs[0], vs[2])), max(ws[1], ws[2])
        if h == '=': return self.tmp('int(%s == %s)' % (vs[0], vs[1])), 0
        if h == 'not': return self.tmp('1 - %s' % vs[0]), 0
        if h == 'and': return self.tmp('int(%s)' % ' and '.join(vs)), 0
        if h == 'or': return self.tmp('int(%s)' % ' or '.join(vs)), 0
        sx = lambda v, w: '(%s - %d if %s >> %d else %s)' % (v, 1 << w, v, w - 1, v)   # signed view
        if h in ('bvslt', 'bvsle', 'bvsgt', 'bvsge'):
            op = {'bvslt': '<', 'bvsle': '<=', 'bvsgt': '>', 'bvsge': '>='}[h]
            return self.tmp('int(%s %s %s)' % (sx(vs[0], ws[0]), op, sx(vs[1], ws[1]))), 0
        if h == 'bvashr': return self.tmp('(%s >> %s) & %d' % (sx(vs[0], w), vs[1], m)), w
        if h == 'bvudiv': return self.tmp('(%s // %s if %s else %d)' % (vs[0], vs[1], vs[1], m)), w
        if h == 'bvurem': return self.tmp('(%s %% %s if %s else %s)' % (vs[0], vs[1], vs[1], vs[0])), w
        if h in ('xor',): return self.tmp('int(%s)' % ' ^ '.join('bool(%s)' % v for v in vs)), 0
        if h == 'distinct': return self.tmp('int(len({%s}) == %d)' % (', '.join(vs), len(vs))), 0
        if h == 'bvcomp': return self.tmp('int(%s == %s)' % (vs[0], vs[1])), 1
        if h == '=>': return self.tmp('int((not %s) or %s)' % (vs[0], vs[1])), 0
        if h in ('bvult', 'bvule', 'bvugt', 'bvuge'):
            return self.tmp('int(%s %s %s)' % (vs[0], {'bvult': '<', 'bvule': '<=', 'bvugt': '>', 'bvuge': '>='}[h], vs[1])), 0
        raise ValueError('op %s' % h)

def compile_term(s, widths):
    c = Compiler(widths); v, w = c.term(parse(s), {})
    src = 'def f(E):\n' + ''.join('    %s\n' % l for l in c.lines) + '    return %s\n' % v
    g = {}; exec(compile(src, '<smt2>', 'exec'), g)
    return g['f'], c.syms, w
