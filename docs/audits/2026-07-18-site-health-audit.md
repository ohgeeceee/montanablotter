# Site Health Audit — 2026-07-18

Auditors: 3 parallel read-only lanes (live site/SEO, ingestion/cron, code/tests/security).
Mode approved: audit + fix everything found, batched with rollback points.

---

## CRITICAL (production degraded NOW)

### C1 — `montanablotter.service` crash-looping; port 5000 squatted by a manually-started dev server
- `systemctl status` → `activating (auto-restart)`, NRestarts=509 since Jul 17; 833 "Address already in use" errors in last 2000 lines of `logs/gunicorn.log`.
- Squatter: **PID 535345** — `./venv/bin/python3 app.py`, cwd `/root/montanablotter`, started 01:44:32 UTC, detached (PPID 1), bound `0.0.0.0:5000`, ~70% CPU.
- The live site is currently served by the Flask **dev server**, exposed on all interfaces. `/healthz` returns 200 from the squatter, so `healthcheck_restart.sh` exits 0 and never repairs the service.
- Fix: kill PID 535345 → `systemctl restart montanablotter` → harden healthcheck to assert the systemd unit state, not just the port.

### C2 — Disk 99% full (4.2 GB free of 232 GB)
- Largest reclaimable: `data/blotter.db.backup-20260705-070944` (13.4 GB stale **uncompressed manual** backup, Jul 5); `data/page_views.db` (9.7 GB); `db_backups/` (14 GB, 14-file chain through Jul 15).
- Fix: delete the Jul 5 manual copy (rolling chain in `db_backups/` remains); set retention/rollup for `page_views.db`.

### C3 — DB backups failing 3 consecutive days
- `logs/backup.log`: "Backup failed with exit code 120" Jul 16/17/18 03:00. Newest good: `db_backups/blotter_20260715_030001.db.gz`.
- Root cause: `scripts/ops/backup_db.sh` stages an uncompressed 13.4 GB copy before gzip — impossible with 4.2 GB free. S3 upload skipped (no AWS creds).
- Fix: C2 frees space; also stream `sqlite3 .backup` through gzip instead of staging uncompressed.

---

## HIGH

### H1 — Warrant paywall bypassed via public JSON API *(Batch 3 — needs sign-off)*
`blueprints/api.py:2085-2088` — `/api/v1/warrants` allows anonymous callers (100 req/day/IP) and returns up to 200 full active warrant records per call (name, **DOB**, charges, bond, mugshot URLs). HTML `/wanted*` routes are correctly gated; the API is not.

### H2 — Preview paywall bypassed via source-PDF viewer *(Batch 3 — needs sign-off)*
`app.py:11181` — `/record/<id>/source` serves the full **pre-redaction** source PDF with no `preview_allowed()`/subscription check, while `/record/<id>` (app.py:11158) enforces previews. Iterable record IDs → full document access.

### H3 — Invalid JSON-LD on record-detail pages
`/record/*` emits `"dateModified": 2026-07-17T20:25:02…` **unquoted** → whole `WebPage` JSON-LD block unparseable; `datePublished: "07/16/26"` non-ISO. Affects all record pages; search engines discard the block.

### H4 — `svor_sync` effectively dead; `sex_offenders` 6 days stale
Last `scheduled_job_runs` row Jul 8 (failed); `database is locked` Jul 12; zero runs Jul 13–17 despite daily cron `7 13 * * *`. Cause: fires at 13:07 while `email_worker` (13:00, ~13-min runs) holds the `blotter_db_writer` shared lock → silent skip (see M1). Fix: re-time (e.g. 13:37) + lock-wait/retry.

### H5 — `missing_person_sync` failing 5+ days — source 403
Montana DOJ endpoint `app.dojmt.gov/apps/missingPersonDatabase/` returns 403 (WAF block). `missing_persons` stale since Jul 11. Fix: proxy/new fetch path — may not be fixable from this host.

### H6 — Two `<h1>` on every page (23/23 sampled)
Masthead brand is `<h1>` site-wide + each page's own H1. Homepage's second H1 is a rotating incident headline. Fix: demote brand to non-heading; stable homepage H1.

---

## MEDIUM

### M1 — Chronic silent job skipping (design flaw)
`job_runner.py:323-327`: on flock contention writes "skipping" to stderr only and exits 0 — no log, no `scheduled_job_runs` row. Observed victims: jail-booking jobs at 02:05–02:50 (silent since Jul 18 00:21), `jail_booking_ingest_cascade` (last Jul 7), `jail_booking_ingest_all` (last Jul 16), svor_sync (H4). Fix: record skips in `scheduled_job_runs`; re-time jail jobs off :00/:08/:15/:30/:45 email runs.

### M2 — `run_all_scrapers` permanently-broken sources (exit 0, silent)
Every 6h for ~4 days: salary ingest 404 (transparency.mt.gov), montanabar.org disciplinary 404, boards.bsd.dli.mt.gov {con,bar,acc} 404, 511 incidents DNS dead. Fix: update/remove dead URLs; per-source failure alerting.

### M3 — `civil_filings` / `code_violations` pipelines producing nothing ~68 days
6 and 2 rows respectively, newest 2026-05-11, while the pipeline "runs" every 6h.

### M4 — Anthropic API credits exhausted + blog date bug
`daily_blog.log` Jul 17: "credit balance too low" → template fallback (also `charge_explainer` Jul 13). Separately: `daily_blog_worker` produced `analysis_date=2027-09-06` / future-dated slug `montana-crime-roundup-2027-09-06` (live on /blog); DB has dirty `records.created_at` values (a max() scan returned the string "Whitford, Austin"). Fix: billing is yours; sanitize date parsing in the blog worker; audit `records.created_at` writes.

### M5 — `TZ=America/Denver` cron entries don't do what comments claim
`TZ=…` as command env prefix sets the command's env, not the schedule — jobs still fire in system time (UTC). Affects `daily_planner`, digests, `charge_explainer`, source-scout chain. Fix: `CRON_TZ=America/Denver` lines (cronie) or convert to UTC.

### M6 — Forgeable ops heartbeat *(Batch 3 — needs sign-off)*
`blueprints/admin/mission_control.py:70-85` — POST `/admin/api/mission-control/heartbeat` needs no login, only static header `X-Internal-Mission-Control: local` (committed across repo). Anyone can spoof agent heartbeats / mask outages. Fix: loopback-only or env shared secret.

### M7 — Silent exception swallow in payment persistence *(Batch 3 — needs sign-off)*
`blueprints/payments.py:219-220` — `persist_donation_checkout()` has `except Exception: pass`; probable root cause of the failing donate test (UNIQUE collision on `provider_payment_intent_id`). Fix: log + handle conflict explicitly.

### M8 — 17 failing tests + 4 errors
Incl. real drift: `test_watchdog_drift` — 4 cron jobs (`charge_explainer`, `missing_person_sync`, `svor_sync`, `warrant_ingest`) missing from watchdog `well_known_jobs`; latent county-page 500 (M9); rest are template/test drift. Full log: `/tmp/pytest_out.txt`.

### M9 — Latent 500 on county pages (schema-ensure gap)
`_active_bail_ad_listings` (app.py:2627-2652) selects `simulator_logo_path` etc., but the ensure-migration only runs on payment/admin paths — county pages 500 on an unmigrated DB (6 SEO test failures demonstrate). Works in prod by luck. Fix: run ensure on the public path or at startup.

---

## LOW

- L1: `/favicon.ico` 404; no `<link rel="icon">` anywhere.
- L2: `https://www.montanablotter.com` serves 200 duplicate content (should 301 → apex).
- L3: Static assets `Cache-Control: no-cache` — every view revalidates every asset (ETag present). Add `max-age`+`immutable` for fingerprinted files.
- L4: County titles >70 chars (5 sampled); meta descriptions >160 chars (5 + homepage + blog post).
- L5: Internal link via 301 (`/post/386` → slugged URL) — emit final slug in templates.
- L6: Heavy pages: `/missing-persons` 1.39 MB, `/jail-bookings` 480 KB HTML. Paginate/cap.
- L7: `ingestion_jobs` dead-letter loop — 30 failed rows, `retry_count` up to 726, still retrying. Cap/quarantine.
- L8: Orphaned `.scraper_heartbeat` (Jun 22, nothing writes it). Delete or rewire.
- L9: Long-tail stale jail counties: lake (Jun 14), lincoln/custer (Jun 17).
- L10: `disposition_watcher` intermittent "database is locked" (6 in 2000 lines) — cadence kept; monitor.
- L11: `mail.log` 39 MB, dominated by unrelated GitHub PR notifications + own alert mail looping back. Filters.
- L12: `.env.example` missing ~96 env vars referenced in code (Twilio, VAPID, OpenAI/Anthropic, reCAPTCHA, Redis, Mapbox… names only).
- L13: Unpinned transitive deps (google-*, openai, grpcio, protobuf…) in requirements.txt. `pip check` clean.
- L14: `page_views.db` 9.7 GB — no retention/rollup policy.
- L15: Jail-booking display path has no redaction pass (likely intentional — rosters are public source data; confirm).
- L16: gitleaks not installed — configured secret scan could not run (manual spot-check clean).

## INFO
- `/post/<slug>` shows full blotter records with no preview gating (possibly deliberate SEO design — confirm).
- `/wanted` 302→subscribe for anonymous: by design.
- Crontab drift: `crontab -l` == `crontab.txt`, zero drift.
- e2e a11y errors: Gunicorn boot >20s fixture timeout — infra, not app logic.

## Verified healthy
- All 23 sampled pages 200, 0.20–0.68s; zero 404s in spot-checked internal links; branded 404 returns true 404.
- Unique titles/descriptions; full OG tags; correct canonicals; rich JSON-LD on county/booking/blog/home.
- robots.txt sane (disallows /admin/ only, references both sitemaps); sitemap index valid, 56 county URLs, 263 blog posts.
- RSS.app ticker live on homepage. http→https 301s work. Security headers solid (CSP/HSTS/XFO/nosniff/Referrer-Policy/Permissions-Policy).
- Email ingestion, news pipeline, meetings/agendas, hourly fetchers, ad health, civic publisher, RQ workers: all fresh and on schedule. Core tables fresh (records/posts/blotters/blog_posts/warrants/court_*).
- PII pipeline fail-closed (audit_status='clean' gate, HIGH-severity blocks auto-clear, no LLM key → no auto-publish). Zero bare `except:` in app/blueprints/services. Parameterized SQL throughout. Admin guard + CSRF with compare_digest. SECRET_KEY raises in prod if unset; DEBUG off in prod.
- Backup chain intact Jul 1–15 (until C3).
- Test suite: 823 passed / 17 failed / 4 errors (of ~844).

## Fix plan (approved batches)

- **Immediate:** C1, C2, C3 (needs destructive-action confirmation: kill PID 535345; delete Jul-5 manual backup file).
- **Batch 0 (zero-risk):** L1 favicon, L2 www→apex 301, L3 cache headers, C3 backup-streaming hardening.
- **Batch 1 (templates/UI):** H3 JSON-LD, H6 single-H1, L4 title/meta trim, L5 slugged URLs.
- **Batch 2 (pipeline):** H4 svor re-time + lock retry, M1 skip observability, M2 dead sources, M3 civil/code-violations, M4 blog date sanitize, M5 CRON_TZ, M8 watchdog map + failing tests, M9 schema-ensure, L7 retry cap, L8 heartbeat file, dirty `records.created_at` investigation.
- **Batch 3 (sign-off per item):** H1 warrants API gating, H2 source-PDF gating, M6 heartbeat auth, M7 payment swallow, L15 confirm.
- **Deferred to user:** Anthropic credits (billing), missing-persons 403 (source WAF; needs proxy decision), L6 pagination (bigger design), L12/L13 hygiene sweeps.
