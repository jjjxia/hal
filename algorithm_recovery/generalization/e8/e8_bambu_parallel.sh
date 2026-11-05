#!/usr/bin/env bash
# E8 amendment 8: the E8.3 designs of build/e8/bambu/manifest.json (declared order), up to K at a time; a design is
# started only when MemAvailable >= MIN_MB (swap is the safety margin). Each design runs e8_bambu_run.sh unchanged
# (6 GiB, 3 h per stage). Log: build/e8/bambu/queue.log.
# Resumable (power loss): a design is finished when queue.log has its result line "<case>: ..."; a design that was
# started but has no result line was interrupted: its case/ is moved to case.interrupted_<time> and it is rerun from
# the start (the Bambu output gen/ is kept). DRY=1 only prints what would be done.
K=${K:-3}; MIN_MB=${MIN_MB:-1500}; DRY=${DRY:-0}
E=/home/jiahao/hal/algorithm_recovery/generalization/e8; B=/home/jiahao/hal/build/e8/bambu
[ "$DRY" = 1 ] || echo "E8.3 parallel start $(date) K=$K MIN_MB=$MIN_MB" >> "$B/queue.log"
for d in $(python3 -c "import json;[print(x) for x in json.load(open('$B/manifest.json'))['designs']]"); do
    if grep -q "^$d: " "$B/queue.log" 2>/dev/null; then [ "$DRY" = 1 ] && echo "skip (finished): $d"; continue; fi
    if [ -e "$B/$d/case" ]; then
        t=$(date +%Y%m%d_%H%M%S)
        if [ "$DRY" = 1 ]; then echo "interrupted, would move case -> case.interrupted_$t and rerun: $d"; continue; fi
        mv "$B/$d/case" "$B/$d/case.interrupted_$t"
        echo "$(date -Is) resume: $d interrupted; case moved to case.interrupted_$t" >> /home/jiahao/hal/build/resume.log
    elif [ "$DRY" = 1 ]; then echo "would run: $d"; continue; fi
    IFS=_ read k c m <<< "$d"
    while :; do
        avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
        [ "$(jobs -rp | wc -l)" -lt "$K" ] && [ "$avail" -ge "$MIN_MB" ] && break
        sleep 30
    done
    bash "$E/e8_bambu_run.sh" "$k" "${c#c}" "${m#m}" >> "$B/queue.log" 2>&1 &
    sleep 90    # let the new design allocate before the next memory check
done
[ "$DRY" = 1 ] && exit 0
wait
echo "E8.3 done $(date)" >> "$B/queue.log"
