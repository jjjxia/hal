"""C1: the DSP48E1 model of E4_certified/fft_run.py asserts only AREG/BREG/MREG/PREG and OPMODE. Read the FFT64 .hal
(3 MB netlist, not the forms) and list every DSP48E1 setting the model silently assumes: INMODE (A1/A2 selection, A
zeroing, pre-adder), ALUMODE (add/subtract), CARRYINSEL/CARRYIN (rounding carry), USE_MULT/USE_DPORT, clock enables,
synchronous resets, and whether A[29:25] are copies of A[24] (the multiplier is 25 x 18 signed: the model multiplies
all 30 bits of A)."""
import json, collections
H = '/home/jiahao/HardwareRE/dynamicRE/reproduction/functional_dynamic_sota/dynamic/artifact/benchmarks/results/xilinx/fft64.hal'
HGL = '/home/jiahao/hal/plugins/gate_libraries/definitions/XILINX_UNISIM_hal.hgl'
N = json.load(open(H))['netlist']; L = json.load(open(HGL))
pins = {c['name']: [p['name'] for g in c.get('pin_groups', []) for p in g['pins']] for c in L['cells'] if c['name'] == 'DSP48E1'}
gates = {g['id']: g for g in N['gates']}
conn = collections.defaultdict(dict)
for n in N['nets']:
    for e in n.get('srcs', []) + n.get('dsts', []):
        g = gates[e['gate_id']]
        if g['type'] == 'DSP48E1': conn[g['id']][pins['DSP48E1'][e['pin_id'] - 1]] = n['name']
gens = collections.Counter(); pinv = collections.Counter(); upperA = collections.Counter()
for gid, c in conn.items():
    g = gates[gid]; gen = {d[1]: d[3] for d in g.get('data', []) if d[0] == 'generic'}
    for k in ('INMODEREG', 'ALUMODEREG', 'CARRYINSELREG', 'CARRYINREG', 'USE_MULT', 'USE_DPORT', 'USE_SIMD', 'A_INPUT', 'B_INPUT', 'ACASCREG', 'BCASCREG', 'ADREG', 'DREG', 'CREG', 'OPMODEREG', 'MASK', 'USE_PATTERN_DETECT'):
        if k in gen: gens[(k, gen[k])] += 1
    for p in sorted(c):
        if p.startswith(('INMODE', 'ALUMODE', 'CARRYINSEL', 'CARRYIN', 'CE', 'RST')): pinv[(p, c[p])] += 1
    a24 = c.get('A(24)'); upperA[tuple(c.get('A(%d)' % k) == a24 for k in range(25, 30))] += 1
print('DSP48E1 instances:', len(conn))
print('generics:', dict(gens))
print('control pins (pin, net) -> count:')
for (p, n), k in sorted(pinv.items()): print('   %-14s %-40s %d' % (p, n, k))
print('A(25..29) connected to the same net as A(24):', dict(upperA))
