#!/usr/bin/env bash
# E7 paired instance (Amendment 1): Yosys synth_ice40 of the secworks sha256 top, same lowering and checks.
set -euo pipefail
E=/home/jiahao/hal/algorithm_recovery/generalization/e7; O=/home/jiahao/hal/build/e7/sha256_yosys
RTL=/home/jiahao/HardwareRE/dynamicRE/harder_netlists/algo_designs/sha256/src/rtl
mkdir -p "$O/src"; cd "$O"
for f in sha256.v sha256_core.v sha256_w_mem.v sha256_k_constants.v; do cp "$RTL/$f" src/; done
printf 'read_verilog sha256_k_constants.v\nread_verilog sha256_w_mem.v\nread_verilog sha256_core.v\nread_verilog sha256.v\n' > synth.ys
yosys -q -l synth.log -p "read_verilog src/sha256_k_constants.v; read_verilog src/sha256_w_mem.v; read_verilog src/sha256_core.v; read_verilog src/sha256.v; synth_ice40 -top sha256 -nobram -json raw.json; tee -o stat.txt stat"
python3 "$E/e7_lower.py" raw.json sha256 sha256_yosys.json --split write_data:0:2:write_data_ctl > lower.json
python3 "$E/e7_check.py" sha256_yosys.json "$RTL" check_fixed_84 12 --fixed 92 84
