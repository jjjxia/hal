#!/usr/bin/env bash
# E7 preparation (E7_VENDOR_PROTOCOL.txt): flatten the Synplify Pro netlist against port-only iCE40 stubs, lower
# buffers and constants, check against the secworks RTL. Results in build/e7/sha256_lattice/.
set -euo pipefail
E=/home/jiahao/hal/algorithm_recovery/generalization/e7; O=/home/jiahao/hal/build/e7/sha256_lattice
NET=/home/jiahao/HardwareRE/dynamicRE/benchmarks/papers/P01/netlists/netlists/lattice/sha256_lattice.v
RTL=/home/jiahao/HardwareRE/dynamicRE/harder_netlists/algo_designs/sha256/src/rtl
mkdir -p "$O/src"; cd "$O"
echo "83a8e93c4b67c7db6dfbc14be9145fa96b71c9df54097f130cfeb9770bca584f  $NET" | sha256sum -c
yosys -q -l flat.log -p "read_verilog $E/ice40_stubs.v; read_verilog $NET; hierarchy -top sha256; flatten; write_json flat_raw.json"
python3 "$E/e7_lower.py" flat_raw.json sha256 sha256_lattice.json --split write_data:0:2:write_data_ctl > lower.json
for f in sha256.v sha256_core.v sha256_w_mem.v sha256_k_constants.v; do cp "$RTL/$f" src/; done
printf 'read_verilog sha256_k_constants.v\nread_verilog sha256_w_mem.v\nread_verilog sha256_core.v\nread_verilog sha256.v\n' > synth.ys
python3 "$E/e7_check.py" sha256_lattice.json "$RTL" check 20
python3 "$E/e7_check.py" sha256_lattice.json "$RTL" check_fixed_84 12 --fixed 92 84
python3 "$E/e7_check.py" sha256_lattice.json "$RTL" check_fixed_83 12 --fixed 91 83
