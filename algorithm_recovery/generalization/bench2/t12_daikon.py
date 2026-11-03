#!/usr/bin/env python3
"""T1.2 with the ORIGINAL Daikon 5.9.0 on hal-recover event graphs (T12_NATIVE_FAMILIES_PROTOCOL.txt, amendment 3).
Two steps around running the third-party jar, which the user runs:
  prepare CASE_DIR GRAPH_JSON WORK_DIR            -> WORK_DIR/{hw.decls, hw.dtrace, meta.json, run_daikon.sh}
  (user)  bash WORK_DIR/run_daikon.sh              -> WORK_DIR/hw.inv.txt
  judge   CASE_DIR GRAPH_JSON WORK_DIR OUT_JSON
Targets and samples are those of t12_native_families.py (every multi-bit (word, phase) node with >= 4 training
samples). Daikon variables per target: every source symbol of the node in an unsigned (u_) and a two's-complement
(s_) view (widths <= 63: Daikon longs) and the next target value (T_u, T_s); one program point per target;
Daikon's default justification with FunctionBinary enabled, as in the earlier Canny study. Every reported
invariant that defines T (T == e, or a linear equation with T coefficient +-1) is translated to an n-bit z3
expression; other invariants on T are counted as untranslatable. A translation must reproduce all training
samples (conversion check). Judge: WRONG if z3 refutes equality with the certified specialised node; CONFIRMED if
some fresh-trace event at that phase is mispredicted while the unspecialised node equation predicts it."""
import ast, collections, json, os, re, sys
import z3
from t12_common import Graph, evaluate, proved_equal

JAR = "/home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/algo/othertools/dl/daikon-5.9.0/daikon.jar"


def signed(v, m):
    return v - (1 << m) if (v >> (m - 1)) & 1 else v


def prepare(case_dir, graph, work):
    os.makedirs(work, exist_ok=False)
    G = Graph(graph)
    decl, trace, meta = ["decl-version 2.0", "var-comparability none", ""], [], {}
    for idx, t in enumerate(G.targets(case_dir)):
        key = {"word": t["word"], "phase": t["phase"], "n": t["n"], "samples": len(t["S"])}
        if t["n"] > 63:
            meta[idx] = dict(key, skipped="target wider than 63 bits (Daikon long)")
            continue
        vs = []
        for name, v in sorted(t["syms"].items()):
            if v.size() > 63:
                continue
            ident = re.sub(r"\W", "_", name)
            vs += [("u_" + ident, name, False), ("s_" + ident, name, True)]
        ppt = f"P{idx}:::POINT"
        decl += ["ppt " + ppt, "ppt-type point"]
        for nm in [x[0] for x in vs] + ["T_u", "T_s"]:
            decl += ["variable " + nm, "  var-kind variable", "  dec-type long", "  rep-type int", "  comparability 1"]
        decl.append("")
        for vals, T in t["S"]:
            rec = [ppt]
            for nm, sym, sg in vs:
                m = t["syms"][sym].size()
                rec += [nm, str(signed(vals[sym], m) if sg else vals[sym]), "1"]
            rec += ["T_u", str(T), "1", "T_s", str(signed(T, t["n"])), "1", ""]
            trace += rec
        meta[idx] = dict(key, vars={nm: [sym, sg] for nm, sym, sg in vs})
    open(f"{work}/hw.decls", "w").write("\n".join(decl) + "\n")
    open(f"{work}/hw.dtrace", "w").write("\n".join(trace) + "\n")
    json.dump({"case_dir": case_dir, "graph": graph, "targets": meta}, open(f"{work}/meta.json", "w"))
    open(f"{work}/run_daikon.sh", "w").write(
        "#!/usr/bin/env bash\n# Unmodified Daikon 5.9.0 (third-party jar), settings of the earlier Canny study.\n"
        f'cd "$(dirname "$0")"; J={JAR}\n'
        "java -Xmx3g -cp $J daikon.Daikon --no_text_output -o hw.inv.gz "
        "--config_option daikon.inv.ternary.threeScalar.FunctionBinary.enabled=true hw.decls hw.dtrace > daikon.log 2>&1 &&\n"
        "java -Xmx3g -cp $J daikon.PrintInvariants hw.inv.gz > hw.inv.txt 2>> daikon.log\n"
        'echo "daikon exit=$? invariants file: $(wc -l < hw.inv.txt) lines"\n')
    print(json.dumps({"targets": len(meta), "skipped": sum("skipped" in m for m in meta.values()), "work": work}))


class Lin:
    def __init__(self, c=None, k=0):
        self.c, self.k = dict(c or {}), k

    @staticmethod
    def of(o):
        return o if isinstance(o, Lin) else Lin(k=o)

    def __add__(a, b):
        b = Lin.of(b); c = dict(a.c)
        for x, y in b.c.items():
            c[x] = c.get(x, 0) + y
        return Lin(c, a.k + b.k)
    __radd__ = __add__

    def __neg__(a):
        return Lin({x: -y for x, y in a.c.items()}, -a.k)

    def __sub__(a, b):
        return a + (-Lin.of(b))

    def __rsub__(a, b):
        return Lin.of(b) - a

    def __mul__(a, b):
        b = Lin.of(b)
        if a.c and b.c:
            raise ValueError("nonlinear")
        if a.c:
            return Lin({x: y * b.k for x, y in a.c.items()}, a.k * b.k)
        return Lin({x: y * a.k for x, y in b.c.items()}, a.k * b.k)
    __rmul__ = __mul__


def lin_eval(node, env):
    if isinstance(node, ast.Expression):
        return lin_eval(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -lin_eval(node.operand, env)
    if isinstance(node, ast.BinOp) and type(node.op) in (ast.Add, ast.Sub, ast.Mult):
        a, b = lin_eval(node.left, env), lin_eval(node.right, env)
        return a + b if isinstance(node.op, ast.Add) else a - b if isinstance(node.op, ast.Sub) else a * b
    raise ValueError("not linear")


def judge(case_dir, graph, work, out_json):
    meta = json.load(open(f"{work}/meta.json"))["targets"]
    invs, cur = collections.defaultdict(list), None
    for line in open(f"{work}/hw.inv.txt"):
        m = re.match(r"^P(\d+):::POINT$", line.strip())
        if m:
            cur = m.group(1); continue
        if cur is not None and line.strip() and not line.startswith("==="):
            invs[cur].append(line.strip())
    G = Graph(graph)
    stats, examples = collections.Counter(), []
    for idx, t in enumerate(G.targets(case_dir)):
        mt = meta.get(str(idx))
        assert mt and mt["word"] == t["word"] and mt["phase"] == t["phase"], "targets differ from the prepared ones"
        if "skipped" in mt:
            stats["targets_skipped_wide"] += 1; continue
        stats["targets"] += 1
        n, V, syms = t["n"], mt["vars"], t["syms"]

        def opnd(nm):
            sym, sg = V[nm]; v = syms[sym]; m = v.size()
            if m == n:
                return v
            if m > n:
                return z3.Extract(n - 1, 0, v)
            return z3.SignExt(n - m, v) if sg else z3.ZeroExt(n - m, v)

        def const(c):
            return z3.BitVecVal(c % (1 << n), n)

        def to_z3(node):
            if isinstance(node, ast.Expression):
                return to_z3(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                return const(node.value)
            if isinstance(node, ast.Name) and node.id in V:
                return opnd(node.id)
            if isinstance(node, ast.UnaryOp):
                if isinstance(node.op, ast.USub):
                    return -to_z3(node.operand)
                if isinstance(node.op, ast.Invert):
                    return ~to_z3(node.operand)
            if isinstance(node, ast.BinOp):
                a, b = to_z3(node.left), to_z3(node.right)
                ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b,
                       ast.BitXor: lambda: a ^ b, ast.BitAnd: lambda: a & b, ast.BitOr: lambda: a | b}
                if type(node.op) in ops:
                    return ops[type(node.op)]()
            raise ValueError("operator outside the translatable set")

        seen = set()
        for inv in invs.get(str(idx), []):
            if not re.search(r"\bT_[us]\b", inv):
                continue
            stats["invariants_on_T"] += 1
            m = re.fullmatch(r"(.+?)\s*==\s*(.+)", inv)
            try:
                if not m or "one of" in inv:
                    raise ValueError("not an equation")
                lhs, rhs = m.group(1).strip(), m.group(2).strip()
                if re.fullmatch(r"T_[us]", lhs) and not re.search(r"\bT_[us]\b", rhs):
                    cand = to_z3(ast.parse(rhs.replace("!", "~"), mode="eval"))
                elif re.fullmatch(r"T_[us]", rhs) and not re.search(r"\bT_[us]\b", lhs):
                    cand = to_z3(ast.parse(lhs.replace("!", "~"), mode="eval"))
                else:
                    env = {nm: Lin({nm: 1}) for nm in list(V) + ["T_u", "T_s"]}
                    e = lin_eval(ast.parse(f"({lhs}) - ({rhs})", mode="eval"), env)
                    tc = [(x, c) for x, c in e.c.items() if x.startswith("T_") and c]
                    if len(tc) != 1 or abs(tc[0][1]) != 1:
                        raise ValueError("T coefficient")
                    sgn = -tc[0][1]
                    cand = const(e.k * sgn)
                    for x, c in e.c.items():
                        if not x.startswith("T_") and c:
                            cand = cand + const(c * sgn) * opnd(x)
            except (ValueError, SyntaxError, KeyError, z3.Z3Exception):
                stats["untranslatable"] += 1; continue
            cand = z3.simplify(cand)
            if any(evaluate(cand, syms, vals) != T for vals, T in t["S"]):
                stats["conversion_fail"] += 1; continue
            if str(cand) in seen:
                continue
            seen.add(str(cand)); stats["accepted"] += 1
            eq = proved_equal(cand, t["cert"])
            if eq is None:
                stats["undecided"] += 1; continue
            if eq:
                continue
            stats["wrong"] += 1
            missed = [(vals, T) for vals, T in t["V"] if evaluate(cand, syms, vals) != T]
            if missed:
                stats["confirmed_any"] += 1
            hit = next(((vals, T) for vals, T in missed if evaluate(t["raw"], syms, vals) == T), None)
            if hit is not None:
                stats["confirmed"] += 1
                if len(examples) < 50:
                    examples.append({"word": t["word"], "phase": t["phase"], "invariant": inv,
                                     "variant_observed": hit[1], "candidate_predicted": evaluate(cand, syms, hit[0])})
    res = {"case": case_dir.rstrip("/").split("/")[-1], "tool": "Daikon 5.9.0 (unmodified)", "stats": dict(stats),
           "confirmed_examples": examples}
    json.dump(res, open(out_json, "w"), indent=1)
    print(json.dumps(res)[:2000])


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "prepare":
        prepare(*sys.argv[2:5])
    elif mode == "judge":
        judge(*sys.argv[2:6])
    else:
        raise SystemExit(__doc__)
