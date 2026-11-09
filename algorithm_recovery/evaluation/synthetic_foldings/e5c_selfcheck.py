#!/usr/bin/env python3
"""Generator self-check: the generated RTL must compute its ground-truth DFG (Icarus, 200 samples, random inputs).
usage: e5c_selfcheck.py DESIGN_DIR      (needs iverilog/vvp)"""
import json, random, subprocess, sys

d = sys.argv[1]
T = json.load(open(f"{d}/truth.json"))
N, W, nodes, outputs = T["N"], T["width"], T["nodes"], T["outputs"]
M = (1 << W) - 1
S = 200
rng = random.Random(7)
xs = [(rng.getrandbits(W), rng.getrandbits(W)) for _ in range(S)]


def dfg_outputs():
    hist = []                      # per sample: {node: value}
    outs = []
    # evaluation order inside a sample: same-sample dependencies only (delays read history)
    order, done = [], set()
    while len(order) < len(nodes):
        for v, nd in sorted(nodes.items()):
            if v in done:
                continue
            if all(not (s in nodes and dl == 0) or s in done for s, dl in nd["operands"]):
                order.append(v); done.add(v)
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
        outs.append([val[o] for o in outputs])
    return outs


tb = [f"module tb; reg clk = 0, rst = 1; reg [{W-1}:0] x0 = 0, x1 = 0;"]
tb += [f"wire [{W-1}:0] y{i};" for i in range(len(outputs))]
tb.append("fold_top dut(.clk(clk), .rst(rst), .x0(x0), .x1(x1)" + "".join(f", .y{i}(y{i})" for i in range(len(outputs))) + ");")
tb.append("integer n, c; initial begin")
tb.append("  #1; clk = 1; #1; clk = 0; #1; clk = 1; #1; clk = 0; rst = 0;")   # two reset cycles
tb.append(f"  for (n = 0; n < {S + 1}; n = n + 1) begin")
tb.append(f"    for (c = 0; c < {N}; c = c + 1) begin")
tb.append("      #1;")
tb.append("      if (c == 0 && n > 0) $display(\"OUT %0d" + " %0d" * len(outputs) + "\", n - 1" + "".join(f", y{i}" for i in range(len(outputs))) + ");")
for i, (a, b) in enumerate(xs):
    pass
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
got = {int(l.split()[1]): [int(x) for x in l.split()[2:]] for l in res.splitlines() if l.startswith("OUT")}
ref = dfg_outputs()
bad = [n for n in range(S) if got.get(n) != ref[n]]
print(json.dumps({"design": d, "samples": S, "mismatches": len(bad), "first": bad[:1] and {"n": bad[0], "rtl": got.get(bad[0]), "dfg": ref[bad[0]]}}))
sys.exit(1 if bad else 0)
