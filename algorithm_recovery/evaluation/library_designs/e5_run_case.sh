#!/usr/bin/env bash
# E5.A/B one design (protocol bench2/E5_EXPANSION_PROTOCOL.txt): the frozen pipeline, then program, checks, RTL, oracle.
# usage: e5_run_case.sh CASE      (spec: bench2/specs/e5/CASE.json; reference: c3/e5_refs/CASE.py; observe.json[CASE])
# Every stage: <= 3 h, <= 6 GiB virtual memory; times and peak memory in OUT/e5_stages.jsonl. Results: build/bench2_e5/CASE/.
set -uo pipefail
case=$1
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
spec=$E/library_designs/specs/e5/$case.json
D=$B/bench2_e5/$case
[ -e "$D/e5_summary.json" ] && { echo "$case: done"; exit 0; }
export HAL_BASE_PATH=$B LD_LIBRARY_PATH=$B/lib OMP_NUM_THREADS=1
receipt=$D/e5_stages.jsonl
st() {   # st NAME CMD...  (6 GiB, 3 h)
    local name=$1; shift
    local t0=$(date +%s.%N)
    ( ulimit -c 0; ulimit -v ${VMEM_KB:-6291456}; /usr/bin/time -f "%M" -o "$D/$name.mem" timeout 10800 "$@" ) > "$D/$name.log" 2>&1
    local rc=$?
    printf '{"stage":"%s","exit":%d,"seconds":%.1f,"peak_kb":%s}\n' "$name" $rc "$(echo "$(date +%s.%N) - $t0" | bc)" \
        "$(tail -n 1 "$D/$name.mem" 2>/dev/null | grep -E '^[0-9]+$' || echo null)" >> "$receipt"
    return $rc
}
if [ ! -e "$D/input" ]; then
    ( ulimit -v ${VMEM_KB:-6291456}; timeout 10800 python3 "$E/library_designs/prepare_bench2.py" "$spec" "$D" ) > "$B/bench2_e5/$case.prepare.log" 2>&1 || { echo "$case: prepare failed (see $case.prepare.log)"; exit 0; }
fi
mkdir -p "$D"
echo "$BACKEND" > "$D/program_backend.txt"
# frozen C1 pipeline under the E5 budget: an outer hard limit of 6 GiB (the pipeline's own `ulimit -v 16777216` then
# cannot raise it) and its own 3 h per stage
[ -e "$D/stages.jsonl" ] || ( ulimit -v ${VMEM_KB:-6291456}; bash "$E/library_designs/run_bench2.sh" "$D" ) > /dev/null 2>&1
# gate (amendment h): a certified schedule. analyze's exit status also covers the separate state-machine export (used
# only for the RAM-backed Index programs), so a certified design whose state export failed continues.
grep -q '"stage":"analyze","exit":0' "$D/stages.jsonl" || \
    grep -q '"status": *"PROTOCOL_SCHEDULE_CERTIFIED"' "$D/analysis/certificate/certificate.json" 2>/dev/null || \
    { echo "$case: C1 pipeline failed: $(tail -n 2 "$D/stages.jsonl" | tr '\n' ' ')"; exit 0; }
win=$(python3 -c "import json;print(len(json.load(open('$D/input/stimulus.json'))['windows']['training']))")
st graph "$B/bin/hal_plugins/hal-recover" graph "$D/groups/ordered.hal" "$D/groups/groups.json" "$D/input/recovered.json" \
    "$D/trace" "$D/graph" --window "$win" --profiles training --protocol "$D/input/protocol.json"
st program "${PROGRAM_CMD[@]}" "$D/graph/graph_training.json" "$D/trace" "$D/input/evaluation_contract.json" \
    "$E/program_recovery_python/observe.json" "$case" "$D/program"
if [ -s "$D/program/program.py" ]; then
    if grep -q '^STREAM' "$E/program_recovery_python/e5_refs/$case.py" 2>/dev/null; then
        st reference "$PY" "$E/program_recovery_python/check_stream.py" "$D/program" "$D/input/evaluation_contract.json" "$case" 2000
        [ -s "$D/program/stream_check.json" ] && cp "$D/program/stream_check.json" "$D/program/reference_check.json"
    else
        st reference "$PY" "$E/program_recovery_python/check_reference.py" "$D/program" "$D/input/evaluation_contract.json" "$case" 2000
    fi
    st close_chain "${CHECK_CMD[@]}" "$D/graph/graph_training.json" "$D/input/evaluation_contract.json" \
        "$E/program_recovery_python/observe.json" "$case" "$D/close_chain.json" "$D/program/program_flat.py" "$D/program/program_rolled.py" "$D/program/program.py"
fi
[ -s "$D/graph/graph_training.json" ] && st rtl "${RTL_CMD[@]}" "$D/graph/graph_training.json" "$D/rtl" "$D/trace" "$D/variant_trace"
# E2.1 oracle: the synthesis script's read_verilog/chparam lines, then the word-level elaboration of E21_ORACLE_PROTOCOL
synth_dir=$(dirname "$(python3 -c "import json;print(json.load(open('$spec'))['synth'])")")
top=$(python3 -c "import json;print(json.load(open('$spec'))['top'])")
{ grep -E '^(read_verilog|chparam)' "$synth_dir/synth.ys"; printf 'hierarchy -top %s\nproc\nflatten\nmemory\nopt_clean\nwrite_json %s\n' "$top" "$D/rtl_wordlevel.json"; } > "$D/rtl_wordlevel.ys"
base=$(echo "$synth_dir" | sed 's|/synth/.*||')
( cd "$base/src" && ulimit -v 4194304 && timeout 1200 yosys -q -s "$D/rtl_wordlevel.ys" ) > "$D/rtl_wordlevel.log" 2>&1
[ -s "$D/rtl_wordlevel.json" ] && [ -s "$D/graph/graph_training.json" ] && \
    st oracle "$PY" "$E/library_designs/c2_oracle.py" "$D" "$D/rtl_wordlevel.json" "$top" "$(python3 -c "import json;print(json.load(open('$spec'))['synth'])")" \
        "$D/graph/graph_training.json" "$D/oracle.json"
python3 "$E/library_designs/e5_summarize.py" "$case" "$D" > "$D/e5_summary.json" 2> "$D/e5_summary.err"
echo "$case: $(python3 -c "import json;s=json.load(open('$D/e5_summary.json'));print(json.dumps({k:s.get(k) for k in ('outcome','period','program','reference','close_chain','rtl','oracle')}))" 2>/dev/null)"
