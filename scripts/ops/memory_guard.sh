#!/bin/bash
# Memory guard: kills excess hermes agent workers when memory pressure is high.
# Runs every 2 minutes via systemd timer.
# Keeps at most MAX_PER_PROFILE workers per hermes profile.
# Kills the NEWEST workers first (they've made the least progress).

MAX_PER_PROFILE=6
MAX_PER_PROFILE_PRESSURE=2
MAX_OPENCLAW_AGENTS=1
MEM_WARN_MB=600
MEM_CRIT_MB=300

available_mb=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
swap_used_pct=$(free | awk '/Swap/{if($2>0) printf "%.0f", $3/$2*100; else print 0}')
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Only act if memory is under pressure
if [ "$available_mb" -gt "$MEM_WARN_MB" ] && [ "$swap_used_pct" -lt 70 ]; then
    exit 0
fi

killed_total=0

for profile in blotter-ingest blotter-ops blotter-dev blotter-civic blotter-parser blotter-scraper; do
    mapfile -t pids < <(pgrep -f "hermes -p ${profile}" 2>/dev/null)
    count=${#pids[@]}
    if [ "$count" -le "$MAX_PER_PROFILE" ]; then
        continue
    fi

    # Sort by process start time ascending (oldest first); kill the newest (tail)
    mapfile -t sorted_pids < <(
        for pid in "${pids[@]}"; do
            start=$(stat -c %Y /proc/$pid/stat 2>/dev/null || echo 0)
            echo "$start $pid"
        done | sort -n | awk '{print $2}'
    )

    excess=$(( count - MAX_PER_PROFILE ))
    to_kill=("${sorted_pids[@]: -$excess}")
    for pid in "${to_kill[@]}"; do
        kill -SIGTERM "$pid" 2>/dev/null && (( killed_total++ ))
    done

    echo "${ts} memory_guard: profile=${profile} had=${count} killed=${excess} available_mb=${available_mb} swap_pct=${swap_used_pct}%"
done

if [ "$killed_total" -gt 0 ]; then
    echo "${ts} memory_guard: total killed=${killed_total} available_mb=${available_mb} swap_pct=${swap_used_pct}%"
fi

# Under pressure, cap hermes workers more aggressively
if [ "$available_mb" -lt "$MEM_WARN_MB" ] || [ "$swap_used_pct" -ge 70 ]; then
    max_profile=$MAX_PER_PROFILE_PRESSURE
    for profile in blotter-ingest blotter-ops blotter-dev blotter-civic blotter-parser blotter-scraper; do
        mapfile -t pids < <(pgrep -f "hermes -p ${profile}" 2>/dev/null)
        count=${#pids[@]}
        if [ "$count" -le "$max_profile" ]; then
            continue
        fi
        mapfile -t sorted_pids < <(
            for pid in "${pids[@]}"; do
                start=$(stat -c %Y /proc/$pid/stat 2>/dev/null || echo 0)
                echo "$start $pid"
            done | sort -n | awk '{print $2}'
        )
        excess=$(( count - max_profile ))
        to_kill=("${sorted_pids[@]: -$excess}")
        for pid in "${to_kill[@]}"; do
            kill -SIGTERM "$pid" 2>/dev/null && (( killed_total++ ))
        done
        echo "${ts} memory_guard: pressure profile=${profile} had=${count} cap=${max_profile} killed=${excess}"
    done
fi

# OpenClaw cron agents pile up when runs exceed the 8-minute stagger (~400MB each).
mapfile -t openclaw_pids < <(pgrep -f 'openclaw-agent' 2>/dev/null)
oc_count=${#openclaw_pids[@]}
if [ "$oc_count" -gt "$MAX_OPENCLAW_AGENTS" ] && { [ "$available_mb" -lt "$MEM_WARN_MB" ] || [ "$swap_used_pct" -ge 60 ]; }; then
    mapfile -t oc_sorted < <(
        for pid in "${openclaw_pids[@]}"; do
            start=$(stat -c %Y /proc/$pid/stat 2>/dev/null || echo 0)
            echo "$start $pid"
        done | sort -n | awk '{print $2}'
    )
    excess=$(( oc_count - MAX_OPENCLAW_AGENTS ))
    to_kill=("${oc_sorted[@]: -$excess}")
    for pid in "${to_kill[@]}"; do
        kill -SIGTERM "$pid" 2>/dev/null && (( killed_total++ ))
    done
    echo "${ts} memory_guard: openclaw had=${oc_count} killed=${excess} available_mb=${available_mb} swap_pct=${swap_used_pct}%"
fi

# Emergency: if still critically low, kill heaviest hermes workers
available_mb=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
if [ "$available_mb" -lt "$MEM_CRIT_MB" ]; then
    echo "${ts} memory_guard: CRITICAL available_mb=${available_mb}, killing heaviest hermes workers"
    ps -eo pid,rss,cmd --no-headers | grep 'hermes -p' | sort -k2 -rn | head -5 | awk '{print $1}' | xargs -r kill -SIGTERM 2>/dev/null
fi
