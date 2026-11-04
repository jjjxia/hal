#!/usr/bin/env bash
# E8.3 queue (E8_PREMISES_PROTOCOL.txt amendment 3): grid in the declared order; identical Bambu Verilog -> one design.
# usage: e8_bambu_queue.sh prepare|manifest|run
set -uo pipefail
E=/home/jiahao/hal/algorithm_recovery/generalization/e8; B=/home/jiahao/hal/build/e8/bambu
grid() {
    for k in biquad horner6 fir8 lattice4 biquad2 matvec4 arf dct8; do
        for c in 10 5 20; do for m in inf 2 1; do echo "$k $c $m"; done; done
    done
}
case ${1:-} in
prepare)  grid | while read k c m; do bash "$E/e8_bambu_run.sh" "$k" "$c" "$m" prepare; done ;;
manifest) grid | python3 -c "
import hashlib, json, os, sys
B = '$B'; rows, rep = [], {}
for line in sys.stdin:
    k, c, m = line.split(); case = f'{k}_c{c}_m{m}'; D = f'{B}/{case}'
    r = {'case': case, 'kernel': k, 'clock_ns': int(c), 'mults': m}
    if os.path.exists(f'{D}/gen/meta.json'):
        v = ''.join(l for l in open(f'{D}/gen/kernel.v') if not l.lstrip().startswith('//'))
        h = hashlib.sha256(v.encode()).hexdigest()
        r.update({'rtl_sha256': h, 'representative': rep.setdefault(h, case), **{x: json.load(open(f'{D}/gen/meta.json'))[x] for x in ('period', 'lut4', 'ffs', 'done_cycles')}})
    else:
        tail = open(f'{D}/bambu.log').read().strip().splitlines()[-1:] if os.path.exists(f'{D}/bambu.log') else ['not prepared']
        r.update({'failed': 'bambu/synthesis/RTL check', 'reason': tail[0][:300] if tail else ''})
    rows.append(r)
json.dump({'configurations': rows, 'designs': sorted(set(rep.values()), key=[r['case'] for r in rows].index)}, open(f'{B}/manifest.json', 'w'), indent=1)
print(len(rows), 'configurations,', len(rep), 'distinct designs,', sum('failed' in r for r in rows), 'failed')
" ;;
run)      python3 -c "import json;[print(d) for d in json.load(open('$B/manifest.json'))['designs']]" | while read d; do
              IFS=_ read k c m <<< "$d"; bash "$E/e8_bambu_run.sh" "$k" "${c#c}" "${m#m}"; done ;;
*) echo "usage: $0 prepare|manifest|run"; exit 2 ;;
esac
