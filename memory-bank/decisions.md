# Decisions Log

Append-only. Newest at top. Format: date, decision, why, where to learn more.

## 2026-11-19 — Paywall: prices, map popups, warrant scope, source-PDF gate (owner)

Four decisions resolving `docs/audits/2026-11-19-paywall-audit.md`:

1. **Prices: the live page is truth** — Member free / Plus $7.99/mo, $79/yr /
   Pro $26/mo, $260/yr. Supersedes the 2026-05-21 prices below.
2. **Crime-map popups stay free-summary**, gate at `/record/<id>` (SEO
   reasoning). Supersedes the "popups paid" note in the 2026-05-21 entry.
3. **Warrant access: bundled with Pro + standalone add-on; Plus excluded.**
   The old code (`WARRANT_PLANS` incl. `plus`, webhook remap
   `warrant_access→plus`) was a bug that gave every Plus subscriber warrants.
4. **`/record/<id>/source` bypass fixed** — source PDFs now count against the
   preview budget like any other detail view.

## 2026-11-17 — Snitch Directory design language: archival ledger

Landing and forum restyled in light paper/ink monochrome (Trirong + EB Garamond +
Space Mono), ruled state-index table, rotated "ON THE RECORD" stamp. Backups kept as
`*.bak.<timestamp>` next to originals. If mtblotter.com gets a redesign, decide
whether to adopt this brand system for coherence.

## 2026-11-16 — Secrets live in env files, not systemd units or git

Inline `Environment=` lines were removed from systemd units; secrets moved to
`/opt/snitchdirectory/snitch.env` (`chmod 600`) and `.env` files. A fine-grained PAT
embedded in a git remote URL was scrubbed the same day. Why: the app repo is PUBLIC.

## 2026-11-16 — Deploy model: git is the deploy mechanism

`git_sync.sh` (every 2 min) makes `main` on GitHub the production source of truth:
pull → restart → health-check → auto-rollback. Why: lets multiple agents commit
locally on the box while GitHub stays canonical. Consequence: treat every push to
`main` as a production deploy.

## 2026-11-16 — Layered backups after the database loss

The old montanablotter.com DB was unrecoverable; the rebrand started with a fresh
archive. Response: daily WAL-safe local snapshots (14-day retention) + nightly
gpg-encrypted offsite push to private repo `mtblotter-backups`. Never bypass these
scripts when copying DBs.

## 2026-05-21 — Paywall product decisions (owner-confirmed)

Tiers: Scout (free, 3 detail views/day, 5/week rolling) / Insider ($7.99/mo,
$69.99/yr) / Professional ($14.99/mo, $129.99/yr). Jail bookings gated identically
to incidents; crime map pins free, popups paid. Full spec: `HANDOFF-paywall.md`.

## Ongoing — Tiered agent authority

`MISSION.md` Green/Yellow/Red model is the law of the fleet. Red-tier proposals go
in `agent-queue/<profile>/<timestamp>-<name>/` with diff, reasoning, rollback.
