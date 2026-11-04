#!/usr/bin/env python3
"""E8.2 generator self-check: the folded RTL must compute the kernel (kernels.evaluate, independent of the folding) on
random sample streams (Icarus). Inputs are held for the N cycles of a sample; outputs are read at the next sample's slot 0.
usage: e8_selfcheck.py DESIGN_DIR SAMPLES"""
import json, os, random, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kernels as KM

d, S = sys.argv[1], int(sys.argv[2])
T = json.load(open(f"{d}/truth.json"))
kern = KM.KERNELS[T["kernel"]]()
N, W, ins, outs = T["N"], T["width"], T["inputs"], T["output_names"]
rng = random.Random(7)
samples = [{i: rng.getrandbits(W) for i in ins} for _ in range(S)]
ref = KM.evaluate(kern, samples)
tb = [f"module tb; reg clk = 0, rst = 1; " + " ".join(f"reg [{W-1}:0] {i} = 0;" for i in ins)]
tb += [f"wire [{W-1}:0] {o};" for o in outs]
tb.append("fold_top dut(.clk(clk), .rst(rst)" + "".join(f", .{i}({i})" for i in ins) + "".join(f", .{o}({o})" for o in outs) + ");")
tb.append("integer n, c; initial begin")
tb.append("  #1; clk = 1; #1; clk = 0; #1; clk = 1; #1; clk = 0; rst = 0;")
tb.append(f"  for (n = 0; n < {S + 1}; n = n + 1) begin")
tb.append(f"    for (c = 0; c < {N}; c = c + 1) begin")
tb.append("      #1;")
tb.append("      if (c == 0 && n > 0) $display(\"OUT %0d" + " %0d" * len(outs) + "\", n - 1" + "".join(f", {o}" for o in outs) + ");")
tb.append("      case (n)")
for n, s in enumerate(samples):
    tb.append(f"        {n}: begin " + " ".join(f"{i} = {s[i]};" for i in ins) + " end")
tb.append("      endcase")
tb.append("      #1; clk = 1; #1; clk = 0;")
tb.append("    end")
tb.append("  end")
tb.append("  $finish; end endmodule")
open(f"{d}/selfcheck_tb.v", "w").write("\n".join(tb) + "\n")
subprocess.run(["iverilog", "-o", f"{d}/selfcheck.vvp", f"{d}/selfcheck_tb.v", f"{d}/fold_top.v"], check=True)
res = subprocess.run(["vvp", "-n", f"{d}/selfcheck.vvp"], capture_output=True, text=True, check=True).stdout
got = {int(l.split()[1]): [int(x) for x in l.split()[2:]] for l in res.splitlines() if l.startswith("OUT")}
exp = {n: [ref[n][v] for v in kern["outputs"].values()] for n in range(S)}
bad = [n for n in range(S) if got.get(n) != exp[n]]
print(json.dumps({"design": d, "samples": S, "mismatches": len(bad), "first": bad[:1] and {"n": bad[0], "rtl": got.get(bad[0]), "kernel": exp[bad[0]]}}))
sys.exit(1 if bad else 0)
