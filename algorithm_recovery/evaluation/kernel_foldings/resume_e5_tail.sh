#!/usr/bin/env bash
# Resume the E5 remainder (sha256_sw, aes_sw_core) after a power loss: a design is finished if e5_summary.json exists
# or build/e5_tail.log has its result line; otherwise a partial directory is moved to <case>.interrupted_<time> and the
# design rerun from the start under the declared 6 GiB budget. Writes "tail done" to e5_tail.log at the end.
B=/home/jiahao/hal/build; E=/home/jiahao/hal/algorithm_recovery/evaluation; T=$B/e5_tail.log
grep -q 'tail done' $T 2>/dev/null && exit 0
pgrep -f 'e5_queue.sh|e5_run_case.sh' > /dev/null && { echo "E5 remainder already running"; exit 0; }
for c in sha256_sw aes_sw_core; do
    D=$B/bench2_e5/$c
    [ -s "$D/e5_summary.json" ] && continue
    grep -q "^$c: " $T 2>/dev/null && continue
    if [ -e "$D" ]; then t=$(date +%Y%m%d_%H%M%S); mv "$D" "$D.interrupted_$t"
        [ -e "$B/bench2_e5/$c.prepare.log" ] && mv "$B/bench2_e5/$c.prepare.log" "$B/bench2_e5/$c.prepare.log.interrupted_$t"
        echo "$(date -Is) resume: E5 $c interrupted; moved to $c.interrupted_$t" >> $B/resume.log; fi
    bash $E/library_designs/e5_run_case.sh $c >> $T 2>&1
done
echo "tail done $(date)" >> $T
