"""D1: the periodicity check of the periodic extension compares write ADDRESSES and read addresses of RAMs (and CE,
SRL shift sets) after the window, but not the write DATA mapping meta['memories'][M]['writes'][t][1] (which word / port
bits are written). A write-data mux whose select has period 2P (ping-pong of the data source, e.g. alternately the
real and imaginary sample into one buffer cell) is replicated from the window's single period and resolves silently
to one source. Design: P = 2 certified, one RAM cell (address 0) written in slot 0 with x in even frames and z in odd
frames, read in slot 0 (latency 1) into D in slot 1; one-period window, extension on. Truth: D alternates x[n-?] / z."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness5 import *
P, ncyc = 2, 80
def mk(win, ext):
    writes = {t: [0, [['port', 'x' if (t // P) % 2 == 0 else 'z', b] for b in range(16)]] for t in range(ncyc) if t % P == 0}
    raddr = {t: 0 for t in range(ncyc) if t % P == 1}
    mem = {'ram': {'latency': 1, 'mode': 'read_first', 'writes': writes, 'raddr': raddr, 'outputs': {'ram_out': list(range(16))}}}
    eqs = [{'T': T, 'target': 'top_module - y', 'smt2': 'D_o_Q_%d' % T} for T in win] + [{'T': T, 'target': 'D - i_D', 'smt2': 'ram_out_%d' % T} for T in win if T % P == 0]
    meta = {'window': list(win), 'widths': {'D': 16}, 'ports': {'in': {'x': 16, 'z': 16}, 'out': {'y': 16}}, 'write_enable': {'D': [int(t % P == 0) for t in range(ncyc)]},
            'memories': mem, 'periodic_extension': ext}
    return eqs, meta
for win, ext in ((range(40, 42), True), (range(40, 48), False)):
    F = recover(*mk(win, ext), period=P)
    print('window %s extension=%s: errors %s | D resolves to %s' % ((win[0], win[-1]), ext, F.report['errors'][:2], sorted({str(v) for k, d in F.nodes.items() if k[0] == 'D' for v in d['res'].values()})))
