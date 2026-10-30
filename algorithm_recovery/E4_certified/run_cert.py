"""Inverse folding on NetNotary-certified forms (exploratory; not preregistered).
Input: a NetNotary config (forms over one certified period, start, period, .hal for widths). Write enables are
constant 1 for the designs handled here (the certified schedule fixes CE = '1'; see the design's cands.json).
For every origin: recover the single-rate graph, report structure (nodes, edges with delays, input phases, nonlinear
nodes, total delay). Then execute the recovered graph on held-out simulation traces and compare every signal with
the word values of the netlist simulation. Time alignment (sample offset of the VCD, input row offset) is
calibrated on the trace the forms came from, then fixed for the held-out traces.
Usage: run_cert.py CONFIG.json OUT.json  (paths in CONFIG relative to NetNotary's algo/ directory)"""
import sys, os, json, re, time, collections
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, '..', 'invfold'))
import invfold, halforms
ALGO = '/home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/algo'

def stim_inputs(path, cols):
    """stim.txt rows: one per cycle, whitespace-separated integers in the order cols."""
    rows = [list(map(int, l.split())) for l in open(path)]
    return {c: [r[i] for r in rows] for i, c in enumerate(cols)}

def structure(F):
    edges = []
    for (w, phi), d in sorted(F.nodes.items()):
        for s, src in sorted(d['res'].items()):
            edges.append(((w, phi), src))
    flat = [(v, c) for v, src in edges for c in (src[2] if src[0] == 'cat' else (src,))]
    tot = sum(src[3] for _, src in {(v, c[:4]) for v, c in flat} if src[0] == 'sig')
    return flat, tot

def main(cfg_path, out_path, design):
    cfg = json.load(open(os.path.join(ALGO, cfg_path)))
    eqs = halforms.load_forms(os.path.join(ALGO, cfg['forms']))
    W, ports, alias, cenet = halforms.netlist_meta(cfg['hal'], design['data_ports'] + design.get('alias_ports', []))
    alias = {a: v for a, v in alias.items() if a[len('top_module_'):] in design.get('alias_ports', [])}
    for a in alias: ports.pop(a[len('top_module_'):], None)
    win = sorted({e['T'] for e in eqs})
    ncyc = design['ncyc']
    we = {w: [1] * ncyc for w in W}; res_ce = None
    if design.get('ce_from_trace'):
        # clock enables are control (part of the certified schedule): read them from the trace the forms came from;
        # the sample time is calibrated so that every non-constant D expression in the window falls on an enabled cycle
        names = sorted({n for n in cenet.values() if n not in ("'1'", "'0'")})
        best_ce = None
        for off in design['ce_offsets']:
            tr = halforms.net_trace(design['sims'][0], names, ncyc, off)
            cand = {w: ([1] * ncyc if cenet.get(w, "'1'") == "'1'" else [int(x == '1') for x in tr[cenet[w]]]) for w in W}
            viol = sum(1 for e in eqs if ' - i_D' in e['target'] and not re.fullmatch(r'0b[01]+', e['text']) and not cand[e['target'].split(' - ')[0]][e['T']])
            print('enable offset', off, 'violations', viol, flush=True)
            if best_ce is None or viol < best_ce[0]: best_ce = (viol, off, cand)
        we = best_ce[2]; res_ce = {'offset_ps': best_ce[1], 'violations': best_ce[0]}
    meta = {'window': win, 'widths': W, 'ports': {'in': ports, 'out': {}}, 'write_enable': we, 'alias': alias, 'dq_perm': halforms.dq_perm(cfg['hal'])}
    P = cfg['period']; res = {'design': design['name'], 'period_certified': P, 'window': [win[0], win[-1]], 'words': W, 'ports': ports, 'alias': {a: len(v) for a, v in alias.items()}, 'enables': res_ce, 'origins': {}}
    best = None
    for o in design.get('origins', range(P)):
        t0 = time.time()
        F = invfold.run_big(invfold.recover, eqs, meta, o, 1, P)
        edges, tot = structure(F)
        delays = collections.Counter(src[3] for _, src in edges if src[0] == 'sig')
        res['origins'][o] = {'errors': F.report['errors'], 'nonlinear': len(F.report.get('nonlinear', [])), 'nodes': len(F.nodes),
                             'input_phases': F.input_phases, 'total_signal_delay': tot, 'delay_histogram': dict(delays), 'seconds': round(time.time() - t0, 1)}
        print('origin', o, res['origins'][o], flush=True)
        if best is None or tot < best[1]: best = (o, tot, F)
    o, tot, F = best
    res['canonical_origin'] = {'origin': o, 'total_signal_delay': tot, 'rule': 'least total delay, then smallest origin (post hoc)'}
    res['graph'] = [{'node': list(v), 'reads': sorted({str(src) for src in d['res'].values()})} for v, d in sorted(F.nodes.items())]
    # ---- execution vs simulation traces ----
    def check(sim, stim, offset, row_shift, n_from, n_to):
        tr = halforms.word_trace(cfg['hal'], sim, ncyc + 2, offset)
        inp = stim_inputs(stim, design['stim_cols'])
        T0 = F.win[0] + F.origin
        def inputs(p, c):
            raw = inp[design['port_col'][p]][c + row_shift]
            return sum(((raw >> j) & 1) << i for i, j in enumerate(perm[p]))
        def init(w, c): return tr[w][c + 1]          # value written at cycle c is visible during cycle c + 1
        val = invfold.run_big(invfold.run_graph, F, inputs, init, n_from, n_to)
        bad = [(k, v, tr[k[0]][T0 + k[2] * P + k[1] + 1]) for k, v in val.items() if tr[k[0]][T0 + k[2] * P + k[1] + 1] != v]
        return len(val), bad
    perm = {p: halforms.port_order(cfg['hal'], p) for p in ports}
    cal = []
    for offset in design['offsets']:
        for rs in design['row_shifts']:
            n, bad = check(design['sims'][0], design['stims'][0], offset, rs, design['n_from'], design['n_from'] + 4)
            cal.append((len(bad), offset, rs)); print('calibration offset', offset, 'row shift', rs, 'mismatches', len(bad), 'of', n, flush=True)
    _, offset, rs = min(cal)
    res['calibration'] = {'offset_ps': offset, 'row_shift': rs, 'trace': design['sims'][0], 'table': cal}
    res['heldout'] = []
    for sim, stim in zip(design['sims'], design['stims']):
        n_to = (ncyc - 2 - F.win[0] - F.origin) // P
        n, bad = check(sim, stim, offset, rs, design['n_from'], n_to)
        res['heldout'].append({'trace': sim, 'signals_compared': n, 'mismatches': len(bad), 'first': [str(b) for b in bad[:3]]})
        print('trace', sim, 'signals', n, 'mismatches', len(bad), flush=True)
    json.dump(res, open(out_path, 'w'), indent=1, default=str)

DES = {'name': 'des', 'data_ports': ['key_ordered', 'desIn_ordered'], 'ncyc': 400,
       'stim_cols': ['decrypt', 'roundSel', 'key', 'desIn'], 'port_col': {'key_ordered': 'key', 'desIn_ordered': 'desIn'},
       'sims': [ALGO + '/des/' + s for s in ('sim1', 'sim2', 'sim3')], 'stims': [ALGO + '/des/stim%d.txt' % i for i in (1, 2, 3)],
       'offsets': [4999, 9999], 'row_shifts': [-1, 0, 1], 'n_from': -1}

XTEA = {'name': 'xtea', 'data_ports': ['key_ordered', 'block_ordered'], 'alias_ports': ['result_ordered'], 'ncyc': 700,
        'ce_from_trace': True, 'ce_offsets': [4999, 5000, 9999],
        'stim_cols': ['reset_n', 'encdec', 'next', 'rounds', 'key', 'block'], 'port_col': {'key_ordered': 'key', 'block_ordered': 'block'},
        'sims': [ALGO + '/xtea/' + s for s in ('sim1', 'sim2', 'sim3')], 'stims': [ALGO + '/xtea/stim%d.txt' % i for i in (1, 2, 3)],
        'offsets': [4999, 9999], 'row_shifts': [-1, 0, 1], 'n_from': -1}

if __name__ == '__main__':
    d = {'des': DES, 'xtea': XTEA}[sys.argv[3]]
    if len(sys.argv) > 4: d['origins'] = [int(x) for x in sys.argv[4].split(',')]
    for i, s in enumerate(d['sims']):
        d['sims'][i] = [os.path.join(s, x, 'waveform.vcd') for x in os.listdir(s) if x.startswith('hal_simulation')][0]
    main(sys.argv[1], sys.argv[2], d)
