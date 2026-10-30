"""Execute the recovered FFT64 graph (fft_run.py setup) on simulation traces and compare the output ports DOR/DOI
with the netlist simulation, cycle by cycle (a test of the recovery, like E1's S5). The graph is feed-forward, so
outputs of frames >= n_from + 3 do not depend on the (zero) initial values. Inputs DR/DI and outputs DOR/DOI are
read from the VCD at the forms' alignment (10000 T + 9999). Usage: fft_exec.py [sim index ...]"""
import sys, os, json, re, collections
sys.path.insert(0, '../invfold'); sys.path.insert(0, '.')
import invfold, halforms, fft_run as R
from vcd import VCD
def main(sims):
    merged = {}
    for f in ('perfdiag/fft/forms_main.txt', 'perfdiag/fft/forms_sus.txt', 'cert/fft_108_S.txt', 'cert/fft_125_S.txt'):
        for e in halforms.load_forms(os.path.join(R.ALGO, f)):
            if e['target'].startswith('top_module'): continue
            merged[(e['T'], e['target'].split(' - ')[0])] = e
    eqs = list(merged.values()); meta, extra, info = R.build_meta(eqs)
    F = invfold.Folded(eqs + extra, meta, 0); F.period(R.P); F.build()
    N = json.load(open(R.H))['netlist']
    unordered = [m['name'] for m in N['modules'] if m['name'].startswith('DANA_module_') and halforms.qgroup(m)['name'] == 'o_Q']
    invfold.run_big(invfold.infer_bit_orders, F, unordered)
    print('errors', F.report['errors'][:3], flush=True)
    res = {}; best = None; best_ish = best_osh = 0
    for si in sims:
        sd = os.path.join(R.ALGO, 'fft', 'sim%d' % si); vcd = [os.path.join(sd, x, 'waveform.vcd') for x in os.listdir(sd) if x.startswith('hal_sim')][0]
        names = ['%s(%d)' % (p, i) for p in ('DR', 'DI') for i in range(16)] + ['%s(%d)' % (p, i) for p in ('DOR', 'DOI') for i in range(19)]
        v = VCD(vcd, set(names))
        def word(p, w, c): return sum((v.value('%s(%d)' % (p, i), 10000 * c + 9999) == '1') << i for i in range(w))
        perm = {p: halforms.port_order(R.H, p) for p in ('DR_ordered', 'DI_ordered')}
        operm = {p: halforms.port_order(R.H, p + '_ordered') for p in ('DOR', 'DOI')}
        for ish in ((-1, 0, 1, 2) if si == sims[0] else (best_ish,)):
            def inputs(p, c, ish=ish):
                raw = word(p[:2], 16, c + ish) if c + ish >= 0 else 0
                return sum(((raw >> j) & 1) << i for i, j in enumerate(perm[p]))
            val, outv = invfold.run_big(invfold.run_graph, F, inputs, lambda w, c: 0, -2, 4, True)
            for osh in ((-1, 0, 1) if si == sims[0] else (best_osh,)):
                stats = collections.Counter(); first = []
                for (port, T), x in sorted(outv.items()):
                    if T < R.T0 + R.P or T + osh >= 690: continue   # frames >= 1 (feed-forward: independent of the zero start)
                    obs = word(port, 19, T + osh); want = sum(((obs >> j) & 1) << i for i, j in enumerate(operm[port]))
                    ok = (x & ((1 << 19) - 1)) == want; stats['match' if ok else 'mismatch'] += 1
                    if not ok and len(first) < 5: first.append((port, T, x, want))
                print('sim%d input shift %d output shift %d' % (si, ish, osh), dict(stats), first[:2], flush=True)
                res['sim%d_i%d_o%d' % (si, ish, osh)] = {'stats': dict(stats), 'first_mismatches': first}
                if si == sims[0] and (best is None or stats['match'] > best[0]): best = (stats['match'], ish, osh)
        if si == sims[0]: best_ish, best_osh = best[1], best[2]; res['calibrated_on'] = 'sim%d' % si; res['shifts'] = best[1:]
    json.dump(res, open('out/fft_exec.json', 'w'), indent=1, default=str)
if __name__ == '__main__': main([int(x) for x in sys.argv[1:]] or [1])
