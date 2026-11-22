#!/bin/bash
# offsite_backup.sh — encrypt latest DB dumps and push to private GitHub repo.
set -uo pipefail
APP=/opt/montanablotter
DEST=$APP/db_backups
REPO_DIR=/home/ubuntu/mtblotter-backups-repo
LOG=$APP/logs/offsite_backup.log
PASS_FILE=$APP/.backup_passphrase
REMOTE=git@github.com:ohgeeceee/mtblotter-backups.git
DOW=$(date +%a | tr "A-Z" "a-z")
mkdir -p "$APP/logs"
log(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

if [ ! -d "$REPO_DIR/.git" ]; then
    git clone "$REMOTE" "$REPO_DIR" >>"$LOG" 2>&1 || { log "clone failed"; exit 1; }
fi
cd "$REPO_DIR" || exit 1
git pull -q >>"$LOG" 2>&1

for name in blotter page_views snitch; do
    src=$(ls -t "$DEST/${name}_"*.db.gz 2>/dev/null | head -1)
    [ -n "$src" ] || { log "no dump found for $name"; continue; }
    out="${name}_${DOW}.db.gz.gpg"
    gpg --batch --yes --passphrase-file "$PASS_FILE" --symmetric --cipher-algo AES256 -o "$out" "$src" \
        && log "encrypted $out ($(stat -c %s "$out") bytes)" || log "FAIL encrypting $name"
    [ "$(date +%d)" = "01" ] && cp "$out" "${name}_monthly.db.gz.gpg"
done
git add -A
git commit -q -m "nightly $(date '+%F %T')" >>"$LOG" 2>&1
git push -q >>"$LOG" 2>&1 && log "pushed to GitHub" || log "push failed"
