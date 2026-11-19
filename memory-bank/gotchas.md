# Gotchas

Hard-won lessons. Append with a date; don't delete — strike through if obsolete.

## Repo & secrets

- **This repo is PUBLIC** (`ohgeeceee/montanablotter`). No secrets, no `.env`, no
  `*.db`, no logs, no real PII — ever. gitleaks runs in CI (`.gitleaks.toml`) and
  `git_sync.sh` scans before pushing.
- An old fine-grained PAT lived in a git remote URL until 2026-11-16. If you find a
  credential anywhere in history, treat it as compromised: scrub + tell the owner to
  revoke.
- Chat interfaces can mangle `+`/`/` in pasted keys. Share keys as downloadable
  files, not pasted text (burned us 2026-11-16).

## Deploys & ops

- **Pushing to `main` deploys to production in ~2 min** and restarts the app via
  `git_sync.sh`. Never push untested code "to see if it works" — there is no staging.
- Doc drift is real: older docs say `/root/montanablotter` + `montanablotter.service`;
  production is `/opt/montanablotter` + `mtblotter.service`, user `ubuntu`
  (see `active-context.md`).
- **SQLite is in WAL mode.** Never `cp` a live DB; use `scripts/ops/backup_db.sh`.
- Two page-view stores exist: the `page_views.db` file and a stale `page_views`
  table inside `blotter.db`. Use the file.

## Codebase

- `app.py` is a ~660 KB monolith. New routes go in `blueprints/`, new domain logic in
  `services/` — don't grow the monolith.
- Root has scattered `test_*.py` one-offs and `.bak` files; the real suite is
  `tests/`. Run `./venv/bin/python3 -m pytest` before pushing code.
- Tests must patch `DB_PATH` to a temp DB. Never run anything against production
  `blotter.db` from a test or a REPL experiment.
- 2026-05 handoff flagged `/alerts` 500 from `import alert_dispatcher` (real module:
  `services.alerts.dispatcher`). Verify before building on alerts.

## Agent fleet

- Multiple agents (Hermes, Codex, Kimi Code, OpenClaw) commit on the same box. Pull
  before you start; keep commits small and Conventional-Commit formatted to make the
  2-minute sync painless.
- Red-tier actions (code changes, restarts, DB writes outside ingestion) need a
  human-approved `agent-queue/` draft per `MISSION.md`.
