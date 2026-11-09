"""Second proof engine for arithmetic lifting: combinational equivalence with ABC `cec`.

The node function (a z3 bit-vector expression over LUT logic) is bit-blasted here into an
ASCII AIGER file whose inputs are the operand bits in the inferred order; the candidate
polynomial is written as Verilog and turned into an AIG by Yosys with techmap only (no
re-synthesis). ABC's `cec` (FRAIG + SAT) decides equivalence with inputs and outputs
matched by order. Trusted: this exporter, Yosys techmap/aigmap, ABC."""
import os, subprocess, tempfile
import z3


class AIG:
    def __init__(self):
        self.inputs, self.ands, self.hash = [], [], {}
        self.next = 1

    def new_input(self):
        lit = 2 * self.next; self.next += 1; self.inputs.append(lit); return lit

    def AND(self, a, b):
        if a == 0 or b == 0:
            return 0
        if a == 1:
            return b
        if b == 1:
            return a
        if a == b:
            return a
        if a == b ^ 1:
            return 0
        key = (min(a, b), max(a, b))
        if key not in self.hash:
            lit = 2 * self.next; self.next += 1
            self.ands.append((lit,) + key); self.hash[key] = lit
        return self.hash[key]

    def OR(self, a, b):
        return self.AND(a ^ 1, b ^ 1) ^ 1

    def XOR(self, a, b):
        return self.OR(self.AND(a, b ^ 1), self.AND(a ^ 1, b))

    def MUX(self, c, t, e):
        return self.OR(self.AND(c, t), self.AND(c ^ 1, e))

    def write(self, path, outputs, input_names=None, output_names=None):
        with open(path, "w") as f:
            f.write(f"aag {self.next - 1} {len(self.inputs)} 0 {len(outputs)} {len(self.ands)}\n")
            for lit in self.inputs:
                f.write(f"{lit}\n")
            for lit in outputs:
                f.write(f"{lit}\n")
            for lit, a, b in self.ands:
                f.write(f"{lit} {b} {a}\n")
            for q, name in enumerate(input_names or []):
                f.write(f"i{q} {name}\n")
            for q, name in enumerate(output_names or []):
                f.write(f"o{q} {name}\n")

    def write_blif(self, path, outputs, input_names, output_names):
        """BLIF with the same port names (ABC reads binary AIGER and BLIF, not ASCII AIGER)."""
        def net(lit):
            return {0: "c0", 1: "c1"}.get(lit, f"n{lit >> 1}")
        with open(path, "w") as f:
            f.write(".model impl\n.inputs " + " ".join(input_names) + "\n.outputs " + " ".join(output_names) + "\n")
            f.write(".names c0\n.names c1\n1\n")
            for lit, name in zip(self.inputs, input_names):
                f.write(f".names {name} n{lit >> 1}\n1 1\n")
            for lit, a, b in self.ands:
                pa = "0" if a & 1 else "1"; pb = "0" if b & 1 else "1"
                f.write(f".names {net(a & ~1 if a > 1 else a)} {net(b & ~1 if b > 1 else b)} n{lit >> 1}\n{pa}{pb} 1\n")
            for lit, name in zip(outputs, output_names):
                if lit in (0, 1):
                    f.write(f".names {name}\n" + ("1\n" if lit else ""))
                else:
                    f.write(f".names n{lit >> 1} {name}\n{'0' if lit & 1 else '1'} 1\n")
            f.write(".end\n")


def blast(expr, input_lits):
    """input_lits: {(symbol, bit): literal}. Returns literal list (LSB first) of expr."""
    g = AIG_current[0]
    memo = {}

    def rec(e):
        key = e.get_id()
        if key in memo:
            return memo[key]
        k = e.decl().kind()
        if z3.is_bv_value(e):
            v = e.as_long(); r = [1 if (v >> i) & 1 else 0 for i in range(e.size())]
        elif z3.is_true(e):
            r = [1]
        elif z3.is_false(e):
            r = [0]
        elif z3.is_const(e) and k == z3.Z3_OP_UNINTERPRETED:
            name = str(e)
            r = [input_lits.get((name, i), None) for i in range(e.size())]
            assert None not in r, f"bit of {name} outside the declared operands"
        else:
            a = [rec(c) for c in e.children()]
            if k == z3.Z3_OP_EXTRACT:
                hi, lo = e.params(); r = a[0][lo:hi + 1]
            elif k == z3.Z3_OP_CONCAT:
                r = [b for part in reversed(a) for b in part]
            elif k in (z3.Z3_OP_BNOT, z3.Z3_OP_NOT):
                r = [x ^ 1 for x in a[0]]
            elif k in (z3.Z3_OP_BAND, z3.Z3_OP_AND):
                r = a[0]
                for o in a[1:]:
                    r = [g.AND(x, y) for x, y in zip(r, o)]
            elif k in (z3.Z3_OP_BOR, z3.Z3_OP_OR):
                r = a[0]
                for o in a[1:]:
                    r = [g.OR(x, y) for x, y in zip(r, o)]
            elif k in (z3.Z3_OP_BXOR, z3.Z3_OP_XOR):
                r = a[0]
                for o in a[1:]:
                    r = [g.XOR(x, y) for x, y in zip(r, o)]
            elif k == z3.Z3_OP_ITE:
                r = [g.MUX(a[0][0], t, f_) for t, f_ in zip(a[1], a[2])]
            elif k == z3.Z3_OP_EQ:
                acc = 1
                for x, y in zip(a[0], a[1]):
                    acc = g.AND(acc, g.XOR(x, y) ^ 1)
                r = [acc]
            elif k == z3.Z3_OP_ZERO_EXT:
                r = a[0] + [0] * e.params()[0]
            elif k == z3.Z3_OP_SIGN_EXT:
                r = a[0] + [a[0][-1]] * e.params()[0]
            elif k == z3.Z3_OP_BADD:
                r = a[0]
                for o in a[1:]:
                    c, s = 0, []
                    for x, y in zip(r, o):
                        s.append(g.XOR(g.XOR(x, y), c)); c = g.OR(g.AND(x, y), g.AND(c, g.XOR(x, y)))
                    r = s
            else:
                raise NotImplementedError(e.decl().name())
        memo[key] = r
        return r
    return rec(expr)


AIG_current = [None]


def candidate_verilog(cand, path):
    m = cand["width"]; r = len(cand["operands"])
    terms = [f"{m}'d{cand['const']}"]
    for g, c in enumerate(cand["linear"]):
        if c:
            terms.append(f"{m}'d{c} * a{g}")
    for (g, h), d in cand["bilinear"].items():
        if d:
            terms.append(f"{m}'d{d} * a{g} * a{h}")
    ports = ", ".join(f"input [{m - 1}:0] a{g}" for g in range(r))
    with open(path, "w") as f:
        f.write(f"module spec({ports}, output [{m - 1}:0] y);\n  assign y = {' + '.join(terms)};\nendmodule\n")


def cec(expr, cand, workdir, timeout_s=600, coarse=True):
    """ABC cec of the node's chain outputs against the candidate. Returns 'EQUIVALENT', 'NOT_EQUIVALENT' or 'UNDECIDED'."""
    m = cand["width"]
    os.makedirs(workdir, exist_ok=True)
    g = AIG(); AIG_current[0] = g
    lits, names = {}, []
    for gi, grp in enumerate(cand["operands"]):
        for i in range(m):
            lit = g.new_input(); names.append(f"a{gi}[{i}]")
            if i < len(grp):
                lits[grp[i]] = lit
    outs = blast(expr, lits)
    impl = os.path.join(workdir, "impl.blif")
    g.write_blif(impl, [outs[o] for o in cand["outputs"]], names, [f"y[{q}]" for q in range(len(cand["outputs"]))])
    candidate_verilog(cand, os.path.join(workdir, "spec.v"))
    spec = os.path.join(workdir, "spec.aig")
    # the same coarse flow as synth_ice40 (wreduce, alumacc -> $macc), then generic techmap
    flows = {"coarse": "synth -flatten -top spec -run begin:fine; techmap; opt_clean",
             "plain": "hierarchy -top spec; proc; flatten; techmap; opt_clean",
             # same technology flow as the benchmarks, then iCE40 primitives flattened to logic
             "ice40": "synth_ice40 -top spec -nobram; read_verilog -overwrite /usr/share/yosys/ice40/cells_sim.v; "
                      "hierarchy -top spec; proc; flatten; opt_clean; techmap; opt_clean"}
    flow = flows[coarse if isinstance(coarse, str) else ("coarse" if coarse else "plain")]
    subprocess.run(["yosys", "-q", "-p", f"read_verilog {workdir}/spec.v; {flow}; aigmap; write_aiger -symbols {spec}"],
                   check=True, timeout=timeout_s, capture_output=True)
    try:
        res = subprocess.run(["yosys-abc", "-c", f"cec -T {timeout_s} -C 100000000 {impl} {spec}"], capture_output=True, text=True, timeout=timeout_s + 30)
        out = res.stdout + res.stderr
    except subprocess.TimeoutExpired:
        out = f"TIMEOUT after {timeout_s + 30} s"
    with open(os.path.join(workdir, "abc.log"), "w") as f:
        f.write(out)
    if "Networks are equivalent" in out:
        return "EQUIVALENT"
    if "NOT EQUIVALENT" in out:
        return "NOT_EQUIVALENT"
    return "UNDECIDED"
