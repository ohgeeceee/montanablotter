# Active Context

*Last verified: 2026-11-16 (direct SSH session on vm2778) for deployment facts;
paywall status code-audited 2026-11-19. Update this file whenever reality changes.
Older docs may contradict this file — this file wins for facts dated 2026-11-16 or
later; verify on the server if unsure.*

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

- Older docs reference `/root/montanablotter` and `montanablotter.service`. Current
  reality: `/opt/montanablotter`, user `ubuntu`, unit `mtblotter.service`.
  `AGENTS.md` was corrected 2026-11-19; `docs/notes.md` and `HANDOFF-paywall.md`
  still reference `/root`.
- `HANDOFF-paywall.md` (2026-05) cites `data/blotter.db` at ~13 GB. The old
  montanablotter.com database was **lost** (the rebrand to mtblotter.com started
  fresh); the current DB is ~71 MB. Treat pre-loss record counts as historical.
- Paywall pricing has three conflicting sources (live page wins until owner says
  otherwise): live = Plus $7.99/mo, $79/yr; Pro $26/mo, $260/yr. See
  `docs/audits/2026-11-19-paywall-audit.md`, Decision 1.

## Current focus

1. **Paywall** — SHIPPED, audited 2026-11-19 (`docs/audits/2026-11-19-paywall-audit.md`).
   9/11 handoff criteria pass. Waiting on 4 owner decisions (audit doc, top section).
   One live bypass: `/record/<id>/source` serves pre-redaction PDFs ungated.
2. **Snitch Directory redesign** — landing + forum done in archival-ledger style
   (2026-11-16/17); old-theme templates remaining: thread, submit, contact, faq,
   auth, admin_*, embed, press, premium.
3. **County/source coverage** — see `docs/COUNTY_COVERAGE_EXPANSION_2026-08-11.md`,
   `county_sources_manifest.md`, `docs/jail_tier2_assessment.md`.

## Open questions

- ~~Is the `/alerts` 500 import bug fixed?~~ **Resolved 2026-11-19: fixed**
  (`app.py:10108` imports `services.alerts.dispatcher`).
- Did the old fine-grained PAT (scrubbed from git remote 2026-11-16) get revoked in
  GitHub settings? Owner holds that action.
- Should Plus/Pro include warrant access? Code currently says yes, docs say no
  (audit Decision 3).
