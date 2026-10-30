"""PROPOSAL §2 worked example as synthetic equations (sanity check): two first-order IIRs on one accumulator, P = 2.
T[2n]: ACC <- x + 3*S1, S2 <- ACC;  T[2n+1]: ACC <- ACC + 5*S2, S1 <- ACC.  Expected: A0 = x + 3 A0[n-1],
A1 = A0 + 5 A1[n-1] (two sections), S1/S2 collapse to delays."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import identify, invfold
W = 16; eqs = []; win = list(range(20, 50)); N = 60
ce = {'acc': [1] * N, 's1': [t % 2 for t in range(N)], 's2': [1 - t % 2 for t in range(N)]}
for T in win:
    if T % 2 == 0:
        eqs.append({'T': T, 'target': 'acc - i_D', 'smt2': '(bvadd top_module_x_%d (bvmul #x0003 s1_o_Q_%d))' % (T, T)})
        eqs.append({'T': T, 'target': 's2 - i_D', 'smt2': 'acc_o_Q_%d' % T})
        eqs.append({'T': T, 'target': 's1 - i_D', 'smt2': 'acc_o_Q_%d' % T})   # held (CE = 0)
    else:
        eqs.append({'T': T, 'target': 'acc - i_D', 'smt2': '(bvadd acc_o_Q_%d (bvmul #x0005 s2_o_Q_%d))' % (T, T)})
        eqs.append({'T': T, 'target': 's1 - i_D', 'smt2': 'acc_o_Q_%d' % T})
        eqs.append({'T': T, 'target': 's2 - i_D', 'smt2': 'acc_o_Q_%d' % T})   # held
    eqs.append({'T': T, 'target': 'top_module - y', 'smt2': 'acc_o_Q_%d' % T})
meta = {'window': win, 'widths': {'acc': W, 's1': W, 's2': W}, 'ports': {'in': {'x': W}, 'out': {'y': W}}, 'write_enable': ce}
F = invfold.run_big(invfold.recover, eqs, meta, 0)
r = {'period': F.P, 'origin': 0, 'errors': F.report['errors'], 'input_phases': F.input_phases}
r.update(identify.analyze(F)); print(identify.summary(r)); print('copies', F.copies)
for k, f in F.fits.items(): print(k, identify.fmt(f))
