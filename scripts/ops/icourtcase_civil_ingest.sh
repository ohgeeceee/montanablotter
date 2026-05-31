#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/montanablotter"
PY="$ROOT/venv/bin/python"

cd "$ROOT"

# Optional secure sidecar env file for session cookies/endpoints.
# Keep this file mode 600 and root-owned.
if [[ -f "$ROOT/.secrets/icourtcase.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.secrets/icourtcase.env"
fi

PREFLIGHT_OUT=$("$ROOT/scripts/ops/icourtcase_preflight.sh" 2>&1) && PREFLIGHT_OK=1 || PREFLIGHT_OK=0

if [[ "$PREFLIGHT_OK" -eq 0 ]]; then
  echo "$PREFLIGHT_OUT"
  # Record the error on all enabled sources so the alert script shows the real cause.
  "$PY" - <<PYEOF
import sqlite3
msg = """$PREFLIGHT_OUT"""[:500]
conn = sqlite3.connect("blotter.db")
conn.execute(
    "UPDATE civil_filing_sources SET last_error=?, updated_at=datetime('now') WHERE adapter_type='icourtcase' AND is_enabled=1",
    (msg,),
)
conn.commit()
conn.close()
PYEOF
  exit 1
fi

echo "$PREFLIGHT_OUT"
"$PY" -m services.ingestion.icourtcase_civil_runner "$@"
