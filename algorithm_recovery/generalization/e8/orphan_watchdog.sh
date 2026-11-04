#!/usr/bin/env bash
# Terminates state-replay compilers whose driver is gone. state_replay.cpp's child timeout ends the driver but not the
# g++ grandchild (cc1plus), which is reparented to init and keeps up to ~5 GB; analyze may retry and start a new
# compile while the orphan still runs. A process is terminated only if it is cc1plus, its command line names
# <case>/analysis/state_replay under build/, and EITHER its parent is init (the driver that would read its output has
# exited) OR <case>/stages.jsonl already records the analyze stage. The live compile of a running analyze (parent: its
# c++ driver) is never touched. Log: build/orphan_cleanup.log. Checked every 20 s.
L=/home/jiahao/hal/build/orphan_cleanup.log
while true; do
    for pid in $(pgrep -x cc1plus); do
        cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null) || continue
        case_dir=$(grep -o '/home/jiahao/hal/build/[^ ]*/analysis/state_replay' <<< "$cmd" | head -1 | sed 's|/analysis/state_replay$||')
        [ -n "$case_dir" ] || continue
        ppid=$(awk '/^PPid/{print $2}' /proc/$pid/status 2>/dev/null)
        parg0=$(tr '\0' '\n' < /proc/$ppid/cmdline 2>/dev/null | head -1)   # WSL's reparenting init relay runs as /init
        why=""
        { [ "$ppid" = 1 ] || [ "$parg0" = /init ]; } && why="parent init"
        case "$(cat /proc/$ppid/comm 2>/dev/null)" in c++|g++|gcc|cc)   # driver itself orphaned
            gp=$(awk '/^PPid/{print $2}' /proc/$ppid/status 2>/dev/null)
            { [ "$gp" = 1 ] || [ "$(tr '\0' '\n' < /proc/$gp/cmdline 2>/dev/null | head -1)" = /init ]; } && why="driver's parent init" ;;
        esac
        grep -q '"stage":"analyze"' "$case_dir/stages.jsonl" 2>/dev/null && why="${why:+$why, }analyze finished"
        [ -n "$why" ] || continue
        rss=$(awk '/VmRSS/{print $2}' /proc/$pid/status 2>/dev/null)
        kill "$pid" 2>/dev/null && echo "$(date -Is) terminated cc1plus $pid (rss ${rss} kB; $why): $case_dir" >> "$L"
    done
    sleep 20
done
