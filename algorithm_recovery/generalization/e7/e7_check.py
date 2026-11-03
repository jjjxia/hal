#!/usr/bin/env python3
"""E7 preparation check: the lowered vendor netlist must behave as the secworks sha256 RTL it was synthesized from,
and the transaction timing is measured on the RTL (before any recovery run).

usage: e7_check.py LOWERED.json RTL_DIR OUT_DIR TRANSACTIONS
Stimulus per transaction (register interface): 16 writes of random 32-bit block words (addresses 0x10..0x1f), one
control write (address 0x08, data 0b101: init, SHA-256 mode), status reads (address 0x09) until digest_valid has
fallen and risen again, then
8 digest reads (0x20..0x27); idle cycles drive random address/data with cs = 0. Both the RTL and the lowered netlist
(Icarus, Yosys iCE40 cells_sim.v) are simulated; read_data is compared in every cycle, and each digest is compared with
a Python SHA-256 compression of the written block from the standard initial value.
"""
import json, os, random, struct, subprocess, sys

low, rtl_dir, out, T = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
# --fixed PERIOD READ0: the spec's fixed schedule (writes in cycles 0..16, digest reads in cycles READ0..READ0+7,
# PERIOD cycles per transaction, no idle cycles in between); block words then have their low 3 bits 0
FIXED = "--fixed" in sys.argv
if FIXED:
    PERIOD, READ0 = int(sys.argv[sys.argv.index("--fixed") + 1]), int(sys.argv[sys.argv.index("--fixed") + 2])
    FIXED_WAIT = READ0 - 17
    assert READ0 + 8 == PERIOD
os.makedirs(out, exist_ok=True)
CELLS = "/usr/share/yosys/ice40/cells_sim.v"
subprocess.run(["yosys", "-q", "-p", f"read_json {low}; rename sha256 sha256_net; write_verilog -noattr {out}/net.v"], check=True)

K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
     0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
     0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
     0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
     0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
     0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
     0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]
IV = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
M32 = 0xffffffff
ror = lambda x, n: ((x >> n) | (x << (32 - n))) & M32


def compress(words):
    w = list(words)
    for t in range(16, 64):
        s0 = ror(w[t - 15], 7) ^ ror(w[t - 15], 18) ^ (w[t - 15] >> 3)
        s1 = ror(w[t - 2], 17) ^ ror(w[t - 2], 19) ^ (w[t - 2] >> 10)
        w.append((w[t - 16] + s0 + w[t - 7] + s1) & M32)
    a, b, c, d, e, f, g, h = IV
    for t in range(64):
        t1 = (h + (ror(e, 6) ^ ror(e, 11) ^ ror(e, 25)) + ((e & f) ^ (~e & g)) + K[t] + w[t]) & M32
        t2 = ((ror(a, 2) ^ ror(a, 13) ^ ror(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) & M32
        a, b, c, d, e, f, g, h = (t1 + t2) & M32, a, b, c, (d + t1) & M32, e, f, g
    return [(x + y) & M32 for x, y in zip(IV, [a, b, c, d, e, f, g, h])]


rng = random.Random(20261104)
blocks = [[rng.getrandbits(32) & (~7 if FIXED else -1) for _ in range(16)] for _ in range(T)]
tb = ["`timescale 1ns/1ps", "module tb;", "reg clk = 0, reset_n = 0, cs = 0, we = 0; reg [7:0] address = 0; reg [31:0] write_data = 0;",
      "wire [31:0] rd_rtl, rd_net; wire err_rtl, err_net;",
      "sha256 rtl(.clk(clk), .reset_n(reset_n), .cs(cs), .we(we), .address(address), .write_data(write_data), .read_data(rd_rtl), .error(err_rtl));",
      "sha256_net net(.clk(clk), .reset_n(reset_n), .cs(cs), .we(we), .address(address), .write_data(write_data[31:3]), "
      ".write_data_ctl(write_data[2:0]), .read_data(rd_net), .error(err_net));",
      "integer cyc = 0, mism = 0, known = 0, t, i, n, wait_c;",
      "task step; begin #1; if (rd_rtl !== rd_net || err_rtl !== err_net) begin mism = mism + 1; if (mism < 5) $display(\"MISMATCH cyc=%0d addr=%h rtl=%h net=%h\", cyc, address, rd_rtl, rd_net); end"
      " else if (^rd_rtl !== 1'bx) known = known + 1; #4 clk = 1; #5 clk = 0; cyc = cyc + 1; end endtask",
      "task idle; begin cs = 0; we = 0; address = $random; write_data = $random; step; end endtask",
      "initial begin", "  reset_n = 0; step; step; reset_n = 1;"]
for n, blk in enumerate(blocks):
    for i, wd in enumerate(blk):
        tb.append(f"  cs = 1; we = 1; address = 8'h{0x10 + i:02x}; write_data = 32'h{wd:08x}; step;")
    tb.append(f"  cs = 1; we = 1; address = 8'h08; write_data = 32'h00000005; step; $display(\"CTRL %0d %0d\", {n}, cyc);")
    if FIXED:   # fixed protocol timing (spec): idle until cycle READ0 of the transaction, then 8 digest reads
        tb.append(f"  repeat ({FIXED_WAIT}) idle;")
    else:
        tb.append("  wait_c = 0; cs = 1; we = 0; address = 8'h09; write_data = $random; #1;"
                  " while (rd_rtl[1] !== 1'b0) begin step; wait_c = wait_c + 1; #1; end"
                  " while (rd_rtl[1] !== 1'b1) begin step; wait_c = wait_c + 1; #1; end")
    tb.append(f"  $display(\"VALID {n} %0d\", cyc);")
    for i in range(8):
        tb.append(f"  cs = 1; we = 0; address = 8'h{0x20 + i:02x}; #1; $display(\"DIGEST {n} {i} %h %h\", rd_rtl, rd_net); step;")
    if not FIXED:
        tb.append("  idle; idle;")
tb.append('  $display("DONE cycles=%0d known=%0d mismatches=%0d", cyc, known, mism); $finish; end endmodule')
open(f"{out}/check_tb.v", "w").write("\n".join(tb) + "\n")
src = [f"{rtl_dir}/{f}" for f in ("sha256.v", "sha256_core.v", "sha256_w_mem.v", "sha256_k_constants.v")]
subprocess.run(["iverilog", "-g2012", "-o", f"{out}/check.vvp", f"{out}/check_tb.v", f"{out}/net.v", CELLS] + src, check=True)
res = subprocess.run(["vvp", "-n", f"{out}/check.vvp"], capture_output=True, text=True, check=True).stdout
open(f"{out}/check.log", "w").write(res)
ctrl = {int(l.split()[1]): int(l.split()[2]) for l in res.splitlines() if l.startswith("CTRL")}
valid = {int(l.split()[1]): int(l.split()[2]) for l in res.splitlines() if l.startswith("VALID")}
dig = {}
for l in res.splitlines():
    if l.startswith("DIGEST"):
        _, n, i, a, b = l.split()
        dig.setdefault(int(n), {})[int(i)] = (int(a, 16), int(b, 16))
ref_ok = sum(all(dig[n][i][0] == compress(blocks[n])[i] for i in range(8)) for n in range(T))
net_ok = sum(all(dig[n][i][1] == compress(blocks[n])[i] for i in range(8)) for n in range(T))
done = [l for l in res.splitlines() if l.startswith("DONE")][0]
lat = sorted({valid[n] - ctrl[n] for n in range(T)}) if valid else None
r = {"transactions": T, "rtl_digests_equal_reference": ref_ok, "netlist_digests_equal_reference": net_ok,
     "cycle_compare": done, "ctrl_to_digest_valid_cycles": lat}
json.dump(r, open(f"{out}/check.json", "w"), indent=1)
print(json.dumps(r))
