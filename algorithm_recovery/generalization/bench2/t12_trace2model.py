#!/usr/bin/env python3
"""T1.2 with the ORIGINAL Trace2Model CLI (Jeppu et al., DAC 2020; syn_event_update.py, CVC4 1.8) on hal-recover
event graphs (T12_NATIVE_FAMILIES_PROTOCOL.txt, amendment 3). Two steps around the third-party tool, which the
user runs:
  export CASE_DIR GRAPH_JSON WORK_DIR [MAX_TARGETS]  -> WORK_DIR/T<i>.txt, index.json, run_t2m.sh
  (user) bash WORK_DIR/run_t2m.sh                     -> WORK_DIR/out_T<i>.txt (resumable)
  import CASE_DIR GRAPH_JSON WORK_DIR OUT_JSON
Per target (word, phase), as in the earlier Canny study: a trace of two event types, 'e' (every source symbol in
an unsigned u<i> and a two's-complement s<i> view, and w = the word's current value) followed by 'n' (w = the
next value); the tool synthesises w' for 'e' over all training samples with its own LIA grammar and solver
timeout. Synthesised expressions built from +, -, *, variables and constants are translated to n-bit z3
expressions (ite and comparisons: untranslatable) and must reproduce the training samples. Judge and
confirmation as T1.2 (amendment 2)."""
import ast, collections, json, os, re, sys
import z3
from t12_common import Graph, evaluate, proved_equal

TOOLS = "/home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/algo/othertools"


def signed(v, m):
    return v - (1 << m) if (v >> (m - 1)) & 1 else v


def export(case_dir, graph, work, max_targets=None):
    os.makedirs(work, exist_ok=False)
    G, index = Graph(graph), {}
    for idx, t in enumerate(G.targets(case_dir)):
        if max_targets is not None and idx >= int(max_targets):
            break
        vs = []
        for i, (name, v) in enumerate(sorted(t["syms"].items())):
            vs += [(f"u{i}", name, False), (f"s{i}", name, True)]
        lines = ["types", "e " + " ".join(f"{nm}:N" for nm, _, _ in vs) + " w:N", "n w:N", "trace"]
        for (vals, T), cur in zip(t["S"], t["S_cur"]):
            row = [str(signed(vals[sym], t["syms"][sym].size()) if sg else vals[sym]) for _, sym, sg in vs]
            lines += ["e " + " ".join(row + [str(cur)]), f"n {T}"]
        lines.append("trace")
        open(f"{work}/T{idx}.txt", "w").write("\n".join(lines) + "\n")
        index[idx] = {"word": t["word"], "phase": t["phase"], "n": t["n"], "samples": len(t["S"]),
                      "vars": {nm: [sym, sg] for nm, sym, sg in vs}}
    json.dump({"case_dir": case_dir, "graph": graph, "targets": index}, open(f"{work}/index.json", "w"))
    open(f"{work}/run_t2m.sh", "w").write(
        "#!/usr/bin/env bash\n# Original Trace2Model CLI (unmodified copy) with CVC4 1.8, as in the earlier Canny study.\n"
        f'W="$(cd "$(dirname "$0")" && pwd)"; H={TOOLS}; R=$W/tool\n'
        '[ -d $R ] || { mkdir -p $R && cp -r $H/dl/Trace2Model-master/. $R/; }\n'
        "cd $R\n"
        "for f in $W/T*.txt; do i=$(basename $f .txt); o=$W/out_$i.txt; [ -s $o ] && continue\n"
        "  PATH=$H/t2m/bin_orig:$PATH timeout 600 $H/t2m/venv/bin/python -I syn_event_update.py -i $f -v w:N -s cvc4 > $o.part 2>&1; mv $o.part $o\n"
        "done\n"
        'echo "T2M DONE $(ls $W/out_T*.txt | wc -l) outputs"\n')
    print(json.dumps({"targets": len(index), "work": work}))


def sparse(s):
    toks = re.findall(r"\(|\)|[^\s()]+", s)
    stack = [[]]
    for t in toks:
        if t == "(":
            stack.append([])
        elif t == ")":
            x = stack.pop(); stack[-1].append(x)
        else:
            stack[-1].append(t)
    return stack[0][0] if stack[0] else None


def do_import(case_dir, graph, work, out_json):
    index = json.load(open(f"{work}/index.json"))["targets"]
    G, stats, examples = Graph(graph), collections.Counter(), []
    for idx, t in enumerate(G.targets(case_dir)):
        mt = index.get(str(idx))
        if mt is None:
            break
        assert mt["word"] == t["word"] and mt["phase"] == t["phase"], "targets differ from the exported ones"
        stats["targets"] += 1
        of = f"{work}/out_T{idx}.txt"
        if not os.path.exists(of):
            stats["not_run"] += 1; continue
        txt = re.sub(r"\x1b\[[0-9;]*m", "", open(of).read())
        dicts = [line for line in txt.splitlines() if line.startswith("{'e'") or line.startswith('{"e"')]
        ev = ast.literal_eval(dicts[-1]).get("e") if dicts else None
        res = [e for e in (ev or []) if e] if isinstance(ev, list) else []
        if not res:
            stats["no_result"] += 1; continue
        n, V = t["n"], mt["vars"]
        wkey = f"word:{t['word']}"
        syms = dict(t["syms"])
        syms.setdefault(wkey, z3.BitVec(wkey, n))  # w = the word's current value (may be unused by the node)
        S = [(dict(vals, **{wkey: cur}), T) for (vals, T), cur in zip(t["S"], t["S_cur"])]
        Vs = [(dict(vals, **{wkey: cur}), T) for (vals, T), cur in zip(t["V"], t["V_cur"])]

        def opnd(nm):
            if nm == "w":
                return syms[wkey]
            sym, sg = V[nm]; v = syms[sym]; m = v.size()
            if m == n:
                return v
            if m > n:
                return z3.Extract(n - 1, 0, v)
            return z3.SignExt(n - m, v) if sg else z3.ZeroExt(n - m, v)

        def tf(x):
            if isinstance(x, str):
                return z3.BitVecVal(int(x) % (1 << n), n) if re.fullmatch(r"-?\d+", x) else opnd(x)
            if x[0] == "-" and len(x) == 2:
                return -tf(x[1])
            if x[0] in ("+", "-", "*"):
                e = tf(x[1])
                for y in x[2:]:
                    e = e + tf(y) if x[0] == "+" else e - tf(y) if x[0] == "-" else e * tf(y)
                return e
            raise ValueError("operator " + str(x[0]))
        try:
            cand = z3.simplify(tf(sparse(res[0].split("' = ", 1)[1])))
        except (ValueError, KeyError, IndexError):
            stats["untranslatable"] += 1; continue
        if any(evaluate(cand, syms, vals) != T for vals, T in S):
            stats["conversion_fail"] += 1; continue
        stats["accepted"] += 1
        eq = proved_equal(cand, t["cert"])
        if eq is None:
            stats["undecided"] += 1; continue
        if eq:
            continue
        stats["wrong"] += 1
        missed = [(vals, T) for vals, T in Vs if evaluate(cand, syms, vals) != T]
        if missed:
            stats["confirmed_any"] += 1
        hit = next(((vals, T) for vals, T in missed if evaluate(t["raw"], syms, vals) == T), None)
        if hit is not None:
            stats["confirmed"] += 1
            if len(examples) < 50:
                examples.append({"word": t["word"], "phase": t["phase"], "synthesised": res[0],
                                 "variant_observed": hit[1], "candidate_predicted": evaluate(cand, syms, hit[0])})
    res_ = {"case": case_dir.rstrip("/").split("/")[-1], "tool": "Trace2Model (original CLI) + CVC4 1.8",
            "stats": dict(stats), "confirmed_examples": examples}
    json.dump(res_, open(out_json, "w"), indent=1)
    print(json.dumps(res_)[:2000])


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "export":
        export(*sys.argv[2:6])
    elif mode == "import":
        do_import(*sys.argv[2:6])
    else:
        raise SystemExit(__doc__)
