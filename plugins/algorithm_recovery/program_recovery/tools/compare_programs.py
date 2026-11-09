#!/usr/bin/env python3
"""Compare two generated C3 programs up to the order of commutative operands and temporary names.

z3's simplify() orders the arguments of commutative operators by AST id, and AST ids depend on the
allocation history of the z3 context (creation order and id recycling). The C++ port and the Python
reference therefore print the same terms with commutative operands in a different order, and the
printer's temporaries t1, t2, ... in a different order. This tool canonicalises both programs:
temporaries are replaced by a hash of their canonical definition, chains of | & ^ + * and of and/or
are flattened and their operands sorted, and the two sides of == are sorted. Every other statement
must match in order. Exit status 0 iff the programs are equal under this normalisation.

usage: compare_programs.py A.py B.py"""
import ast, hashlib, sys, threading

COMM = (ast.BitOr, ast.BitAnd, ast.BitXor, ast.Add, ast.Mult)
TEMP = lambda n: n[:1] == "t" and n[1:].isdigit()


class Canon:
    def __init__(self):
        self.env = {}

    def expr(self, n):
        if isinstance(n, ast.Name):
            return self.env.get(n.id, n.id) if TEMP(n.id) else n.id
        if isinstance(n, ast.Constant):
            return repr(n.value)
        if isinstance(n, ast.BinOp):
            if isinstance(n.op, COMM):
                parts = []
                self.flatten_bin(n, type(n.op), parts)
                return "(" + type(n.op).__name__ + " " + " ".join(sorted(parts)) + ")"
            return "(" + type(n.op).__name__ + " " + self.expr(n.left) + " " + self.expr(n.right) + ")"
        if isinstance(n, ast.BoolOp):
            parts = []
            self.flatten_bool(n, type(n.op), parts)
            return "(" + type(n.op).__name__ + " " + " ".join(sorted(parts)) + ")"
        if isinstance(n, ast.Compare):
            assert len(n.ops) == 1
            a, b = self.expr(n.left), self.expr(n.comparators[0])
            if isinstance(n.ops[0], (ast.Eq, ast.NotEq)):
                a, b = sorted((a, b))
            return "(" + type(n.ops[0]).__name__ + " " + a + " " + b + ")"
        if isinstance(n, ast.UnaryOp):
            return "(" + type(n.op).__name__ + " " + self.expr(n.operand) + ")"
        if isinstance(n, ast.IfExp):
            return "(If " + self.expr(n.test) + " " + self.expr(n.body) + " " + self.expr(n.orelse) + ")"
        if isinstance(n, ast.Subscript):
            return self.expr(n.value) + "[" + self.expr(n.slice) + "]"
        if isinstance(n, (ast.Tuple, ast.List)):
            return ("(" if isinstance(n, ast.Tuple) else "[") + ", ".join(self.expr(e) for e in n.elts) + ")"
        if isinstance(n, ast.Call):
            return self.expr(n.func) + "(" + ", ".join(self.expr(a) for a in n.args) + ")"
        if isinstance(n, ast.Attribute):
            return self.expr(n.value) + "." + n.attr
        if isinstance(n, ast.Starred):
            return "*" + self.expr(n.value)
        if isinstance(n, ast.Slice):
            return ":".join("" if x is None else self.expr(x) for x in (n.lower, n.upper, n.step))
        return ast.dump(n)

    def flatten_bin(self, n, op, out):
        if isinstance(n, ast.BinOp) and type(n.op) is op:
            self.flatten_bin(n.left, op, out); self.flatten_bin(n.right, op, out)
        else:
            out.append(self.expr(n))

    def flatten_bool(self, n, op, out):
        if isinstance(n, ast.BoolOp) and type(n.op) is op:
            for v in n.values:
                self.flatten_bool(v, op, out)
        else:
            out.append(self.expr(n))

    def stmts(self, body, out):
        for s in body:
            if isinstance(s, ast.Assign) and len(s.targets) == 1 and isinstance(s.targets[0], ast.Name) and TEMP(s.targets[0].id):
                self.env[s.targets[0].id] = "T" + hashlib.sha1(self.expr(s.value).encode()).hexdigest()[:16]
            elif isinstance(s, ast.Assign):
                out.append(" = ".join(self.expr(t) for t in s.targets) + " = " + self.expr(s.value))
            elif isinstance(s, ast.AugAssign):
                out.append(self.expr(s.target) + " " + type(s.op).__name__ + "= " + self.expr(s.value))
            elif isinstance(s, ast.For):
                out.append("for " + self.expr(s.target) + " in " + self.expr(s.iter))
                self.stmts(s.body, out)
                out.append("end for")
            elif isinstance(s, ast.Return):
                out.append("return " + self.expr(s.value))
            elif isinstance(s, ast.FunctionDef):
                if s.name == "transaction":
                    out.append("def transaction")
                    self.stmts(s.body, out)
                else:
                    out.append(ast.dump(s))
            else:
                out.append(ast.dump(s))


def canonical(path):
    src = open(path).read()
    c = Canon()
    out = []
    c.stmts(ast.parse(src).body, out)
    return src.count("\n"), out


def main():
    a, b = sys.argv[1:3]
    la, ca = canonical(a)
    lb, cb = canonical(b)
    if la != lb:
        print(f"LINES {la} != {lb}")
    for k, (x, y) in enumerate(zip(ca, cb)):
        if x != y:
            print(f"STATEMENT {k} differs:\n  {x[:300]}\n  {y[:300]}")
            break
    if len(ca) != len(cb):
        print(f"STATEMENTS {len(ca)} != {len(cb)}")
    same = la == lb and ca == cb
    print("EQUIVALENT" if same else "DIFFERENT")
    return 0 if same else 1


if __name__ == "__main__":
    sys.setrecursionlimit(2_000_000)
    threading.stack_size(1 << 30)
    res = []
    t = threading.Thread(target=lambda: res.append(main()))
    t.start(); t.join()
    sys.exit(res[0] if res else 2)
