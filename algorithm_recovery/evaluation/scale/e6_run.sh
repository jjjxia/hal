#!/usr/bin/env bash
# E6 one design (protocol e6/E6_SCALING_PROTOCOL.txt): generate -> self-check -> word-level RTL -> equations -> fold
# -> score -> generic gate count. Every stage <= 3 h and <= 24 GiB virtual memory; receipts in OUT/e6_stages.jsonl.
# usage: e6_run.sh SEED K N          results in build/e6/s<SEED>_k<K>_n<N>/
set -uo pipefail
seed=$1; K=$2; N=$3
H=/home/jiahao/hal; E=$H/algorithm_recovery/evaluation/scale; B=$H/build/e6; PY=$H/build/python_venv/bin/python
D=$B/s${seed}_k${K}_n${N}; G=$D/gen
[ -e "$D/score.json" ] && { echo "$(basename $D): done"; exit 0; }
[ -e "$D" ] && mv "$D" "$D.prev.$(date +%s)"   # keep an earlier attempt; never delete
mkdir -p "$G"
export LD_LIBRARY_PATH=$H/build/lib:$H/build/lib/hal_plugins
receipt=$D/e6_stages.jsonl
st() {   # st NAME CMD...
    local name=$1; shift
    local t0=$(date +%s.%N)
    ( ulimit -c 0; ulimit -v 25165824; /usr/bin/time -f "%M" -o "$D/$name.mem" timeout 10800 "$@" ) > "$D/$name.log" 2>&1
    local rc=$?
    printf '{"stage":"%s","exit":%d,"seconds":%.2f,"peak_kb":%s}\n' "$name" $rc "$(echo "$(date +%s.%N) - $t0" | bc)" \
        "$(tail -n 1 "$D/$name.mem" 2>/dev/null | grep -E '^[0-9]+$' || echo null)" >> "$receipt"
    return $rc
}
samples=$([ "$K" -le 10000 ] && echo 200 || echo 20)
st gen python3 "$E/e6_gen.py" "$seed" "$K" "$N" "$G" || { echo "$(basename $D): generator failed"; exit 1; }
st selfcheck python3 "$E/e6_selfcheck.py" "$G" "$samples" || { echo "$(basename $D): SELF-CHECK FAILED"; exit 1; }
st rtl yosys -q -p "read_verilog $G/fold_top.v; proc; flatten; opt_clean; write_json $D/rtl.json" || { echo "$(basename $D): rtl failed"; exit 1; }
st equations python3 "$E/e6_equations.py" "$D/rtl.json" fold_top "$D/equations.json" || { echo "$(basename $D): equations: $(tail -n 1 $D/equations.log)"; exit 0; }
st fold "$B/bin/e6_fold" "$D/equations.json" "$D/graph.json" || { echo "$(basename $D): fold: $(tail -n 1 $D/fold.log)"; exit 0; }
st score "$PY" "$E/e6_score.py" "$G/truth.json" "$D/graph.json" "$D/score.json"
st gates yosys -p "read_verilog $G/fold_top.v; proc; flatten; opt; techmap; opt -fast; tee -o $D/gates_stat.txt stat"
echo "$(basename $D): $(tail -n 1 $D/score.log | cut -c1-300) | fold: $(tail -n 1 $D/fold.log | cut -c1-300)"
