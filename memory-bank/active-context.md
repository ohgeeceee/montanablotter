# Active Context

*Last verified: 2026-11-16 (direct SSH session on vm2778) for deployment facts;
paywall audited AND owner decisions implemented 2026-11-19. Update this file
whenever reality changes.
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
  `AGENTS.md` and `CLAUDE.md` corrected 2026-11-19; `HANDOFF-paywall.md` carries
  a superseded banner; `docs/notes.md` still references `/root`.
- `HANDOFF-paywall.md` (2026-05) cites `data/blotter.db` at ~13 GB. The old
  montanablotter.com database was **lost** (the rebrand to mtblotter.com started
  fresh); the current DB is ~71 MB. Treat pre-loss record counts as historical.
- ~~Paywall pricing has three conflicting sources~~ Resolved 2026-11-19: owner
  confirmed the live page (Plus $7.99/mo, $79/yr; Pro $26/mo, $260/yr). Docs
  updated; legacy `/checkout/subscription` (old $14.99 Professional) retirement
  is staged in the queue draft.

## Current focus

1. **Paywall** — SHIPPED, audited and owner-decided 2026-11-19
   (`docs/audits/2026-11-19-paywall-audit.md`, "Resolutions"). ALL code changes
   (warrant scope, source-PDF gate, duplicate `/pricing` removal,
   `already_subscribed` fix, legacy `/checkout/subscription` retirement) await an
   on-box run of
   `agent-queue/dev/2026-11-19-paywall-warrant-scope/patch_paywall_warrant.py`
   followed by the migration script. Until then: Plus subscribers still get
   `/wanted` (the confirmed bug), `/record/<id>/source` is still ungated, and
   `/checkout/subscription` still sells Professional at the old $14.99/mo.
2. **Snitch Directory redesign** — landing + forum done in archival-ledger style
   (2026-11-16/17); old-theme templates remaining: thread, submit, contact, faq,
   auth, admin_*, embed, press, premium.
3. **County/source coverage** — see `docs/COUNTY_COVERAGE_EXPANSION_2026-08-11.md`,
   `county_sources_manifest.md`, `docs/jail_tier2_assessment.md`.
4. **Black Friday 2026** — deployed on commit `8addc38a` and scheduled for
   November 27–30. Live route-driven Stripe checks confirmed $39.50 Plus and
   $130 Pro; all six verification sessions expired unpaid with no subscriptions.
   Facebook start announcement and free-member email sent 2026-11-26; other
   advertising remains draft-only with no spend. Launch handoff:
   `agent-queue/dev/2026-11-26-black-friday-2026/`.
5. **Standard Plus/Pro checkout pricing** — fixed and deployed on commit
   `52a6238a` 2026-11-26. New checkouts use the displayed $7.99/$79 Plus and
   $26/$260 Pro prices; outdated Payment Link fallbacks were removed. Existing
   subscriptions were not changed. Handoff:
   `agent-queue/dev/2026-11-26-standard-checkout-pricing/`.

## Open questions

- ~~Is the `/alerts` 500 import bug fixed?~~ **Resolved 2026-11-19: fixed**
  (`app.py:10108` imports `services.alerts.dispatcher`).
- Did the old fine-grained PAT (scrubbed from git remote 2026-11-16) get revoked in
  GitHub settings? Owner holds that action.
- ~~Should Plus/Pro include warrant access?~~ **Resolved 2026-11-19: Pro yes,
  Plus no; standalone `warrant_access` add-on preserved.**
