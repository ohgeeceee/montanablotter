#!/bin/bash
# Healthcheck — restart montanablotter if gunicorn stops responding.
# Runs every 3 minutes via cron. Logs to /root/montanablotter/logs/healthcheck.log

SERVICE="montanablotter"
PORT="5000"
LOG="/root/montanablotter/logs/healthcheck.log"
TIMEOUT=15   # seconds before curl gives up on one attempt
RETRIES=5
RETRY_DELAY=3
POST_RESTART_RETRIES=10
POST_RESTART_DELAY=3
MIN_UPTIME_SECS=300  # don't restart again within 5 min of a prior start

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

# Quick HTTP check against a dedicated lightweight liveness endpoint.
# The homepage does real DB work and can be slow or blocked under load.
for attempt in $(seq 1 "$RETRIES"); do
    if curl -sf --max-time "$TIMEOUT" "http://127.0.0.1:${PORT}/healthz" -o /dev/null; then
        exit 0
    fi
    if [ "$attempt" -lt "$RETRIES" ]; then
        sleep "$RETRY_DELAY"
    fi
done

# No response — log and restart
echo "$(timestamp) [WARN] gunicorn not responding on :${PORT} — restarting ${SERVICE}" >> "$LOG"

# Avoid restart loops: if the service was just started, log and bail.
active_enter=$(systemctl show "${SERVICE}.service" -p ActiveEnterTimestampMonotonic --value 2>/dev/null || echo 0)
now_monotonic=$(awk '/^now/ {print $2}' /proc/timer_list 2>/dev/null || echo 0)
if [ -n "$active_enter" ] && [ -n "$now_monotonic" ] && [ "$active_enter" -gt 0 ] && [ "$now_monotonic" -gt 0 ]; then
    uptime_secs=$(( (now_monotonic - active_enter) / 1000000000 ))
    if [ "$uptime_secs" -lt "$MIN_UPTIME_SECS" ]; then
        echo "$(timestamp) [WARN] ${SERVICE} started only ${uptime_secs}s ago; skipping restart to avoid loop" >> "$LOG"
        exit 0
    fi
fi

systemctl restart "$SERVICE"

# Give the service a brief grace period, then verify the cheap liveness probe
# rather than the homepage. The homepage does real DB work and can be slow
# under normal load, which creates false negatives during recovery.
for attempt in $(seq 1 "$POST_RESTART_RETRIES"); do
    if curl -sf --max-time "$TIMEOUT" "http://127.0.0.1:${PORT}/healthz" -o /dev/null; then
        echo "$(timestamp) [OK] ${SERVICE} restarted successfully" >> "$LOG"
        exit 0
    fi
    sleep "$POST_RESTART_DELAY"
done

echo "$(timestamp) [ERROR] ${SERVICE} still not responding after restart" >> "$LOG"
