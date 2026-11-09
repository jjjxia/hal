import json, random, sys, itertools
sys.path.insert(0, '/home/jiahao/hal/algorithm_recovery/evaluation/kernel_foldings')
import kernels as KM
case = sys.argv[1]; prog = f'{case}/program'
contract = json.load(open(f'{case}/input/evaluation_contract.json')); iface = json.load(open(f'{prog}/interface.json'))
reads = sorted({(b[3:], int(ph)) for b, ph in iface['pad_reads']}); keys = [tuple(k) for k in iface.get('state_keys', [])]
by_bit = {str(p['physical_bit']): p for p in contract['port_bits']}
src = open(f'{prog}/program_flat.py').read().replace('    v = {}\n', '    v = _CAP.new()\n', 1)
class Cap:
    def __init__(s): s.all = []
    def new(s): d = {}; s.all.append(d); return d
ns = {'__name__': 'p', '_CAP': Cap()}; exec(compile(src, 'p', 'exec'), ns)
k = KM.KERNELS['dct8'](); M = 0xFFFF; rng = random.Random(7); S = 203
samples = [{i: rng.getrandbits(16) for i in k['inputs']} for _ in range(S)]
st = {x: 0 for x in keys}
for n in range(S):
    pads = {(f'pad{p}', ph): (samples[n].get(by_bit[p]['name'], 0) >> by_bit[p]['offset']) & 1 for p, ph in reads}
    _, st = ns['transaction'](pads, st)
W = ns['_CAP'].all; T = range(3, S)
bitsig = {}
for key in W[3]:
    w = max(W[n].get(key, 0) for n in T).bit_length()
    for b in range(w):
        bitsig.setdefault(tuple((W[n].get(key, 0) >> b) & 1 for n in T), []).append((key, b))
def cover(vals):     # share of non-constant bits of a value sequence present in some recovered write
    sigs = [tuple((v >> i) & 1 for v in vals) for i in range(16)]
    sigs = [g for g in sigs if len(set(g)) > 1]
    return sum(g in bitsig for g in sigs), len(sigs)
x = lambda i: [samples[n][f'x{i}'] for n in T]
cands = {}
for i in range(8):
    cands[f'x{i}'] = x(i)
for i in range(4):
    j = 7 - i
    cands[f'x{i}+x{j}'] = [(a + b) & M for a, b in zip(x(i), x(j))]
    cands[f'x{i}-x{j}'] = [(a - b) & M for a, b in zip(x(i), x(j))]
tot = {nm: cover(v) for nm, v in cands.items()}
print('recovered writes:', len(W[3]), 'bit signatures:', len(bitsig))
for nm, (f, t) in tot.items(): print(f'{nm:8s} bits present {f}/{t}')
