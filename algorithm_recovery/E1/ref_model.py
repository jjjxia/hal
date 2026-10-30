"""Ground truth for E1 (scoring only; the recognizer never reads this file).
Algorithm-level model of the two cascaded biquads, bit-exact with biquad2_folded.v (24-bit wraparound, floor products),
and the timing map from the testbench rows (io.txt) to samples: x[n] is row 4+10n, y[n] appears in row 14+10n."""
import sys
A = dict(a11=1280, a12=-576, b10=320, b11=-704, b12=448, a21=-384, a22=-256, b20=896, b21=192, b22=-512)
def wrap(v, w=24): v &= (1 << w) - 1; return v - (1 << w) if v >> (w - 1) else v
def m(c, v): return wrap((c * v) >> 10)
def run(xs):
    v1 = v2 = s1 = s2 = 0; ys = []
    for x in xs:
        v = wrap(wrap(x + m(A['a11'], v1)) + m(A['a12'], v2))
        u = wrap(wrap(m(A['b10'], v) + m(A['b11'], v1)) + m(A['b12'], v2))
        s = wrap(wrap(u + m(A['a21'], s1)) + m(A['a22'], s2))
        y = wrap(wrap(m(A['b20'], s) + m(A['b21'], s1)) + m(A['b22'], s2))
        ys.append((y >> 4) & 0xFFFF); v2, v1, s2, s1 = v1, v, s1, s
    return ys
def check(io_path):
    rows = [tuple(map(int, l.split())) for l in open(io_path)]
    ns = (len(rows) - 15) // 10
    xs = [rows[4 + 10 * n][1] for n in range(ns)]; ys = run(xs)
    got = [rows[14 + 10 * n][2] for n in range(ns)]
    bad = [n for n in range(ns) if got[n] != ys[n]]
    return ns, bad
if __name__ == '__main__':
    ns, bad = check(sys.argv[1]); print('samples', ns, 'mismatches', len(bad), bad[:5])
