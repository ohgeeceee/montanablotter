#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/montanablotter"
PY="$ROOT/venv/bin/python"

if [[ -z "${MB_SEX_OFFENDER_CACHE_FILE:-}" ]]; then
  echo "MB_SEX_OFFENDER_CACHE_FILE is not set"
  exit 1
fi

cd "$ROOT"
"$PY" -m services.persons.sex_offender_import --file "$MB_SEX_OFFENDER_CACHE_FILE" "${@}"
