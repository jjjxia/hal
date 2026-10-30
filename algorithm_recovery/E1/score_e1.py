"""E1 scoring (PREREG.md S1-S5). Uses the RTL coefficients (ref_model.A) and the held-out gate-level simulation,
both only here, never in the recognizer. Also runs the recognizer for every slot origin (retiming check, not a
criterion). Usage: score_e1.py OUT_DIR HELDOUT_DIR"""
import sys, os, json
from fractions import Fraction as Fr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'invfold'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import invfold, identify, ref_model

def canon(t):   # transfer function up to boundary delay and global scale: (num/den0, den/den0) with num scaled to num[0] = 1
    num, den = [Fr(x) for x in t['num']], [Fr(x) for x in t['den']]
    return tuple(x / num[0] for x in num), tuple(x / den[0] for x in den), num[0] / den[0]

def main(out, held):
    eqs, meta = json.load(open(os.path.join(out, 'equations.json'))), json.load(open(os.path.join(out, 'meta.json')))
    F = invfold.run_big(invfold.recover, eqs, meta, 0)
    an = identify.analyze(F)
    A = {k: Fr(v, 1024) for k, v in ref_model.A.items()}
    gt = [([A['b10'], A['b11'], A['b12']], [Fr(1), -A['a11'], -A['a12']]), ([A['b20'], A['b21'], A['b22']], [Fr(1), -A['a21'], -A['a22']])]
    gtc = [canon({'num': n, 'den': d}) for n, d in gt]
    S = {}
    S['S1'] = F.P == 10
    S['S2'] = not F.report['errors'] and all(len(v) == 1 for v in F.input_phases.values()) and an['zero_delay_acyclic']
    rec = [s for s in an['sections'] if s['recursive']]
    ok3 = len(rec) == 2 and len(an['sections']) == 2 and all(len(s['transfer']) == 1 and len(s['transfer'][0]['den']) == 3 for s in rec)
    ok3 = ok3 and rec[1]['inputs'] == rec[0]['outputs'] and all(len(s['nodes']) > 2 for s in rec)   # cascade, output stages
    S['S3'] = ok3
    got = [canon(s['transfer'][0]) for s in rec] if ok3 else []
    S['S4_transfer'] = ok3 and [g[:2] for g in got] == [g[:2] for g in gtc] and [g[2] for g in got] == [g[2] for g in gtc]
    nround = sum(len(s['rounding_sites']) for s in an['sections'])
    out_fits = [f for k, f in F.fits.items() if k[0] == 'out']
    S['S4_rounding'] = nround == 10 and all(f and f['mode'] == 'floor' and list(f['coeffs'].values()) == [Fr(1, 16)] for f in out_fits)
    # S5: execute the recovered graph on the held-out stimulus, compare y with gate-level simulation
    xs = [int(l) for l in open(os.path.join(held, 'stim.txt'))]
    ys = [l.strip() for l in open(os.path.join(held, 'y.txt'))]
    ncyc = len(ys)
    pred = invfold.run_big(identify.execute, F, {'x': [invfold.signed(x, 16) for x in xs]}, 4, ncyc)['y']
    cmp_ = [T for T in range(5, ncyc) if ys[T] not in ('x', 'z')]
    bad = [T for T in cmp_ if pred.get(T) != int(ys[T])]
    S['S5'] = not bad and len(cmp_) >= 20000
    S['S5_detail'] = {'cycles_compared': len(cmp_), 'samples': len(cmp_) // 10, 'mismatches': len(bad), 'first': bad[:5]}
    # retiming check: every slot origin gives the same section canonical forms (up to boundary delay)
    inv = {}
    for o in range(F.P):
        Fo = invfold.run_big(invfold.recover, eqs, meta, o); ao = identify.analyze(Fo)
        inv[o] = {'errors': len(Fo.report['errors']), 'sections': [(canon(s['transfer'][0])[:2], s['transfer'][0]['delay']) if s['transfer'] else None for s in ao['sections'] if s['recursive']],
                  'n_sections': len(ao['sections'])}
    forms = {str(sorted(str(x[0]) for x in v['sections'] if x)) for v in inv.values()}   # as a set: origins may list sections in another order
    S['retiming_invariant_forms'] = len(forms) == 1
    S['boundary_delays_by_origin'] = {o: [x[1] for x in v['sections'] if x] for o, v in inv.items()}
    S['recovered'] = [{'num': s['transfer'][0]['num'], 'den': s['transfer'][0]['den'], 'delay': s['transfer'][0]['delay'], 'poles': s['transfer'][0]['poles'], 'class': s['transfer'][0]['class']} for s in rec]
    S['PASS'] = all(S[k] for k in ('S1', 'S2', 'S3', 'S4_transfer', 'S4_rounding', 'S5'))
    json.dump(S, open(os.path.join(out, 'score.json'), 'w'), indent=1, default=str)
    print(json.dumps(S, indent=1, default=str))

if __name__ == '__main__': main(sys.argv[1], sys.argv[2])
