"""Frame-level polyphase matrix H(z) from a recovered graph (z = one frame), and the pseudo-circulant test of X1."""
import collections
from harness import *
RF, zk = identify.RF, identify.zk

def poly(p):
    t = ['%s%s' % (c, '' if i == 0 else 'z^-%d' % i) for i, c in enumerate(p) if c != 0]
    return ' + '.join(t) or '0'
def pretty(r): return poly(r.n) if r.d == [1] else '(%s)/(%s)' % (poly(r.n), poly(r.d))

def frameH(F, outs):
    """outs: list of node keys (word, phi) taken as output phases, in slot order. Returns (H rows=outs, cols=input
    phases in slot order, input list)."""
    fits = {k: f for k, f in F.fits.items() if f is not None and k[0] == 'sig'}
    nodes = sorted(k[1:] for k in fits)
    ins = sorted({s[:3] for f in fits.values() for s in f['coeffs'] if s[0] == 'in'}, key=lambda s: s[2])
    eqsys = {}
    for k, f in fits.items():
        e = collections.defaultdict(lambda: RF([0]))
        for s, cf in f['coeffs'].items():
            key = s[1:3] if s[0] == 'sig' else s[:3]
            e[key] = e[key] + zk(s[3], cf)
        eqsys[k[1:]] = dict(e)
    H = identify.solve(nodes, eqsys, ins)
    return [[H[o][i] for i in ins] for o in outs], ins

def eq(a, b): return (a - b).zero()
ZINV = zk(1); Z = RF([1], [0, 1])

def pc_test(H):
    """H: M x M list of RF. Try every output alignment c (row rotation; rows that wrap are advanced by z).
    Returns list of (c, [E_0..E_{M-1}]) for which the aligned H is pseudo-circulant."""
    M = len(H); found = []
    for cc in range(M):
        A = [[(H[(i + cc) % M][j] * (Z if i + cc >= M else RF([1]))) for j in range(M)] for i in range(M)]
        E = [A[r][0] for r in range(M)]
        ok = all(eq(A[i][j], E[i - j]) if i >= j else eq(A[i][j], ZINV * E[M + i - j]) for i in range(M) for j in range(M))
        if ok: found.append((cc, E))
    return found

def stream_G(E):
    """G(z) = sum_r z^-r E_r(z^M) for polynomial E_r (denominator 1 only)."""
    M = len(E); out = collections.defaultdict(lambda: 0)
    for r, e in enumerate(E):
        if e.d != [1]: return 'E=' + str([pretty(x) for x in E])
        for k, cf in enumerate(e.n): out[M * k + r] += cf
    n = max(out) + 1 if out else 1
    return poly([out[i] for i in range(n)])
