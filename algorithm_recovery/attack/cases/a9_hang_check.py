"""A9b: confirm that the hangs of a9_random_diff.py (seed 11) are copy cycles: run invfold's steps up to the fits,
then look for a cycle in the copy relation."""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv = ['x', '11', '0']; LOOKBACK = False
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'a9_random_diff.py')).read().split("rng = random.Random(int")[0])
rng = random.Random(11); found = 0
for it in range(80):
    P, W, step, plan = rand_design(rng)
    IN = {'x': 16}; OUT = {'y': 16}; N = 30 * P + 20
    eqs, meta = build(W, IN, OUT, step, N, range(10, N))
    xs = {'x': [rng.randrange(-200, 200) & 0xffff if t >= 4 else 0 for t in range(N)]}
    for o in range(P):
        F = invfold.Folded(eqs, meta, o); r2 = random.Random(1)
        if F.period() is None: continue
        F.build(); alias = {}
        for k, d in F.nodes.items():
            f = F.fit(('sig',) + k, d, r2)
            if f and f['mode'] == 'exact' and f['const'] == 0 and len(f['coeffs']) == 1:
                (s, cf), = f['coeffs'].items()
                if cf == 1 and s[0] == 'sig' and F.W[s[1]] == f['width']: alias[k] = s[1:3]
        cyc = None
        for k in alias:
            seen, u = [], k
            while u in alias and u not in seen: seen.append(u); u = alias[u]
            if u in seen: cyc = seen[seen.index(u):]; break
        if cyc:
            found += 1
            if found <= 2: print('design', it, 'origin', o, 'P', F.P, 'copy cycle', cyc)
print('designs/origins with a copy cycle:', found)
