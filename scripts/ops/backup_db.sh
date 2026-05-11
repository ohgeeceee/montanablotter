#!/bin/bash
set -euo pipefail

DB_PATH="/root/montanablotter/blotter.db"
BACKUP_DIR="/root/montanablotter/db_backups"
BUCKET="s3://montanablotter-backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/blotter_$TIMESTAMP.db.gz"
LOG="/root/montanablotter/logs/backup.log"
LOCK_FILE="$BACKUP_DIR/.backup_db.lock"
TEMP_DB="$BACKUP_DIR/blotter_$TIMESTAMP.db"

mkdir -p "$BACKUP_DIR"
exec 9>"$LOCK_FILE"

if ! flock -n 9; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Skipping backup; another backup run is still active." >> "$LOG"
    exit 0
fi

cleanup() {
    local exit_code=$?
    rm -f "$TEMP_DB" "$TEMP_DB-journal" "$TEMP_DB-wal" "$TEMP_DB-shm"
    if [[ $exit_code -ne 0 ]]; then
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

# Copy the live SQLite database in small batches so web traffic keeps winning
# write contention. The source connection stays read-only and the destination
# copy runs at idle I/O priority.
run_low_priority /root/montanablotter/venv/bin/python3 - <<EOF
import sqlite3

src = sqlite3.connect("file:$DB_PATH?mode=ro", uri=True)
dst = sqlite3.connect("$TEMP_DB")
src.backup(dst, pages=64, sleep=0.050)
dst.close()
src.close()
EOF
run_low_priority gzip "$TEMP_DB"

# Upload to S3
run_low_priority aws s3 cp "$BACKUP_FILE" "$BUCKET/$(basename "$BACKUP_FILE")" >> "$LOG" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Uploaded $(basename "$BACKUP_FILE") to $BUCKET" >> "$LOG"

# Remove local backup copies older than 7 days
find "$BACKUP_DIR" -name "blotter_*.db.gz" -mtime +7 -delete

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Done." >> "$LOG"
