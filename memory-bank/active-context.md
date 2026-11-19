# Active Context

*Last verified: 2026-11-16 (direct SSH session on vm2778). Update this file whenever
deployment reality changes. Older docs may contradict this file — this file wins for
ops facts dated 2026-11-16 or later; verify on the server if unsure.*

## Production reality (verified 2026-11-16)

| Fact | Value |
|------|-------|
| VPS | DreamHost `vm2778`, Ubuntu 26.04.1 LTS, IP 208.113.214.199 |
| Server user | `ubuntu` (passwordless sudo) |
| App path | `/opt/montanablotter` |
| App service | systemd `mtblotter.service` → gunicorn on `127.0.0.1:5000` |
| Front | nginx on 80/443, certbot auto-renews TLS |
| Cache/queue | redis on 6379 |
| Main DB | `/opt/montanablotter/blotter.db` (~71 MB, WAL mode) |
| Other DBs | `page_views.db` (separate file), `/opt/snitchdirectory/snitch.db` |
| Sister site | snitchdirectory.com — Flask on port 5001, units `snitchdirectory.service` + `snitchdirectory-bot.service`, code `/opt/snitchdirectory` |
| GitHub auth on box | SSH key `~/.ssh/id_ed25519` (on the `ohgeeceee` account) |

## Deploy pipeline (ubuntu crontab, installed 2026-11-16)

- **Every 2 min** — `scripts/ops/git_sync.sh`: fetch origin; push if local-ahead
  (with secret scan); ff-only pull if remote-ahead, restart `mtblotter`, health-check
  :5000, auto-rollback on failure. **Pushing to `main` on GitHub deploys to production
  within ~2 minutes and restarts the app.**
- **03:00** — `scripts/ops/backup_db.sh`: WAL-safe SQLite snapshots, gzip, 14-day
  retention in `/opt/montanablotter/db_backups/`.
- **03:30** — `scripts/ops/offsite_backup.sh`: gpg AES-256 encrypted push to private
  repo `ohgeeceee/mtblotter-backups`.

## Known doc drift (fix as you touch)

- `AGENTS.md`, `HANDOFF-paywall.md`, and `docs/notes.md` reference `/root/montanablotter`
  and `montanablotter.service`. Current reality: `/opt/montanablotter`, user `ubuntu`,
  unit `mtblotter.service` (since ~Nov 2026).
- `HANDOFF-paywall.md` (2026-05) cites `data/blotter.db` at ~13 GB. The old
  montanablotter.com database was **lost** (the rebrand to mtblotter.com started
  fresh); the current DB is ~71 MB. Treat pre-loss record counts in old docs as
  historical, not current.

## Current focus

1. **Paywall / subscriptions** — handoff spec exists (`HANDOFF-paywall.md`, May 2026);
   `services/monetization/` now exists, so work partially landed. Audit against the
   handoff's acceptance criteria before writing new code.
2. **Snitch Directory redesign** — landing + forum done in archival-ledger style
   (2026-11-16/17); old-theme templates remaining: thread, submit, contact, faq,
   auth, admin_*, embed, press, premium.
3. **County/source coverage** — see `docs/COUNTY_COVERAGE_EXPANSION_2026-08-11.md`,
   `county_sources_manifest.md`, `docs/jail_tier2_assessment.md`.

## Open questions

- Is the `/alerts` 500 import bug from the paywall handoff fixed? (`app.py` importing
  `alert_dispatcher` instead of `services.alerts.dispatcher`.)
- Did the old fine-grained PAT (scrubbed from git remote 2026-11-16) get revoked in
  GitHub settings? Owner holds that action.
