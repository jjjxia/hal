"""C7: invfold's block-RAM model (meta['memories'], built by fft_run.build_meta) uses a fixed latency 1 + DOA_REG and
ignores REGCE (output register clock enable), RSTRAM/RSTREG (output resets), byte-wise write enables (any nonzero
WEBWE = full write) and WRITE_MODE. List these settings for the FFT64 block RAMs (3 MB .hal, not the forms)."""
import json, collections
H = '/home/jiahao/HardwareRE/dynamicRE/reproduction/functional_dynamic_sota/dynamic/artifact/benchmarks/results/xilinx/fft64.hal'
HGL = '/home/jiahao/hal/plugins/gate_libraries/definitions/XILINX_UNISIM_hal.hgl'
N = json.load(open(H))['netlist']; L = json.load(open(HGL))
pins = {c['name']: [p['name'] for g in c.get('pin_groups', []) for p in g['pins']] for c in L['cells']}
gates = {g['id']: g for g in N['gates']}
conn = collections.defaultdict(dict)
for n in N['nets']:
    for e in n.get('srcs', []) + n.get('dsts', []):
        g = gates[e['gate_id']]
        if 'RAM' in g['type'] or g['type'].startswith('SRL'): conn[g['id']][pins[g['type']][e['pin_id'] - 1]] = n['name']
for gid, c in conn.items():
    g = gates[gid]; gen = {d[1]: d[3] for d in g.get('data', []) if d[0] == 'generic'}
    keys = [k for k in gen if any(s in k for s in ('WRITE_MODE', 'DOA_REG', 'DOB_REG', 'RAM_MODE', 'READ_WIDTH', 'WRITE_WIDTH', 'SRVAL', 'INIT_A', 'INIT_B', 'RSTREG_PRIORITY'))]
    ctl = {p: v for p, v in c.items() if p.startswith(('REGCE', 'RST', 'WE', 'EN', 'CE', 'A(', 'A0', 'A1', 'A2', 'A3')) and not p.startswith(('ADDR',))}
    print(g['type'], g['name'][-40:], {k: gen[k] for k in sorted(keys)})
    print('    control pins:', {p: v for p, v in sorted(ctl.items()) if not p.startswith('ADDR')})
