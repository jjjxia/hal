#!/usr/bin/env bash
# End-to-end translation validation (c3/close_chain.py) of flat, rolled and final programs, all eight frozen designs.
B=/home/jiahao/hal/build; C=/home/jiahao/hal/algorithm_recovery/evaluation/program_recovery_python; OUT=$B/bench2_v1/close_chain_v2
mkdir -p $OUT; cd $C
for spec in present:present:program_v5:graph_v1 fir_decim:fir_decim:program_v5:graph_v1 small_hb_dec_r2:small_hb_dec:program_v5:graph_v1 \
            ibex_mul:ibex_mul:program_v5:graph_v1 qdiv:qdiv:program_v5:graph_v1 sha1:sha1:program_v6:analysis \
            bambu_arf:bambu_arf:program_v6:graph_v4 ascon:ascon:program_v5:analysis; do
  IFS=: read d case prog graph <<< "$spec"; D=$B/bench2_v1/$d
  [ -s $OUT/$case.json ] && continue
  ( ulimit -v 6291456; /usr/bin/time -f "$case %e s %M KB" timeout 14400 $B/c3_venv/bin/python close_chain.py \
      $D/$graph/graph_training.json $D/input/evaluation_contract.json observe.json $case $OUT/$case.json \
      $D/$prog/program_flat.py $D/$prog/program_rolled.py $D/$prog/program.py ) > $OUT/$case.log 2>&1
  echo "$case exit=$?"; grep -h '^{' $OUT/$case.log | python3 -c "
import json,sys
for l in sys.stdin:
    r=json.loads(l); print('  ', r['program'].split('/')[-1], r['writes'], r['outputs'], r['results'], 'closed' if r['closed'] else 'OPEN', r['exec_s'], r['prove_s'])"
  tail -n 1 $OUT/$case.log
done
# mutation control (PRESENT): four injected defects must each leave the chain open
S=$OUT/mutants; mkdir -p $S; D=$B/bench2_v1/present
python3 - "$D/program_v5" "$S" <<'PY'
import re, sys
D, S = sys.argv[1:]
flat = open(f"{D}/program_flat.py").read(); fin = open(f"{D}/program.py").read()
r = [m for m in re.finditer(r"v\[\('DANA_module_0', 7\)\]", flat) if not flat[m.end():m.end()+3].startswith(" =")][0]
open(f"{S}/m1_wrong_producer.py", "w").write(flat[:r.start()] + "v[('DANA_module_0', 5)]" + flat[r.end():])
t = re.search(r"TABLE0 = \[([^\]]*)\]", fin); items = t.group(1).split(","); items[5] = " " + str(int(items[5]) ^ 1)
open(f"{S}/m2_table.py", "w").write(fin[:t.start(1)] + ",".join(items) + fin[t.end(1):])
t = re.search(r"R0 = \(\((\d+), (\d+)\)", fin)
open(f"{S}/m3_route.py", "w").write(fin[:t.start(2)] + str(int(t.group(2)) ^ 1) + fin[t.end(2):])
open(f"{S}/m4_output.py", "w").write(fin.replace("(route(s0, X0) ^ route(s1, X1))", "(route(s0, X1) ^ route(s1, X0))"))
PY
( ulimit -v 6291456; $B/c3_venv/bin/python close_chain.py $D/graph_v1/graph_training.json $D/input/evaluation_contract.json observe.json present \
    $S/mutants.json $S/m1_wrong_producer.py $S/m2_table.py $S/m3_route.py $S/m4_output.py ) > $S/mutants.log 2>&1
echo "mutants:"; grep -h '^{' $S/mutants.log | python3 -c "
import json,sys
for l in sys.stdin:
    r=json.loads(l); print('  ', r['program'].split('/')[-1], r['results'], 'closed' if r['closed'] else 'OPEN')"
