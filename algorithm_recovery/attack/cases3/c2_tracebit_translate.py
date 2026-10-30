"""C2: fft_run.trace_bit evaluates gate functions of the helper libraries (im_hal_i4.hgl, aoixm_hal_i4.hgl) by text
substitution: '!' -> 'not ', '&' -> ' and ', '|' -> ' or ', '^' kept. Python precedence is ^ > not > and > or, while
the Boolean functions use ! > & > ^ > | (HAL). Compare the translation with a correct evaluator on every function and
every input assignment; count mismatches and syntax errors."""
import json, re, itertools
fn_lib = {}
for f in ('im_hal_i4.hgl', 'aoixm_hal_i4.hgl'):
    for c in json.load(open('/home/jiahao/hal/plugins/gate_libraries/definitions/helper_libs/' + f))['cells']:
        for g in c['pin_groups']:
            for pn in g['pins']:
                if pn.get('direction') == 'output' and pn.get('function'): fn_lib[(c['name'], pn['name'])] = pn['function']
def theirs(fstr):   # the exact code of fft_run.trace_bit
    return compile(re.sub(r'\b([A-Z][A-Z0-9]*)\b', r"v['\1']", fstr.replace('!', 'not ')).replace('&', ' and ').replace('|', ' or '), 'g', 'eval')
def correct(fstr):  # bitwise on 0/1 ints keeps HAL precedence (! as 1 - x is unary and binds tightest)
    s = re.sub(r'\b([A-Z][A-Z0-9]*)\b', r"v['\1']", fstr)
    s = re.sub(r'!\s*', '1^', s)   # !x -> (1 ^ x): careful, ^ binds looser than &, so wrap below
    return None
# a small recursive-descent evaluator with HAL precedence (! > & > ^ > |)
TOK = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*|[()!&|^]|0b[01]+|[01])")
def ev(fstr, v):
    toks = TOK.findall(fstr); pos = [0]
    def peek(): return toks[pos[0]] if pos[0] < len(toks) else None
    def eat(): t = toks[pos[0]]; pos[0] += 1; return t
    def atom():
        t = eat()
        if t == '(': x = orx(); eat(); return x
        if t == '!': return 1 - atom()
        if t in ('0', '1'): return int(t)
        if t.startswith('0b'): return int(t[2:], 2)
        return v[t]
    def andx():
        x = atom()
        while peek() == '&': eat(); x &= atom()
        return x
    def xorx():
        x = andx()
        while peek() == '^': eat(); x ^= andx()
        return x
    def orx():
        x = xorx()
        while peek() == '|': eat(); x |= xorx()
        return x
    r = orx(); assert pos[0] == len(toks), fstr; return r
bad, syn, total = [], [], 0
for (cell, pin), f in sorted(fn_lib.items()):
    names = sorted(set(re.findall(r'\b([A-Z][A-Z0-9]*)\b', f)))
    try: code = theirs(f)
    except SyntaxError: syn.append((cell, f)); continue
    total += 1
    for bits in itertools.product((0, 1), repeat=len(names)):
        v = dict(zip(names, bits))
        a = int(bool(eval(code, {}, {'v': v}))); b = ev(f, v) & 1
        if a != b: bad.append((cell, f, v, a, b)); break
print('functions:', len(fn_lib), 'compiled:', total, 'syntax errors:', len(syn), 'wrong truth tables:', len(bad))
for x in syn[:3]: print('  SYNTAX', x)
for x in bad[:5]: print('  WRONG', x)
print('operators present:', sorted({ch for f in fn_lib.values() for ch in f if ch in '!&|^'}))
