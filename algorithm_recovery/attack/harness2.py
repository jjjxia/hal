"""Attack harness: build synthetic guided equation systems (as tests/worked_example.py does), simulate the hardware they
describe, run the invfold snapshot, and compare.

A design is given by
  W        : {register: width}
  IN       : {input port: width}
  OUT      : {output port: width}
  P        : schedule period
  step(T)  : -> (writes, outs) with writes = {register: smt2 text or None (hold, CE = 0)} and outs = {port: smt2 text}
             Text may use R(name, T') for 'name_o_Q_T'' and X(port, T') for 'top_module_port_T''.
  ncyc     : total cycles (ce is given from cycle 0), window = list of cycles given to the recognizer.
Semantics (the paper's Definition 1): Q_r(t+1) = E_{r,t} when ce_r(t) = 1, else Q_r(t).
"""
import sys, os, random, json, collections, signal
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'snapshot2'))
import invfold, identify, smt2

def R(name, T): return '%s_o_Q_%d' % (name, T)
def X(port, T): return 'top_module_%s_%d' % (port, T)
def c(v, w): return '#b' + format(v & ((1 << w) - 1), '0%db' % w)   # bit-vector constant

def build(W, IN, OUT, step, ncyc, window, ce_override=None):
    eqs, ce = [], {r: [0] * ncyc for r in W}
    for T in range(ncyc):
        wr, outs = step(T)
        for r in W:
            e = wr.get(r)
            ce[r][T] = 1 if e is not None else 0
            if T in window:
                if e is not None: eqs.append({'T': T, 'target': '%s - i_D' % r, 'smt2': e})
        if T in window:
            for p, e in outs.items(): eqs.append({'T': T, 'target': 'top_module - %s' % p, 'smt2': e})
    # every register must appear as a target at least once (held registers: give a dummy at a hold cycle)
    have = {e['target'] for e in eqs}
    for r in W:
        if '%s - i_D' % r not in have:
            eqs.append({'T': window[0], 'target': '%s - i_D' % r, 'smt2': R(r, window[0])})
    if ce_override: ce = ce_override(ce)
    meta = {'window': list(window), 'widths': dict(W), 'ports': {'in': dict(IN), 'out': dict(OUT)}, 'write_enable': ce}
    return eqs, meta

def widths_for(text, W, IN):
    ws = {}
    import re
    for m in re.finditer(r'\b([A-Za-z0-9_]+?)_o_Q_(\d+)\b', text): ws[m.group(0)] = W[m.group(1)]
    for m in re.finditer(r'\btop_module_([A-Za-z0-9]+)_(\d+)\b', text): ws[m.group(0)] = IN[m.group(1)]
    return ws

def simulate(W, IN, OUT, step, ncyc, xs, q0):
    """True hardware semantics of the step function. xs[port][t]; q0[reg] initial value at cycle 0.
    Returns Q[reg][t] (value during cycle t), D[reg][t] (written value, if written), Y[port][t]."""
    Q = {r: [None] * (ncyc + 1) for r in W}; D = {r: {} for r in W}; Y = {p: {} for p in OUT}
    for r in W: Q[r][0] = q0[r]
    for T in range(ncyc):
        wr, outs = step(T)
        def env_of(text):
            ws = widths_for(text, W, IN); env = {}
            for s in ws:
                if s.startswith('top_module_'):
                    p, t = s[len('top_module_'):].rsplit('_', 1); env[s] = xs[p][int(t)] & ((1 << IN[p]) - 1)
                else:
                    r, t = s.rsplit('_o_Q_', 1); env[s] = Q[r][int(t)]
            return ws, env
        for p, e in outs.items():
            ws, env = env_of(e); fn, _, _ = smt2.compile_term(e, ws); Y[p][T] = fn(env)
        for r in W:
            e = wr.get(r)
            if e is None: Q[r][T + 1] = Q[r][T]
            else:
                ws, env = env_of(e); fn, _, w = smt2.compile_term(e, ws); v = fn(env) & ((1 << W[r]) - 1)
                D[r][T] = v; Q[r][T + 1] = v
    return Q, D, Y

class Timeout(Exception): pass
def with_timeout(sec, fn, *a):
    def h(*_): raise Timeout()
    old = signal.signal(signal.SIGALRM, h); signal.alarm(sec)
    try: return fn(*a)
    finally: signal.alarm(0); signal.signal(signal.SIGALRM, old)

def recover(eqs, meta, origin=0, period=None):
    return invfold.recover(eqs, meta, origin, 1, period)

def report(F):
    r = {'period': F.P, 'origin': F.origin, 'errors': F.report['errors'], 'input_phases': getattr(F, 'input_phases', None)}
    if F.P:
        r.update(identify.analyze(F))
    return r

def check_run_graph(F, W, IN, OUT, step, ncyc, xs, q0, n_from=1, n_to=None):
    """Execute the recovered graph with the node expressions and compare every write with the simulated hardware."""
    Q, D, Y = simulate(W, IN, OUT, step, ncyc, xs, q0)
    P, T0 = F.P, F.win[0] + F.origin
    if n_to is None: n_to = (ncyc - T0) // P - 1
    def inputs(p, cyc): return xs[p][cyc]
    def init(w, cyc): return D[w].get(cyc, Q[w][cyc + 1] if cyc + 1 < len(Q[w]) else 0)
    val = invfold.run_graph(F, inputs, init, n_from, n_to)
    bad = []
    for (w, phi, n), v in val.items():
        cyc = T0 + n * P + phi
        if cyc in D[w] and D[w][cyc] != (v & ((1 << W[w]) - 1)): bad.append((w, phi, n, D[w][cyc], v))
    return bad, len(val)

def check_execute(F, W, IN, OUT, step, ncyc, xs, q0, first):
    """identify.execute (the linear fitted model) vs simulated output ports, from cycle `first` (state zero before it:
    the simulated hardware is started from q0 = 0 at cycle 0 and inputs are zero before `first`)."""
    Q, D, Y = simulate(W, IN, OUT, step, ncyc, xs, q0)
    xin = {p: [invfold.signed(v, IN[p]) for v in xs[p]] for p in IN}
    pred = identify.execute(F, xin, first, ncyc)
    bad = []
    for p in pred:
        for T, v in pred[p].items():
            if T in Y[p] and Y[p][T] != v: bad.append((p, T, Y[p][T], v))
    return bad, sum(len(v) for v in pred.values())
