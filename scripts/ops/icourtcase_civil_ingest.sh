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

"$ROOT/scripts/ops/icourtcase_preflight.sh"
"$PY" -m services.ingestion.icourtcase_civil_runner "$@"
