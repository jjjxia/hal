#!/usr/bin/env bash
# E8 amendment 8 / E5 amendment n: designs whose stage aborted under the 6 GiB outer budget (std::bad_alloc) are rerun
# (E8.2, E8.3, E5) with the frozen pipeline's own limits (16 GiB, 3 h per stage; RAM 7.7 GB + 33 GB swap), serially,
# after the E5 remainder (E8.3 designs after the E8.3 lane). The 6 GiB outcome stays the declared-budget result; the
# earlier directories are kept (case.prev.* for E8.2, case.oom6g for E8.3, <case>.oom6g for E5). Log:
# build/extended_rerun.log; one result line per design ("E8.2 <case>: ...", "E8.3 <case>: ...", "E5 <case>: ...").
# Resumable (power loss): candidates are fixed once in build/extended_candidates_{e82,e5,e83}.txt; a candidate with a
# result line is skipped; a partial rerun directory is moved to *.interrupted_<time> and the design rerun.
B=/home/jiahao/hal/build; E=/home/jiahao/hal/algorithm_recovery/evaluation; L=$B/extended_rerun.log; R=$B/resume.log
done_line() { grep -q "^$1 $2: " $L 2>/dev/null; }
until grep -q 'tail done' $B/e5_tail.log; do sleep 120; done
export VMEM_KB=16777216
echo "extended reruns start $(date)" >> $L
C=$B/extended_candidates_e82.txt
if [ ! -s $C ]; then
    for D in $B/e8/fold/*/; do D=${D%/}
        [ -e "$D/score.json" ] && continue
        grep -q bad_alloc "$D/case/"*.log 2>/dev/null && basename $D
    done > $C
fi
for c in $(cat $C); do
    done_line E8.2 $c && continue
    IFS=_ read k ns p <<< "$c"          # e8_run.sh moves a previous case/ to case.prev.<time> itself
    echo "E8.2 $c: $(bash $E/kernel_foldings/e8_run.sh $k $ns $p 1 | tail -n 1 | cut -c1-300)" >> $L
done
C=$B/extended_candidates_e5.txt
if [ ! -s $C ]; then
    for c in chacha_r8 chacha_r12 chacha_r20 siphash_c1d3 siphash_c2d4 siphash_c4d8 sha256_sw aes_sw_core; do
        D=$B/bench2_e5/$c
        [ -e "$D/e5_summary.json" ] && continue
        grep -q bad_alloc "$D/"*.log 2>/dev/null && echo $c
    done > $C
fi
for c in $(cat $C); do
    done_line E5 $c && continue
    D=$B/bench2_e5/$c
    if [ ! -e "$D.oom6g" ]; then mv "$D" "$D.oom6g"
    elif [ -e "$D" ]; then t=$(date +%Y%m%d_%H%M%S); mv "$D" "$D.interrupted_$t"; echo "$(date -Is) resume: E5 $c extended rerun interrupted; moved to $c.interrupted_$t" >> $R; fi
    echo "E5 $c: $(bash $E/library_designs/e5_run_case.sh $c | tail -n 1 | cut -c1-300)" >> $L
done
until grep -q 'E8.3 done' $B/e8/bambu/queue.log; do sleep 120; done   # E8.3 designs only after its lane
C=$B/extended_candidates_e83.txt
if [ ! -s $C ]; then
    for D in $B/e8/bambu/*_c*_m*/; do D=${D%/}           # E8.3 (amendment 8c)
        [ -e "$D/v_metrics.json" ] && continue
        grep -q bad_alloc "$D/case/"*.log 2>/dev/null && basename $D
    done > $C
fi
for c in $(cat $C); do
    done_line E8.3 $c && continue
    D=$B/e8/bambu/$c
    if [ ! -e "$D/case.oom6g" ]; then mv "$D/case" "$D/case.oom6g"
    elif [ -e "$D/case" ]; then t=$(date +%Y%m%d_%H%M%S); mv "$D/case" "$D/case.interrupted_$t"; echo "$(date -Is) resume: E8.3 $c extended rerun interrupted; moved to case.interrupted_$t" >> $R; fi
    IFS=_ read k cl ml <<< "$c"
    echo "E8.3 $c: $(bash $E/kernel_foldings/e8_bambu_run.sh $k ${cl#c} ${ml#m} | tail -n 1 | cut -c1-300)" >> $L
done
echo "extended reruns done $(date)" >> $L
