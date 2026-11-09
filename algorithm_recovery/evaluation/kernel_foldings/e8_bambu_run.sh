#!/usr/bin/env bash
# E8.3 one design: Bambu preparation -> frozen bench2 C1 pipeline -> graph -> program -> end-to-end check -> V1-V3.
# usage: e8_bambu_run.sh KERNEL CLOCK_NS MULTS [prepare]   results in build/e8/bambu/<kernel>_c<clock>_m<mults>/
#        ("prepare": stop after Bambu + synthesis + RTL check, used to deduplicate identical RTL first)
# Every stage <= 3 h and <= 6 GiB, as E5/E8.2; receipts in OUT/e8_stages.jsonl.
set -uo pipefail
kernel=$1; clock=$2; mults=$3; only=${4:-}
H=/home/jiahao/hal; E=$H/algorithm_recovery/evaluation; B=$H/build; PY=$B/c3_venv/bin/python
# Program backend (program, end-to-end check, folded RTL; same arguments and outputs):
#   PROGRAM_BACKEND=cpp (default since 2026-11-08) runs recover-program, the C++ port in
#     plugins/algorithm_recovery/program_recovery (build: see its README.txt; binary in build/program_recovery);
#   PROGRAM_BACKEND=py runs the frozen Python scripts in program_recovery_python, which produced the earlier runs.
# The backend used is written to OUT/program_backend.txt.
BACKEND=${PROGRAM_BACKEND:-cpp}
case $BACKEND in
    cpp) X=$B/program_recovery/recover-program
         [ -x "$X" ] || { echo "missing $X (build plugins/algorithm_recovery/program_recovery, or set PROGRAM_BACKEND=py)" >&2; exit 2; }
         PROGRAM_CMD=("$X" generate); CHECK_CMD=("$X" check); RTL_CMD=("$X" rtl) ;;
    py)  PROGRAM_CMD=("$PY" "$E/program_recovery_python/decompile.py"); CHECK_CMD=("$PY" "$E/program_recovery_python/close_chain_deep.py"); RTL_CMD=("$PY" "$E/program_recovery_python/emit_rtl.py") ;;
    *)   echo "PROGRAM_BACKEND must be cpp or py, not '$BACKEND'" >&2; exit 2 ;;
esac
case=${kernel}_c${clock}_m${mults}
D=$B/e8/bambu/$case
[ -e "$D/v_metrics.json" ] && { echo "$case: done"; exit 0; }
mkdir -p "$D"
echo "$BACKEND" > "$D/program_backend.txt"
export HAL_BASE_PATH=$B LD_LIBRARY_PATH=$B/lib OMP_NUM_THREADS=1
receipt=$D/e8_stages.jsonl
st() {
    local name=$1; shift
    local t0=$(date +%s.%N)
    ( ulimit -c 0; ulimit -v ${VMEM_KB:-6291456}; /usr/bin/time -f "%M" -o "$D/$name.mem" timeout 10800 "$@" ) > "$D/$name.log" 2>&1
    local rc=$?
    printf '{"stage":"%s","exit":%d,"seconds":%.1f,"peak_kb":%s}\n' "$name" $rc "$(echo "$(date +%s.%N) - $t0" | bc)" \
        "$(tail -n 1 "$D/$name.mem" 2>/dev/null | grep -E '^[0-9]+$' || echo null)" >> "$receipt"
    return $rc
}
[ -s "$D/gen/spec.json" ] || st bambu python3 "$E/kernel_foldings/e8_bambu_prepare.py" "$kernel" "$clock" "$mults" "$D/gen" "$case" || { echo "$case: bambu/prepare failed: $(tail -n 1 $D/bambu.log)"; exit 0; }
[ "$only" = prepare ] && { echo "$case: prepared"; exit 0; }
[ -e "$D/case/input" ] || st prepare python3 "$E/library_designs/prepare_bench2.py" "$D/gen/spec.json" "$D/case" || { echo "$case: prepare failed"; exit 0; }
[ -e "$D/case/stages.jsonl" ] || ( ulimit -v ${VMEM_KB:-6291456}; bash "$E/library_designs/run_bench2.sh" "$D/case" ) > /dev/null 2>&1
grep -q '"status": *"PROTOCOL_SCHEDULE_CERTIFIED"' "$D/case/analysis/certificate/certificate.json" 2>/dev/null || \
    { echo "$case: not certified: $(tail -n 2 $D/case/stages.jsonl | tr '\n' ' ')"; exit 0; }
win=$(python3 -c "import json;print(len(json.load(open('$D/case/input/stimulus.json'))['windows']['training']))")
st graph "$B/bin/hal_plugins/hal-recover" graph "$D/case/groups/ordered.hal" "$D/case/groups/groups.json" \
    "$D/case/input/recovered.json" "$D/case/trace" "$D/case/graph" --window "$win" --profiles training --protocol "$D/case/input/protocol.json"
st program "${PROGRAM_CMD[@]}" "$D/case/graph/graph_training.json" "$D/case/trace" "$D/case/input/evaluation_contract.json" \
    "$D/gen/observe.json" "$case" "$D/case/program" || { echo "$case: program refused: $(tail -n 1 $D/program.log | cut -c1-200)"; exit 0; }
st close_chain "${CHECK_CMD[@]}" "$D/case/graph/graph_training.json" "$D/case/input/evaluation_contract.json" \
    "$D/gen/observe.json" "$case" "$D/close_chain.json" "$D/case/program/program_flat.py" "$D/case/program/program_rolled.py" \
    "$D/case/program/program.py"
st stream "$PY" "$E/kernel_foldings/e8_stream_check.py" "$D/case" "$kernel" "$D/v_metrics.json"
echo "$case: $(tail -n 1 $D/stream.log | cut -c1-400)"
