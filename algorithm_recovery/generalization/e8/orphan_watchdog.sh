#!/usr/bin/env bash
# Terminates state-replay compilers left behind by a FINISHED analyze stage (state_replay.cpp's child timeout ends the
# driver but not the g++ grandchild, which is reparented to init and keeps up to ~5 GB). A process is terminated only if
# it is cc1plus, its command line names <case>/analysis/state_replay under build/, and <case>/stages.jsonl already
# records the analyze stage (its exit status and all metrics are written by then). Log: build/orphan_cleanup.log.
L=/home/jiahao/hal/build/orphan_cleanup.log
while true; do
    for pid in $(pgrep -x cc1plus); do
        cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null) || continue
        case_dir=$(grep -o '/home/jiahao/hal/build/[^ ]*/analysis/state_replay' <<< "$cmd" | head -1 | sed 's|/analysis/state_replay$||')
        [ -n "$case_dir" ] || continue
        grep -q '"stage":"analyze"' "$case_dir/stages.jsonl" 2>/dev/null || continue
        rss=$(awk '/VmRSS/{print $2}' /proc/$pid/status 2>/dev/null)
        kill "$pid" 2>/dev/null && echo "$(date -Is) terminated cc1plus $pid (rss ${rss} kB) of finished analyze: $case_dir" >> "$L"
    done
    sleep 60
done
