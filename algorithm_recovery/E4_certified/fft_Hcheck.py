"""Validate the FFT64 frame matrix H (out/fft_H.pkl, built from the FITTED node coefficients) as a linear model
against the netlist simulation (round 5 of the adversarial review: fft_exec runs the raw expressions, so it does not
validate H). For every output component, the prediction sum c * x[phase](frame - delay) is compared with the observed
DOR/DOI at the best output phase over frames 1..3; agreement within a few LSB (roundings dropped by linearization)
validates H. Input alignment +1 as calibrated in fft_exec."""
import pickle, collections, sys, os, json
from fractions import Fraction as Fr
sys.path.insert(0, '../invfold'); sys.path.insert(0, '.')
import fft_run as R
from vcd import VCD
d = pickle.load(open('out/fft_H.pkl', 'rb')); H = d['H']; P, T0 = 128, 260
rows = collections.defaultdict(list)
for (r, x), (dl, c) in H.items():
    rows[r].append((x.split("'")[3], int(x.split(', ')[2].rstrip(')')), dl, float(Fr(c))))
res = {}
for si in (1, 2, 3):
    sd = os.path.join(R.ALGO, 'fft', 'sim%d' % si); vcd = [os.path.join(sd, x, 'waveform.vcd') for x in os.listdir(sd) if x.startswith('hal_sim')][0]
    names = ['%s(%d)' % (p, i) for p in ('DR', 'DI') for i in range(16)] + ['%s(%d)' % (p, i) for p in ('DOR', 'DOI') for i in range(19)]
    v = VCD(vcd, set(names))
    def word(p, w, c):
        x = sum((v.value('%s(%d)' % (p, i), 10000 * c + 9999) == '1') << i for i in range(w)); return x - (1 << w) if x >> (w - 1) else x
    xin = lambda p, ph, m: word(p[:2], 16, T0 + m * P + ph + 1)
    obs = {p: [word(p, 19, c) for c in range(700)] for p in ('DOR', 'DOI')}
    errs = []
    for r, ent in rows.items():
        port = r.split("'")[1]
        pred = {n: sum(c * xin(p, ph, n - dl) for p, ph, dl, c in ent) for n in (1, 2, 3)}
        best = min(((max(abs(pred[n] - obs[port][T0 + n * P + q]) for n in (1, 2, 3) if T0 + n * P + q < 690), q) for q in range(P)
                    if all(T0 + n * P + q < 690 for n in (1, 2))), key=lambda t: t[0])
        errs.append(best[0])
    res['sim%d' % si] = {'components': len(errs), 'max_abs_error': max(errs), 'within_4_lsb': sum(e <= 4 for e in errs), 'error_hist': dict(collections.Counter(int(round(e)) for e in errs).most_common(6))}
    print('sim%d' % si, res['sim%d' % si], flush=True)
json.dump(res, open('out/fft_Hcheck.json', 'w'), indent=1)
