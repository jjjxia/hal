"""FFT64 (Xilinx, certified forms, P = 128): inverse folding with memories (X2) and frame classification (X1).
Exploratory, not preregistered. Inputs: NetNotary's forms (perfdiag/fft/forms_main.txt, cycles 260..387), the .hal
(widths, pin groups, RAM ports), and the simulation trace sim1 for control only: effective write enables (CE, R, S),
RAM write/read enables and addresses (data-independent; certified untainted by NetNotary). The data inputs DR/DI
are written into block RAM U_BUF1; the outputs DOR/DOI are read data of block RAM Ubuf3 (observed as memory reads).
Fits run in parallel (fork). Usage: fft_run.py [workers]"""
import sys, os, json, re, time, random, collections, multiprocessing as mp
sys.path.insert(0, '../invfold'); sys.path.insert(0, '.')
import invfold, halforms, frame, fft_memcheck as MC
sys.setrecursionlimit(1000000)
ALGO = MC.ALGO; H = MC.H
NCYC = 700; T0, P = 260, 128

def control_trace(names, offset, sim=1):
    sd = os.path.join(ALGO, 'fft', 'sim%d' % sim); vcd = [os.path.join(sd, x, 'waveform.vcd') for x in os.listdir(sd) if x.startswith('hal_sim')][0]
    vname = lambda nm: re.sub(r'_IBUF(\(\d+\))?$', r'\1', nm)
    return halforms.net_trace(vcd, sorted({vname(n) for n in names}), NCYC, offset), vname

def build_meta(eqs):
    N = json.load(open(H))['netlist']; nets = {n['id']: n for n in N['nets']}
    W, ports, alias, cenet = halforms.netlist_meta(H, ['DR_ordered', 'DI_ordered'])
    rnet, snet = {}, {}
    for m in N['modules']:
        if m['name'].startswith('DANA_module_'):
            for g in m['pin_groups']:
                if g['name'] == 'i_R': rnet[m['name']] = nets[g['pins'][0]['net_id']]['name']
                if g['name'] == 'i_S': snet[m['name']] = nets[g['pins'][0]['net_id']]['name']
    const = {"'1'": 1, "<const1>": 1, "'0'": 0, "<const0>": 0}
    names = [n for d in (cenet, rnet, snet) for n in d.values() if n not in const]
    nonconst = collections.defaultdict(set)
    for e in eqs:
        if ' - i_D' in e['target'] and not re.fullmatch(r'0b[01]+', e['text']): nonconst[e['target'].split(' - ')[0]].add(e['T'])
    # control sampled just before the clock edge (4999 ps), the convention validated on the block RAMs (fft_memcheck);
    # a D expression that is non-constant at a disabled cycle is normal here (the datapath runs, CE gates the write),
    # so the violation count is reported, not used to choose the offset
    best = None
    for off in (9999,):   # forms cycle T = the interval after clock edge 5000 + 10000 T (NetNotary cert_gen convention)
        tr, vname = control_trace(names, off)
        def v(nm, t, dflt):
            if nm is None: return dflt
            if nm in const: return const[nm]
            x = tr[vname(nm)][t]; return 1 if x == '1' else 0
        we = {w: [int(v(cenet.get(w), t, 1) or v(rnet.get(w), t, 0) or v(snet.get(w), t, 0)) for t in range(NCYC)] for w in W}
        rs = {w: [(v(rnet.get(w), t, 0), v(snet.get(w), t, 0)) for t in range(NCYC)] for w in W}
        viol = sum(1 for w, ts in nonconst.items() for t in ts if not we[w][t])
        print('enable offset', off, 'violations', viol, flush=True)
        if best is None or viol < best[0]: best = (viol, off, we, rs)
    viol, off, we, rs = best
    # synchronous reset / set write the reset / set value, not D
    for e in eqs:
        w = e['target'].split(' - ')[0]
        if ' - i_D' in e['target'] and w in rs:
            r, st = rs[w][e['T']]
            if r: e['text'] = '0b' + '0' * W[w]
            elif st: e['text'] = '0b' + '1' * W[w]
    # memories
    R = MC.ram_ports(); qbit = {}
    for m in N['modules']:
        if m['name'].startswith('DANA_module_'):
            for i, p in enumerate(halforms.qgroup(m)['pins']): qbit[p['net_id']] = (m['name'], i)
    portbit = {}
    for m in N['modules']:
        if m['name'] == 'top_module':
            for g in m['pin_groups']:
                if g['name'] in ('DR_ordered', 'DI_ordered'):
                    for i, p in enumerate(g['pins']): portbit[p['net_id']] = (g['name'], i)
    mnames = set()
    for r in R.values():
        for k in ('ra', 'wa', 'we', 'di'): mnames |= {n['name'] for n in r[k].values()}
        for k in ('ena', 'enb'):
            if r[k]: mnames.add(r[k]['name'])
    mtr, vname = control_trace(mnames, 9999)   # same alignment as the forms (the RAM model itself is validated in fft_memcheck)
    def mv(n, t):
        if n is None: return 1
        if n['name'] in const: return const[n['name']]
        x = mtr[vname(n['name'])][t]; return 1 if x == '1' else 0
    def word(bits, t): return sum(mv(n, t) << i for i, n in bits.items())
    memories = {}
    modules = {m['name']: m for m in N['modules']}
    for name, r in R.items():
        keys = sorted(r['di']); pos = {k: i for i, k in enumerate(keys)}
        src = []
        for k in keys:
            nid = r['di'][k]['id']
            if nid in qbit: src.append(['word', qbit[nid][0], qbit[nid][1]])
            elif nid in portbit: src.append(['port', portbit[nid][0], portbit[nid][1]])
            elif r['di'][k]['name'] in const: src.append(['const', '', const[r['di'][k]['name']]])
            else: src.append(['const', '', 0]); print('WARN data bit %s/%d from %s (not a word/port): treated as 0' % (name, k, r['di'][k]['name']))
        writes = {t: [word(r['wa'], t), src] for t in range(NCYC) if mv(r['enb'], t) and word(r['we'], t)}
        raddr = {t: word(r['ra'], t) for t in range(NCYC) if mv(r['ena'], t)}
        dpos = {n['id']: pos[k] for k, n in r['do'].items()}
        outs = {}
        mod = modules.get('module_' + name)
        if mod:
            for g in mod['pin_groups']:
                if g['name'].startswith('out_DATA'):
                    outs['module_%s_%s' % (name, g['name'])] = [dpos[p['net_id']] for p in g['pins']]
        memories[name] = {'latency': 1 + r['doreg'], 'mode': 'read_first', 'writes': writes, 'raddr': raddr, 'outputs': outs}
    # SRL16E shift registers: depth = address + 1 (constant address pins), shift when CE; data bits are RAM output bits
    L = json.load(open(MC.HGL)); pins = {c['name']: [p['name'] for g in c.get('pin_groups', []) for p in g['pins']] for c in L['cells']}
    gates = {g['id']: g for g in N['gates']}; conn = collections.defaultdict(dict)
    for n in N['nets']:
        for e in n.get('srcs', []) + n.get('dsts', []):
            g = gates[e['gate_id']]
            if g['type'].startswith('SRL'): conn[g['id']][pins[g['type']][e['pin_id'] - 1]] = n
    memout_bit = {}
    for name, r in R.items():   # every DO pin of the primitive (also bits outside the module pin groups)
        keys = sorted(r['di']); pos = {k: i for i, k in enumerate(keys)}; base = 'RAMDO_' + name.split('/')[0]
        memories[name]['outputs'][base] = [pos[k] for k in sorted(r['do'])]
        for i, k in enumerate(sorted(r['do'])):
            memout_bit[r['do'][k]['id']] = (base, i)
            memories[name]['outputs']['net_%d' % r['do'][k]['id']] = [pos[k]]   # a DO net read directly in the forms
    for M, mm in memories.items():
        for base, bits in mm['outputs'].items():
            mod = modules.get('module_' + M)
            if mod and base.startswith('module_'):
                g = [g for g in mod['pin_groups'] if 'module_%s_%s' % (M, g['name']) == base][0]
                for i, p in enumerate(g['pins']): memout_bit[p['net_id']] = (base, i)
    srl_ce = sorted({c['CE']['name'] for c in conn.values()})
    str_, _ = control_trace([n for n in srl_ce if n not in const], 9999)
    for gid, c in conn.items():
        addr = sum(const[c['A%d' % k]['name']] << k for k in range(4))
        d = c['D']['id']
        if d in qbit: src = ['word', qbit[d][0], qbit[d][1]]
        elif d in memout_bit: src = ['memout', memout_bit[d][0], memout_bit[d][1]]
        else: src = ['const', '', 0]; print('WARN SRL data from', c['D']['name'])
        cen = c['CE']['name']
        shifts = list(range(NCYC)) if const.get(cen) == 1 else [t for t in range(NCYC) if str_[vname(cen)][t] == '1']
        memories['srl_%d' % gid] = {'kind': 'srl', 'depth': addr + 1, 'shifts': shifts, 'data': [src], 'outputs': {'net_%d' % c['Q']['id']: [0]}}
    # ---- DSP48E1 multipliers (twiddle products): P(T) = A(T-3) * B(T-2) for AREG = 2, BREG = 1, MREG = 0, PREG = 1,
    # OPMODE selecting X = Y = M, Z = 0 (checked). B is data-independent (the twiddle ROM; certified untainted) and is
    # read from the trace; A is routing logic from words resolved under known controls (trace_bit). Each DSP becomes a
    # pseudo-word 'DSP_<name>' whose equation at cycle T is A(T-2) * B(T-1), and its P output nets are aliases of it.
    unt = set(json.load(open(os.path.join(ALGO, 'cert/fft_untainted.json'))))
    fn_lib = {}
    for f in ('im_hal_i4.hgl', 'aoixm_hal_i4.hgl'):
        for c in json.load(open('/home/jiahao/hal/plugins/gate_libraries/definitions/helper_libs/' + f))['cells']:
            for g in c['pin_groups']:
                for pn in g['pins']:
                    if pn.get('direction') == 'output' and pn.get('function'): fn_lib[(c['name'], pn['name'])] = pn['function']
    gconn = collections.defaultdict(dict)
    for n in N['nets']:
        for e in n.get('dsts', []):
            g = gates[e['gate_id']]; gconn[g['id']][pins[g['type']][e['pin_id'] - 1]] = n
    srl_q = {int(b[4:]): b for M, mm in memories.items() if mm.get('kind') == 'srl' for b in mm['outputs']}
    unt_names = sorted({n['name'] for n in N['nets'] if n['name'] in unt})
    utr, _ = control_trace(unt_names, 9999)
    # protocol constants of the certificate (perfdiag/fft/fft_cfg_part*.json: RST = START = 0, ED = 1, SHIFT = 0000)
    proto = {'RST_IBUF': 0, 'START_IBUF': 0, 'ED_IBUF': 1, **{'SHIFT_IBUF(%d)' % k: 0 for k in range(4)}}
    def known(n, t):
        if n['name'] in const: return const[n['name']]
        if n['name'] in proto: return proto[n['name']]
        if n['name'] in unt:
            x = utr[vname(n['name'])][t]; return 1 if x == '1' else 0 if x == '0' else None
        return None
    def trace_bit(n, t, depth=0):
        nid = n['id']
        if nid in qbit: return ['word', qbit[nid][0], qbit[nid][1]]
        if nid in memout_bit: return ['memout', memout_bit[nid][0], memout_bit[nid][1]]
        if nid in srl_q: return ['memout', srl_q[nid], 0]
        k = known(n, t)
        if k is not None: return ['const', '', k]
        if depth > 40 or not n.get('srcs'): return None
        g = gates[n['srcs'][0]['gate_id']]; fstr = fn_lib.get((g['type'], 'O'))
        if fstr is None: return None
        ins = gconn[g['id']]; unknown = [pn for pn, m in ins.items() if known(m, t) is None]
        if len(unknown) > 4: return None
        code = compile(re.sub(r'\b([A-Z][A-Z0-9]*)\b', r"v['\1']", fstr.replace('!', 'not ')).replace('&', ' and ').replace('|', ' or '), 'g', 'eval')
        base = {pn: known(m, t) for pn, m in ins.items() if pn not in unknown}
        outs = []
        for bits in range(1 << len(unknown)):
            v = dict(base); v.update({pn: (bits >> i) & 1 for i, pn in enumerate(unknown)}); outs.append(int(bool(eval(code, {}, {'v': v}))))
        if len(set(outs)) == 1: return ['const', '', outs[0]]
        for i, pn in enumerate(unknown):
            if all(outs[b] == (b >> i) & 1 for b in range(len(outs))): return trace_bit(ins[pn], t, depth + 1)
        return None
    dsp_conn = collections.defaultdict(dict)
    for n in N['nets']:
        for e in n.get('srcs', []) + n.get('dsts', []):
            g = gates[e['gate_id']]
            if g['type'] == 'DSP48E1': dsp_conn[g['id']][pins[g['type']][e['pin_id'] - 1]] = n
    dsp_words, dsp_eqs, dsp_alias, dsp_fail = {}, [], {}, 0
    for gid, c in dsp_conn.items():
        g = gates[gid]; gen = {d[1]: d[3] for d in g.get('data', []) if d[0] == 'generic'}
        assert (gen.get('AREG'), gen.get('BREG'), gen.get('MREG'), gen.get('PREG')) == ('2', '1', '0', '1'), gen
        opm = sum(const[c['OPMODE(%d)' % k]['name']] << k for k in range(7)); assert opm == 0b0000101, bin(opm)   # X = M, Y = M, Z = 0
        # two pseudo-words per DSP, following its pipeline: DSPA (the A register, written with the routed A input; this
        # is where a shift such as A = floor(v / 2) happens) and DSP (the P register = A register x constant B)
        wname = 'DSP_' + g['name'].split('/')[-1]; aname = 'DSPA_' + g['name'].split('/')[-1]
        dsp_words[wname] = 48; dsp_words[aname] = 30
        for T in range(T0, T0 + P):
            abits = []
            for k in range(29, -1, -1):
                src = trace_bit(c['A(%d)' % k], T)
                if src is None: dsp_fail += 1; src = ['const', '', 0]
                if src[0] == 'word': abits.append('Slice(%s_o_Q_ordered_%d, %d, %d)' % (src[1], T, src[2], src[2]))
                elif src[0] == 'memout': abits.append('Slice(%s_%d, %d, %d)' % (src[1], T, src[2], src[2]))
                else: abits.append('0b%d' % src[2])
            bval = [known(c['B(%d)' % k], T - 1) for k in range(18)]
            if None in bval: dsp_fail += 1; bval = [0 if b is None else b for b in bval]
            btxt = '0b' + ''.join(str(b) for b in reversed(bval))
            dsp_eqs.append({'T': T, 'target': '%s - i_D' % aname, 'text': '(%s)' % ' ++ '.join(abits)})
            # P(T + 1) = A2(T) * B2(T) = A(T - 2) * B(T - 1); the A register read at T - 1 holds A(T - 2)
            dsp_eqs.append({'T': T, 'target': '%s - i_D' % wname, 'text': '(Sext(%s_o_Q_%d, 48) * Sext(%s, 48))' % (aname, T - 1, btxt)})
        for k in range(48):
            if 'P(%d)' % k in c: dsp_alias['net_%d' % c['P(%d)' % k]['id']] = [(wname, k)]
    print('DSPs', len(dsp_words), 'untraced A/B bits', dsp_fail, flush=True)
    # outputs DOR / DOI = read data of Ubuf3
    top = modules['top_module']; out_eqs = []
    for g in top['pin_groups']:
        if g['name'] in ('DOR_ordered', 'DOI_ordered'):
            r = R['Ubuf3/URAM/ram_reg']; keys = sorted(r['di']); pos = {k: i for i, k in enumerate(keys)}
            dpos = {n['id']: pos[k] for k, n in r['do'].items()}
            base = 'FFTOUT_' + g['name'][:3]
            memories['Ubuf3/URAM/ram_reg']['outputs'][base] = [dpos[p['net_id']] for p in g['pins']]
            out_eqs += [{'T': T, 'target': 'top_module - %s' % g['name'][:3], 'text': '%s_%d' % (base, T)} for T in range(T0, T0 + P)]
    W.update(dsp_words)
    for wn in dsp_words: we[wn] = [1] * NCYC   # CEP/CEA/CEB are driven by ED (protocol: always 1)
    meta = {'window': list(range(T0, T0 + P)), 'widths': W, 'ports': {'in': ports, 'out': {'DOR': 19, 'DOI': 19}},
            'write_enable': we, 'alias': dsp_alias, 'dq_perm': halforms.dq_perm(H), 'memories': memories, 'periodic_extension': True}
    return meta, out_eqs + dsp_eqs, {'enable_offset': off, 'enable_violations': viol, 'dsp_untraced_bits': dsp_fail}

G = {}
def fit_chunk(keys):
    F = G['F']; out = {}; F.report['incomplete'] = []
    for key in keys:
        d = F.nodes[key[1:]] if key[0] == 'sig' else F.outputs[key[1:]]
        try: out[key] = F.fit(key, d, random.Random(hash(key) & 0xffffffff))
        except Exception as e: out[key] = None; print('fit error', key, repr(e)[:200], flush=True)
    return out, list(F.report['incomplete'])

def main(workers):
    t0 = time.time()
    # NetNotary's FFT certificate uses four forms files: the main run and re-recovered words (suspects, 108/109, 125);
    # later files replace earlier equations of the same (cycle, target)
    merged = {}
    for f in ('perfdiag/fft/forms_main.txt', 'perfdiag/fft/forms_sus.txt', 'cert/fft_108_S.txt', 'cert/fft_125_S.txt'):
        for e in halforms.load_forms(os.path.join(ALGO, f)):
            if e['target'].startswith('top_module'): continue   # status outputs (RDY, OVF): not data
            merged[(e['T'], e['target'].split(' - ')[0])] = e
    eqs = list(merged.values()); print('forms loaded', len(eqs), '%.0fs' % (time.time() - t0), flush=True)
    meta, out_eqs, info = build_meta(eqs); print('meta', info, 'memories', {m: (len(v['writes']), len(v['raddr']), list(v['outputs'])) for m, v in meta['memories'].items() if v.get('kind') != 'srl'}, 'srl', sum(1 for v in meta['memories'].values() if v.get('kind') == 'srl'), flush=True)
    F = invfold.Folded(eqs + out_eqs, meta, 0); F.period(P); F.build()
    print('built: nodes', len(F.nodes), 'outputs', len(F.outputs), 'errors', len(F.report['errors']), F.report['errors'][:5], '%.0fs' % (time.time() - t0), flush=True)
    keys = [('sig',) + k for k in F.nodes] + [('out',) + k for k in F.outputs]
    G['F'] = F
    chunks = [keys[i::workers] for i in range(workers)]
    with mp.get_context('fork').Pool(workers) as pool:
        F.fits = {}
        inc = []
        for r, i in pool.imap_unordered(fit_chunk, chunks): F.fits.update(r); inc += i
    F.report['incomplete'] = inc
    F.report['nonlinear'] = [str(k) for k, f in F.fits.items() if f is None]
    print('fitted: nonlinear', len(F.report['nonlinear']), 'of', len(F.fits), '%.0fs' % (time.time() - t0), flush=True)
    invfold.collapse_copies(F); invfold.observe(F)
    print('after collapse: nodes', sum(1 for k in F.fits if k[0] == 'sig'), 'observable', len(F.observable), 'notes', F.report['notes'][:3], flush=True)
    c = frame.classify_frame(F, [(('DR_ordered', 'DI_ordered'), ('DOR', 'DOI'))])
    by_word = collections.Counter(eval(k)[1] for k in F.report['nonlinear'])
    inc_obs = [k for k in F.report['incomplete'] if eval(k) in F.observable]
    res = {'info': info, 'errors': F.report['errors'][:20], 'n_errors': len(F.report['errors']), 'nonlinear': F.report['nonlinear'][:40],
           'nonlinear_by_word': dict(by_word), 'incomplete_equations': len(F.report['incomplete']), 'incomplete_observable': len(inc_obs),
           'nonlinear_observable': sum(1 for k in F.report['nonlinear'] if eval(k) in F.observable),
           'n_nonlinear': len(F.report['nonlinear']), 'input_phases': F.input_phases, 'classification': c, 'seconds': time.time() - t0}
    json.dump(res, open('out/fft_run.json', 'w'), indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != 'nonlinear'}, indent=1, default=str)[:4000])

if __name__ == '__main__': main(int(sys.argv[1]) if len(sys.argv) > 1 else 12)
