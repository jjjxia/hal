"""Tests of frame semantics / polyphase classification (frame.py) on hand-written equation systems with known
answers, plus E1's HAL equations. Each case states its expected class; negatives must not be named."""
import sys, os, json, math
from fractions import Fraction as Fr
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import invfold, identify, frame
from identify import RF

def bv(v, w): return '(_ bv%d %d)' % (v % (1 << w), w)
def mk(P, win, words, cycles):
    """cycles(T) -> list of (target, smt2); words: {name: (width, enable(T))}"""
    eqs = []
    for T in win:
        for tgt, e in cycles(T): eqs.append({'T': T, 'target': tgt, 'smt2': e})
    N = win[-1] + 5
    return eqs, {w: [en(t) for t in range(N)] for w, (_, en) in words.items()}

def run(eqs, meta, pairs=None):
    F = invfold.run_big(invfold.recover, eqs, meta, 0)
    assert not F.report['errors'], F.report['errors']
    return F, frame.classify_frame(F, pairs)

results = {}
W = 16; win = list(range(20, 60))

# (a) ping-pong FIR, P = 2: y[t] = 3 x[t] + 5 x[t-1] + 7 x[t-2], delay line stored alternately in A and B
def ping(T, h=(3, 5, 7), tv=False):
    q = lambda r: '%s_o_Q_%d' % (r, T)
    x = 'x_%d' % T
    if T % 2 == 0:
        h0 = h[0]
        return [('A - i_D', x), ('B - i_D', q('A')),
                ('Y - i_D', '(bvadd (bvmul %s %s) (bvadd (bvmul %s %s) (bvmul %s %s)))' % (bv(h0, W), x, bv(h[1], W), q('B'), bv(h[2], W), q('A'))),
                ('top_module - y', q('Y'))]
    h0 = 11 if tv else h[0]
    return [('B - i_D', x), ('A - i_D', q('B')),
            ('Y - i_D', '(bvadd (bvmul %s %s) (bvadd (bvmul %s %s) (bvmul %s %s)))' % (bv(h0, W), x, bv(h[1], W), q('A'), bv(h[2], W), q('B'))),
            ('top_module - y', q('Y'))]
words = {'A': (W, lambda t: t % 2 == 0), 'B': (W, lambda t: t % 2 == 1), 'Y': (W, lambda t: True)}
for name, tv in (('pingpong_fir', False), ('time_varying', True)):
    eqs, ce = mk(2, win, words, lambda T: ping(T, tv=tv))
    meta = {'window': win, 'widths': {w: W for w in words}, 'ports': {'in': {'x': W}, 'out': {'y': W}}, 'write_enable': ce}
    F, c = run(eqs, meta)
    results[name] = c
ok_a = results['pingpong_fir'].get('class') == 'LTI at stream rate' and results['pingpong_fir']['G_num'] == ['3', '5', '7'] and results['pingpong_fir']['G_den'] == ['1']
ok_b = 'LTI' not in results['time_varying'].get('class', '')

# (c) 8-point complex DFT, fixed point (8 fraction bits), outputs in bit-reversed order; (d) one corrupted twiddle
N, Q = 8, 256; win8 = list(range(40, 120))
def dft_case(corrupt):
    cs = [[round(Q * math.cos(2 * math.pi * j * k / N)) for j in range(N)] for k in range(N)]
    sn = [[round(Q * math.sin(2 * math.pi * j * k / N)) for j in range(N)] for k in range(N)]
    if corrupt: cs[3][5] += 40
    br = frame.bit_reverse_perm(N)
    WX, WY = 16, 24
    def cyc(T):
        phi = T % N; q = lambda r: '%s_o_Q_%d' % (r, T); out = []
        out += [('XR%d - i_D' % phi, 'xr_%d' % T), ('XI%d - i_D' % phi, 'xi_%d' % T)]
        if phi == 0:   # all inputs of the previous frame are in XR*/XI*: compute the 8 outputs
            for k in range(N):
                sx = lambda r: '((_ sign_extend 16) %s)' % q(r)
                re_ = ' '.join('(bvmul %s %s) (bvmul %s %s)' % (bv(cs[k][j], 32), sx('XR%d' % j), bv(sn[k][j], 32), sx('XI%d' % j)) for j in range(N))
                im_ = ' '.join('(bvmul %s %s) (bvmul %s %s)' % (bv(cs[k][j], 32), sx('XI%d' % j), bv(-sn[k][j], 32), sx('XR%d' % j)) for j in range(N))
                out += [('YR%d - i_D' % k, '((_ extract 31 8) (bvadd %s))' % re_), ('YI%d - i_D' % k, '((_ extract 31 8) (bvadd %s))' % im_)]
        o = br[(phi - 1) % N]   # output port shows Y[bitrev(0)] at phase 1, ..., Y[bitrev(7)] at phase 0 of the next frame
        out += [('top_module - yr', q('YR%d' % o)), ('top_module - yi', q('YI%d' % o))]
        return out
    words = {**{'XR%d' % j: (WX, (lambda j: lambda t: t % N == j)(j)) for j in range(N)}, **{'XI%d' % j: (WX, (lambda j: lambda t: t % N == j)(j)) for j in range(N)},
             **{'YR%d' % k: (WY, lambda t: t % N == 0) for k in range(N)}, **{'YI%d' % k: (WY, lambda t: t % N == 0) for k in range(N)}}
    eqs, ce = mk(N, win8, words, cyc)
    meta = {'window': win8, 'widths': {w: v[0] for w, v in words.items()}, 'ports': {'in': {'xr': WX, 'xi': WX}, 'out': {'yr': WY, 'yi': WY}}, 'write_enable': ce}
    F, c = run(eqs, meta, pairs=[(('xr', 'xi'), ('yr', 'yi'))])
    return c
results['dft8_bitrev'] = dft_case(False)
results['dft8_corrupted'] = dft_case(True)
d = results['dft8_bitrev'].get('dft', {})
ok_c = results['dft8_bitrev'].get('class') == 'block transform' and d.get('match') and d.get('output_order') == 'bit-reversed' and d.get('input_order') == 'natural'
ok_d = not results['dft8_corrupted'].get('dft', {}).get('match', False)

# (e) E1 (HAL equations): LTI at frame rate; G must equal the product of the two RTL biquads up to a pure delay
E1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'E1', 'build', 'out_dev')
ok_e = None
if os.path.exists(os.path.join(E1, 'equations.json')):
    F = invfold.run_big(invfold.recover, json.load(open(os.path.join(E1, 'equations.json'))), json.load(open(os.path.join(E1, 'meta.json'))), 0)
    c = frame.classify_frame(F); results['E1'] = c
    sys.path.insert(0, os.path.join(E1, '..', '..')); import ref_model
    A = {k: Fr(v, 1024) for k, v in ref_model.A.items()}
    want = RF([A['b10'], A['b11'], A['b12']], [1, -A['a11'], -A['a12']]) * RF([A['b20'], A['b21'], A['b22']], [1, -A['a21'], -A['a22']]) * RF([Fr(1, 16)])
    got = RF([Fr(x) for x in c['G_num']], [Fr(x) for x in c['G_den']]) if 'G_num' in c else None
    ok_e = got is not None and (got - want).zero()

summary = {'a pingpong FIR -> LTI, G = 3+5z^-1+7z^-2': ok_a, 'b time-varying -> not LTI': ok_b,
           'c DFT8 -> DFT, output bit-reversed, input natural, within bound': ok_c, 'd corrupted DFT8 -> no match': ok_d,
           'e E1 -> LTI, G = H1*H2/16': ok_e}
print(json.dumps(results, indent=1, default=str)[:6000])
for k, v in summary.items(): print('PASS' if v else ('SKIP' if v is None else 'FAIL'), k)
