#!/usr/bin/env bash
# Two explicitly authorized independent-source trials. Recovery cases are serial.
set -euo pipefail
trial_repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd -- "$trial_repo"
export HAL_BASE_PATH="$trial_repo/build"
export LD_LIBRARY_PATH="$trial_repo/build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
trial_cli="$trial_repo/build/bin/hal_plugins/hal-recover"
trial_driver="$trial_repo/build/bin/benchmark-driver"

if [[ ${1:-} == --one ]]; then
    trial_case=$2
    trial_batch=$3
    trial_dir="$trial_batch/$trial_case"
    mkdir -p "$trial_dir/input"
    printf '%s\n' synthesis > "$trial_dir/stage"
    case "$trial_case" in
        slowfil_srl)
            trial_source="$trial_batch/sources/dspfilters/rtl/slowfil_srl.v"
            printf '%s\n' 03 fb 07 0b f3 11 13 00 > "$trial_dir/input/taps.hex"
            cat > "$trial_dir/synthesis.ys" <<YOSYS
read_verilog "$trial_source"
chparam -set LGNTAPS 3 -set NTAPS 7 -set IW 8 -set TW 8 -set OW 19 -set FIXED_TAPS 1 -set INITIAL_COEFFS "$trial_dir/input/taps.hex" slowfil_srl
synth_ice40 -top slowfil_srl -nobram -json "$trial_dir/raw.json"
write_verilog -noattr "$trial_dir/raw.v"
YOSYS
            ;;
        seqcordic)
            trial_source="$trial_batch/sources/cordic/rtl/seqcordic.v"
            cat > "$trial_dir/synthesis.ys" <<YOSYS
read_verilog "$trial_source"
synth_ice40 -top seqcordic -nobram -json "$trial_dir/raw.json"
write_verilog -noattr "$trial_dir/raw.v"
YOSYS
            ;;
        *) exit 2 ;;
    esac
    yosys -Q -s "$trial_dir/synthesis.ys" > "$trial_dir/synthesis.log" 2>&1
    printf '%s\n' prepare > "$trial_dir/stage"
    if [[ $trial_case == slowfil_srl ]]; then
        "$trial_driver" prepare "$trial_case" "$trial_dir/raw.json" "$trial_dir/input" "$trial_dir/input/taps.hex"
    else
        "$trial_driver" prepare "$trial_case" "$trial_dir/raw.json" "$trial_dir/input"
    fi
    printf '%s\n' reference > "$trial_dir/stage"
    iverilog -g2012 -s test -o "$trial_dir/reference.vvp" "$trial_dir/input/reference_tb.v" \
        "$trial_source" "$trial_dir/input/recovered.v" /usr/share/yosys/ice40/cells_sim.v > "$trial_dir/reference_compile.log" 2>&1
    vvp "$trial_dir/reference.vvp" > "$trial_dir/reference.log" 2>&1
    "$trial_driver" score "$trial_case" "$trial_dir/input"
    printf '%s\n' import > "$trial_dir/stage"
    "$trial_cli" import "$trial_dir/input/recovered.json" plugins/gate_libraries/definitions/ice40ultra_hal.hgl "$trial_dir/import"
    printf '%s\n' groups > "$trial_dir/stage"
    "$trial_cli" groups "$trial_dir/import/import.hal" "$trial_dir/groups" --mi-types counter,addition --mi-controls 3 --mi-threads 1 --dana-threads 1
    printf '%s\n' trace > "$trial_dir/stage"
    "$trial_cli" trace "$trial_dir/input/recovered.json" "$trial_dir/input/recovered.v" /usr/share/yosys/ice40/cells_sim.v \
        "$trial_dir/trace" --stimulus-json "$trial_dir/input/stimulus.json"
    printf '%s\n' analyze > "$trial_dir/stage"
    trial_window=$(jq '.windows.training | length' "$trial_dir/input/stimulus.json")
    "$trial_cli" analyze "$trial_dir/groups/ordered.hal" "$trial_dir/groups/groups.json" "$trial_dir/input/recovered.json" \
        "$trial_dir/trace" "$trial_dir/analysis" --window "$trial_window" --profiles training
    printf '%s\n' complete > "$trial_dir/stage"
    exit 0
fi

[[ $# == 1 ]] || { printf '%s\n' 'Usage: run_two.sh new_output_directory'; exit 2; }
trial_batch=$(realpath -m -- "$1")
[[ ! -e $trial_batch ]] || { printf '%s\n' 'Refusing an existing trial output directory.'; exit 2; }
[[ -x $trial_cli && -x $trial_driver ]] || { printf '%s\n' 'Build hal-recover and benchmark-driver first.'; exit 2; }
mkdir -p "$trial_batch/sources"
git clone --quiet --no-checkout https://github.com/ZipCPU/dspfilters.git "$trial_batch/sources/dspfilters"
git -C "$trial_batch/sources/dspfilters" checkout --quiet --detach 16d7dc3b23af167759693da9caff7c7111805873
git clone --quiet --no-checkout https://github.com/ZipCPU/cordic.git "$trial_batch/sources/cordic"
git -C "$trial_batch/sources/cordic" checkout --quiet --detach 8ede08a1865d87a357a3a2c188206e843dd9bde6
for trial_case in slowfil_srl seqcordic; do
    mkdir "$trial_batch/$trial_case"
    trial_started=$(date -u +%FT%TZ)
    if (
        ulimit -c 0
        ulimit -v 16777216
        exec timeout --signal=TERM --kill-after=30s 180m bash "$0" --one "$trial_case" "$trial_batch"
    ) > "$trial_batch/$trial_case/pipeline.log" 2>&1; then trial_exit=0; else trial_exit=$?; fi
    jq -n --arg case "$trial_case" --arg start "$trial_started" --arg end "$(date -u +%FT%TZ)" \
        --arg stage "$(cat "$trial_batch/$trial_case/stage")" --argjson code "$trial_exit" \
        '{case:$case,started:$start,ended:$end,stage:$stage,exit_code:$code,serial_cases:true,timeout_seconds:10800,address_space_bytes:17179869184,mi_threads:1,dana_threads:1,status:(if $code==0 then "COMPLETE" elif $code==124 then "TIME_BUDGET_EXHAUSTED" else "FAILED_WITH_PARTIAL_EVIDENCE" end)}' \
        > "$trial_batch/$trial_case/receipt.json"
    printf '%s exit=%s stage=%s\n' "$trial_case" "$trial_exit" "$(cat "$trial_batch/$trial_case/stage")"
done
jq -s '{serial_cases:true,cases:.}' "$trial_batch/slowfil_srl/receipt.json" "$trial_batch/seqcordic/receipt.json" > "$trial_batch/batch.json"
