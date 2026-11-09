#!/usr/bin/env python3
"""E6 generator self-check: the generated RTL must compute its ground-truth DFG (Icarus, random inputs).
As e5c/e5c_selfcheck.py, with a linear-time evaluation order and outputs printed in chunks.
usage: e6_selfcheck.py DESIGN_DIR SAMPLES"""
import collections, json, random, subprocess, sys

d, S = sys.argv[1], int(sys.argv[2])
T = json.load(open(f"{d}/truth.json"))
N, W, nodes, outputs = T["N"], T["width"], T["nodes"], T["outputs"]
M = (1 << W) - 1
rng = random.Random(7)
xs = [(rng.getrandbits(W), rng.getrandbits(W)) for _ in range(S)]

# same-sample dependencies only (Kahn); delays read history
indeg = {v: 0 for v in nodes}
succ = collections.defaultdict(list)
for v, nd in nodes.items():
    for s, dl in nd["operands"]:
        if s in nodes and dl == 0:
            indeg[v] += 1
            succ[s].append(v)
order = [v for v in nodes if indeg[v] == 0]
for v in order:
    for w in succ[v]:
        indeg[w] -= 1
        if indeg[w] == 0:
            order.append(w)
assert len(order) == len(nodes), "same-sample cycle"

hist, ref = [], []
for n in range(S):
    val = {}
    for v in order:
        nd = nodes[v]

        def get(src, dl):
            if src == "x0":
                return xs[n][0]
            if src == "x1":
                return xs[n][1]
            if dl == 0:
                return val[src]
            return hist[n - dl][src] if n - dl >= 0 else 0
        a = get(*nd["operands"][0])
        if nd["op"] == "mulc":
            r = a * nd["const"]
        else:
            b = get(*nd["operands"][1])
            r = {"add": a + b, "sub": a - b, "xor": a ^ b, "and": a & b}[nd["op"]]
        val[v] = r & M
    hist.append(val)
    if n >= 3:
        hist[n - 3] = None   # delays are at most 2
    ref.append([val[o] for o in outputs])

C = 64
tb = [f"module tb; reg clk = 0, rst = 1; reg [{W-1}:0] x0 = 0, x1 = 0;"]
tb += [f"wire [{W-1}:0] y{i};" for i in range(len(outputs))]
tb.append("fold_top dut(.clk(clk), .rst(rst), .x0(x0), .x1(x1)" + "".join(f", .y{i}(y{i})" for i in range(len(outputs))) + ");")
tb.append("integer n, c; initial begin")
tb.append("  #1; clk = 1; #1; clk = 0; #1; clk = 1; #1; clk = 0; rst = 0;")   # two reset cycles
tb.append(f"  for (n = 0; n < {S + 1}; n = n + 1) begin")
tb.append(f"    for (c = 0; c < {N}; c = c + 1) begin")
tb.append("      #1;")
tb.append("      if (c == 0 && n > 0) begin")
for k in range(0, len(outputs), C):
    idx = range(k, min(k + C, len(outputs)))
    tb.append(f"        $display(\"OUT %0d {k}" + " %0d" * len(idx) + "\", n - 1" + "".join(f", y{i}" for i in idx) + ");")
tb.append("      end")
tb.append("      case (n)")
for i, (a, b) in enumerate(xs):
    tb.append(f"        {i}: begin x0 = {a}; x1 = {b}; end")
tb.append("      endcase")
tb.append("      #1; clk = 1; #1; clk = 0;")
tb.append("    end")
tb.append("  end")
tb.append("  $finish; end endmodule")
open(f"{d}/selfcheck_tb.v", "w").write("\n".join(tb) + "\n")
subprocess.run(["iverilog", "-o", f"{d}/selfcheck.vvp", f"{d}/selfcheck_tb.v", f"{d}/fold_top.v"], check=True)
res = subprocess.run(["vvp", "-n", f"{d}/selfcheck.vvp"], capture_output=True, text=True, check=True).stdout
got = collections.defaultdict(dict)
for l in res.splitlines():
    if l.startswith("OUT"):
        f = l.split()
        n, k = int(f[1]), int(f[2])
        for j, x in enumerate(f[3:]):
            got[n][k + j] = int(x)
bad = [n for n in range(S) if [got[n].get(i) for i in range(len(outputs))] != ref[n]]
print(json.dumps({"design": d, "samples": S, "outputs": len(outputs), "mismatches": len(bad)}))
sys.exit(1 if bad else 0)
