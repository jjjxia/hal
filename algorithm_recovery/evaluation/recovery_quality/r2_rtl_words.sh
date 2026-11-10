#!/usr/bin/env bash
# R2 control with RTL words (protocol amendment 2): the E6 path on one generated design whose gate-level grouping failed.
# usage: r2_rtl_words.sh DESIGN_DIR      (DESIGN_DIR/gen/{fold_top.v,truth.json}); results in DESIGN_DIR/rtl_words/
set -uo pipefail
D=$1; G=$D/gen; R=$D/rtl_words
H=/home/jiahao/hal; E=$H/algorithm_recovery/evaluation/scale; PY=$H/build/python_venv/bin/python
[ -e "$R/score.json" ] && { echo "$(basename $D): done"; exit 0; }
mkdir -p $R; export LD_LIBRARY_PATH=$H/build/lib:$H/build/lib/hal_plugins
st() { local name=$1; shift; ( ulimit -c 0; ulimit -v 6291456; timeout 10800 "$@" ) > "$R/$name.log" 2>&1; }
st rtl yosys -q -p "read_verilog $G/fold_top.v; proc; flatten; opt_clean; write_json $R/rtl.json" || { echo "$(basename $D): rtl failed"; exit 0; }
st equations python3 $E/e6_equations.py $R/rtl.json fold_top $R/equations.json || { echo "$(basename $D): equations: $(tail -n 1 $R/equations.log)"; exit 0; }
st fold $H/build/e6/bin/e6_fold $R/equations.json $R/graph.json || { echo "$(basename $D): fold: $(tail -n 1 $R/fold.log)"; exit 0; }
st score $PY $E/e6_score.py $G/truth.json $R/graph.json $R/score.json
echo "$(basename $D): $(tail -n 1 $R/score.log | cut -c1-300)"
