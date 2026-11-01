#!/usr/bin/env bash
# Generic native HAL pipeline. The source directory is produced by the existing
# bitstream frontend and must contain recovered.json and recovered.v.
# Only process orchestration is shell; grouping, extraction, synthesis and
# verification are native C++. No design names, word orders or protocol hints.
set -euo pipefail
recovery_repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ ${1:-} == --help || $# -lt 2 || $# -gt 3 ]]; then
    printf '%s\n' 'Usage: recover_to_cpp.sh recovered_netlist_directory new_output_directory [MI_TYPES]' \
        'MI_TYPES defaults to counter,addition; use all for the nine HAL candidate types.' \
        'Each complete invocation: 180 min wall time, 16 GiB per-process address space, serial stages.' \
        'The existing serial all-type firmware trial is separate; do not run firmware jobs concurrently.'
    [[ ${1:-} == --help ]] && exit 0 || exit 2
fi
recovery_source=$(realpath -- "$1")
recovery_output=$(realpath -m -- "$2")
recovery_types=${3:-counter,addition}
recovery_cli="$recovery_repo/build/bin/hal_plugins/hal-recover"
export HAL_BASE_PATH="$recovery_repo/build"
export LD_LIBRARY_PATH="$recovery_repo/build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
if [[ ${RECOVERY_INSIDE_LIMITS:-0} != 1 ]]; then
    [[ -x "$recovery_cli" && -f "$recovery_source/recovered.json" && -f "$recovery_source/recovered.v" ]] || { printf '%s\n' 'Missing native CLI or recovered source files.' >&2; exit 2; }
    [[ ! -e "$recovery_output" ]] || { printf 'Refusing existing output: %s\n' "$recovery_output" >&2; exit 2; }
    mkdir -p -- "$recovery_output"
    recovery_started=$(date -u +%FT%TZ)
    if (
        ulimit -c 0
        ulimit -v 16777216
        export RECOVERY_INSIDE_LIMITS=1
        exec timeout --signal=TERM --kill-after=30s 180m bash "$0" "$recovery_source" "$recovery_output" "$recovery_types"
    ) > "$recovery_output/pipeline.log" 2>&1; then recovery_exit=0; else recovery_exit=$?; fi
    jq -n --arg started "$recovery_started" --arg ended "$(date -u +%FT%TZ)" --arg source "$recovery_source" \
        --arg types "$recovery_types" --arg stage "$(cat "$recovery_output/stage" 2>/dev/null || printf startup)" --argjson code "$recovery_exit" \
        '{started:$started,ended:$ended,source_directory:$source,mi_types:$types,exit_code:$code,stage:$stage,serial_stages:true,
          timeout_seconds:10800,address_space_bytes:17179869184,mi_threads:1,dana_threads:1,
          status:(if $code==0 then "COMPLETE" elif $code==124 then "TIME_BUDGET_EXHAUSTED" else "FAILED_WITH_PARTIAL_ARTIFACTS" end)}' > "$recovery_output/pipeline.json"
    if [[ $recovery_exit == 0 ]]; then
        printf 'Generated control flow: %s\n' "$recovery_output/analysis/state_machine/state_machine.cpp"
        printf 'Generated arithmetic: %s\n' "$recovery_output/analysis/state_machine/arithmetic_kernels.h"
        printf 'Generated algorithm view: %s\n' "$recovery_output/analysis/state_machine/algorithm_view.txt"
        printf 'Verification receipt: %s\n' "$recovery_output/analysis/native_verified.json"
    else printf 'Recovery incomplete; inspect %s\n' "$recovery_output/pipeline.json" >&2; fi
    exit "$recovery_exit"
fi
printf '%s\n' import > "$recovery_output/stage"
"$recovery_cli" import "$recovery_source/recovered.json" "$recovery_repo/plugins/gate_libraries/definitions/ice40ultra_hal.hgl" "$recovery_output/import"
printf '%s\n' groups > "$recovery_output/stage"
"$recovery_cli" groups "$recovery_output/import/import.hal" "$recovery_output/groups" --mi-types "$recovery_types" --mi-controls 3 --mi-threads 1 --dana-threads 1
printf '%s\n' trace > "$recovery_output/stage"
"$recovery_cli" trace "$recovery_source/recovered.json" "$recovery_source/recovered.v" /usr/share/yosys/ice40/cells_sim.v "$recovery_output/trace"
printf '%s\n' analyze > "$recovery_output/stage"
"$recovery_cli" analyze "$recovery_output/groups/ordered.hal" "$recovery_output/groups/groups.json" "$recovery_source/recovered.json" "$recovery_output/trace" "$recovery_output/analysis"
printf '%s\n' complete > "$recovery_output/stage"
