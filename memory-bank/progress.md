# Progress

Completed work, newest first. Move items here from `roadmap.md` when they ship.

## 2026-11

- **2026-11-19 — Paywall decisions implemented**: owner resolved the 4 audit
  questions (prices = live page; map popups stay free; warrants = Pro +
  standalone add-on, Plus excluded; source-PDF bypass fixed). Docs corrected;
  all code changes staged in
  `agent-queue/dev/2026-11-19-paywall-warrant-scope/` (one patch script +
  migration; tests included and verified against the patched logic).
- **2026-11-19 — Paywall audit** (`docs/audits/2026-11-19-paywall-audit.md`):
  9/11 handoff criteria pass; July H1 (warrant API) confirmed fixed; H2
  (source-PDF bypass) confirmed open, fix approved and staged same day.
- **2026-11-19 — Memory bank created** (`memory-bank/`): read/write protocol, active
  context, decisions, gotchas, progress, roadmap, session-log template. Linked from
  `AGENTS.md`.
- **2026-11-17 — Snitch Directory forum restyled** to the archival-ledger system,
  with persisted light/dark toggle.
- **2026-11-16 — Snitch Directory landing redesigned** (archival ledger: paper/ink,
  Trirong/EB Garamond/Space Mono, 50-row state index, live submission counter).
- **2026-11-16 — Backup & deploy pipeline installed** on vm2778: daily WAL-safe local
  snapshots (14-day retention), nightly gpg-encrypted offsite backups to private
  `mtblotter-backups` repo, 2-minute `git_sync.sh` auto-deploy with health-check and
  auto-rollback.
- **2026-11-16 — Secret hygiene pass**: secrets out of systemd units into
  `chmod 600` env files; PAT scrubbed from git remote; server SSH key added to
  GitHub account.

## 2026-03

- **2026-03-05 — Facebook autopost MVP**: queue tables, publisher engine, worker,
  `/admin/facebook` UI, auto-queue hook in summarizer, cron entry.
- **2026-03-05 — Homepage filters fix**: sidebar layout decoupled from Tailwind
  utilities (`templates/index.html`).
