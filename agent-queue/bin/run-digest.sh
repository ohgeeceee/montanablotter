#!/usr/bin/env bash
set -euo pipefail
export PATH="/root/.local/bin:$PATH"

# Drive the digest cycle:
#   1. Ask each profile to write its raw section
#   2. Run the aggregator
#   3. Optionally pipe the result through a delivery command
#
# Configure delivery via env:
#   DIGEST_DELIVERY_CMD='mail -s "Montana Blotter daily" you@example.com'
#   DIGEST_DELIVERY_CMD='curl -sS -X POST -H "Content-Type: text/markdown" --data-binary @- $SLACK_WEBHOOK'
# If unset, the digest is only written to disk.

export AGENT_QUEUE="${AGENT_QUEUE:-/root/montanablotter/agent-queue}"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
MODE="${1:-daily}"
TODAY="$(date +%F)"
WEEK="$(date +%G-W%V)"

case "$MODE" in
    daily)
        STAMP="$TODAY"
        PROMPT_KIND="daily raw digest section"
        ;;
    weekly)
        STAMP="$WEEK"
        PROMPT_KIND="weekly raw digest section"
        ;;
    *)
        echo "Usage: $0 daily|weekly" >&2
        exit 1
        ;;
esac

mkdir -p "$AGENT_QUEUE/digests/raw"/{ops,ingest,dev,civic}

# Step 1 — trigger each profile.
# NOTE: confirm the invocation form against your Hermes version. Profiles
# typically register as commands once `hermes profile create <name>` is run.
# Alternative form: `hermes chat --profile <name> -q "..."`
for PROFILE in blotter-ops blotter-ingest blotter-dev blotter-civic; do
    SHORT="${PROFILE#blotter-}"
    OUT="$AGENT_QUEUE/digests/raw/$SHORT/$STAMP.md"
    PROMPT="Write your $PROMPT_KIND to $OUT per the schema in MISSION.md.
Use only Green-tier observation. Do not take any action beyond writing
this single file. Reply only with the path you wrote."

    echo "[$(date +%T)] triggering $PROFILE"
    if ! "$PROFILE" chat -q "$PROMPT"; then
        echo "[$(date +%T)] WARN: $PROFILE failed; aggregator will mark missing" >&2
    fi
done

# Step 2 — small settle, then aggregate.
sleep 5
DIGEST_PATH="$(python3 "$SCRIPT_DIR/aggregator.py" "$MODE" "$STAMP")"
echo "[$(date +%T)] aggregated → $DIGEST_PATH"

# Step 3 — optional delivery.
if [[ -n "${DIGEST_DELIVERY_CMD:-}" ]]; then
    echo "[$(date +%T)] delivering via DIGEST_DELIVERY_CMD"
    eval "$DIGEST_DELIVERY_CMD" < "$DIGEST_PATH"
else
    echo "[$(date +%T)] no DIGEST_DELIVERY_CMD set; written to disk only"
fi
