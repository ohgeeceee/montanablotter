#!/bin/bash
# Memory guard: kills excess hermes agent workers when memory pressure is high.
# Runs every 2 minutes via systemd timer.
# Keeps at most MAX_PER_PROFILE workers per hermes profile.
# Kills the NEWEST workers first (they've made the least progress).

MAX_PER_PROFILE=6
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

# Emergency: if still critically low, kill heaviest hermes workers
available_mb=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
if [ "$available_mb" -lt "$MEM_CRIT_MB" ]; then
    echo "${ts} memory_guard: CRITICAL available_mb=${available_mb}, killing heaviest hermes workers"
    ps -eo pid,rss,cmd --no-headers | grep 'hermes -p' | sort -k2 -rn | head -5 | awk '{print $1}' | xargs -r kill -SIGTERM 2>/dev/null
fi
