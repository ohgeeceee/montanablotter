#!/bin/bash
# git_sync.sh — keep vm2778 in sync with GitHub; auto-deploy on upstream changes.
set -uo pipefail
APP=/opt/montanablotter
LOG=$APP/logs/git_sync.log
LOCK=$APP/.git_sync.lock
cd "$APP" || exit 1
mkdir -p "$APP/logs"
exec 9>"$LOCK"; flock -n 9 || exit 0
log(){ echo "[$(date '+%F %T')] $*" >> "$LOG"; }

git fetch origin -q 2>>"$LOG" || { log "fetch failed"; exit 1; }
LOCAL=$(git rev-parse HEAD); REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0

if git merge-base --is-ancestor "$REMOTE" "$LOCAL"; then
    # Local ahead: push, but never push anything that smells like a secret.
    if git diff "$REMOTE..$LOCAL" | grep -qaiE "BEGIN OPENSSH PRIVATE KEY|BEGIN RSA PRIVATE KEY|github_pat_[A-Za-z0-9_]{20}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10}"; then
        log "REFUSED push: possible secret in outgoing commits"; exit 1
    fi
    git push origin main >>"$LOG" 2>&1 && log "pushed local commits to origin/main" || log "push failed (auth not set up yet?)"
    exit 0
fi

if git merge-base --is-ancestor "$LOCAL" "$REMOTE"; then
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        log "SKIP deploy: dirty working tree"; exit 1
    fi
    OLD=$LOCAL
    REQ_CHANGED=$(git diff --name-only "$LOCAL..$REMOTE" | grep -c "^requirements" || true)
    git pull --ff-only -q >>"$LOG" 2>&1 || { log "pull failed"; exit 1; }
    if [ "$REQ_CHANGED" -gt 0 ]; then
        "$APP/venv/bin/pip" install -q -r "$APP/requirements.txt" >>"$LOG" 2>&1 || log "WARN pip install issues"
    fi
    sudo systemctl restart mtblotter
    sleep 8
    CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://127.0.0.1:5000/)
    if [ "$CODE" = "200" ]; then
        log "deployed ${REMOTE:0:8} OK"
    else
        log "DEPLOY FAILED (http $CODE) — rolling back to ${OLD:0:8}"
        git reset --hard "$OLD" >>"$LOG" 2>&1
        sudo systemctl restart mtblotter
    fi
    exit 0
fi
log "DIVERGED from origin/main — manual action needed"
