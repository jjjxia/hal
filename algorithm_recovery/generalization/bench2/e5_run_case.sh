#!/usr/bin/env bash
# E5.A/B one design (protocol bench2/E5_EXPANSION_PROTOCOL.txt): the frozen pipeline, then program, checks, RTL, oracle.
# usage: e5_run_case.sh CASE      (spec: bench2/specs/e5/CASE.json; reference: c3/e5_refs/CASE.py; observe.json[CASE])
# Every stage: <= 3 h, <= 6 GiB virtual memory; times and peak memory in OUT/e5_stages.jsonl. Results: build/bench2_e5/CASE/.
set -uo pipefail
case=$1
H=/home/jiahao/hal; E=$H/algorithm_recovery/generalization; B=$H/build; PY=$B/c3_venv/bin/python
spec=$E/bench2/specs/e5/$case.json
D=$B/bench2_e5/$case
[ -e "$D/e5_summary.json" ] && { echo "$case: done"; exit 0; }
export HAL_BASE_PATH=$B LD_LIBRARY_PATH=$B/lib OMP_NUM_THREADS=1
receipt=$D/e5_stages.jsonl
st() {   # st NAME CMD...  (6 GiB, 3 h)
    local name=$1; shift
    local t0=$(date +%s.%N)
    ( ulimit -c 0; ulimit -v 6291456; /usr/bin/time -f "%M" -o "$D/$name.mem" timeout 10800 "$@" ) > "$D/$name.log" 2>&1
    local rc=$?
    printf '{"stage":"%s","exit":%d,"seconds":%.1f,"peak_kb":%s}\n' "$name" $rc "$(echo "$(date +%s.%N) - $t0" | bc)" \
        "$(tail -n 1 "$D/$name.mem" 2>/dev/null | grep -E '^[0-9]+$' || echo null)" >> "$receipt"
    return $rc
}
if [ ! -e "$D/input" ]; then
    python3 "$E/bench2/prepare_bench2.py" "$spec" "$D" > "$B/bench2_e5/$case.prepare.log" 2>&1 || { echo "$case: prepare failed"; exit 1; }
fi
mkdir -p "$D"
# frozen C1 pipeline (its own 16 GiB/3 h caps; this machine has 7.7 GB, so its memory failures are reported as such)
[ -e "$D/stages.jsonl" ] || bash "$E/bench2/run_bench2.sh" "$D" > /dev/null 2>&1
grep -q '"stage":"analyze","exit":0' "$D/stages.jsonl" || { echo "$case: C1 pipeline failed: $(tail -n 2 "$D/stages.jsonl" | tr '\n' ' ')"; exit 0; }
win=$(python3 -c "import json;print(len(json.load(open('$D/input/stimulus.json'))['windows']['training']))")
st graph "$B/bin/hal_plugins/hal-recover" graph "$D/groups/ordered.hal" "$D/groups/groups.json" "$D/input/recovered.json" \
    "$D/trace" "$D/graph" --window "$win" --profiles training --protocol "$D/input/protocol.json"
st program "$PY" "$E/c3/decompile.py" "$D/graph/graph_training.json" "$D/trace" "$D/input/evaluation_contract.json" \
    "$E/c3/observe.json" "$case" "$D/program"
if [ -s "$D/program/program.py" ]; then
    if grep -q '^STREAM' "$E/c3/e5_refs/$case.py" 2>/dev/null; then
        st reference "$PY" "$E/c3/check_stream.py" "$D/program" "$D/input/evaluation_contract.json" "$case" 2000
        [ -s "$D/program/stream_check.json" ] && cp "$D/program/stream_check.json" "$D/program/reference_check.json"
    else
        st reference "$PY" "$E/c3/check_reference.py" "$D/program" "$D/input/evaluation_contract.json" "$case" 2000
    fi
    st close_chain "$PY" "$E/c3/close_chain.py" "$D/graph/graph_training.json" "$D/input/evaluation_contract.json" \
        "$E/c3/observe.json" "$case" "$D/close_chain.json" "$D/program/program_flat.py" "$D/program/program_rolled.py" "$D/program/program.py"
fi
[ -s "$D/graph/graph_training.json" ] && st rtl "$PY" "$E/c3/emit_rtl.py" "$D/graph/graph_training.json" "$D/rtl" "$D/trace" "$D/variant_trace"
# E2.1 oracle: the synthesis script's read_verilog/chparam lines, then the word-level elaboration of E21_ORACLE_PROTOCOL
synth_dir=$(dirname "$(python3 -c "import json;print(json.load(open('$spec'))['synth'])")")
top=$(python3 -c "import json;print(json.load(open('$spec'))['top'])")
{ grep -E '^(read_verilog|chparam)' "$synth_dir/synth.ys"; printf 'hierarchy -top %s\nproc\nflatten\nmemory\nopt_clean\nwrite_json %s\n' "$top" "$D/rtl_wordlevel.json"; } > "$D/rtl_wordlevel.ys"
base=$(echo "$synth_dir" | sed 's|/synth/.*||')
( cd "$base/src" && ulimit -v 4194304 && timeout 1200 yosys -q -s "$D/rtl_wordlevel.ys" ) > "$D/rtl_wordlevel.log" 2>&1
[ -s "$D/rtl_wordlevel.json" ] && [ -s "$D/graph/graph_training.json" ] && \
    st oracle "$PY" "$E/bench2/c2_oracle.py" "$D" "$D/rtl_wordlevel.json" "$top" "$(python3 -c "import json;print(json.load(open('$spec'))['synth'])")" \
        "$D/graph/graph_training.json" "$D/oracle.json"
python3 "$E/bench2/e5_summarize.py" "$case" "$D" > "$D/e5_summary.json" 2> "$D/e5_summary.err"
echo "$case: $(python3 -c "import json;s=json.load(open('$D/e5_summary.json'));print(json.dumps({k:s.get(k) for k in ('outcome','period','program','reference','close_chain','rtl','oracle')}))" 2>/dev/null)"
