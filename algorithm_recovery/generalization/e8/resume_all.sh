#!/usr/bin/env bash
# After a power loss or WSL restart: restart all remaining E5/E8 work. Idempotent: a lane that is already running is
# not started again; finished designs are skipped and interrupted ones rerun (see each script). Check swap first
# (free -h): the extended reruns rely on it. Log of resume actions: build/resume.log.
E=/home/jiahao/hal/algorithm_recovery/generalization/e8; B=/home/jiahao/hal/build
echo "$(date -Is) resume_all (swap: $(awk '/SwapTotal/{print int($2/1048576)}' /proc/meminfo) GiB)" >> $B/resume.log
start() {   # start NAME SCRIPT [DIR]: unless a process running SCRIPT exists
    if pgrep -f "$2" > /dev/null; then echo "$1: already running"; return; fi
    ( cd "${3:-$B}" && setsid nohup bash "$2" >> "$B/resume_$1.out" 2>&1 < /dev/null & )
    echo "$1: started"
}
start watchdog "$E/orphan_watchdog.sh"
start e5_tail  "$E/resume_e5_tail.sh"
start e83      "$E/e8_bambu_parallel.sh" "$B/e8/bambu"
start extended "$E/extended_rerun.sh"
