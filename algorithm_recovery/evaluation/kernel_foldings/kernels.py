#!/usr/bin/env python3
"""E8 kernels (E8_PREMISES_PROTOCOL.txt): standard DSP/HLS kernels as single-rate dataflow graphs.

A kernel is {"inputs": [...], "outputs": {name: node}, "nodes": {id: {"op", "operands": [[src, delay], ...], "const"?}}}.
Ops: add, sub (two operands), mul (two operands, product mod 2^W), mulc (operand * const), addc (operand + const).
An operand is [src, delay]: src is an input name or a node id, delay the sample delay (>= 0); delay 0 operands must not form
a cycle. Words are W = 16 bits; all arithmetic is modulo 2^16. Each kernel can also be printed as C (to_c) for gcc
cross-checks and for Bambu (E8.3); state (sample delays) becomes static variables updated at the end of the call.
"""
import math

W = 16


class K:
    def __init__(self, inputs):
        self.inputs, self.nodes, self.outputs, self.n = list(inputs), {}, {}, 0

    def op(self, op, *operands, const=None):
        v = f"v{self.n}"; self.n += 1
        nd = {"op": op, "operands": [list(o) if isinstance(o, (list, tuple)) else [o, 0] for o in operands]}
        if const is not None:
            nd["const"] = const & ((1 << W) - 1)
        self.nodes[v] = nd
        return v

    def done(self, **outputs):
        self.outputs = outputs
        return {"inputs": self.inputs, "outputs": self.outputs, "nodes": self.nodes}


def fir8():
    """Transposed-form FIR, 8 taps: s_k[n] = h_k x[n] + s_{k+1}[n-1], y = s_0."""
    h = [3, -7, 18, 41, 41, 18, -7, 3]
    k = K(["x"])
    p = [k.op("mulc", "x", const=c) for c in h]
    s = p[7]
    for i in range(6, -1, -1):
        s = k.op("add", p[i], [s, 1])
    return k.done(y=s)


def _biquad(k, x, a1, a2, b0, b1, b2):
    # direct form II: w = x - a1 w[n-1] - a2 w[n-2]; y = b0 w + b1 w[n-1] + b2 w[n-2]; w is defined after its delayed reads
    m1 = k.op("mulc", ["__w", 1], const=a1)
    m2 = k.op("mulc", ["__w", 2], const=a2)
    t = k.op("sub", x, m1)
    w = k.op("sub", t, m2)
    for v in (m1, m2):
        k.nodes[v]["operands"][0][0] = w
    p0 = k.op("mulc", w, const=b0)
    p1 = k.op("mulc", [w, 1], const=b1)
    p2 = k.op("mulc", [w, 2], const=b2)
    u = k.op("add", p0, p1)
    return k.op("add", u, p2)


def biquad():
    k = K(["x"])
    return k.done(y=_biquad(k, "x", 23, -9, 5, 10, 5))


def biquad2():
    k = K(["x"])
    y1 = _biquad(k, "x", 23, -9, 5, 10, 5)
    return k.done(y=_biquad(k, y1, 31, -14, 3, -6, 3))


def lattice4():
    """4-stage lattice FIR: f_i = f_{i-1} + k_i g_{i-1}[n-1], g_i = k_i f_{i-1} + g_{i-1}[n-1], f_0 = g_0 = x."""
    ks = [11, -5, 7, -3]
    k = K(["x"])
    f, g = "x", "x"
    for c in ks:
        a = k.op("mulc", [g, 1], const=c)
        b = k.op("mulc", f, const=c)
        f, g = k.op("add", f, a), k.op("add", b, [g, 1])
    return k.done(y=f, z=g)


def horner6():
    """y = c6 x^6 + ... + c0 by Horner's rule (variable multiplications)."""
    c = [7, -3, 5, 2, -11, 4, 9]
    k = K(["x"])
    h = k.op("addc", k.op("mulc", "x", const=c[6]), const=c[5])
    for i in range(4, -1, -1):
        h = k.op("addc", k.op("mul", h, "x"), const=c[i])
    return k.done(y=h)


def matvec4():
    A = [[3, -1, 4, 1], [5, 9, -2, 6], [-5, 3, 5, 8], [9, 7, -9, 3]]
    k = K(["x0", "x1", "x2", "x3"])
    ys = {}
    for i in range(4):
        acc = k.op("mulc", "x0", const=A[i][0])
        for j in range(1, 4):
            acc = k.op("add", acc, k.op("mulc", f"x{j}", const=A[i][j]))
        ys[f"y{i}"] = acc
    return k.done(**ys)


def dct8():
    """8-point DCT-II by its definition, coefficients round(64 * c_k cos((2n+1) k pi / 16))."""
    k = K([f"x{n}" for n in range(8)])
    ys = {}
    for kk in range(8):
        ck = math.sqrt(0.5) if kk == 0 else 1.0
        acc = None
        for n in range(8):
            c = round(64 * ck * math.cos((2 * n + 1) * kk * math.pi / 16))
            p = k.op("mulc", f"x{n}", const=c)
            acc = p if acc is None else k.op("add", acc, p)
        ys[f"y{kk}"] = acc
    return k.done(**ys)


def arf():
    """Bambu examples/arf/module.c (auto-regressive lattice filter, 16 mul + 12 add), transcribed operation by operation."""
    k = K(["i1", "i2", "i3", "i4", "i5", "i6", "G1", "G2", "GG1", "GG2"])
    m = lambda a, b: k.op("mul", a, b)
    a = lambda x, y: k.op("add", x, y)
    op1, op2, op3, op4 = m("GG1", "i1"), m("GG2", "i2"), m("G1", "i2"), m("G2", "i1")
    op5, op6, op7, op8 = m("G1", "i3"), m("G2", "i4"), m("G1", "i4"), m("G2", "i3")
    op9, op10, op11, op12 = a(op1, op2), a(op3, op4), a(op5, op6), a(op7, op8)
    op13 = a(op11, "i5"); op14 = a("i6", op12)
    op15, op16, op17, op18 = m("G1", op14), m(op13, "G2"), m(op13, "G1"), m(op14, "G2")
    op19 = m(op15, op16); op20 = a(op17, op18)
    op21, op22, op23, op24 = m("G1", op20), m(op19, "G2"), m(op19, "G1"), m(op20, "G2")
    op25, op26 = a(op21, op22), a(op23, op24)
    op27, op28 = a(op9, op25), a(op10, op26)
    return k.done(o1=op13, o2=op14, o3=op27, o4=op28)


KERNELS = {"fir8": fir8, "biquad": biquad, "biquad2": biquad2, "lattice4": lattice4, "horner6": horner6,
           "matvec4": matvec4, "dct8": dct8, "arf": arf}
M = (1 << W) - 1


def evaluate(kern, samples):
    """Per-sample node values (zero history), for a list of {input: value} samples."""
    order, done = [], set()
    while len(order) < len(kern["nodes"]):
        for v, nd in kern["nodes"].items():
            if v not in done and all(d > 0 or s in kern["inputs"] or s in done for s, d in nd["operands"]):
                order.append(v); done.add(v)
    hist, out = [], []
    for n, x in enumerate(samples):
        val = {}
        def get(s, d):
            if s in kern["inputs"]:
                return samples[n - d][s] if n - d >= 0 else 0
            return val[s] if d == 0 else (hist[n - d][s] if n - d >= 0 else 0)
        for v in order:
            nd = kern["nodes"][v]; a = get(*nd["operands"][0])
            b = get(*nd["operands"][1]) if len(nd["operands"]) > 1 else None
            val[v] = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b,
                      "mulc": lambda: a * nd["const"], "addc": lambda: a + nd["const"]}[nd["op"]]() & M
        hist.append(val); out.append(val)
    return out


def to_c(name, kern):
    """C function name(inputs..., outputs*) with static state for delayed values (updated at the end of the call)."""
    need = {}
    for nd in kern["nodes"].values():
        for s, d in nd["operands"]:
            if d > 0:
                need[s] = max(need.get(s, 0), d)
    L = ["typedef unsigned short uint16_t;   /* no system headers: Bambu compiles for i386 without multilib */", f"void {name}(" + ", ".join([f"uint16_t {i}" for i in kern["inputs"]] +
                                                       [f"uint16_t *{o}" for o in kern["outputs"]]) + ") {"]
    for s, dm in sorted(need.items()):
        for d in range(1, dm + 1):
            L.append(f"  static uint16_t h_{s}_{d} = 0;")
    order, done = [], set()
    while len(order) < len(kern["nodes"]):
        for v, nd in kern["nodes"].items():
            if v not in done and all(d > 0 or s in kern["inputs"] or s in done for s, d in nd["operands"]):
                order.append(v); done.add(v)
    ref = lambda s, d: s if d == 0 else f"h_{s}_{d}"
    for v in order:
        nd = kern["nodes"][v]; a = ref(*nd["operands"][0])
        b = ref(*nd["operands"][1]) if len(nd["operands"]) > 1 else None
        e = {"add": f"{a} + {b}", "sub": f"{a} - {b}", "mul": f"{a} * {b}",
             "mulc": f"{a} * {nd.get('const', 0)}u", "addc": f"{a} + {nd.get('const', 0)}u"}[nd["op"]]
        L.append(f"  uint16_t {v} = (uint16_t)({e});")
    for o, v in kern["outputs"].items():
        L.append(f"  *{o} = {v};")
    for s, dm in sorted(need.items()):
        for d in range(dm, 1, -1):
            L.append(f"  h_{s}_{d} = h_{s}_{d-1};")
        L.append(f"  h_{s}_1 = {s};")
    L.append("}")
    return "\n".join(L) + "\n"
