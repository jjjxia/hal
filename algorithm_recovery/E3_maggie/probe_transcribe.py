"""Feasibility probe (not a preregistered run): transcribe whitebox_output.txt mechanically into a cycle model
r_w[t] = D_w(t) and compare with the recorded trace in whitebox_output.py, with and without the authors' manual
r28 correction. Uses the artifact's recorded register values for the first 30 cycles (as the manual model does)."""
import sys, re, ast
sys.path.insert(0, '/home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/eq2algo/lib')
import forms as F
WB = '/home/jiahao/HardwareRE/dynamicRE/reproduction/functional_dynamic_sota/dynamic/artifact/whitebox/'
src = open(WB + 'whitebox_output.py').read()
real = ast.literal_eval(src[src.index('real = {') + 7:src.index('cycles = len')].strip())
# parse T[512] block
eq, cur = {}, None
for line in open(WB + 'whitebox_output.txt'):
    s = line.strip()
    if s.startswith('T['): cur = int(s[2:-1]); continue
    m = re.fullmatch(r'(\S+) - (\S+):', s)
    if m: tgt = (m.group(1), m.group(2)); continue
    if s and cur == 512: eq[tgt] = F.parse(s)
words = sorted(k[0] for k in eq if k[1] == 'i_D')
# width inference: HAL operands of + and * have equal width; D width = Q width of the same word
W = {}
def names(e, acc):
    if e[0] == 'name': acc.add(e[1])
    for x in e[1:]:
        if isinstance(x, tuple): names(x, acc)
    return acc
def infer(e):
    k = e[0]
    if k == 'const': return e[2]
    if k == 'name':
        b = re.sub(r'_\d+$', '', e[1]); return W.get(b)
    if k == 'slice': infer(e[1]); return e[3] - e[2] + 1
    if k in ('sext', 'zext'): infer(e[1]); return e[2]
    if k == 'cat':
        a, b = infer(e[1]), infer(e[2]); return a + b if a and b else None
    if k in ('+', '*'):
        a, b = infer(e[1]), infer(e[2]); w = a or b
        for x in (e[1], e[2]):
            if x[0] == 'name' and w: W.setdefault(re.sub(r'_\d+$', '', x[1]), w)
        return w
for _ in range(10):
    for w in words:
        d = infer(eq[(w, 'i_D')])
        if d: W.setdefault(w + '_o_Q', d)
W.setdefault('data_i', 14)   # port width: top-level port metadata (data_i is 14 bits in the artifact netlist) -- TODO confirm
print('inferred widths', {k: v for k, v in sorted(W.items())})
missing = [w for w in words if w + '_o_Q' not in W]; print('words without width', missing)
num = {int(w.split('_')[-1]): w for w in words}
rname = {w: 'r%d' % int(w.split('_')[-1]) for w in words}
print('words', [rname[w] for w in words], 'recorded', sorted(k for k in real if k.startswith('r')))
def run(fix):
    n = len(real['inp']); R = {w: [None] * n for w in words}
    for t in range(n):
        for w in words:
            if t < 30: R[w][t] = real[rname[w]][t] & ((1 << W[w + '_o_Q']) - 1); continue
        if t < 30: continue
        for w in words:
            e = eq[(w, 'i_D')]; env = {}; Wl = {}
            for nm in names(e, set()):
                b, T = re.sub(r'_(\d+)$', '', nm), int(nm.rsplit('_', 1)[1]); d = 512 - T
                if b == 'data_i': env[nm] = real['inp'][t - d] & 0x3FFF; Wl[nm] = 14
                else: env[nm] = R[b[:-len('_o_Q')]][t - d - 1]; Wl[nm] = W[b]   # Q(T') = D(T'-1)
            v = F.ev(e, env, Wl)
            if fix and w == 'DANA_module_28':
                q = R[w][t - 2]; x = real['inp'][t]
                if q & x & 1: v = (v + 1) & ((1 << W[w + '_o_Q']) - 1)
            R[w][t] = v
    return R
for fix in (False, True):
    R = run(fix)
    for w in words:
        m = W[w + '_o_Q']; bad = [t for t in range(30, len(real['inp'])) if R[w][t] != real[rname[w]][t] & ((1 << m) - 1)]
        if bad: print('fix' if fix else 'raw', rname[w], 'mismatches', len(bad), 'first', bad[:3])
    o1 = sum(R['DANA_module_22'][t - 1] & 0xFFFF != real['out1'][t] & 0xFFFF for t in range(31, len(real['inp'])))
    o2 = sum(R['DANA_module_42'][t - 1] & 0xFFFF != real['out2'][t] & 0xFFFF for t in range(31, len(real['inp'])))
    print('fix' if fix else 'raw', 'out1 mismatches', o1, 'out2 mismatches', o2)
