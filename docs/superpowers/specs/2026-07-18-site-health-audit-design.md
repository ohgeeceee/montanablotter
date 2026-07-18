# Site Health Audit + Fix — Design

Date: 2026-07-18
Status: Approved by user (audit mode: fix everything found; structure: parallel lanes, batched fixes)

## Goal

Full health audit of montanablotter.com — live site/SEO, ingestion pipeline, code/tests/security — followed by prioritized fix batches deployed to production.

## Audit lanes (parallel, read-only)

### Lane 1 — Live site & SEO
- Crawl key public routes on `https://montanablotter.com`: `/`, `/counties`, `/arrests`, `/courts`, `/jail-bookings`, `/missing-persons`, `/datacenter`, `/blog`, `/status`, a sample of `/county/<slug>` pages, and a sample of record-detail pages discovered from `/arrests`.
- Per page: HTTP status, `<title>`, meta description, OG tags, canonical, H1, JSON-LD structured data, sampled internal-link validity, response time.
- Also: `robots.txt`, `sitemap.xml`, static asset cache headers, RSS.app ticker presence on homepage.

### Lane 2 — Ingestion pipeline & cron health
- `crontab.txt` vs installed `crontab -l`.
- Recent log tails in `logs/`; `.scraper_heartbeat`; last-run freshness per job.
- Per-table data freshness via read-only SQLite queries on `data/blotter.db` (`max(created_at)` on `records`, `jail_bookings`, `court_events`, `court_cases`, `missing_persons`, `sex_offenders`, `posts`, `blog_posts`).
- Stuck/failed `blotters` batches.
- Does NOT run `script_watchdog.py` (it can restart services).

### Lane 3 — Code, tests & security
- Full pytest run (`./venv/bin/python3 -m pytest tests/ -q`).
- `gitleaks git --config .gitleaks.toml --redact`.
- Flask debug-mode check; route exposure spot checks.
- Paywall gating: `user_has_warrant_access()` on `/wanted` routes; `preview_allowed()` usage.
- `.env.example` key coverage vs `os.environ`/`os.getenv` references in code (key names only; `.env` itself is never opened).

## Fix batches

Each batch: extend/write tests → run pytest → commit (Conventional Commits, only files changed by this work) → `systemctl restart montanablotter` → verify live → next batch. Rollback per batch: `git revert` + restart (< 5 min).

- **Batch 0 — zero-risk:** meta/OG tags, sitemap, robots, dead links, cache headers.
- **Batch 1 — templates/UI:** rendering bugs, broken markup, accessibility.
- **Batch 2 — ingestion/pipeline:** stale jobs, parser bugs, cron drift.
- **Batch 3 — paywall/auth-adjacent:** only with per-item user sign-off.

**Hard exception:** anything touching the PII auditor (`services/blotter/auditor.py`), Stripe flows, or auth logic is flagged to the user before any edit, even under "fix everything".

**Working tree note:** pre-existing uncommitted changes (`.env.example`, `AGENTS.md`, `CLAUDE.md`, deleted `agent-queue/*`) are left untouched; commits include only this work's files.

## Deliverables

- Findings report: `docs/audits/2026-07-18-site-health-audit.md`.
- Per-batch commits + live verification notes.
- Memory files (`.agents/memory/`) updated after deploys.

## Verification

- pytest after each batch (full suite).
- Live curl verification of every affected route after each deploy.
- Before/after comparison for SEO items (title/meta/OG/JSON-LD).
