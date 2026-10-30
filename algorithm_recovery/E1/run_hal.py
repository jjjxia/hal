"""E1 frontend (PREREG.md, variant A): synthesized netlist -> per-cycle word equations via HAL guided symbolic execution.
Steps: load + artifact preprocessing; words from flip-flop Q-net names (register ground truth, as in the authors'
white-box run) with anonymized, shuffled ids; cycle simulation of the HAL netlist with HAL's own gate functions;
known (control) nets = nets outside the sequential fan-out of the data port x, plus flip-flop control pins; cutpoints =
data words whose D bits are driven by combinational gates; SSE of every cutpoint D and every output port per cycle.
Writes OUT/equations.txt (HAL print format), OUT/meta.json (recognizer input: widths, ports, per-cycle write enables)
and OUT/private_names.json (anonymization map, scoring only).
Usage: run_hal.py NETLIST.v STIM.txt OUT [--b]  (STIM: one 'x' value per cycle; rst high for the first 4 cycles)"""
import sys, os, re, json, random, collections, time
sys.path.append('/home/jiahao/hal/build/lib'); os.environ['HAL_BASE_PATH'] = '/home/jiahao/hal/build/'
import hal_py
hal_py.plugin_manager.load_all_plugins()
from hal_plugins import netlist_preprocessing, xilinx_toolbox, sequential_symbolic_execution as sse

GL = '/home/jiahao/hal/plugins/gate_libraries/definitions/XILINX_UNISIM_hal.hgl'
DATA_PORTS, CTRL_PORTS = ['x'], ['clk', 'rst']   # port metadata (the only design knowledge besides the netlist)
WARMUP, WINDOW = 40, int(os.environ.get('WINDOW', 60))

def preprocess(nl):
    xilinx_toolbox.split_luts(nl)
    netlist_preprocessing.reconstruct_top_module_pin_groups(nl)
    while True:
        n = 0
        n += netlist_preprocessing.remove_unconnected_gates(nl)
        n += netlist_preprocessing.remove_unconnected_nets(nl)
        n += netlist_preprocessing.propagate_constants(nl)
        n += netlist_preprocessing.simplify_lut_inits(nl)
        n += netlist_preprocessing.remove_unused_lut_inputs(nl)
        n += netlist_preprocessing.remove_buffers(nl)
        n += netlist_preprocessing.remove_redundant_gates(nl)
        n += netlist_preprocessing.remove_redundant_loops(nl)
        n += netlist_preprocessing.remove_unconnected_looped(nl)
        if n == 0: break

def port_bits(nl, direction):
    nets = nl.get_global_input_nets() if direction == 'in' else nl.get_global_output_nets()
    out = collections.defaultdict(dict)
    for n in nets:
        m = re.fullmatch(r'(.*)\((\d+)\)', n.name)
        b, i = (m.group(1), int(m.group(2))) if m else (n.name, 0)
        out[b][i] = n
    return {b: [d[i] for i in sorted(d)] for b, d in out.items()}

def compile_bf(s):
    """HAL Boolean function string -> Python expression over env v (fully parenthesized input assumed)."""
    toks = re.findall(r'[A-Za-z_][A-Za-z_0-9]*(?:\(\d+\))?|0b[01]+|[()!&|^]', s)
    out = []
    for t in toks:
        if t == '!': out.append('1^')
        elif t in '()&|^': out.append(t)
        elif t.startswith('0b'): out.append(str(int(t[2:], 2)))
        else: out.append('v[%r]' % t)
    return compile(' '.join(out), s[:40], 'eval')

class Sim:
    """Cycle simulation: values[c][net_id] = value during cycle c (before clock edge c); FDRE: Q' = R ? 0 : CE ? D : Q
    (next_state of FDRE in XILINX_UNISIM_hal.hgl)."""
    def __init__(self, nl):
        self.nl = nl; self.comb, self.ffs = [], []
        for g in nl.gates:
            ins = {ep.pin.name: ep.net.id for ep in g.get_fan_in_endpoints()}
            outs = {ep.pin.name: ep.net.id for ep in g.get_fan_out_endpoints()}
            if g.type.has_property(hal_py.GateTypeProperty.ff):
                assert g.type.name == 'FDRE', g.type.name
                self.ffs.append((ins, outs['Q']))
            else:
                fs = g.get_boolean_functions()
                self.comb.append((g, ins, outs, {p: compile_bf(str(f)) for p, f in fs.items()}))
        # topological order of combinational gates (internal pin references such as CARRY4 CO(k) are resolved per gate)
        drv = {}
        for i, (g, ins, outs, fs) in enumerate(self.comb):
            for n in outs.values(): drv[n] = i
        order, state = [], {}
        def visit(i):
            if state.get(i) == 2: return
            assert state.get(i) != 1, 'combinational loop'
            state[i] = 1
            for n in self.comb[i][1].values():
                if n in drv: visit(drv[n])
            state[i] = 2; order.append(i)
        sys.setrecursionlimit(100000)
        for i in range(len(self.comb)): visit(i)
        self.order = order
    def run(self, inputs):
        """inputs: list over cycles of {net_id: value} for global inputs. Returns per-cycle net values."""
        q = {qn: 0 for _, qn in self.ffs}; vals = []
        consts = {n.id: (1 if n.is_vcc_net() else 0) for n in self.nl.nets if n.is_vcc_net() or n.is_gnd_net()}
        for inp in inputs:
            v = dict(consts); v.update(inp); v.update(q)
            for i in self.order:
                g, ins, outs, fs = self.comb[i]
                env = {p: v.get(n, 0) for p, n in ins.items()}
                pending = dict(fs)
                while pending:   # gate-internal references to its own output pins (CARRY4)
                    for p in list(pending):
                        try: env[p] = eval(pending[p], {}, {'v': env}); del pending[p]
                        except KeyError: pass
                for p, n in outs.items(): v[n] = env[p]
            vals.append(v)
            for ins, qn in self.ffs:
                r, ce, d = v.get(ins.get('R'), 0), v.get(ins.get('CE'), 1), v.get(ins['D'], 0)
                q[qn] = 0 if r else (d if ce else q[qn])
        return vals

def main(netlist, stim, out, variant):
    os.makedirs(out, exist_ok=True)
    nl = hal_py.NetlistFactory.load_netlist(netlist, GL)
    for m in nl.get_modules():
        if not m.is_top_module(): nl.delete_module(m)
    # port bit -> net ids before preprocessing (buffer removal renames the port nets to the buffer outputs)
    alias = {}
    for b, nets in port_bits(nl, 'in').items():
        for i, n in enumerate(nets):
            work = [n]
            while work:
                m = work.pop(); alias[m.id] = (b, i)
                work += [on for ep in m.get_destinations() if ep.gate.type.name in ('IBUF', 'BUFG') for on in ep.gate.fan_out_nets]
    # gate id -> (RTL register, bit): from the Q connection of each flip-flop instance in the synthesized Verilog
    # (HAL merges aliased nets and renames them, so the HAL net names are not reliable)
    qconn = dict(re.findall(r'FDRE #\(\s*\.INIT\([^)]*\)\s*\)\s*(\S+)\s*\((?:[^;]*?)\.Q\(([^)]*)\)', open(netlist).read(), re.S))
    ff_names = {}
    for g in nl.get_gates(lambda g: g.type.has_property(hal_py.GateTypeProperty.ff)):
        m = re.fullmatch(r'(\S+?)(?:\[(\d+)\])?', qconn[g.name])
        ff_names[g.id] = (m.group(1), int(m.group(2) or 0))
    preprocess(nl)
    assert all(nl.get_gate_by_id(i) for i in ff_names), 'preprocessing removed a flip-flop'
    ins = collections.defaultdict(dict)
    for n in nl.get_global_input_nets():
        b, i = alias[n.id]; ins[b][i] = n
    ins = {b: [d[i] for i in sorted(d)] for b, d in ins.items()}
    outs = port_bits(nl, 'out')
    # ---- words: flip-flops grouped by the RTL name of their Q net (variant A; names used here only) ----
    groups = collections.defaultdict(dict)
    for gid, (b, i) in ff_names.items(): groups[b][i] = nl.get_gate_by_id(gid)
    names = sorted(groups); rng = random.Random(20261029); ids = list(range(len(names))); rng.shuffle(ids)
    anon = {nm: 'w%d' % k for nm, k in zip(names, ids)}
    words = {}
    for nm in names:
        gs = [groups[nm][i] for i in sorted(groups[nm])]
        mod = nl.create_module(anon[nm], nl.get_top_module(), gs)
        qp = [mod.get_pin_by_net(g.get_fan_out_net('Q')) for g in gs]
        dp = [mod.get_pin_by_net(g.get_fan_in_net('D')) for g in gs]
        if all(qp): mod.create_pin_group('o_Q', qp, hal_py.PinDirection.output, hal_py.PinType.state, True, 0, True, True)
        if all(dp): mod.create_pin_group('i_D', dp, hal_py.PinDirection.input, hal_py.PinType.data, True, 0, True, True)
        words[anon[nm]] = {'mod': mod, 'gates': gs}
    # ---- simulation ----
    xs = [int(l) for l in open(stim)]
    ncyc = len(xs); inputs = []
    for c in range(ncyc):
        v = {}
        for i, n in enumerate(ins['x']): v[n.id] = (xs[c] >> i) & 1
        v[ins['rst'][0].id] = 1 if c < 4 else 0
        v[ins['clk'][0].id] = 1   # SSE convention: the clock is 1 at every sampled time index
        inputs.append(v)
    sim = Sim(nl); vals = sim.run(inputs)
    ybits = outs['y']
    ys = [sum(vals[c][n.id] << i for i, n in enumerate(ybits)) for c in range(ncyc)]
    json.dump({'x': xs, 'y': ys}, open(os.path.join(out, 'sim_io.json'), 'w'))
    # ---- known nets: complement of the sequential fan-out of the data ports ----
    tainted = set(n.id for p in DATA_PORTS for n in ins[p]); work = list(tainted)
    while work:
        n = nl.get_net_by_id(work.pop())
        for ep in n.get_destinations():
            g = ep.gate
            if g.type.has_property(hal_py.GateTypeProperty.ff) and ep.pin.type != hal_py.PinType.data: continue
            for on in g.fan_out_nets:
                if on.id not in tainted: tainted.add(on.id); work.append(on.id)
    known = [n.id for n in nl.nets if n.id not in tainted]
    for g in nl.get_gates(lambda g: g.type.has_property(hal_py.GateTypeProperty.ff)):
        for ep in g.get_fan_in_endpoints():
            if ep.pin.type != hal_py.PinType.data: known.append(ep.net.id)
    known = sorted(set(known))
    # clock tree nets are 1 at every sampled index (as in the authors' scripts)
    clk_nets = {g.get_fan_in_net('C').id for g in nl.get_gates(lambda g: g.type.has_property(hal_py.GateTypeProperty.ff))}
    ctrl_values = []
    for c in range(ncyc):
        d = {nid: (1 if nid in clk_nets else vals[c].get(nid, 0)) for nid in known}
        if c == 0:
            for _, qn in sim.ffs: d[qn] = 0
        ctrl_values.append(d)
    # ---- cutpoints ----
    data_words = {w for w, d in words.items() if any(g.get_fan_out_net('Q').id in tainted for g in d['gates'])}
    cut = sorted(w for w in data_words
                 if any(g.get_fan_in_net('D').get_sources() and g.get_fan_in_net('D').get_sources()[0].gate.type.has_property(hal_py.GateTypeProperty.combinational)
                        for g in words[w]['gates']))
    if os.environ.get('CUT_ALL'): cut = sorted(data_words)   # robustness run (post hoc): every data word is a cutpoint
    byte_map = [True] * (max(g.id for g in nl.gates) + 1)
    for w in cut:
        for g in words[w]['gates']: byte_map[g.id] = False
    # effective writes of cutpoint words per cycle. FDRE: Q' = R ? 0 : (CE ? D : Q), so a write happens when CE or R is
    # 1, and with R = 1 the written value is the reset value 0, not D (attack a3: a counter-cleared accumulator is
    # mapped to CE = 1, R = (phi == 0); reading CE alone turns an FIR into an integrator)
    def pinval(n, c, default):
        if n is None: return default
        net = nl.get_net_by_id(n)
        if net.is_vcc_net(): return 1
        if net.is_gnd_net(): return 0
        return vals[c].get(n, 0)
    ce, srst = {}, {}
    for w in cut:
        cen = {g.get_fan_in_net('CE').id if g.get_fan_in_net('CE') else None for g in words[w]['gates']}
        rn = {g.get_fan_in_net('R').id if g.get_fan_in_net('R') else None for g in words[w]['gates']}
        pe, pr = [], []
        for c in range(ncyc):
            e = {pinval(n, c, 1) for n in cen}; r = {pinval(n, c, 0) for n in rn}
            assert len(e) == 1 and len(r) == 1, 'word with mixed enables or resets'
            e, r = e.pop(), r.pop(); pe.append(int(e or r)); pr.append(r)
        ce[w], srst[w] = pe, pr
    print('CUT', cut, 'DATA', sorted(data_words)); json.dump({'anon': anon}, open(os.path.join(out, 'private_names.json'), 'w'))
    if os.environ.get('NOSSE'): return
    # ---- guided symbolic execution ----
    top = nl.get_top_module()
    for p, nets in list(ins.items()) + list(outs.items()):   # port pin groups (buffer removal may have split them)
        if p in CTRL_PORTS: continue
        top.create_pin_group(p, [top.get_pin_by_net(n) for n in nets], top.get_pin_by_net(nets[0]).direction, hal_py.PinType.data, True, 0, True, True)
    targets = [(words[w]['mod'], words[w]['mod'].get_pin_group_by_name('i_D')) for w in cut]
    targets += [(top, top.get_pin_group_by_name(p)) for p in sorted(outs)]
    cycles = list(range(WARMUP, WARMUP + WINDOW))
    wl = [t for _ in cycles for t in targets]; ix = [c for c in cycles for _ in targets]
    t0 = time.time()
    res = sse.get_pg_word_values_at_z3_smt2(wl, ix, byte_map, ctrl_values, {}, True)
    assert res is not None, 'SSE failed'
    eqs = [{'T': c, 'target': '%s - %s' % (m.name, pg.name), 'smt2': e} for (m, pg), c, e in zip(wl, ix, res)]
    for e in eqs:   # cycles with the synchronous reset active write the reset value, not the D expression
        w = e['target'].split(' - ')[0]
        if w in srst and srst[w][e['T']]: e['smt2'] = '(_ bv0 %d)' % len(words[w]['gates'])
    # an output port whose bits are exactly the Q bits of a cutpoint word (in order) is printed under the top module's
    # name; rename it to the word so that reads of it resolve (structural fact, no names involved)
    for p, nets in outs.items():
        for w in cut:
            if [g.get_fan_out_net('Q').id for g in words[w]['gates']] == [n.id for n in nets]:
                rx = re.compile(r'\btop_module_%s_(\d+)\b' % re.escape(p))
                for e in eqs: e['smt2'] = rx.sub(lambda m: '%s_o_Q_%s' % (w, m.group(1)), e['smt2'])
    json.dump(eqs, open(os.path.join(out, 'equations.json'), 'w'))
    print('SSE %.1fs, %d equations, %d chars' % (time.time() - t0, len(eqs), sum(len(e['smt2']) for e in eqs)))
    meta = {'window': cycles, 'cutpoints': cut,
            'widths': {w: len(d['gates']) for w, d in words.items() if w in data_words},
            'ports': {'in': {p: len(v) for p, v in ins.items() if p in DATA_PORTS}, 'out': {p: len(v) for p, v in outs.items()}},
            'write_enable': ce, 'ncyc': ncyc}
    json.dump(meta, open(os.path.join(out, 'meta.json'), 'w'))
    json.dump({'anon': anon, 'cut_rtl': [nm for nm in names if anon[nm] in cut]}, open(os.path.join(out, 'private_names.json'), 'w'))
    print('words', len(words), 'data words', len(data_words), 'cutpoints', cut, 'known nets', len(known), 'tainted', len(tainted))

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3], 'B' if '--b' in sys.argv else 'A')
