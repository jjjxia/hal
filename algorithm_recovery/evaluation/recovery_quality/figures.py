#!/usr/bin/env python3
"""pgfplots figures and tables of the recovery-quality results (data from the JSON files of this directory).
usage: figures.py OUT_DIR      -> OUT_DIR/{folding_strength,cost,cost_fold,program_size}.tex (tikzpicture),
  {structure,funnel,loop,operator}_table.tex (tabular), preview_*.tex (standalone) and preview_all.tex (llncs)
Colours: categorical slots 1-3 of the dataviz reference palette (validated, light mode); every series also has its own
marker or fill pattern, so the figures read in black and white."""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
COL = {"NetNotary": "2A78D6", "B0": "1BAF7A", "B1": "EB6834"}
PRE = r"""\definecolor{nn}{HTML}{%s}\definecolor{bz}{HTML}{%s}\definecolor{bo}{HTML}{%s}
\definecolor{ink}{HTML}{52514E}\definecolor{grid}{HTML}{E4E3DF}""" % (COL["NetNotary"], COL["B0"], COL["B1"])
AX = r"""width=5.9cm, height=3.6cm, ymin=0, ymax=1.05, ytick={0,0.5,1}, grid=major, grid style={grid, line width=0.3pt},
axis line style={ink, line width=0.4pt}, tick style={ink}, tick label style={font=\scriptsize}, label style={font=\scriptsize},
every axis plot/.append style={line width=1pt, mark size=1.6pt}, clip=false"""


def series(pop, levels, key):
    A = json.load(open(f"{HERE}/r2_folding.json"))["aggregate"]
    return [(i, A[f"{pop} {lv}"][key]) for i, lv in enumerate(levels) if f"{pop} {lv}" in A and A[f"{pop} {lv}"][key] is not None]


def outcome_shares(pop, levels):
    A = json.load(open(f"{HERE}/r2_folding.json"))["aggregate"]
    out = []
    for i, lv in enumerate(levels):
        a = A.get(f"{pop} {lv}")
        if not a:
            continue
        n = a["designs"]; c = a["classes"]
        exact = c.get("exact", 0); front = sum(v for k, v in c.items() if k.startswith("frontend"))
        out.append((i, exact / n, front / n, (n - exact - front) / n, n))
    return out


def panel(pop, levels, labels, title, legend_on=False):
    coords = lambda pts: " ".join(f"({x},{y})" for x, y in pts)
    nn = series(pop, levels, "NetNotary_correct_words_structural_share")
    b0 = series(pop, levels, "B0_exact_node_share")
    b1 = series(pop, levels, "B1_exact_node_share")
    xt = ",".join(str(i) for i in range(len(levels)))
    xl = ",".join(labels)
    last = len(levels) - 1
    lab = lambda pts, name, col, dy: (rf"\node[font=\scriptsize, text={col}, anchor=west] at (axis cs:{pts[-1][0]},{pts[-1][1]}) [yshift={dy}pt] {{{name}}};"
                                      if pts else "")
    legend = (r"\legend{NetNotary, cycle view (B0), register view (B1)}" if legend_on else "")
    lstyle = ("legend columns=3, legend style={at={(1.06,1.32)}, anchor=south, draw=none, font=\\scriptsize, column sep=6pt}," if legend_on else "")
    top = rf"""\begin{{axis}}[{lstyle}{AX}, name=top{pop}, title={{\scriptsize {title}}}, title style={{yshift=-1.5ex}},
  xmin=-0.3, xmax={last}+0.3, xtick={{{xt}}}, xticklabels={{}}, ylabel={{exact nodes}}]
\addplot[nn, mark=square*] coordinates {{{coords(nn)}}};
\addplot[bz, mark=triangle*] coordinates {{{coords(b0)}}};
\addplot[bo, mark=*] coordinates {{{coords(b1)}}};
{legend}
\end{{axis}}"""
    sh = outcome_shares(pop, levels)
    counts = {t[0]: t[4] for t in sh}
    xl = ",".join("{%s\\\\[-1pt]\\tiny %s}" % (l, counts.get(i, "")) for i, l in enumerate(labels))
    bars = lambda k: " ".join(f"({x},{s[k]})" for x, *s in [(t[0], *t[1:]) for t in sh] for _ in [0]) if sh else ""
    ex = " ".join(f"({t[0]},{t[1]})" for t in sh)
    fr = " ".join(f"({t[0]},{t[2]})" for t in sh)
    ot = " ".join(f"({t[0]},{t[3]})" for t in sh)
    ns = ""
    blegend = ("legend columns=3, area legend, legend style={at={(1.06,-0.62)}, anchor=north, draw=none, font=\\scriptsize, column sep=6pt}," if legend_on else "")
    bottom = rf"""\begin{{axis}}[{AX}, height=2.6cm, at={{(top{pop}.south west)}}, anchor=north west, yshift=-1mm,
  xmin=-0.3, xmax={last}+0.3, xtick={{{xt}}}, xticklabels={{{xl}}}, xticklabel style={{align=center}}, ylabel={{designs}},
  ybar stacked, bar width=7pt, {blegend}
  every axis plot/.append style={{line width=0pt, draw=white}}]
\addplot[fill=nn] coordinates {{{ex}}};
\addplot[fill=ink!35, postaction={{pattern=north east lines, pattern color=ink}}] coordinates {{{fr}}};
\addplot[fill=ink!80] coordinates {{{ot}}};
{(r"\legend{exact at gate level, word grouping failed, other}" if legend_on else "")}
\end{{axis}}"""
    return top, bottom


def folding_strength():
    t1, b1 = panel("R2a", ["2", "4", "8", "16", "32"], ["2", "4", "8", "16", "32"], "random graphs ($N$)", legend_on=True)
    t2, b2 = panel("R2b", ["min", "2min", "4min", "8min"], ["1", "2", "4", "8"], r"kernels ($N/N_{\min}$)")
    body = rf"""{PRE}
\begin{{tikzpicture}}
\begin{{scope}}
{t1}
{b1}
\end{{scope}}
\begin{{scope}}[xshift=6.6cm]
{t2}
{b2}
\end{{scope}}
\end{{tikzpicture}}"""
    open(f"{OUT}/folding_strength.tex", "w").write(body + "\n")
    return body


def preview(name, body):
    open(f"{OUT}/preview_{name}.tex", "w").write(r"""\documentclass[border=2pt]{standalone}
\usepackage{pgfplots}\usepgfplotslibrary{groupplots}\usetikzlibrary{patterns}\pgfplotsset{compat=1.17}
\begin{document}
""" + body + "\n\\end{document}\n")


def cost():
    C = json.load(open(f"{HERE}/r4_cost.json"))
    groups = [(("frontend",), "word grouping", "ink", "square*"), (("certify", "graph"), "certificate + graph", "bo", "triangle*"),
              (("program", "check"), "program + checks", "nn", "*")]
    plots = []
    for keys, name, col, mk in groups:
        pts = [(r["flip_flops"], sum(r["seconds"].get(k, 0) for k in keys)) for r in C["rows"]
               if r.get("flip_flops") and "program" in r["seconds"] and sum(r["seconds"].get(k, 0) for k in keys) > 0]
        plots.append(rf"\addplot[only marks, mark={mk}, mark size=1.4pt, color={col}] coordinates {{{' '.join(f'({x},{y})' for x, y in pts)}}};")
    stages = groups
    left = rf"""\begin{{axis}}[name=cl, width=6.2cm, height=4.6cm, xmode=log, ymode=log, grid=major, grid style={{grid, line width=0.3pt}},
  axis line style={{ink, line width=0.4pt}}, tick label style={{font=\scriptsize}}, label style={{font=\scriptsize}},
  xlabel={{flip-flops}}, ylabel={{seconds per stage}}, legend columns=3, legend style={{at={{(1.08,1.04)}}, anchor=south, draw=none,
  font=\scriptsize, column sep=4pt}}]
{chr(10).join(plots)}
\legend{{{", ".join(s[1] for s in stages)}}}
\end{{axis}}"""
    e6 = C["e6"]
    eplots = []
    for n, col, mk in ((4, "nn", "square*"), (16, "bz", "triangle*"), (64, "bo", "*")):
        pts = sorted((r["K"], r["fold_seconds"]) for r in e6 if r["N"] == n)
        eplots.append(rf"\addplot[color={col}, mark={mk}, line width=1pt, mark size=1.6pt] coordinates {{{' '.join(f'({x},{y})' for x, y in pts)}}};")
    sl = C["e6_loglog_slope_fold_seconds_vs_K"]
    right = rf"""\begin{{axis}}[at={{(cl.south east)}}, anchor=south west, xshift=1.3cm, width=6.2cm, height=4.6cm, xmode=log, ymode=log,
  grid=major, grid style={{grid, line width=0.3pt}}, axis line style={{ink, line width=0.4pt}}, tick label style={{font=\scriptsize}},
  label style={{font=\scriptsize}}, xlabel={{DFG nodes $K$ (E6, equations from RTL)}}, ylabel={{inverse folding (s)}},
  legend pos=north west, legend style={{draw=none, font=\scriptsize, fill=white, fill opacity=0.8, text opacity=1}}]
{chr(10).join(eplots)}
\legend{{$N=4$ ({sl['4']:.2f}), $N=16$ ({sl['16']:.2f}), $N=64$ ({sl['64']:.2f})}}
\end{{axis}}"""
    body = rf"""{PRE}
\begin{{tikzpicture}}
{left}
{right}
\end{{tikzpicture}}"""
    open(f"{OUT}/cost.tex", "w").write(body + "\n")
    alone = right.replace("at={(cl.south east)}, anchor=south west, xshift=1.3cm, width=6.2cm", "width=7cm")
    assert alone != right
    open(f"{OUT}/cost_fold.tex", "w").write(f"{PRE}\n\\begin{{tikzpicture}}\n{alone}\n\\end{{tikzpicture}}\n")
    return body


def structure_table():
    A = json.load(open(f"{HERE}/r1_structure.json"))["aggregate"]
    pops = [("E5.C", "random"), ("E8.2", "kernels"), ("E6", "large")]
    f = lambda x: "1" if x == 1 else ("0" if x == 0 else f"{x:.2f}".lstrip("0"))
    rows = []
    for view, name in (("B0", "cycle view"), ("B1", "register view"), ("NetNotary", "NetNotary")):
        for k, (lab, key) in enumerate((("node recall", "M1_node_recall"), ("node precision", "M2_node_precision"),
                                        ("edge recall", "M3_edge_recall"), ("edge precision", "M4_edge_precision"),
                                        ("delay recovery", "M5_delay_recovery"))):
            rows.append((name if k == 0 else "", lab, [f(A[p][view][key]) for p, _ in pops]))
    head = " & ".join(f"{lab} ({A[p]['designs']})" for p, lab in pops)
    body = "\n".join(f"{a} & {b} & " + " & ".join(c) + r"\\" + (r"\midrule" if b == "delay recovery" and i < len(rows) - 1 else "")
                     for i, (a, b, c) in enumerate(rows))
    tex = rf"""\begin{{tabular}}{{@{{}}llrrr@{{}}}}\toprule
View & Measure & {head}\\\midrule
{body}
\bottomrule\end{{tabular}}"""
    open(f"{OUT}/structure_table.tex", "w").write(tex + "\n")
    return tex



TEX = lambda s: s.replace("_", r"\_")
E83 = re.compile(r"_c\d+_(m\d+|minf)$")      # E8.3 Bambu kernels: shown in aggregate, not per design


def program_size():
    """R3c: lines of the flat, rolled and lifted program of each library design (E8.3 kernels left out)."""
    L = json.load(open(f"{HERE}/r3_programs.json"))["library"]
    rows = sorted((r for r in L if not E83.search(r["design"])), key=lambda r: r["lines"][0])
    pts = lambda k: " ".join(f"({r['lines'][k]},{i})" for i, r in enumerate(rows))
    spans = "\n".join(rf"\draw[ink!45, line width=0.5pt] (axis cs:{min(r['lines'])},{i}) -- (axis cs:{max(r['lines'])},{i});"
                      for i, r in enumerate(rows))
    names = ",".join(TEX(r["design"]) for r in rows)
    body = rf"""{PRE}
\begin{{tikzpicture}}
\begin{{axis}}[scale only axis, width=8.6cm, height={0.29 * len(rows):.2f}cm, xmode=log, xmin=40, xmax=6e5,
  ymin=-0.7, ymax={len(rows) - 0.3}, ytick={{0,...,{len(rows) - 1}}}, yticklabels={{{names}}},
  xmajorgrids, grid style={{grid, line width=0.3pt}}, axis line style={{ink, line width=0.4pt}}, tick style={{ink}},
  tick label style={{font=\scriptsize}}, label style={{font=\scriptsize}}, xlabel={{program lines (log scale)}},
  legend columns=3, legend style={{at={{(0.5,1.01)}}, anchor=south, draw=none, font=\scriptsize, column sep=6pt}}]
{spans}
\addplot[only marks, mark=o, mark size=1.7pt, ink!70] coordinates {{{pts(0)}}};
\addplot[only marks, mark=triangle*, mark size=1.9pt, ink] coordinates {{{pts(1)}}};
\addplot[only marks, mark=square*, mark size=1.5pt, nn] coordinates {{{pts(2)}}};
\legend{{flat (one transaction unrolled), rolled (proved loops), lifted (word level)}}
\end{{axis}}
\end{{tikzpicture}}"""
    open(f"{OUT}/program_size.tex", "w").write(body + "\n")
    return body


# R3a loop table: rows by design family; designs of a family share a row, values differing between them are listed
# in the family's design order. Every Annex A design must appear (qdiv of the frozen eight is the E5 qdiv at N = 32).
FAMILIES = [("aes_oc", ["aes_oc"]), ("aes_sw128, aes_sw_core", ["aes_sw128", "aes_sw_core"]),
            ("present, present_saiedhk", ["present", "present_saiedhk"]), ("ascon", ["ascon"]), ("sha1", ["sha1"]),
            ("sha256 lattice / sw / yosys / oc", ["sha256_lattice", "sha256_sw", "sha256_yosys_top", "sha256_oc"]),
            ("cordic", ["cordic"]), ("icrc1", ["icrc1"]), ("pcpi_div", ["pcpi_div"]), ("ibex_mul", ["ibex_mul"]),
            ("qmults $N$ = 16 / 32 / 64", ["qmults_n16", "qmults", "qmults_n64"]),
            ("qdiv $N$ = 16 / 32 / 64", ["qdiv_n16", "qdiv", "qdiv_n64"]),
            ("hb_dec / fir_decim / small_hb_dec", ["hb_dec", "fir_decim", "small_hb_dec_r2"])]
SAME_AS = {"qdiv (frozen)": "qdiv"}


def loop_table():
    A = json.load(open(f"{HERE}/r3a_annex.json"))["designs"]
    listed = {d for _, ds in FAMILIES for d in ds}
    assert {d for d, ls in A.items() if ls} - listed <= set(SAME_AS), set(A) - listed     # loop-free designs need no row
    rec = lambda l: "+".join(map(str, l["new"])) if l["new"] else "--"
    one = lambda vals: vals[0] if len(set(vals)) == 1 else " / ".join(vals)
    rows = []
    for fam, ds in FAMILIES:
        loops = [A[d] for d in ds]
        if all(l.get("unaligned") for ls in loops for l in ls):         # Ascon: rounds inside FSM-spanning loops
            ls = loops[0]
            rows.append((fam, f"{len(ls)} permutations", ", ".join(str(l["trip"]) for l in ls), "1",
                         f"{sum(min(sum(l['new']), l['trip']) for l in ls)} of {sum(l['trip'] for l in ls)} rounds "
                         r"in 2 FSM-spanning loops$^b$"))
            continue
        for j in range(len(loops[0])):
            col = [ls[j] for ls in loops]
            rows.append((fam if j == 0 else "", col[0]["loop"], one([str(l["trip"]) for l in col]),
                         one([str(l["cpi"]) for l in col]),
                         one([rec(l) for l in col]) + (r"$^a$" if col[0]["cpi"] > 1 and col[0]["new"] else "")
                         + (r"$^c$" if col[0].get("below_threshold") else "")))
    allL = [l for ls in A.values() for l in ls]
    l1 = sum(1 for l in allL if l["new"] and not l.get("unaligned"))
    l1_old = sum(1 for l in allL if l["old"] and not l.get("unaligned"))
    l2 = sum(min(sum(l["new"]), l["trip"]) for l in allL); T = sum(l["trip"] for l in allL)
    l2_old = sum(min(sum(l["old"]), l["trip"]) for l in allL)
    body = "\n".join(" & ".join(TEX(c) if k < 2 else c for k, c in enumerate(r)) + r"\\" for r in rows)
    tex = rf"""\begin{{tabular}}{{@{{}}>{{\raggedright}}p{{2.7cm}}>{{\raggedright}}p{{3.5cm}}lc>{{\raggedright\arraybackslash}}p{{2.8cm}}@{{}}}}\toprule
Design & Algorithm loop & Iterations & Cycles & Recovered loops\\\midrule
{body}
\midrule
\multicolumn{{5}}{{@{{}}l}}{{{len(allL)} loops in {len(A)} designs. Found: {l1} ({l1_old} with one-step loop bodies only).}}\\
\multicolumn{{5}}{{@{{}}l}}{{Iterations inside recovered loops: {l2} of {T} ({l2_old} with one-step loop bodies only).}}\\
\bottomrule\end{{tabular}}"""
    open(f"{OUT}/loop_table.tex", "w").write(tex + "\n")
    return tex


STOP = {"frontend-resource": "memory", "frontend-refused": "refused", "frontend-wrong": "wrong words",
        "function-unknown": "mult.\\ unproved", "truth-dead": "dead node", "performance": "timeout",
        "defect": "defect$^a$", "applicability": "no flip-flops"}
LIB_POPS = [("E5", "E5 open designs"), ("E7", "E7 vendor synthesis"), ("frozen eight", "frozen eight"),
            ("E8.3", "E8.3 HLS kernels")]
TRUTH_POPS = [("E5.C", "E5.C random graphs"), ("R2a", "R2a random, $N$ sweep"),
              ("E8.2", r"E8.2 kernels $1\times$, $2\times$"), ("R2b", r"R2b kernels $4\times$, $8\times$")]
EXPECTED = {"R2b": 32}


def funnel_table():
    F = json.load(open(f"{HERE}/r4_funnel.json"))
    A = F["aggregate"]
    stops = lambda a: ", ".join(f"{n} {STOP[k.split(': ')[1]]}" for k, n in sorted(a["stopped"].items(), key=lambda x: -x[1]))
    lib = []
    for p, name in LIB_POPS:
        a = A[p]; r = a["reached"]
        lib.append(f"{name} & {a['designs']} & {r['words']} & {r['program']} & {r['closed']} & {r['reference']} & {stops(a)}\\\\")
    tru = []
    for p, name in TRUTH_POPS:
        a = A[p]; r = a["reached"]
        fails = [x for x in F["rows"] if x["population"] == p and (x["class"] or "").startswith("frontend")]
        rw = f"{sum(1 for x in fails if x['rtl_words_exact'])}/{len(fails)}" if fails else "--"
        n = a["designs"]
        lab = name + (r"$^b$" if n < EXPECTED.get(p, n) else "")
        tru.append(f"{lab} & {n} & {r['words']} & {r['graph']} & {r['exact']} & {rw} & {stops(a)}\\\\")
    tex = rf"""\begin{{tabular}}{{@{{}}lrrrrr>{{\raggedright\arraybackslash}}p{{3.5cm}}@{{}}}}\toprule
Population & Designs & Words & Program & Closed & Ref.\ & Stopped\\\midrule
{chr(10).join(lib)}
\midrule
 & Designs & Words & Graph & Exact & RTL words & Stopped\\\midrule
{chr(10).join(tru)}
\bottomrule\end{{tabular}}"""
    open(f"{OUT}/funnel_table.tex", "w").write(tex + "\n")
    return tex


def operator_table():
    A = json.load(open(f"{HERE}/r3_programs.json"))["aggregate"]
    pops = [("E5.C", "random graphs"), ("R2a", "$N$ sweep"), ("E8.2", "HLS kernels")]
    ops = [("add", "$a+b$"), ("sub", "$a-b$"), ("addc", "$a+c$"), ("mulc", r"$c\cdot a$"), ("mul", r"$a\cdot b$"),
           ("xor", r"$a\oplus b$"), ("and", r"$a\wedge b$")]
    cell = lambda p, o: A[p]["operators_identified"].get(o, "--")
    body = "\n".join(f"{o} & {sym} & " + " & ".join(cell(p, o) for p, _ in pops) + r"\\" for o, sym in ops)
    tex = rf"""\begin{{tabular}}{{@{{}}llrrr@{{}}}}\toprule
Operator & & {' & '.join(f'{lab} ({A[p]["programs"]})' for p, lab in pops)}\\\midrule
{body}
\midrule
all & & {' & '.join(A[p]['identified_total'] for p, _ in pops)}\\
\bottomrule\end{{tabular}}"""
    open(f"{OUT}/operator_table.tex", "w").write(tex + "\n")
    return tex


ALL = r"""\documentclass{llncs}
\usepackage{array,booktabs,pgfplots}\usepgfplotslibrary{groupplots}\usetikzlibrary{patterns}\pgfplotsset{compat=1.17}
\begin{document}
\begin{table}\centering\scriptsize
\caption{Three views of the same certified equations (R1). B0 has one node per register and cycle, B1 one node per
register. Designs whose recovery is structure exact.}
%(structure)s
\end{table}
\begin{figure}\centering
%(folding)s
\caption{Folding strength (R2). Top: share of DFG nodes recovered exactly, when the words are right. Bottom: gate-level
outcome per design.}
\end{figure}
\begin{table}\centering\scriptsize
\caption{Coverage (R4a). Top: library designs, from netlist to closed program. Bottom: generated designs with a known
DFG. RTL words: word-grouping failures that are exact with the words of the RTL. Memory, refused: word grouping ran
out of memory or refused the netlist. $^a$Closes with the current generator. $^b$%(r2b)s}
%(funnel)s
\end{table}
\begin{figure}\centering
%(size)s
\caption{Program size per library design (R3c). Rolling shrinks the flat program wherever the algorithm has a
loop. Lifting replaces bit-level terms by word arithmetic, routes and tables.}
\end{figure}
\begin{table}\centering\scriptsize
\caption{Algorithm loops against the source (R3a). $^a$Multi-step loop body. $^b$Not aligned with a permutation.
$^c$Below the 3 iterations roll-up needs.}
%(loops)s
\end{table}
\begin{table}\centering\scriptsize
\caption{Operators identified as proved word-level arithmetic (R3d), DFG nodes of the structure-exact designs.}
%(ops)s
\end{table}
\begin{figure}\centering
%(cost)s
\caption{Cost (R4). Left: seconds per stage against design size. Right: inverse folding alone on RTL equations;
log-log slope in parentheses.}
\end{figure}
\end{document}
"""


if __name__ == "__main__":
    parts = {"structure": structure_table(), "folding": folding_strength(), "funnel": funnel_table(),
             "size": program_size(), "loops": loop_table(), "ops": operator_table(), "cost": cost()}
    n = json.load(open(f"{HERE}/r4_funnel.json"))["aggregate"]["R2b"]["designs"]
    parts["r2b"] = (f"{n} of {EXPECTED['R2b']} designs run so far." if n < EXPECTED["R2b"] else "")
    preview("folding_strength", parts["folding"])
    preview("cost", parts["cost"])
    open(f"{OUT}/preview_all.tex", "w").write(ALL % parts)
