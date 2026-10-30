"""C6: meta['periodic_extension'] (steady-state resolution: control before the window = periodic image of the window).
The certificate fixes the period of the FORMS; control (write enables, RAM addresses) comes from the trace and is only
'untainted', not proved P-periodic. With the extension and a one-period window (as in fft_run.py), control whose period
is a multiple of the certified P is silently replaced by the window's image:
 (a) a register written only in every other frame (decimating integrator, CE period 2P): recovered as written every
     frame, y[n] = x[n] + y[n-1] at the frame rate; without the extension the same input is reported as an error.
 (b) circular-buffer memory of 4 cells (write cell n mod 4 in slot 1, read cell n mod 4 in slot 0, i.e. the value
     written 4 frames ago): the window's only write has the read address, so its periodic image one frame back
     'matches' -> delay 1 frame instead of 4, no error.
Both are compared with the same system over a 4-period window and no extension (the resolution check fires)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness3 import *
P = 2
def show(tag, eqs, meta, period=P):
    F = recover(eqs, meta, period=period)
    fits = {str(k): {str(a): str(b) for a, b in f['coeffs'].items()} for k, f in F.fits.items() if f and (k[0] == 'sig' or k[1] == 'y')}
    fits.update({'res' + str(k): sorted(set(str(v) for v in d['res'].values())) for k, d in F.nodes.items() if k[0] == 'D'})
    print('%-46s errors %s | fits %s' % (tag, F.report['errors'][:2], fits))
# (a)
W = {'y': 16}; IN = {'x': 16}; OUT = {'yo': 16}
def step_a(T): return ({'y': '(bvadd %s %s)' % (X('x', T), R('y', T))} if T % (2 * P) == 0 else {}), {'yo': R('y', T)}
for ext in (True, False):
    eqs, meta = build(W, IN, OUT, step_a, 60, range(20, 22)); meta['periodic_extension'] = ext
    show('(a) one-period window, extension=%s' % ext, eqs, meta)
eqs, meta = build(W, IN, OUT, step_a, 60, range(20, 28)); show('(a) four-period window, no extension', eqs, meta)
# (b)
K = 4; ncyc = 80
def mem_meta(win, ext):
    writes = {t: [(t // P) % K, [['port', 'x', b] for b in range(16)]] for t in range(ncyc) if t % P == 1}
    raddr = {t: (t // P) % K for t in range(ncyc) if t % P == 0}
    mem = {'ram': {'latency': 1, 'mode': 'read_first', 'writes': writes, 'raddr': raddr, 'outputs': {'ram_out': list(range(16))}}}
    # D <- read data in slot 1 (the read issued in slot 0 has latency 1); y port shows D
    eqs = [{'T': T, 'target': 'top_module - y', 'smt2': 'D_o_Q_%d' % T} for T in win] + [{'T': T, 'target': 'D - i_D', 'smt2': 'ram_out_%d' % T} for T in win if T % P == 1]
    meta = {'window': list(win), 'widths': {'D': 16}, 'ports': {'in': {'x': 16}, 'out': {'y': 16}}, 'write_enable': {'D': [int(t % P == 1) for t in range(ncyc)]},
            'memories': mem, 'periodic_extension': ext}
    return eqs, meta
for ext in (True, False):
    show('(b) one-period window, extension=%s' % ext, *mem_meta(range(40, 42), ext))
show('(b) four-period window, no extension', *mem_meta(range(40, 48), False))

# (c) a memory read with no matching write (here: the output read in a cycle without a read) is reported as an error but
# then crashes the node fit (lanes_of/_fit do not handle the 'init' source)
eqs, meta = mem_meta(range(40, 48), False)
eqs += [{'T': T, 'target': 'E - i_D', 'smt2': 'ram_out_%d' % T} for T in range(40, 48)]; meta['widths']['E'] = 16; meta['write_enable']['E'] = [1] * ncyc
try: show('(c) read output used in a cycle without a read', eqs, meta)
except Exception as e: print('(c) read output used in a cycle without a read: CRASH %s: %s' % (type(e).__name__, e))
