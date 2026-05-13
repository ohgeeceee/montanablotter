#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/montanablotter"
PY="$ROOT/venv/bin/python"

cd "$ROOT"
"$PY" -m services.ingestion.civil_violations_pipeline "$@"
