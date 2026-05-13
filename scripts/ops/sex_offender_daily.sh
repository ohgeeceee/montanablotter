#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/montanablotter"
PY="$ROOT/venv/bin/python"

cd "$ROOT"

if [[ -f "$ROOT/.secrets/sex_offender.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.secrets/sex_offender.env"
fi

# Primary source: public ArcGIS feed used by Montana SVOR website.
"$PY" -m services.persons.sex_offender_arcgis_sync --full-sync
# Secondary delta computation for change tracking/alerts.
"$PY" -m services.persons.sex_offender_delta
