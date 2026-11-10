#!/usr/bin/env bash
# R2 (RECOVERY_QUALITY_PROTOCOL.txt): R2a random DFGs (seeds 101-120 x N in {2,4,8,16,32}, 4 at a time), then
# R2b kernels (8 kernels x {4min, 8min} x {asap, random}, seed 1, 2 at a time). Resumable: finished designs are skipped
# by the runners. Log: build/r2/queue.log.
set -u
H=/home/jiahao/hal; E=$H/algorithm_recovery/evaluation; B=$H/build; L=$B/r2/queue.log
echo "R2a start $(date -Is)" >> $L
for N in 2 4 8 16 32; do for s in $(seq 101 120); do echo "$s $N"; done; done | \
    E5C_ROOT=$B/r2/random xargs -P 4 -L 1 bash -c 'bash '"$E"'/synthetic_foldings/e5c_run.sh $0 $1 none' >> $L 2>&1
echo "R2a done $(date -Is)" >> $L
for k in fir8 biquad biquad2 lattice4 horner6 matvec4 dct8 arf; do for n in 4min 8min; do for p in asap random; do echo "$k $n $p"; done; done; done | \
    E8_FOLD_ROOT=$B/r2/kernels xargs -P 2 -L 1 bash -c 'bash '"$E"'/kernel_foldings/e8_run.sh $0 $1 $2 1' >> $L 2>&1
echo "R2b done $(date -Is)" >> $L
