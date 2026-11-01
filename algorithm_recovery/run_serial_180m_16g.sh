#!/usr/bin/env bash
# Native HAL recovery only. Cases and their validation run sequentially.
set -euo pipefail
recovery_repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd -- "$recovery_repo"
export HAL_BASE_PATH="$recovery_repo/build"
export LD_LIBRARY_PATH="$recovery_repo/build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
recovery_cli="$recovery_repo/build/bin/hal_plugins/hal-recover"
comparison_cli="$recovery_repo/build/bin/hal_plugins/check-recovery-grouping-comparison"

if [[ ${1:-} == --one ]]; then
    recovery_case=$2
    recovery_batch=$3
    case "$recovery_case" in
        controller)
            recovery_import=build/native_controller_import/import.hal
            recovery_previous=build/controller_groups_arithmetic3/groups.json
            recovery_trace=build/native_controller_trace
            recovery_original=/home/jiahao/HardwareRE/dynamicRE/harder_netlists/consumer_bitstreams/artifacts/netlists/indexcontroller_fpga_2_26_fw/recovered.json
            ;;
        hmd)
            recovery_import=build/native_hmd_import/import.hal
            recovery_previous=build/hmd_groups_arithmetic3_threads2/groups.json
            recovery_trace=build/native_hmd_trace
            recovery_original=/home/jiahao/HardwareRE/dynamicRE/harder_netlists/consumer_bitstreams/artifacts/netlists/ice40cm_corelv_20181219v2_26_fw/recovered.json
            ;;
        *) exit 2 ;;
    esac
    printf '%s\n' groups > "$recovery_batch/$recovery_case.stage"
    "$recovery_cli" groups "$recovery_import" "$recovery_batch/${recovery_case}_groups" \
        --mi-types all --mi-controls 3 --mi-threads 2 --dana-threads 4
    printf '%s\n' compare > "$recovery_batch/$recovery_case.stage"
    "$comparison_cli" "$recovery_previous" "$recovery_batch/${recovery_case}_groups/groups.json" \
        "$recovery_batch/${recovery_case}_orders.json"
    printf '%s\n' analyze > "$recovery_batch/$recovery_case.stage"
    "$recovery_cli" analyze "$recovery_batch/${recovery_case}_groups/ordered.hal" \
        "$recovery_batch/${recovery_case}_groups/groups.json" "$recovery_original" \
        "$recovery_trace" "$recovery_batch/${recovery_case}_verified"
    printf '%s\n' complete > "$recovery_batch/$recovery_case.stage"
    exit 0
fi

if [[ $# -gt 1 ]]; then
    printf '%s\n' 'Usage: run_serial_180m_16g.sh [new_output_directory]' >&2
    exit 2
fi
recovery_batch=$(realpath -m -- "${1:-build/expanded_180m_16g_serial}")
if [[ -e "$recovery_batch" ]]; then
    printf 'Refusing existing output directory: %s\n' "$recovery_batch" >&2
    exit 1
fi
mkdir -p -- "$recovery_batch"
for recovery_case in controller hmd; do
    recovery_started=$(date -u +%FT%TZ)
    printf 'CASE_START %s %s\n' "$recovery_case" "$recovery_started"
    jq -n --arg case "$recovery_case" --arg started "$recovery_started" \
        '{case:$case,status:"RUNNING",started:$started,serial_cases:true,mi_threads:2,dana_threads:4,
          max_control_signals:3,types:"all",address_space_bytes:17179869184,case_timeout_seconds:10800,
          timeout_scope:"groups + preservation comparison + downstream analysis",termination_grace_seconds:30}' \
        > "$recovery_batch/$recovery_case.receipt.json"
    if (
        ulimit -c 0
        ulimit -v 16777216
        exec timeout --signal=TERM --kill-after=30s 180m \
            bash "$recovery_repo/algorithm_recovery/run_serial_180m_16g.sh" \
            --one "$recovery_case" "$recovery_batch"
    ) > "$recovery_batch/$recovery_case.log" 2>&1; then
        recovery_exit=0
    else
        recovery_exit=$?
    fi
    recovery_stage=$(cat "$recovery_batch/$recovery_case.stage" 2>/dev/null || printf '%s' startup)
    jq --arg ended "$(date -u +%FT%TZ)" --arg stage "$recovery_stage" --argjson code "$recovery_exit" \
        '. + {ended:$ended,stage:$stage,exit_code:$code,
          status:(if $code == 0 then "COMPLETE" elif $code == 124 then "TIME_BUDGET_EXHAUSTED" else "PROCESS_FAILED" end)}' \
        "$recovery_batch/$recovery_case.receipt.json" > "$recovery_batch/$recovery_case.receipt.next"
    mv -- "$recovery_batch/$recovery_case.receipt.next" "$recovery_batch/$recovery_case.receipt.json"
    printf 'CASE_END %s exit=%s stage=%s\n' "$recovery_case" "$recovery_exit" "$recovery_stage"
done
jq -s '{serial_cases:true,cases:.}' "$recovery_batch/controller.receipt.json" \
    "$recovery_batch/hmd.receipt.json" > "$recovery_batch/batch.json"
