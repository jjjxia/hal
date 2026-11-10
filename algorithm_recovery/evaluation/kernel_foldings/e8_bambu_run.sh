#!/usr/bin/env bash
# E8.3 one design: Bambu preparation -> C1 pipeline with the event graph -> program (recover-program) -> trace validation
# and end-to-end check (CPython) -> V1-V3.
# usage: e8_bambu_run.sh KERNEL CLOCK_NS MULTS [prepare]   results in build/e8/bambu/<kernel>_c<clock>_m<mults>/
#        ("prepare": stop after Bambu + synthesis + RTL check, used to deduplicate identical RTL first)
# Every stage <= 3 h and <= 6 GiB as E5/E8.2, except the CPython checks of the programs (validate, close_chain,
# stream: <= 16 GiB, amendment 10); receipts in OUT/e8_stages.jsonl.
set -uo pipefail
kernel=$1; clock=$2; mults=$3; only=${4:-}
H=/home/jiahao/hal; E=$H/algorithm_recovery/evaluation; B=$H/build; PY=$B/python_venv/bin/python
X=$B/bin/hal_plugins/recover-program; C=$E/program_checks
case=${kernel}_c${clock}_m${mults}
D=$B/e8/bambu/$case
[ -e "$D/v_metrics.json" ] && { echo "$case: done"; exit 0; }
mkdir -p "$D"
export HAL_BASE_PATH=$B LD_LIBRARY_PATH=$B/lib OMP_NUM_THREADS=1
receipt=$D/e8_stages.jsonl
st() {
    local name=$1; shift
    local kb=${VMEM_KB:-6291456}
    case $name in validate|close_chain|stream) [ $kb -lt 16777216 ] && kb=16777216;; esac   # CPython checks: 16 GiB (amendment 10)
    local t0=$(date +%s.%N)
    ( ulimit -c 0; ulimit -v $kb; /usr/bin/time -f "%M" -o "$D/$name.mem" timeout 10800 "$@" ) > "$D/$name.log" 2>&1
    local rc=$?
    printf '{"stage":"%s","exit":%d,"seconds":%.1f,"peak_kb":%s}\n' "$name" $rc "$(echo "$(date +%s.%N) - $t0" | bc)" \
        "$(tail -n 1 "$D/$name.mem" 2>/dev/null | grep -E '^[0-9]+$' || echo null)" >> "$receipt"
    return $rc
}
[ -s "$D/gen/spec.json" ] || st bambu python3 "$E/kernel_foldings/e8_bambu_prepare.py" "$kernel" "$clock" "$mults" "$D/gen" "$case" || { echo "$case: bambu/prepare failed: $(tail -n 1 $D/bambu.log)"; exit 0; }
[ "$only" = prepare ] && { echo "$case: prepared"; exit 0; }
[ -e "$D/case/input" ] || st prepare python3 "$E/library_designs/prepare_bench2.py" "$D/gen/spec.json" "$D/case" || { echo "$case: prepare failed"; exit 0; }
[ -e "$D/case/stages.jsonl" ] || ( ulimit -v ${VMEM_KB:-6291456}; bash "$E/library_designs/run_bench2.sh" "$D/case" ) > /dev/null 2>&1
grep -q '"status": *"PROTOCOL_SCHEDULE_CERTIFIED"' "$D/case/graph/certificate/certificate.json" 2>/dev/null || \
    { echo "$case: not certified: $(tail -n 2 $D/case/stages.jsonl | tr '\n' ' ')"; exit 0; }
G=$D/case/graph/graph_training.json; K=$D/case/input/evaluation_contract.json
[ -s "$G" ] || { echo "$case: no event graph: $(tail -n 1 $D/case/graph.log | cut -c1-200)"; exit 0; }
st program "$X" generate "$G" "$K" "$D/gen/observe.json" "$case" "$D/case/program" || { echo "$case: program refused: $(tail -n 1 $D/program.log | cut -c1-200)"; exit 0; }
st validate "$PY" "$C/validate_programs.py" "$G" "$D/case/trace" "$K" "$D/case/program"
st close_chain "$PY" "$C/close_chain.py" "$G" "$K" "$D/gen/observe.json" "$case" "$D/close_chain.json" \
    "$D/case/program/program_flat.py" "$D/case/program/program_rolled.py" "$D/case/program/program.py"
st stream "$PY" "$E/kernel_foldings/e8_stream_check.py" "$D/case" "$kernel" "$D/v_metrics.json"
echo "$case: $(tail -n 1 $D/stream.log | cut -c1-400)"
