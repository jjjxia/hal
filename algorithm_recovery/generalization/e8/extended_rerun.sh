#!/usr/bin/env bash
# E8 amendment 8 / E5 amendment n: designs whose stage aborted under the 6 GiB outer budget (std::bad_alloc) are rerun
# with the frozen pipeline's own limits (16 GiB, 3 h per stage; RAM 7.7 GB + 33 GB swap), serially, after the E5
# remainder. The 6 GiB outcome stays the declared-budget result; the earlier directories are kept (case.prev.* for
# E8.2, <case>.oom6g for E5). Log: build/extended_rerun.log.
B=/home/jiahao/hal/build; E=/home/jiahao/hal/algorithm_recovery/generalization; L=$B/extended_rerun.log
until grep -q 'tail done' $B/e5_tail.log; do sleep 120; done
export VMEM_KB=16777216
echo "extended reruns start $(date)" >> $L
for D in $B/e8/fold/*/; do
    D=${D%/}; c=$(basename $D)
    [ -e "$D/score.json" ] && continue
    grep -q '"exit":134' "$D/case/stages.jsonl" 2>/dev/null && grep -q bad_alloc "$D/case/"*.log 2>/dev/null || continue
    IFS=_ read k ns p <<< "$c"
    echo "E8.2 $c: $(bash $E/e8/e8_run.sh $k $ns $p 1 | tail -n 1 | cut -c1-300)" >> $L
done
for c in chacha_r8 chacha_r12 chacha_r20 siphash_c1d3 siphash_c2d4 siphash_c4d8 sha256_sw aes_sw_core; do
    D=$B/bench2_e5/$c
    [ -e "$D/e5_summary.json" ] && continue
    grep -q bad_alloc "$D/"*.log 2>/dev/null || continue
    mv "$D" "$D.oom6g"
    echo "E5 $c: $(bash $E/bench2/e5_run_case.sh $c | tail -n 1 | cut -c1-300)" >> $L
done
echo "extended reruns done $(date)" >> $L
