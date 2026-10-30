"""B0 (= A9 on snapshot2): randomized differential test. Random folded linear designs (P = 2..5, 3-5 registers, holds via CE = 0 and via
D = Q, copies, integer and >>1 / >>2 floor coefficients, x read in slot 0 only), every origin. Compares
identify.execute (fitted linear model after copy collapse) and invfold.run_graph (raw expressions) with the
simulated hardware. Any mismatch without an error is a silent failure."""
import sys, os, random, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness2 import *

def rand_design(rng):
    P = rng.randint(2, 5); m = rng.randint(3, 5); regs = ['r%d' % i for i in range(m)]
    plan = {}
    for p in range(P):
        for r in regs:
            u = rng.random()
            if u < 0.35: continue                       # CE = 0
            if u < 0.5: plan[(p, r)] = ('copy', rng.choice(regs)); continue
            terms = [(rng.choice([-3, -2, -1, 1, 2, 3]), rng.choice(regs), rng.choice([0, 0, 1, 2]) if LOOKBACK else 0) for _ in range(rng.randint(1, 3))]
            if (p == 0 or LOOKBACK) and rng.random() < 0.7: terms.append((1, 'x', p))   # x read at a slot-0 cycle (T - p)
            plan[(p, r)] = ('lin', terms, rng.choice([0, 0, 1, 2]))
    if not any(p == 0 and k[0] == 'lin' and any(t[1] == 'x' for t in k[1]) for (p, r), k in plan.items()):
        plan[(0, regs[0])] = ('lin', [(1, 'x', 0), (1, regs[0], 0)], 0)
    for r in regs:   # every register written at least once with a non-copy, to avoid copy cycles
        if not any(k[0] == 'lin' for (p, rr), k in plan.items() if rr == r): plan[(rng.randrange(P), r)] = ('lin', [(1, regs[0], 0)], 1)
    def step(T):
        p = T % P; wr = {}
        for r in regs:
            k = plan.get((p, r))
            if k is None: continue
            if k[0] == 'copy': wr[r] = R(k[1], T); continue
            acc = None
            for cf, s, dl in k[1]:
                v = X('x', max(T - dl, 0)) if s == 'x' else R(s, max(T - dl, 0))
                t = '(bvmul %s %s)' % (c(cf, 18), '((_ sign_extend 2) %s)' % v)
                acc = t if acc is None else '(bvadd %s %s)' % (acc, t)
            wr[r] = '((_ extract %d %d) %s)' % (15 + k[2], k[2], acc)
        return wr, {'y': R(regs[0], T)}
    W = {r: 16 for r in regs}
    return P, W, step, plan

LOOKBACK = len(sys.argv) > 3
rng = random.Random(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
stats = collections.Counter(); examples = []
for it in range(int(sys.argv[2]) if len(sys.argv) > 2 else 60):
    P, W, step, plan = rand_design(rng)
    IN = {'x': 16}; OUT = {'y': 16}; N = 30 * P + 20
    eqs, meta = build(W, IN, OUT, step, N, range(10, N))
    xs = {'x': [rng.randrange(-200, 200) & 0xffff if t >= 4 else 0 for t in range(N)]}
    for o in range(P):
        try: F = with_timeout(20, recover, eqs, meta, o)
        except Timeout: stats['hang'] += 1; continue
        if F.P is None: stats['noperiod'] += 1; continue
        errs = F.report['errors']
        if errs: stats['errors'] += 1; continue
        b1, n1 = check_run_graph(F, W, IN, OUT, step, N, xs, {r: 0 for r in W})
        if F.report.get('nonlinear'): stats['nonlinear'] += 1; continue
        b2, n2 = check_execute(F, W, IN, OUT, step, N, xs, {r: 0 for r in W}, 4)
        if b1 or b2:
            stats['MISMATCH'] += 1
            if len(examples) < 3: examples.append((it, o, P, plan, b1[:2], b2[:2], F.P))
        else: stats['ok'] += 1
print(dict(stats))
for e in examples: print('EXAMPLE', e)
