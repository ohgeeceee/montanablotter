# Red-tier: Fix stale import in ingestion/run_all_scrapers.py

## Summary
`ingestion/run_all_scrapers.py` fails immediately with ImportError because it tries to import `run_alert_delivery` from `services.alerts.engine`, which no longer exports that symbol. The function moved to `ingestion/alert_engine.py`.

## Proposed fix
Edit `ingestion/run_all_scrapers.py` line 17:
- Remove `run_alert_delivery` from the `from services.alerts.engine import ...` line.
- Optionally add `from ingestion.alert_engine import run_alert_delivery` if the script still needs it, or remove all references if it is unused.

## Reasoning
This script is the unified scraper runner for Transparency Portal, Professional Boards, Federal Courts, and other sources that do not have individual cron entries. It is currently not in the cron schedule because it crashes on startup. Fixing the import restores coverage for those sources.

## Rollback
`git checkout -- ingestion/run_all_scrapers.py`

## Verification
Run: `cd /root/montanablotter && /root/montanablotter/venv/bin/python3 ingestion/run_all_scrapers.py --help`
Should print help text instead of ImportError.
