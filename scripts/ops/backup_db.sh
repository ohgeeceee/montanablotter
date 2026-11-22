#!/bin/bash
# Montana Blotter — database backup (rebuilt 2026-11-16 after path drift)
# Backs up blotter.db + page_views.db via SQLite online backup API (safe with WAL),
# gzips them, keeps 14 days locally, uploads to S3 only if aws cli is configured.
set -uo pipefail

APP_DIR="/opt/montanablotter"
BACKUP_DIR="$APP_DIR/db_backups"
LOG="$APP_DIR/logs/backup.log"
LOCK_FILE="$APP_DIR/.backup_db.lock"
RETENTION_DAYS=14
BUCKET="s3://montanablotter-backups"
PY="$APP_DIR/venv/bin/python3"
STAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR" "$APP_DIR/logs"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "[$(date '+%F %T')] SKIP: another backup is running" >> "$LOG"
    exit 0
fi

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

backup_one() {
    local db="$1" name="$2"
    local tmp="$BACKUP_DIR/${name}_${STAMP}.db"
    local out="$tmp.gz"
    [ -f "$db" ] || { log "MISS: $db not found"; return 1; }
    "$PY" - "$db" "$tmp" <<'PYEOF'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
s.backup(d)
d.close(); s.close()
PYEOF
    [ -f "$tmp" ] || { log "FAIL: snapshot of $db not created"; return 1; }
    gzip -9 "$tmp" || { log "FAIL: gzip $tmp"; rm -f "$tmp"; return 1; }
    log "OK: $(basename "$out") $(stat -c %s "$out") bytes"
    if command -v aws >/dev/null 2>&1 && aws sts get-caller-identity >/dev/null 2>&1; then
        aws s3 cp "$out" "$BUCKET/$(basename "$out")" >> "$LOG" 2>&1 \
            && log "OK: uploaded $(basename "$out") to $BUCKET" \
            || log "WARN: S3 upload failed for $(basename "$out")"
    fi
    return 0
}

rc=0
backup_one "$APP_DIR/blotter.db"            "blotter"     || rc=1
backup_one "$APP_DIR/page_views.db"         "page_views"  || rc=1
backup_one "/opt/snitchdirectory/snitch.db" "snitch"      || rc=1

# Retention: drop local backups older than N days
find "$BACKUP_DIR" -maxdepth 1 -name "*_*.db.gz" -mtime +$RETENTION_DAYS -delete

log "DONE rc=$rc"
exit $rc
