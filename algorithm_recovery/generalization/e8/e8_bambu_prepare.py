#!/usr/bin/env python3
"""E8.3 preparation: kernel C -> Bambu 2024.10 (HLS) -> Yosys synth_ice40 netlist, protocol measured on the Bambu RTL.

usage: e8_bambu_prepare.py KERNEL CLOCK_NS MULTS OUT_DIR [CASE]  (MULTS: an integer limit on ui_mult_expr_FU, or "inf";
       CASE: the case name in spec.json/observe.json, default the basename of OUT_DIR)
Bambu runs from the extracted AppImage with its bundled libraries and a private libtinfo.so.5 (build/_tools); the C is
kernels.to_c (no system headers). The RTL testbench (Icarus) holds the inputs for a transaction, pulses start_port in
its cycle 0 and records done_port and every <out>_vld; the transaction period is (last done cycle) + 2. A second run with
that fixed period checks every output against kernels.evaluate on a stream of transactions from the reset state.
Writes OUT_DIR/{kernel.c, kernel.v, net.json, spec.json, observe.json, meta.json}.
"""
import json, os, random, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kernels as KM

kname, clock, mults, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
case = sys.argv[5] if len(sys.argv) > 5 else os.path.basename(out.rstrip("/"))
os.makedirs(out, exist_ok=True)
H = "/home/jiahao/hal"
A = f"{H}/build/_tools/bambu-2024.10/squashfs-root"
TINFO = f"{H}/build/_tools/libtinfo5_dl/x/lib/x86_64-linux-gnu"
kern = KM.KERNELS[kname]()
open(f"{out}/kernel.c", "w").write(KM.to_c(kname, kern))
args = [f"{A}/usr/bin/bambu", f"--top-fname={kname}", "--generate-interface=INFER", "-O2", f"--clock-period={clock}", "-v1",
        "kernel.c"]
if mults != "inf":
    open(f"{out}/constraints.xml", "w").write(
        '<?xml version="1.0"?>\n<constraints>\n   <HLS_constraints>\n'
        f'      <tech_constraints fu_name="ui_mult_expr_FU" fu_library="STD_FU" n="{int(mults)}"/>\n'
        '   </HLS_constraints>\n</constraints>\n')
    args.append("constraints.xml")
env = dict(os.environ, APPDIR=A, PATH=f"{A}/usr/bin:" + os.environ["PATH"], LC_ALL="C",
           LD_LIBRARY_PATH=f"{A}/usr/lib:{A}/usr/lib/x86_64-linux-gnu:{TINFO}")
r = subprocess.run(args, cwd=out, env=env, capture_output=True, text=True, timeout=3600)
open(f"{out}/bambu.log", "w").write(r.stdout + r.stderr)
if r.returncode != 0 or not os.path.exists(f"{out}/{kname}.v"):
    raise SystemExit(f"bambu failed ({r.returncode})")
os.replace(f"{out}/{kname}.v", f"{out}/kernel.v")
lat = re.findall(r"(Minimum|Maximum) number of cycles:? (\d+)", r.stdout)
# Bambu maps static variables to 1-element memories initialised by $readmemb from files next to the Verilog: synthesis
# and simulation run in OUT_DIR, and -defer elaborates modules only with the instances' parameters (not the defaults)
subprocess.run(["yosys", "-q", "-l", "synth.log", "-p",
                f"read_verilog -defer kernel.v; synth_ice40 -top {kname} -nobram -json net.json; tee -o stat.txt stat"],
               check=True, capture_output=True, cwd=out)

W, ins, outs = KM.W, kern["inputs"], list(kern["outputs"])


def testbench(path, period, S, samples):
    tb = [f"module tb; reg clock = 0, reset = 0, start_port = 0; " + " ".join(f"reg [{W-1}:0] {i} = 0;" for i in ins),
          "wire done_port;"] + [f"wire [{W-1}:0] {o}; wire {o}_vld;" for o in outs]
    tb.append(f"{kname} dut(.clock(clock), .reset(reset), .start_port(start_port)" + "".join(f", .{i}({i})" for i in ins) +
              ", .done_port(done_port)" + "".join(f", .{o}({o}), .{o}_vld({o}_vld)" for o in outs) + ");")
    tb.append("integer n, c; initial begin")
    tb.append("  reset = 0; #1 clock = 1; #1 clock = 0; #1 clock = 1; #1 clock = 0; reset = 1;")
    tb.append(f"  for (n = 0; n < {S}; n = n + 1) begin")
    tb.append(f"    for (c = 0; c < {period}; c = c + 1) begin")
    tb.append("      case (n)")
    for n, s in enumerate(samples):
        tb.append(f"        {n}: begin " + " ".join(f"{i} = {s[i]};" for i in ins) + " end")
    tb.append("      endcase")
    tb.append("      start_port = (c == 0); #1;")
    tb.append("      if (done_port) $display(\"DONE %0d %0d\", n, c);")
    for o in outs:
        tb.append(f"      if ({o}_vld) $display(\"OUT {o} %0d %0d %0d\", n, c, {o});")
    tb.append("      clock = 1; #1 clock = 0;")
    tb.append("    end")
    tb.append("  end")
    tb.append("  $finish; end endmodule")
    open(path, "w").write("\n".join(tb) + "\n")
    subprocess.run(["iverilog", "-o", path + ".vvp", path, f"{out}/kernel.v"], check=True, capture_output=True, cwd=out)
    return subprocess.run(["vvp", "-n", path + ".vvp"], capture_output=True, text=True, check=True, cwd=out).stdout


rng = random.Random(11)
S = 40
samples = [{i: rng.getrandbits(W) for i in ins} for _ in range(S)]
res = testbench(f"{out}/tb_measure.v", 400, 4, samples)        # generous period: measure done and valid cycles
done = sorted({int(l.split()[2]) for l in res.splitlines() if l.startswith("DONE")})
vld = {}
for l in res.splitlines():
    if l.startswith("OUT"):
        _, o, n, c, _v = l.split()
        vld.setdefault(o, set()).add(int(c))
assert done and all(vld.get(o) for o in outs), f"no done/valid observed: {res[:400]}"
period = max(done) + 2
res = testbench(f"{out}/tb_check.v", period, S, samples)
ref = KM.evaluate(kern, samples)
got = {}
for l in res.splitlines():
    if l.startswith("OUT"):
        _, o, n, c, v = l.split()
        got.setdefault((o, int(n)), set()).add(int(v))
bad = [(o, n) for n in range(S) for o in outs if got.get((o, n)) != {ref[n][kern["outputs"][o]]}]
assert not bad, f"RTL does not compute the kernel at the measured period: {bad[:4]}"
spec = {"synth": f"{out}/net.json", "top": kname,
        "source": {"url": "e8/kernels.py via Bambu 2024.10", "commit": "see hal git log", "license": "n/a", "author": "E8.3",
                   "category": "HLS kernel", "parameters": f"kernel={kname} clock={clock}ns mults={mults} -O2"},
        "clock": "clock", "non_data": ["reset", "start_port"], "defaults": {"reset": 1, "start_port": 0},
        "prefix": [{"cycles": 2, "values": {"reset": 0}}], "period": period, "schedule": {"0": {"start_port": 1}},
        "data_mode": "hold", "case": case, "transactions": 128, "training_transactions": 64,
        "seed": 20261104}
json.dump(spec, open(f"{out}/spec.json", "w"), indent=1)
json.dump({spec["case"]: [{"port": o, "strobe": f"{o}_vld"} for o in outs]}, open(f"{out}/observe.json", "w"), indent=1)
stat = open(f"{out}/stat.txt").read()
meta = {"kernel": kname, "clock_ns": clock, "mults": mults, "period": period, "done_cycles": done,
        "valid_cycles": {o: sorted(v) for o, v in vld.items()}, "bambu_cycles": lat,
        "lut4": int((re.search(r"SB_LUT4\s+(\d+)", stat) or [0, 0])[1]),
        "ffs": sum(int(x) for x in re.findall(r"SB_DFF\w*\s+(\d+)", stat)), "rtl_check_transactions": S}
json.dump(meta, open(f"{out}/meta.json", "w"), indent=1)
print(json.dumps(meta))
