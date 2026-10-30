"""C9 (survival check): SRL16E model (kind 'srl') against a cycle simulation: depth 3, shift enable every other
cycle (CE = t odd), data = input x; register Y <- SRL output in every cycle, P = 2. Compare the resolved source of Y
(input phase, delay) with the simulated value of Q, with and without periodic extension."""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness3 import *
P, depth, ncyc = 2, 3, 80
shifts = [t for t in range(ncyc) if t % 2 == 1]
for ext in (False, True):
    win = range(40, 48)
    mem = {'s': {'kind': 'srl', 'depth': depth, 'shifts': shifts, 'data': [['port', 'x', b] for b in range(8)] if False else [['port', 'x', 0]], 'outputs': {'srl_q': [0]}}}
    eqs = [{'T': T, 'target': 'Y - i_D', 'smt2': 'srl_q_%d' % T} for T in win] + [{'T': T, 'target': 'top_module - y', 'smt2': 'Y_o_Q_%d' % T} for T in win]
    meta = {'window': list(win), 'widths': {'Y': 1}, 'ports': {'in': {'x': 1}, 'out': {'y': 1}}, 'write_enable': {'Y': [1] * ncyc}, 'memories': mem, 'periodic_extension': ext}
    F = recover(eqs, meta, period=P)
    # simulate: SR shifts in x(t) at the edge after cycle t when t in shifts; Q during t = SR[depth-1]
    rng = random.Random(1); x = [rng.getrandbits(1) for _ in range(ncyc)]; sr = [0] * 16; Q = []
    for t in range(ncyc):
        Q.append(sr[depth - 1])
        if t in shifts: sr = [x[t]] + sr[:-1]
    bad = 0
    for (w, phi), d in F.nodes.items():
        (src,) = d['res'].values()
        for n in range(0, 3):
            T = F.win[0] + F.origin + n * P + phi
            kind, port, ph, k = src[:4]
            if x[F.win[0] + F.origin + (n - k) * P + ph] != Q[T]: bad += 1
    print('periodic_extension=%s: resolution %s, mismatches vs simulated SRL output %d' % (ext, {str(k): str(d['res']) for k, d in F.nodes.items()}, bad))
