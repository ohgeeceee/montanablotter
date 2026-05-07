#!/bin/bash
# =============================================================================
# Cron wrapper for email_blotter_ingest.py
# =============================================================================
# Add to crontab to run every 15 minutes:
#   */15 * * * * /root/montanablotter/scripts/ops/email_blotter_cron.sh >> /var/log/montanablotter/email_blotter.log 2>&1
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
LOG_DIR="/var/log/montanablotter"
LOG_FILE="$LOG_DIR/email_blotter.log"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Activate virtualenv if present
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

cd "$PROJECT_ROOT"

# Run with unbuffered output so logs are written immediately
export PYTHONUNBUFFERED=1

python3 "$PROJECT_ROOT/email_blotter_ingest.py" "$@"
