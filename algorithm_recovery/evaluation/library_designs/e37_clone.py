#!/usr/bin/env python3
"""E3.7 clone fidelity: replace the certified node equations by the equations a trace-guided
family accepted (T1.2), keep every other node, and write the graph for c3/decompile.py.
usage: e37_clone.py GRAPH_JSON ACCEPTED_JSON FAMILY OUT_GRAPH_JSON"""
import json, sys
g = json.load(open(sys.argv[1])); acc = json.load(open(sys.argv[2]))[sys.argv[3]]
import z3


def canonical(smt2):
    """(assert (= __out <expr>)) with every symbol declared (z3 may emit the equality reversed)."""
    eq = z3.parse_smt2_string(smt2)[0]
    a, b = eq.arg(0), eq.arg(1)
    body = b if str(a) == "__out" else a
    syms, seen, st = {}, set(), [body]
    while st:
        x = st.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            syms[str(x)] = x
        st.extend(x.children())
    decl = "".join(f"(declare-fun |{k}| () (_ BitVec {v.size()}))\n" for k, v in sorted(syms.items()))
    return decl + f"(declare-fun __out () (_ BitVec {body.size()}))\n(assert (= __out {body.sexpr()}))\n"


repl = {(a["name"], a["phase"]): canonical(a["smt2"]) for a in acc}
n = 0
for nd in g["nodes"]:
    k = (nd["name"], nd["phase"])
    if k in repl:
        nd["smt2"] = repl[k]; n += 1
g["clone_of"] = {"family": sys.argv[3], "replaced_nodes": n}
json.dump(g, open(sys.argv[4], "w"))
print("replaced", n, "of", len(g["nodes"]), "nodes")
