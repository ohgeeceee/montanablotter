#!/bin/bash
# Healthcheck — restart montanablotter if gunicorn stops responding.
# Runs every 3 minutes via cron. Logs to /root/montanablotter/logs/healthcheck.log

SERVICE="montanablotter"
PORT="5000"
LOG="/root/montanablotter/logs/healthcheck.log"
TIMEOUT=5   # seconds before curl gives up on one attempt
RETRIES=3
RETRY_DELAY=2
POST_RESTART_RETRIES=6
POST_RESTART_DELAY=5

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
