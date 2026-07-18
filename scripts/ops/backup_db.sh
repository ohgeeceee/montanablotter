#!/bin/bash
set -euo pipefail

# Read MB_DB_PATH from .env if present, otherwise default to the new data dir.
DB_PATH="/root/montanablotter/data/blotter.db"
if [ -f /root/montanablotter/.env ]; then
    while IFS='=' read -r key value; do
        [ "$key" = "MB_DB_PATH" ] && DB_PATH="$value"
    done < <(grep '^MB_DB_PATH=' /root/montanablotter/.env)
fi
BACKUP_DIR="/root/montanablotter/db_backups"
BUCKET="s3://montanablotter-backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/blotter_$TIMESTAMP.db.gz"
LOG="/root/montanablotter/logs/backup.log"
LOCK_FILE="/root/montanablotter/.backup_db.lock"
TEMP_DB="$BACKUP_DIR/blotter_$TIMESTAMP.db"

mkdir -p "$BACKUP_DIR"
exec 9>"$LOCK_FILE"

if ! flock -n 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping backup; another backup run is still active." >> "$LOG"
    exit 0
fi

cleanup() {
    local exit_code=$?
    # Always remove temp files (the uncompressed copy used during gzip)
    rm -f "$TEMP_DB" "$TEMP_DB-journal" "$TEMP_DB-wal" "$TEMP_DB-shm"
    # Only remove the final .gz if the backup failed — on success we keep it
    if [[ $exit_code -ne 0 ]]; then
        rm -f "$BACKUP_FILE"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup failed with exit code $exit_code." >> "$LOG"
    fi
    exit "$exit_code"
}

run_low_priority() {
    if command -v ionice >/dev/null 2>&1; then
        ionice -c3 nice -n 19 "$@"
        return
    fi
    nice -n 19 "$@"
}

trap cleanup EXIT

find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'blotter_*.db' -o -name 'blotter_*.db-journal' -o -name 'blotter_*.db-wal' -o -name 'blotter_*.db-shm' \) \
    -mmin +180 -delete

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting backup..." >> "$LOG"

# Pre-flight: the backup stages a full uncompressed copy of the DB before
# gzip, so bail out loudly (instead of dying mid-copy with exit 120) when the
# volume can't hold DB size + 10% margin.
DB_SIZE_BYTES=$(stat -c %s "$DB_PATH")
FREE_BYTES=$(df -PB1 "$BACKUP_DIR" | awk 'NR==2 {print $4}')
NEEDED_BYTES=$(( DB_SIZE_BYTES + DB_SIZE_BYTES / 10 ))
if [ "$FREE_BYTES" -lt "$NEEDED_BYTES" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: only $((FREE_BYTES/1024/1024)) MB free, need $((NEEDED_BYTES/1024/1024)) MB (DB $((DB_SIZE_BYTES/1024/1024)) MB + 10%). Backup aborted." >> "$LOG"
    exit 1
fi

# Copy the live SQLite database in batches so web traffic keeps winning
# write contention. The source connection stays read-only and the destination
# copy runs at idle I/O priority.
# Tuned for a ~13 GB production DB (pages=1024 ~= 4 MB per batch).
run_low_priority /root/montanablotter/venv/bin/python3 - <<EOF
import sqlite3

src = sqlite3.connect("file:$DB_PATH?mode=ro", uri=True)
dst = sqlite3.connect("$TEMP_DB")
dst.execute("PRAGMA journal_mode=OFF")
dst.execute("PRAGMA synchronous=OFF")
src.backup(dst, pages=32768, sleep=0.1)
dst.close()
src.close()
EOF
run_low_priority gzip "$TEMP_DB"

# Upload to S3 only if credentials are present; keep local backup regardless.
if aws sts get-caller-identity >/dev/null 2>&1; then
    run_low_priority aws s3 cp "$BACKUP_FILE" "$BUCKET/$(basename "$BACKUP_FILE")" >> "$LOG" 2>&1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Uploaded $(basename "$BACKUP_FILE") to $BUCKET" >> "$LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARNING: AWS credentials not configured. Skipping S3 upload. Local backup kept at $BACKUP_FILE" >> "$LOG"
fi

# Remove local backup copies older than 7 days (documented rolling 7-day chain)
find "$BACKUP_DIR" -name "blotter_*.db.gz" -mtime +7 -delete

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Done." >> "$LOG"
