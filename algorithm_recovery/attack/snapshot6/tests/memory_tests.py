"""X2 tests: memories with data-independent addressing resolved through the address-specific last write
(invfold meta['memories']). (m1) circular delay line -> LTI at stream rate, G = pure delay; (m2) ping-pong
bit-reversal reorder buffer -> block permutation, bit-reversed; (m3) WRITE_FIRST vs READ_FIRST change the delay."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import invfold, frame
W = 16

def run(P, raddr, waddr, latency, mode='read_first', origin=0, ncyc=120, win=range(48, 96)):
    """memory 'ram' written every cycle with x at waddr(t), read address raddr(t), output port y = read data"""
    writes = {t: [waddr(t), [['port', 'x', b] for b in range(W)]] for t in range(ncyc)}
    mem = {'ram': {'latency': latency, 'mode': mode, 'writes': writes, 'raddr': {t: raddr(t) for t in range(ncyc)}, 'outputs': {'ram_out': list(range(W))}}}
    eqs = [{'T': T, 'target': 'top_module - y', 'smt2': 'ram_out_%d' % T} for T in win]
    eqs += [{'T': T, 'target': 'D - i_D', 'smt2': 'x_%d' % T} for T in win]   # a dummy register so the word table is not empty
    meta = {'window': list(win), 'widths': {'D': W}, 'ports': {'in': {'x': W}, 'out': {'y': W}}, 'write_enable': {'D': [1] * ncyc}, 'memories': mem}
    F = invfold.run_big(invfold.recover, eqs, meta, origin, 1, P)
    assert not F.report['errors'], F.report['errors']
    return frame.classify_frame(F)

ok = {}
# (m1) circular buffer, 4 entries: write t mod 4, read (t+1) mod 4, latency 1 -> y(t) = x(t-4)
c = run(4, lambda t: (t + 1) % 4, lambda t: t % 4, 1)
ok['m1 circular delay line -> LTI, G = pure delay (G = 1 up to delay)'] = c.get('class') == 'LTI at stream rate' and c['G_num'] == ['1'] and c['G_den'] == ['1']
print('m1', c)
# (m2) ping-pong reorder: bank = frame parity, write natural, read bit-reversed from the other bank
N = 8; br = frame.bit_reverse_perm(N)
c = run(2 * N, lambda t: ((t // N + 1) % 2) * N + br[t % N], lambda t: (t // N % 2) * N + t % N, 1)
print('m2', {k: v for k, v in c.items() if k not in ('row_delays', 'col_delays')})
ok['m2 reorder buffer -> block permutation, 2 blocks of 8, bit-reversed'] = c.get('class', '').startswith('block permutation') and c.get('order') == 'bit-reversed' and c.get('sub_block') == 8 and c.get('blocks_per_frame') == 2
# (m3) read-during-write: read the address being written in the same cycle
for mode in ('read_first', 'write_first'):
    c = run(4, lambda t: t % 4, lambda t: t % 4, 1, mode=mode)
    print('m3', mode, c.get('class'), c.get('G_delay'), c.get('G_num'))
    ok['m3 %s -> pure delay' % mode] = c.get('class') == 'LTI at stream rate' and c['G_num'] == ['1']
for k, v in ok.items(): print('PASS' if v else 'FAIL', k)
