"""Temporal rerolling on certified XTEA forms (exploratory). Same setup as run_cert.py XTEA (enables from the trace,
result port aliased to the four state words). Reports round groups per word and the constant parameters per round;
scoring only: whether the varying constants form the XTEA sum sequence i * 0x9E3779B9 (mod 2^32), in some bit slice."""
import sys, os, json, collections, re
sys.path.insert(0, '../invfold'); sys.path.insert(0, '.')
import run_cert as R, invfold, halforms, reroll
d = R.XTEA
for i, s in enumerate(d['sims']): d['sims'][i] = [os.path.join(s, x, 'waveform.vcd') for x in os.listdir(s) if x.startswith('hal_sim')][0]
cfg = json.load(open(R.ALGO + '/perfdiag/xtea_cfg_aig.json'))
eqs = halforms.load_forms(R.ALGO + '/' + cfg['forms'])
W, ports, alias, cenet = halforms.netlist_meta(cfg['hal'], d['data_ports'] + d['alias_ports'])
alias = {a: v for a, v in alias.items() if 'result' in a}; ports.pop('result_ordered', None)
names = sorted({n for n in cenet.values() if n not in ("'1'", "'0'")})
tr = halforms.net_trace(d['sims'][0], names, 700, 5000)
we = {w: ([1] * 700 if cenet.get(w, "'1'") == "'1'" else [int(x == '1') for x in tr[cenet[w]]]) for w in W}
meta = {'window': sorted({e['T'] for e in eqs}), 'widths': W, 'ports': {'in': ports, 'out': {}}, 'write_enable': we, 'alias': alias, 'dq_perm': halforms.dq_perm(cfg['hal'])}
F = invfold.run_big(invfold.recover, eqs, meta, 0, 1, 80)
groups = invfold.run_big(reroll.reroll, F)
res = {'groups': []}
for g in groups:
    info = {k: v for k, v in g.items() if k != 'table'}
    # constant parameters per round (values of 'const' placeholders that vary)
    consts = [[p[1] for p in row if p[0] == 'const'] for row in g['table']]
    info['const_params_per_round'] = len(consts[0]) if consts else 0
    info['key_params_per_round'] = len([p for p in g['table'][0] if p[0] == 'in']) if g['table'] else 0
    res['groups'].append(info); print(info)
    if g['rounds'] >= 8 and info['const_params_per_round']:
        delta = 0x9E3779B9
        vals = [[int(c[2:], 2) for c in row] for row in consts]
        hits = []
        for c in range(len(vals[0])):
            seq = [r[c] for r in vals]; wdt = len(consts[0][c]) - 2
            for sh in range(0, 33):
                for base in range(0, 66):
                    if all(((((base + i) * delta) % (1 << 32)) >> sh) % (1 << wdt) == seq[i] for i in range(len(seq))): hits.append((c, sh, base)); break
                else: continue
                break
        info['xtea_sum_matches'] = hits[:5]; print('   constant columns matching (base+i)*delta >> sh:', len(hits), 'of', len(vals[0]), hits[:3])
json.dump(res, open('out/xtea_reroll.json', 'w'), indent=1, default=str)
