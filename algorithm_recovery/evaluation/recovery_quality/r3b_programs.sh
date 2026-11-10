#!/usr/bin/env bash
# R3b (RECOVERY_QUALITY_PROTOCOL.txt): programs of the structure-exact folded designs (no program stage existed for them).
# The observation prior is the generator's interface: output port k is read one cycle after its node's slot.
# usage: r3b_programs.sh DESIGN_DIR      results in DESIGN_DIR/r3/ (observe.json, program/, validate.log)
set -uo pipefail
D=$1; R=$D/r3; H=/home/jiahao/hal; B=$H/build; C=$H/algorithm_recovery/evaluation/program_checks; PY=$B/python_venv/bin/python
[ -s "$R/program/validation.json" ] && { echo "$(basename $D): done"; exit 0; }
mkdir -p $R; case=$(python3 -c "import json;print(json.load(open('$D/case/input/evaluation_contract.json'))['case'])")
python3 - "$D/gen/truth.json" "$case" "$R/observe.json" <<'PY'
import json, sys
T = json.load(open(sys.argv[1])); names = T.get("output_names") or [f"y{i}" for i in range(len(T["outputs"]))]
json.dump({sys.argv[2]: [{"port": p, "cycle": T["nodes"][v]["slot"] + 1} for p, v in zip(names, T["outputs"])]}, open(sys.argv[3], "w"), indent=1)
PY
G=$D/case/graph/graph_training.json; K=$D/case/input/evaluation_contract.json
( ulimit -v 6291456; timeout 10800 $B/bin/hal_plugins/recover-program generate $G $K $R/observe.json $case $R/program ) > $R/program.log 2>&1 || { echo "$(basename $D): program failed: $(tail -n 1 $R/program.log | cut -c1-200)"; exit 0; }
( ulimit -v 16777216; timeout 10800 $PY $C/validate_programs.py $G $D/case/trace $K $R/program ) > $R/validate.log 2>&1
echo "$(basename $D): $(python3 -c "import json;v=json.load(open('$R/program/validation.json'));r=json.load(open('$R/program/report.json'));print(v['status'], 'loops', [l['iterations'] for l in r['loops']], 'lines', r['flat_program_lines'], r['rolled_program_lines'], r['program_lines'])" 2>&1 | tail -1)"
