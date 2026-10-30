"""Inputs in the NetNotary / HAL-replay format: per-cycle forms printed by HAL (T[...] blocks, '<word> - i_D:' targets,
HAL Boolean-function text), word and port widths from the .hal netlist's pin groups (structural metadata), and
per-cycle word values from a HAL simulation VCD (used only to start runs and to score them).
compile_text: HAL text -> (fn, symbols, width), same interface as smt2.compile_term."""
import re, json, sys, collections
sys.path.insert(0, '/home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/eq2algo/lib')
sys.path.insert(0, '/home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/algo')
import forms as F

def load_forms(path):
    eqs, cur, tgt = [], None, None
    for line in open(path):
        if line.startswith('['): continue
        s = line.strip()
        if not s: continue
        m = re.fullmatch(r'T\[(\d+)\]', s)
        if m: cur = int(m.group(1)); continue
        m = re.fullmatch(r'(\S+) - (\S+):', s)
        if m: tgt = '%s - %s' % (m.group(1), m.group(2)); continue
        if cur is not None and tgt: eqs.append({'T': cur, 'target': tgt, 'text': s}); tgt = None
    return eqs

def qgroup(m):
    gs = {g['name']: g for g in m['pin_groups']}
    return gs.get('o_Q_ordered') or gs.get('o_Q')

def netlist_meta(hal_path, data_ports):
    """word widths (o_Q / o_Q_ordered pin group of DANA modules), data-port widths (top-module pin groups), output
    port aliases (an output pin group whose bits are all word Q bits: [(word, bit)] per port bit) and each word's
    clock-enable net name (structural metadata only)."""
    N = json.load(open(hal_path))['netlist']; nets = {n['id']: n['name'] for n in N['nets']}
    W, ports, qbit, ce = {}, {}, {}, {}
    for m in N['modules']:
        if m['name'].startswith('DANA_module_'):
            g = qgroup(m); W[m['name']] = len(g['pins'])
            for i, p in enumerate(g['pins']): qbit[p['net_id']] = (m['name'], i)
            for h in m['pin_groups']:
                if h['name'] == 'i_CE': ce[m['name']] = nets[h['pins'][0]['net_id']]
    alias = {}
    for m in N['modules']:
        if m['name'] == 'top_module':
            for g in m['pin_groups']:
                if g['name'] in data_ports: ports[g['name']] = len(g['pins'])
                src = [qbit.get(p['net_id']) for p in g['pins']]
                if src and all(src): alias['top_module_' + g['name']] = src
    return W, ports, alias, ce

def port_order(hal_path, port_group):
    """bit i of a top-module pin group (e.g. key_ordered) = bit perm[i] of the port's natural index (net 'key(j)')."""
    N = json.load(open(hal_path))['netlist']; nets = {n['id']: n['name'] for n in N['nets']}
    for m in N['modules']:
        if m['name'] == 'top_module':
            for g in m['pin_groups']:
                if g['name'] == port_group:
                    return [int(re.search(r'\((\d+)\)', nets[p['net_id']]).group(1)) for p in g['pins']]
    raise KeyError(port_group)

def dq_perm(hal_path):
    """word -> perm with perm[d] = Q-group index of the flip-flop whose D pin is bit d of the word's i_D group. The two
    pin groups of a DANA word are ordered independently, so the written value must be permuted into Q order."""
    N = json.load(open(hal_path))['netlist']
    src = {n['id']: {s['gate_id'] for s in n.get('srcs', [])} for n in N['nets']}
    dst = {n['id']: {d['gate_id'] for d in n.get('dsts', [])} for n in N['nets']}
    out = {}
    for m in N['modules']:
        if not m['name'].startswith('DANA_module_'): continue
        gs = {g['name']: [p['net_id'] for p in g['pins']] for g in m['pin_groups']}
        q = qgroup(m); qn = [p['net_id'] for p in q['pins']]
        dn = gs.get('i_D_ordered') or gs.get('i_D')
        if not dn or len(dn) != len(qn): continue
        qpos = {}
        for j, n in enumerate(qn):
            for g in src[n]: qpos[g] = j
        perm = []
        for n in dn:
            ff = [g for g in dst[n] if g in qpos]
            if len(ff) != 1: perm = None; break
            perm.append(qpos[ff[0]])
        if perm and sorted(perm) == list(range(len(qn))): out[m['name']] = perm
    return out

def net_trace(vcd_path, names, ncyc, offset):
    from vcd import VCD
    v = VCD(vcd_path, set(names))
    return {n: [v.value(n, 10000 * c + offset) for c in range(ncyc)] for n in names}

def word_trace(hal_path, vcd_path, ncyc, offset):
    """values[word][c] = word value sampled at 10000*c + offset ps (o_Q bit i = pin o_Q(i))."""
    from vcd import VCD
    N = json.load(open(hal_path))['netlist']; nets = {n['id']: n['name'] for n in N['nets']}
    bits = {}
    for m in N['modules']:
        if m['name'].startswith('DANA_module_'):
            bits[m['name']] = [nets[p['net_id']] for p in qgroup(m)['pins']]   # bit i = i-th pin (as NetNotary)
    v = VCD(vcd_path, {b for bs in bits.values() for b in bs})
    out = {}
    for w, bs in bits.items():
        vals = []
        for c in range(ncyc):
            x = 0
            for i, b in enumerate(bs):
                ch = v.value(b, 10000 * c + offset)
                if ch not in '01': x = None; break
                x |= (ch == '1') << i
            vals.append(x)
        out[w] = vals
    return out

_cache = {}
def compile_text(text, widths):
    """HAL text -> straight-line Python over a hash-consed DAG (HAL prints trees without sharing; DES round nodes
    are ~275k characters, FFT forms 322 MB). Same semantics as eq2algo's forms.ev. Returns (fn, symbols, width)."""
    key = (text, tuple(sorted(widths.items())))   # by content: id(text) is reused after a text is freed (attack a13)
    if key in _cache: return _cache[key]
    ast = F.parse(text)
    ids, lines, wid = {}, [], []
    memo = {}
    def node(e):
        k0 = id(e)
        if k0 in memo: return memo[k0]
        op = e[0]
        if op == 'name': sig = ('name', e[1]); w = widths[e[1]]; code = 'E[%r]' % e[1]
        elif op == 'const': sig = ('const', e[1], e[2]); w = e[2]; code = str(e[1])
        elif op == 'unknown': raise ValueError('UNKNOWN_OPERATION in a node expression')
        else:
            ch = [node(x) for x in e[1:] if isinstance(x, tuple)]; prm = tuple(x for x in e[1:] if not isinstance(x, tuple))
            sig = (op,) + tuple(c[0] for c in ch) + prm
            v = ['t%d' % c[0] for c in ch]; ws = [c[1] for c in ch]
            if op == 'slice': lo, hi = prm; w = hi - lo + 1; code = '(%s >> %d) & %d' % (v[0], lo, (1 << w) - 1)
            elif op == 'sext':
                w = prm[0]; w0 = ws[0]; code = '%s | (%d if (%s >> %d) & 1 else 0)' % (v[0], ((1 << w) - 1) ^ ((1 << w0) - 1), v[0], w0 - 1)
            elif op == 'zext': w = prm[0]; code = v[0]
            elif op == 'cat': w = ws[0] + ws[1]; code = '(%s << %d) | %s' % (v[0], ws[1], v[1])
            elif op in ('+', '*'): w = max(ws); code = '(%s %s %s) & %d' % (v[0], op, v[1], (1 << w) - 1)
            elif op == '==': w = 1; code = 'int(%s == %s)' % (v[0], v[1])
            elif op in ('&', '|', '^'): w = max(ws); code = '%s %s %s' % (v[0], op, v[1])
            elif op == '!': w = ws[0]; code = '%s ^ %d' % (v[0], (1 << w) - 1)
            elif op == 'ite': w = max(ws[1], ws[2]); code = '%s if %s else %s' % (v[1], v[0], v[2])
            else: raise ValueError(op)
        if sig not in ids:
            ids[sig] = len(lines); lines.append('t%d = %s' % (len(lines), code)); wid.append(w)
        r = (ids[sig], wid[ids[sig]]); memo[k0] = r; return r
    root, w = node(ast)
    src = 'def f(E):\n' + ''.join('    %s\n' % l for l in lines) + '    return t%d\n' % root
    g = {}; exec(compile(src, '<hal>', 'exec'), g)
    r = (g['f'], F.names(ast), w); _cache[key] = r
    return r
