"""Extension X2, step 1: check the memory model on the FFT64 simulation trace before using it in inverse folding.
Model (Xilinx SDP block RAM, READ_FIRST): in cycle c, if ENARDEN the read latch takes mem[ADDR_A(c)] (old data on
a same-cycle write); then if ENBWREN and WEBWE the cell ADDR_B(c) takes DI(c). DO = latch, delayed one more cycle
through the output register when DO*_REG = 1 (if REGCE). Every value comes from the trace: addresses, enables and
data-in nets; the prediction of DO is compared with the DO nets in the same trace.
Usage: fft_memcheck.py [sim index]"""
import sys, json, os, collections, re
sys.path.insert(0, '/home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/algo')
from vcd import VCD
H = '/home/jiahao/HardwareRE/dynamicRE/reproduction/functional_dynamic_sota/dynamic/artifact/benchmarks/results/xilinx/fft64.hal'
HGL = '/home/jiahao/hal/plugins/gate_libraries/definitions/XILINX_UNISIM_hal.hgl'
ALGO = '/home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/algo'

def ram_ports():
    N = json.load(open(H))['netlist']; L = json.load(open(HGL))
    pins = {c['name']: [p['name'] for g in c.get('pin_groups', []) for p in g['pins']] for c in L['cells']}
    gates = {g['id']: g for g in N['gates']}
    conn = collections.defaultdict(dict)
    for n in N['nets']:
        for e in n.get('srcs', []) + n.get('dsts', []):
            g = gates[e['gate_id']]
            if 'RAM' in g['type']: conn[g['id']][pins[g['type']][e['pin_id'] - 1]] = n
    out = {}
    for gid, c in conn.items():
        g = gates[gid]; gen = {d[1]: d[3] for d in g.get('data', []) if d[0] == 'generic'}
        def bus(prefix):
            bits = {}
            for p, n in c.items():
                m = re.fullmatch(re.escape(prefix) + r'\((\d+)\)', p)
                if m: bits[int(m.group(1))] = n
            return bits
        # data bits: DIADI/DIBDI and the parity lanes DIPADIP/DIPBDIP (18- and 36-bit layouts use them as data bits)
        di = {**{i: n for i, n in bus('DIADI').items()}, **{32 + i: n for i, n in bus('DIBDI').items()},
              **{64 + i: n for i, n in bus('DIPADIP').items()}, **{72 + i: n for i, n in bus('DIPBDIP').items()}}
        do = {**{i: n for i, n in bus('DOADO').items()}, **{32 + i: n for i, n in bus('DOBDO').items()},
              **{64 + i: n for i, n in bus('DOPADOP').items()}, **{72 + i: n for i, n in bus('DOPBDOP').items()}}
        common = sorted(set(di) & set(do))
        out[g['name']] = {'ra': bus('ADDRARDADDR'), 'wa': bus('ADDRBWRADDR'), 'we': bus('WEBWE'), 'di': {i: di[i] for i in common}, 'do': {i: do[i] for i in common},
                          'ena': c.get('ENARDEN'), 'enb': c.get('ENBWREN'), 'regce': c.get('REGCEAREGCE'), 'doreg': int(gen.get('DOA_REG', '0'))}
    return out

def main(sim_i=1, ncyc=700, offset=4999):   # the FFT traces end at cycle 700
    sim = os.path.join(ALGO, 'fft', 'sim%d' % sim_i); vcd = [os.path.join(sim, x, 'waveform.vcd') for x in os.listdir(sim) if x.startswith('hal_sim')][0]
    R = ram_ports()
    names = set()
    for r in R.values():
        for k in ('ra', 'wa', 'we', 'di', 'do'): names |= {n['name'] for n in r[k].values()}
        for k in ('ena', 'enb', 'regce'):
            if r[k]: names.add(r[k]['name'])
    vname = lambda nm: re.sub(r'_IBUF(\(\d+\))?$', r'\1', nm)   # input-buffer outputs are recorded under the port name
    v = VCD(vcd, {vname(n) for n in names})
    def val(n, c):
        if n is None: return 1
        if n['name'] in ("'1'", "<const1>"): return 1
        if n['name'] in ("'0'", "<const0>"): return 0
        x = v.value(vname(n['name']), 10000 * c + offset); return None if x not in '01' else int(x)
    def word(bits, c):
        x = 0
        for i, n in bits.items():
            b = val(n, c)
            if b is None: return None
            x |= b << i
        return x
    res = {}
    for name, r in R.items():
        mem = {}; latch = [None] * (ncyc + 2); dout = [None] * (ncyc + 3); stats = collections.Counter(); we_kinds = collections.Counter()
        for c in range(ncyc):
            if val(r['ena'], c): ra = word(r['ra'], c); latch[c + 1] = mem.get(ra)   # READ_FIRST: before the write
            else: latch[c + 1] = latch[c]
            we = word(r['we'], c); we_kinds[we] += 1
            if val(r['enb'], c) and we:
                mem[word(r['wa'], c)] = word(r['di'], c)
            if r['doreg']: dout[c + 2] = latch[c + 1] if val(r['regce'], c + 1) else dout[c + 1]
            else: dout[c + 1] = latch[c + 1]
        for c in range(200, ncyc):
            obs = word(r['do'], c)
            if dout[c] is None or obs is None: stats['unknown'] += 1
            elif dout[c] == obs: stats['match'] += 1
            else: stats['mismatch'] += 1
        res[name] = {'stats': dict(stats), 'doreg': r['doreg'], 'data_bits': len(r['di']), 'we_values': dict(we_kinds)}
        print(name, res[name], flush=True)
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out', 'fft_memcheck_sim%d.json' % sim_i), 'w'), indent=1)

if __name__ == '__main__': main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
