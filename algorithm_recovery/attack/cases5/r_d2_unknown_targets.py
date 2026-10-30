"""D2: targeted UNKNOWN sampling sets ONE source at a time to each constant of the expression (others 0). Guards that
need a constant in a high slice, or two sources at once, are still missed (node fitted linear, not 'incomplete'):
 (i)   Slice(x, 12, 15) == 0b1010   (needs x = 0xA...; the probe x = 10 has high bits 0; random samples hit it 1 in 16)
 (i')  Slice(x, 1, 15) == 15-bit constant (the probe x = constant is shifted by one bit; random samples: 1 in 32768)
 (ii)  (x == 3) & (w == 5)          (needs both at once)
 (iii) (x + w) == 0b...0111 with x and w both nonzero is fine (x = 7, w = 0 hits): control
 (iv)  Slice(x, 0, 3) == 0b0101     (low slice: the probe x = 5 hits): control"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'snapshot5'))
import invfold
B16 = lambda v: '0b' + format(v, '016b')
cases = {
 '(i) Slice(x,12,15) == 0b1010': lambda T: 'Ite((Slice(x_ordered_%d, 12, 15) == 0b1010), UNKNOWN_OPERATION, (w_o_Q_ordered_%d + x_ordered_%d))' % (T, T, T),
 "(i') Slice(x,1,15) == 0x2AAA... (15 bits)": lambda T: 'Ite((Slice(x_ordered_%d, 1, 15) == 0b101010101010101), UNKNOWN_OPERATION, (w_o_Q_ordered_%d + x_ordered_%d))' % (T, T, T),
 '(ii) (x == 3) & (w == 5)': lambda T: 'Ite(((x_ordered_%d == %s) & (w_o_Q_ordered_%d == %s)), UNKNOWN_OPERATION, (w_o_Q_ordered_%d + x_ordered_%d))' % (T, B16(3), T, B16(5), T, T),
 '(iii) (x + w) == 7': lambda T: 'Ite(((x_ordered_%d + w_o_Q_ordered_%d) == %s), UNKNOWN_OPERATION, (w_o_Q_ordered_%d + x_ordered_%d))' % (T, T, B16(7), T, T),
 '(iv) Slice(x,0,3) == 0b0101': lambda T: 'Ite((Slice(x_ordered_%d, 0, 3) == 0b0101), UNKNOWN_OPERATION, (w_o_Q_ordered_%d + x_ordered_%d))' % (T, T, T),
}
for name, txt in cases.items():
    win = list(range(10, 40))
    eqs = [{'T': T, 'target': 'w - i_D', 'text': txt(T)} for T in win]
    meta = {'window': win, 'widths': {'w': 16}, 'ports': {'in': {'x_ordered': 16}, 'out': {}}, 'write_enable': {'w': [1] * 50}}
    F = invfold.recover(eqs, meta)
    f = F.fits.get(('sig', 'w', 0))
    print('%-32s fit %s | incomplete %s' % (name, None if f is None else (f['mode'], {str(a): str(b) for a, b in f['coeffs'].items()}), F.report.get('incomplete')))
