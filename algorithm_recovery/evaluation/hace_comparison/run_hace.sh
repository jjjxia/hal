#!/usr/bin/env bash
# Run HACE (tag hace-v1.0.0, CDFG extraction only: _1.py parse + flow.py; no fuzzing) on one Verilog file.
# usage: run_hace.sh VARIANT_DIR NAME.legup.v
set -u
H=/home/jiahao/hal/build/_tools/hace-src; PY=/home/jiahao/hal/build/_tools/hace-venv/bin/python
cd "$1"; v=$2; base=${v%.v}
( ulimit -v 4194304; export PYTHONPATH=$H
  /usr/bin/time -f "parse %e s %M KB" timeout 1800 $PY $H/hace/_1.py "$v" "$base.ast" &&
  /usr/bin/time -f "flow %e s %M KB" timeout 1800 $PY $H/hace/flow.py "$v" "$base.mlir" ) > "$base.hace.log" 2>&1
rc=$?
echo "$1/$v exit=$rc mlir_bytes=$( [ -s $base.mlir ] && wc -c < $base.mlir || echo 0 )"
