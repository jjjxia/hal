#!/usr/bin/env bash
# E5.A/B one design (protocol library_designs/E5_EXPANSION_PROTOCOL.txt): C1 pipeline with the event graph, then the
# program (recover-program generate), its trace validation, reference check and end-to-end check (CPython), folded RTL
# (recover-program rtl) and the oracle.
# usage: e5_run_case.sh CASE      (spec: library_designs/specs/e5/CASE.json; reference: program_checks/e5_refs/CASE.py)
# Every stage <= 3 h and <= 6 GiB virtual memory, except the CPython checks of the programs (validate, reference,
# close_chain: <= 16 GiB, amendment o); times and peak memory in OUT/e5_stages.jsonl. Results: build/bench2_e5/CASE/.
set -uo pipefail
case=$1
H=/home/jiahao/hal; E=$H/algorithm_recovery/evaluation; B=$H/build; PY=$B/python_venv/bin/python
X=$B/bin/hal_plugins/recover-program; C=$E/program_checks
spec=$E/library_designs/specs/e5/$case.json
D=$B/bench2_e5/$case
[ -e "$D/e5_summary.json" ] && { echo "$case: done"; exit 0; }
export HAL_BASE_PATH=$B LD_LIBRARY_PATH=$B/lib OMP_NUM_THREADS=1
receipt=$D/e5_stages.jsonl
st() {   # st NAME CMD...  (3 h; 6 GiB, the checks 16 GiB)
    local name=$1; shift
    local kb=${VMEM_KB:-6291456}
    case $name in validate|reference|close_chain) [ $kb -lt 16777216 ] && kb=16777216;; esac   # CPython checks: 16 GiB (amendment o)
    local t0=$(date +%s.%N)
    ( ulimit -c 0; ulimit -v $kb; /usr/bin/time -f "%M" -o "$D/$name.mem" timeout 10800 "$@" ) > "$D/$name.log" 2>&1
    local rc=$?
    printf '{"stage":"%s","exit":%d,"seconds":%.1f,"peak_kb":%s}\n' "$name" $rc "$(echo "$(date +%s.%N) - $t0" | bc)" \
        "$(tail -n 1 "$D/$name.mem" 2>/dev/null | grep -E '^[0-9]+$' || echo null)" >> "$receipt"
    return $rc
}
if [ ! -e "$D/input" ]; then
    ( ulimit -v ${VMEM_KB:-6291456}; timeout 10800 python3 "$E/library_designs/prepare_bench2.py" "$spec" "$D" ) > "$B/bench2_e5/$case.prepare.log" 2>&1 || { echo "$case: prepare failed (see $case.prepare.log)"; exit 0; }
fi
mkdir -p "$D"
# C1 pipeline under the E5 budget: an outer hard limit of 6 GiB (the pipeline's own `ulimit -v 16777216` then
# cannot raise it) and its own 3 h per stage; it ends with the certificate and event graph in graph/
[ -e "$D/stages.jsonl" ] || ( ulimit -v ${VMEM_KB:-6291456}; bash "$E/library_designs/run_bench2.sh" "$D" ) > /dev/null 2>&1
# gate (amendment h): a certified schedule
grep -q '"status": *"PROTOCOL_SCHEDULE_CERTIFIED"' "$D/graph/certificate/certificate.json" 2>/dev/null || \
    { echo "$case: C1 pipeline failed: $(tail -n 2 "$D/stages.jsonl" | tr '\n' ' ')"; exit 0; }
G=$D/graph/graph_training.json; K=$D/input/evaluation_contract.json
[ -s "$G" ] && st program "$X" generate "$G" "$K" "$C/observe.json" "$case" "$D/program"
if [ -s "$D/program/program.py" ]; then
    st validate "$PY" "$C/validate_programs.py" "$G" "$D/trace" "$K" "$D/program"
    if grep -q '^STREAM' "$C/e5_refs/$case.py" 2>/dev/null; then
        st reference "$PY" "$C/check_stream.py" "$D/program" "$K" "$case" 2000
        [ -s "$D/program/stream_check.json" ] && cp "$D/program/stream_check.json" "$D/program/reference_check.json"
    else
        st reference "$PY" "$C/check_reference.py" "$D/program" "$K" "$case" 2000
    fi
    st close_chain "$PY" "$C/close_chain.py" "$G" "$K" "$C/observe.json" "$case" "$D/close_chain.json" \
        "$D/program/program_flat.py" "$D/program/program_rolled.py" "$D/program/program.py"
fi
[ -s "$G" ] && st rtl "$X" rtl "$G" "$D/rtl" "$D/trace" "$D/variant_trace"
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
