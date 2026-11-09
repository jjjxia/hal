#!/usr/bin/env bash
# Per-case pipeline (see C2C3_BENCHMARKS_FROZEN.txt): import, word groups, original-gate trace, then one `graph` run that
# writes the schedule certificate (graph/certificate/) and the event graph (graph/graph_training.json); variant trace
# and the cross-run taint check. Until 2026-11-08 a separate `analyze` stage wrote the same certificate to analysis/.
# usage: run_bench2.sh CASE_DIRECTORY   (prepared by prepare_bench2.py)
set -uo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
case_dir=$(realpath -- "$1")
export HAL_BASE_PATH="$repo/build" LD_LIBRARY_PATH="$repo/build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
cli="$repo/build/bin/hal_plugins/hal-recover"
in="$case_dir/input"
receipt="$case_dir/stages.jsonl"
stage() {
    local name=$1; shift
    local start=$(date +%s.%N)
    ( ulimit -c 0; ulimit -v 16777216; timeout 10800 "$@" ) > "$case_dir/$name.log" 2>&1
    local code=$?
    local end=$(date +%s.%N)
    printf '{"stage":"%s","exit":%d,"seconds":%.1f}\n' "$name" "$code" "$(echo "$end - $start" | bc)" >> "$receipt"
    return $code
}
window=$(python3 -c "import json;print(len(json.load(open('$in/stimulus.json'))['windows']['training']))")
stage import "$cli" import "$in/recovered.json" "$repo/plugins/gate_libraries/definitions/ice40ultra_hal.hgl" "$case_dir/import" || exit 1
stage groups "$cli" groups "$case_dir/import/import.hal" "$case_dir/groups" --mi-types counter,addition --mi-controls 3 --mi-threads 1 --dana-threads 1 || exit 1
stage trace "$cli" trace "$in/recovered.json" "$in/recovered.v" /usr/share/yosys/ice40/cells_sim.v "$case_dir/trace" --stimulus-json "$in/stimulus.json" || exit 1
stage graph "$cli" graph "$case_dir/groups/ordered.hal" "$case_dir/groups/groups.json" "$in/recovered.json" "$case_dir/trace" "$case_dir/graph" \
    --window "$window" --profiles training --protocol "$in/protocol.json"
python3 "$repo/algorithm_recovery/evaluation/library_designs/variant_stimulus.py" "$in/stimulus.json" "$in/protocol.json" "$case_dir/variant_stimulus.json" 20261103
stage variant_trace "$cli" trace "$in/recovered.json" "$in/recovered.v" /usr/share/yosys/ice40/cells_sim.v "$case_dir/variant_trace" --stimulus-json "$case_dir/variant_stimulus.json"
if [[ -f "$case_dir/graph/certificate/taint_observed.txt" ]]; then
    stage crossrun python3 "$repo/algorithm_recovery/evaluation/check_taint_crossrun.py" "$case_dir/graph/certificate" "$case_dir/trace" "$case_dir/variant_trace"
fi
echo DONE >> "$receipt"
