"""Temporal rerolling on certified DES forms (canonical origin 15 = load slot). Scoring at the end only: the DES
key-schedule shift sequence (FIPS 46-3) is used to check the recovered parameter table, never by the recognizer."""
import sys, os, json, collections
sys.path.insert(0, '../invfold'); sys.path.insert(0, '.')
import run_cert as R, invfold, halforms, reroll
cfg = json.load(open(R.ALGO + '/perfdiag/des_cfg_aig.json'))
eqs = halforms.load_forms(R.ALGO + '/' + cfg['forms'])
W, ports, alias, cenet = halforms.netlist_meta(cfg['hal'], ['key_ordered', 'desIn_ordered'])
win = sorted({e['T'] for e in eqs})
meta = {'window': win, 'widths': W, 'ports': {'in': ports, 'out': {}}, 'write_enable': {w: [1] * 400 for w in W}, 'alias': {}, 'dq_perm': halforms.dq_perm(cfg['hal'])}
F = invfold.run_big(invfold.recover, eqs, meta, 15, 1, 16)
groups = invfold.run_big(reroll.reroll, F)
res = {'groups': [{k: v for k, v in g.items() if k != 'table'} for g in groups]}
for g in groups: print({k: v for k, v in g.items() if k != 'table'})
# Feistel test on every round node of word 0 (reads word 0 and word 1 of the previous slot)
ft = {}
for v in sorted(F.nodes):
    if v[0] == 'DANA_module_0': ft[v[1]] = invfold.run_big(reroll.feistel_test, F, v, 'DANA_module_0', 'DANA_module_1')
res['feistel'] = {str(k): v for k, v in ft.items()}
print('feistel:', collections.Counter(str(v) for v in ft.values()))
# full Feistel round check with the partner word: new R (word 0, slot s) and new L (word 1, slot s)
fr = {}
for v in sorted(F.nodes):
    if v[0] == 'DANA_module_0' and ('DANA_module_1', v[1]) in F.nodes and v[1] != 0:
        fr[v[1]] = invfold.run_big(reroll.feistel_round, F, v, ('DANA_module_1', v[1]), 'DANA_module_1', 'DANA_module_0', 'xor', 16)['feistel_round']
res['feistel_round_with_partner'] = fr; print('feistel round (with partner check):', collections.Counter(fr.values()))
# word 1 nodes: pure bit permutation of word 0?
cp = {}
for v in sorted(F.nodes):
    if v[0] == 'DANA_module_1':
        srcs = {s[:2] for s in F.nodes[v]['res'].values()}
        cp[v[1]] = sorted(map(str, srcs))
print('word-1 node sources:', collections.Counter(str(x) for x in cp.values()))
# ---- scoring: key schedule ----
big = max(groups, key=lambda g: g['rounds'])
perm = halforms.port_order(cfg['hal'], 'key_ordered')
slots = big['slots']; table = big['table']
shifts = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]   # FIPS 46-3 left shifts per round
rounds = [s + 1 for s in slots]   # slot 0 = round 1 (load); slot s = round s+1
nat = [[perm[p[2]] for p in row] for row in table]   # natural key bit index per (round, class)
ok_cols = 0; cols = len(table[0])
for c in range(cols):
    seq = [nat[r][c] for r in range(len(rounds))]
    half = {x // 28 for x in seq}
    if len(half) != 1: continue
    good = True
    for r in range(1, len(rounds)):
        d = (seq[r] - seq[r - 1]) % 28; s = shifts[rounds[r] - 1]
        if d not in (s, (-s) % 28): good = False; break
    ok_cols += good
res['key_schedule'] = {'parameter_classes': cols, 'classes_following_DES_rotations': ok_cols, 'rounds_in_group': rounds}
print('key schedule: %d / %d parameter classes follow the DES rotation schedule within one 28-bit half' % (ok_cols, cols))
json.dump(res, open('out/des_reroll.json', 'w'), indent=1, default=str)
# coordinate-free check of the rotation structure (no assumption about the key port layout): the round-to-round map
# of key indices must be one partial permutation sigma for every shift-1 transition and sigma o sigma for shift 2
trans = {1: [], 2: []}
for r in range(1, len(rounds)):
    s = shifts[rounds[r] - 1]
    trans[s].append({table[r - 1][c][2]: table[r][c][2] for c in range(cols)})
def merge(ms):
    out = {}
    for m in ms:
        for a, b in m.items():
            if out.get(a, b) != b: return None
            out[a] = b
    return out
sigma = merge(trans[1]); sigma2 = merge(trans[2])
comp_ok = sigma is not None and sigma2 is not None and all(sigma.get(sigma.get(a)) in (None, b) for a, b in sigma2.items())
covered = sum(1 for a, b in (sigma2 or {}).items() if sigma.get(a) is not None and sigma.get(sigma[a]) == b)
res['key_schedule_coordinate_free'] = {'shift1_transitions': len(trans[1]), 'shift2_transitions': len(trans[2]),
    'shift1_consistent': sigma is not None, 'shift2_consistent': sigma2 is not None, 'shift2_equals_sigma_squared_where_defined': comp_ok,
    'shift2_pairs_checked': covered, 'shift2_pairs': len(sigma2 or {})}
print('coordinate-free key schedule check:', res['key_schedule_coordinate_free'])
json.dump(res, open('out/des_reroll.json', 'w'), indent=1, default=str)
