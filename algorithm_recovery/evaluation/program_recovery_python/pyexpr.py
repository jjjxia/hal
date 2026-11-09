"""Python code emission for z3 bit-vector expressions (copied verbatim from decompile.py, class Printer)."""
import z3


class Printer:
    def __init__(self, env):
        self.env, self.lines, self.memo, self.count = env, [], {}, 0

    def mask(self, w):
        return hex((1 << w) - 1)

    def emit(self, e):
        key = e.get_id()
        if key in self.memo:
            return self.memo[key]
        code = self.build(e)
        if e.num_args() > 0 and len(code) > 40:
            self.count += 1
            name = f"t{self.count}"
            self.lines.append(f"    {name} = {code}")
            code = name
        self.memo[key] = code
        return code

    def build(self, e):
        k = e.decl().kind()
        a = [self.emit(c) for c in e.children()]
        w = e.size() if z3.is_bv(e) else 1
        m = self.mask(w)
        if z3.is_bv_value(e):
            return hex(e.as_long())
        if z3.is_true(e):
            return "True"
        if z3.is_false(e):
            return "False"
        if z3.is_const(e):
            return self.env(str(e))
        if k == z3.Z3_OP_EXTRACT:
            hi, lo = e.params()
            return f"(({a[0]} >> {lo}) & {self.mask(hi - lo + 1)})"
        if k == z3.Z3_OP_CONCAT:
            parts, shift = [], 0
            for c, code in reversed(list(zip(e.children(), a))):
                parts.append(f"({code} << {shift})" if shift else code); shift += c.size()
            return "(" + " | ".join(parts) + ")"
        if k == z3.Z3_OP_BNOT:
            return f"({a[0]} ^ {m})"
        if k in (z3.Z3_OP_BAND, z3.Z3_OP_BOR, z3.Z3_OP_BXOR):
            op = {z3.Z3_OP_BAND: " & ", z3.Z3_OP_BOR: " | ", z3.Z3_OP_BXOR: " ^ "}[k]
            return "(" + op.join(a) + ")"
        if k == z3.Z3_OP_BADD:
            return f"(({' + '.join(a)}) & {m})"
        if k == z3.Z3_OP_BSUB:
            return f"(({a[0]} - {a[1]}) & {m})"
        if k == z3.Z3_OP_BMUL:
            return f"(({' * '.join(a)}) & {m})"
        if k == z3.Z3_OP_BNEG:
            return f"((-{a[0]}) & {m})"
        if k == z3.Z3_OP_BSHL:
            return f"(({a[0]} << {a[1]}) & {m} if {a[1]} < {w} else 0)"
        if k == z3.Z3_OP_BLSHR:
            return f"(({a[0]} >> {a[1]}) if {a[1]} < {w} else 0)"
        if k == z3.Z3_OP_BASHR:
            return f"(sra({a[0]}, {a[1]}, {w}))"
        if k == z3.Z3_OP_ITE:
            return f"({a[1]} if {a[0]} else {a[2]})"
        if k == z3.Z3_OP_EQ:
            return f"({a[0]} == {a[1]})"
        if k == z3.Z3_OP_NOT:
            return f"(not {a[0]})"
        if k == z3.Z3_OP_AND:
            return "(" + " and ".join(a) + ")"
        if k == z3.Z3_OP_OR:
            return "(" + " or ".join(a) + ")"
        if k == z3.Z3_OP_ULEQ:
            return f"({a[0]} <= {a[1]})"
        if k == z3.Z3_OP_ULT:
            return f"({a[0]} < {a[1]})"
        if k in (z3.Z3_OP_SLEQ, z3.Z3_OP_SLT):
            sw = e.children()[0].size()
            op = "<=" if k == z3.Z3_OP_SLEQ else "<"
            return f"(sgn({a[0]}, {sw}) {op} sgn({a[1]}, {sw}))"
        if k == z3.Z3_OP_SIGN_EXT:
            ext = e.params()[0]; sw = e.children()[0].size()
            return f"(sgn({a[0]}, {sw}) & {self.mask(sw + ext)})"
        if k == z3.Z3_OP_ZERO_EXT:
            return a[0]
        raise NotImplementedError(f"operator {e.decl().name()}")


