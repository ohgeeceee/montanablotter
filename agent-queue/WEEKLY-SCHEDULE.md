# Montana Blotter — Agent Fleet Weekly Schedule

> Week of 2026-05-18 (Mon) through 2026-05-24 (Sun)  
> Generated: 2026-05-21  
> Current state: 73 stuck PDFs, 5 counties broken, Gallatin scraper down since May 11, court calendars broken since May 17.

---

## blotter-ops — Infrastructure & Site Health

**Tier boundaries:** Green (monitor/read), Yellow (restart/requeue/rotate/backup), Red (code/deploy/DB-write/outbound — never without approval)

### Continuous (every 5 minutes)
- [ ] Probe public site root path; alert on 3 consecutive non-200 responses
- [ ] Check systemd status: `montanablotter`, `nginx`, `fb-page-manager`, `blog-dup-checker`
- [ ] Sample disk usage on `/` and `/root`; alert at 80%, escalate at 90%
- [ ] Check TLS cert expiry; alert at 21 days, escalate at 14 days
- [ ] Tail `fail2ban` / auth.log for anomaly volume (currently elevated: ~11k failed attempts/day)

### Daily (07:00 MT digest prep)
- [ ] Compile one-screen health digest: uptime, disk, certs, backups, anomalies
- [ ] Verify backup from 02:00 cron completed; if missing, trigger Yellow-tier snapshot
- [ ] Rotate any log >100MB in `logs/`
- [ ] Check `agent-queue/` total item count; if >20, flag fleet over-alerting

### Monday
- [ ] Full backup chain audit: verify 7 rolling daily `.bak` files exist
- [ ] Review weekend SSH brute-force volume; if sustained >10k/day, draft fail2ban hardening note for Jon
- [ ] Verify nginx config syntax (`nginx -t`) and TLS intermediate chain
- [ ] Clean `agent-queue/ops/` items aged >7d → `archive/`

### Tuesday
- [ ] Review `montanablotter` service restart count (7-day); flag if >5
- [ ] Spot-check `db_backups/` retention; purge local copies older than 14 days (Yellow)
- [ ] Verify `script_watchdog.py` is catching silent cron failures

### Wednesday
- [ ] Mid-week disk deep-dive: identify top 10 largest files/dirs under `/root/montanablotter`
- [ ] Check `uploads/` for orphan files >30 days not in DB
- [ ] Review gunicorn error log for 502/504 patterns

### Thursday (today — catch-up)
- [ ] **P0:** Backup chain is 3.2 days stale. Trigger `backup_db.sh` Yellow-tier snapshot now.
- [ ] **P1:** SSH brute-force sustained elevation. Prepare `red-tier/` proposal for key-based-only SSH if Jon wants to tighten.
- [ ] Verify all 4 queue items from earlier this week are tracked

### Friday
- [ ] Pre-weekend readiness check: all systemd units active, disk <85%, cert >14 days
- [ ] Summarize weekly anomaly trends for Sunday weekly digest
- [ ] Queue `archive/` sweep for anything older than 30 days

### Saturday / Sunday
- [ ] Reduced monitoring cadence (site probe every 15 min instead of 5 min)
- [ ] Sunday 18:50 MT: prepare raw ops section for weekly digest

---

## blotter-ingest — Pipeline & Data Flow

**Tier boundaries:** Green (read/query/draft), Yellow (re-queue PDF after backup), Red (DB writes outside pipeline, parser deploys)

### Continuous (every 15 minutes)
- [ ] Confirm `email_worker` last run within expected window
- [ ] For each of 11 counties: confirm trailing 72h ingest; flag gap >72h as "possibly broken"
- [ ] Scan `uploads/` for PDFs >6h old; count stuck files
- [ ] Check RQ queue depths (`rq info` or equivalent)

### Daily (07:00 MT digest prep)
- [ ] Pipeline health report: counties on schedule, drifting, broken, stuck PDFs, format drift
- [ ] Per-county record count spot-check: flag any county at <30% of trailing 30-day median
- [ ] Parse success rate (7-day, per county): flag if any drops below 95%
- [ ] Run `stuck-pdf-triage` in report-only mode for each stuck PDF; save diagnosis

### Monday
- [ ] Weekly county coverage scorecard: ingest rate per county over trailing 7 days
- [ ] Review `services/ingestion/jail_bookings.py` Gallatin failure (ongoing since May 11)
  - Draft diagnosis update for dev queue
  - Verify if endpoint is returning HTML vs JSON
- [ ] Check `ingestion/transparency_portal.py` for new data sources

### Tuesday
- [ ] Deep-dive on 5 broken counties: Lewis and Clark, Unknown, Cascade, Carbon, Valley
  - Check last successful parse timestamps
  - Determine if source changed, feed dead, or parser broken
  - Draft one summary per county → `agent-queue/ingest/`
- [ ] Review `rq-ingestion.log` for repeating error patterns

### Wednesday
- [ ] Mid-week stuck-PDF purge attempt: for PDFs stuck >48h, run manual re-parse in dry-run
  - If parse succeeds, re-queue (Yellow) after backing up to `uploads/retry/`
  - If parse fails, update diagnosis and escalate to dev
- [ ] Verify `email_image_blotter.py` (Havre PD image emails) is extracting images correctly

### Thursday (today — catch-up)
- [ ] **P0:** 73 stuck PDFs. Batch-diagnose top 10 by age. Prioritize any from major counties (Gallatin, Missoula, Yellowstone).
- [ ] **P1:** No new blotters ingested since 2026-05-20 19:10 despite cron running. Investigate `email_worker.py` or source silence.
- [ ] Update county gap flags for daily digest

### Friday
- [ ] Pre-weekend pipeline check: ensure all active counties have ingested within 24h
- [ ] Format-drift scan: compare this week's parser success rates vs prior week
- [ ] Queue any new drift flags for dev review

### Saturday / Sunday
- [ ] Reduced checks (every 30 min): email worker, major counties only
- [ ] Sunday 18:50 MT: prepare raw ingest section for weekly digest
- [ ] Weekly trend: counties gaining/losing consistency

---

## blotter-dev — Code, Parsers & Tests

**Tier boundaries:** Green (read/draft/branch), Yellow (none — this profile rarely acts alone), Red (merge/deploy/dependency change — never without approval)

Runs on-demand from ingest flags, not on a fixed schedule. All work lands as draft PRs or queue items.

### Monday
- [ ] Review all `agent-queue/dev/` items opened over weekend
- [ ] For each format-drift flag from ingest:
  - Branch: `fix/parser-<county>-<date>`
  - Draft patch; open draft PR
  - Add regression test if coverage is thin
- [ ] Review `requirements.txt` vs PyPI security advisories; draft notes file (do not update)

### Tuesday
- [ ] Pick highest-priority parser patch from queue (currently: court calendar scrapers)
  - Run `services/court/refresh.py --debug` locally
  - Identify selector/endpoint changes
  - Draft patch PR with before/after test case
- [ ] Review test coverage on `services/ingestion/`; draft new tests for uncovered counties

### Wednesday
- [ ] Continue active draft PRs
- [ ] Review `agent-queue/new-counties/` for any counties Jon wants scaffolded
  - Run `add-county-parser` skill in dry-run
  - Output skeleton to queue; do not commit
- [ ] Run full pytest suite: `pytest tests/ -q` — flag any new failures

### Thursday (today — catch-up)
- [ ] **P0:** Court calendar scrapers (`montana-colj-calendar`, `montana-district-court-calendar`) broken since May 17.
  - Capture actual HTML response
  - Draft selector fix in `services/court/refresh.py`
  - Open draft PR with test fixture
- [ ] **P1:** Gallatin jail scraper (`services/ingestion/jail_bookings.py`) — JSON parse error at char 0.
  - Coordinate with ingest on actual response body
  - If endpoint moved/added anti-bot, evaluate headless browser need
  - Draft patch or red-tier proposal if new dependency required

### Friday
- [ ] Close or update any draft PRs older than 7 days
- [ ] Weekly dev digest: open PRs, coverage gaps, dependency notes
- [ ] Prepare Sunday weekly digest raw section

### Saturday / Sunday
- [ ] Off unless urgent parser fix flagged by ops/ingest
- [ ] Sunday 18:50 MT: prepare raw dev section for weekly digest

---

## blotter-civic — Outreach, Roster & Press

**Tier boundaries:** Green (draft/read), Yellow (none), Red (send email/post/modify roster upstream — never without approval)

### Monday
- [ ] Weekly press scan: search for "Montana Blotter" mentions; draft summary for queue
- [ ] Check Montana public-records-law news; summarize any bills/cases affecting mission
- [ ] Review contact roster (`_roster.yaml`) — **currently missing, P0 to create**
  - Scaffold `_roster.yaml` with known counties from ingest config
  - Populate from any existing email threads or source metadata

### Tuesday
- [ ] For each county feed silent >7 days: draft polite check-in email
  - Save to `agent-queue/civic/` with subject, body, proposed send date
  - Do not send
- [ ] Update roster with last-contact dates for any counties drafted

### Wednesday
- [ ] Review `agent-queue/new-counties/` for outreach targets
  - Draft initial outreach email per target county
  - Include public-records-law citation and data-sharing pitch
- [ ] Press mention follow-up: if any coverage found Monday, draft FAQ update or thank-you note

### Thursday (today — catch-up)
- [ ] **P0:** Create `_roster.yaml` in `agent-queue/civic/`.
  - Columns: county, facility, contact_name, title, email, phone, last_contact, source_url, notes
  - Seed from `services/ingestion/jail_bookings.py` and `ingestion/` adapters
- [ ] **P1:** 5 counties broken >72h (Lewis and Clark, Cascade, Carbon, Valley, Unknown). Draft check-in emails for any not already in queue.
- [ ] Review existing outreach draft `20260519_0840-outreach-no-adapter-counties`

### Friday
- [ ] Roster quality check: verify all 56 Montana counties have at least a stub entry
  - Flag any with no known contact
- [ ] Summarize civic queue for Jon: drafts ready for send-or-discard

### Saturday / Sunday
- [ ] Light monitoring: check for any urgent press or legal news
- [ ] Sunday 18:50 MT: prepare raw civic section for weekly digest

---

## blotter-scraper — Scraping Execution & Maintenance

**Tier boundaries:** Green (run existing scrapers, read logs), Yellow (re-run failed scraper once), Red (scraper code changes, new dependencies)

### Daily
- [ ] Verify all scraper cron jobs wrote log entries within expected windows:
  - `jail_bookings.py` (every 2h for active counties)
  - `court.refresh` (every 3h)
  - `transparency_portal.py` (as scheduled)
  - Custom scrapers in `scrapers/` (as scheduled)
- [ ] Check `logs/` for scraper-specific ERROR lines

### Monday
- [ ] Full scraper health audit: success/failure per source (7-day)
- [ ] Review `scrapers/fwp_violations.py`, `scrapers/mt_511_crashes.py`, `scrapers/professional_boards.py`
- [ ] Verify `madison_county_monthly.py` ran (monthly cadence)

### Tuesday
- [ ] Re-run Gallatin scraper manually with full debug output
  - Capture raw HTTP response (headers + first 2KB body)
  - Save capture to `agent-queue/ingest/` for dev review
- [ ] Re-run court refresh manually with debug
  - Compare 03:18 (last success) vs 12:18 (first failure) responses

### Wednesday
- [ ] Attempt transient recovery runs on any source that failed only once in 24h
- [ ] Verify `federal_court_cases.py` PACER connectivity (if applicable)

### Thursday (today — catch-up)
- [ ] **P0:** Gallatin scraper — capture and document actual endpoint behavior
- [ ] **P1:** Court calendars — run manual refresh, diff HTML structure vs last known good
- [ ] Stuck PDF investigation: for PDFs in `uploads/`, verify they are parseable by existing parsers

### Friday
- [ ] Pre-weekend scraper sweep: re-run any source that hasn't succeeded in 48h
- [ ] Summarize scraper reliability trends

### Saturday / Sunday
- [ ] Reduced checks: major counties + court sources only
- [ ] Sunday: contribute scraper reliability stats to weekly digest

---

## blotter-parser — PDF Parsing & Record Extraction

**Tier boundaries:** Green (parse in dry-run, inspect output), Yellow (re-queue stuck PDF after backup), Red (parser code changes, DB writes outside pipeline)

### Daily
- [ ] Sample parsed records from previous 24h: check for zero-record blotters
- [ ] Verify `records/` output matches `uploads/` input (accounting for failures)
- [ ] Check parse error log for repeating patterns

### Monday
- [ ] Weekly parse quality report: success rate per county, common error types
- [ ] Review any parser that had <95% success rate in prior week

### Tuesday
- [ ] Deep-dive on 73 stuck PDFs: batch attempt headless parse on 10 oldest
  - If parse succeeds in dry-run, prepare Yellow-tier re-queue list
  - If parse fails, document error pattern for dev

### Wednesday
- [ ] Review charge-category extraction accuracy: spot-check 20 random records
- [ ] Verify `pattern_conversion_report.py` output is sane

### Thursday (today — catch-up)
- [ ] **P0:** Investigate why no new blotters since May 20 19:10. Is it ingestion silence or parse failure?
- [ ] **P1:** Batch-diagnose stuck PDFs by county; prioritize major counties

### Friday
- [ ] Verify all PDFs ingested Mon-Thu have corresponding parsed records
- [ ] Flag any county with >5% parse failure rate for dev attention

### Saturday / Sunday
- [ ] Reduced checks: sample only
- [ ] Sunday: contribute parse quality stats to weekly digest

---

## ops (Scribe) — Content, Documentation & Communications

**Tier boundaries:** Green (draft all content), Yellow (publish blog posts if auto-publish is enabled in admin), Red (send outbound email, post to social, publish external-facing content without approval)

### Monday
- [ ] Weekly county digest copy review (generated by `weekly_county_digest` cron)
- [ ] Weekly safety trend report copy review (generated by `weekly_safety` cron)
- [ ] Cascade County safety snapshot blog draft review
- [ ] Charge explainer pages: review any new charge types generated

### Tuesday
- [ ] Morning briefing copy edit (daily at 7am)
- [ ] Daily blog worker post review (7:15am)
- [ ] Update `docs/` if any new features landed last week

### Wednesday
- [ ] Review and polish any civic outreach drafts from blotter-civic
- [ ] Draft changelog entry for any pending dev PRs
- [ ] Review README for accuracy against current deployment

### Thursday (today — catch-up)
- [ ] **P1:** No new blotters since yesterday — prepare "pipeline delay" notice draft for site banner (do not deploy without Red-tier approval)
- [ ] Review and update `HANDOFF-paywall.md` if any paywall source changes discovered

### Friday
- [ ] Compile weekly content calendar: what published, what drafted, what scheduled
- [ ] Review Facebook autopost queue (if `facebook_worker` is enabled)
- [ ] Draft weekend or Monday morning briefing if needed

### Saturday / Sunday
- [ ] Sunday evening: prepare weekly digest formatting and any narrative summary
- [ ] Review site copy for any stale references

---

## sage (Ops Analyst) — Metrics, Strategy & Competitive Intel

**Tier boundaries:** Green (compute/read/draft reports), Yellow (none), Red (any automated action based on analysis — never without approval)

### Monday
- [ ] Weekly metrics compilation:
  - Record ingest volume vs prior week
  - Site uptime %
  - County coverage count (active / drifting / broken)
  - Parse success rate trend
  - Queue depth trend
- [ ] Competitor scan: Cursor, Windsurf, Continue, Cline, etc. (if relevant to project tools)
- [ ] User feedback triage: any emails, Discord, or other inbound

### Tuesday
- [ ] Cost analysis: API/infra spend if any metered services in use
- [ ] RQ throughput analysis: jobs/sec, failure rate, worker utilization
- [ ] Draft "recommended next actions" for Jon

### Wednesday
- [ ] Mid-week trend check: are broken counties recovering or degrading?
- [ ] Alert fatigue audit: how many alerts fired vs actionable? Recommend threshold tuning
- [ ] Backup chain reliability metric

### Thursday (today — catch-up)
- [ ] **P1:** Analyze why backup chain is stale and ingest pipeline stalled
  - Correlate timeline: did both break around same time?
  - Draft incident timeline for Jon
- [ ] SSH brute-force trend: is this a sustained campaign or noise?

### Friday
- [ ] Week-over-week comparison tables for all key metrics
- [ ] Risk register update: top 3 risks to mission based on this week's data
- [ ] Prepare Sunday weekly digest raw analytics section

### Saturday / Sunday
- [ ] Sunday 18:50 MT: compile final weekly digest with trend lines and recommendations
- [ ] Archive last week's metrics for longitudinal tracking

---

## swarm1 / swarm10 / swarm11 — Ad-hoc Parallel Workers

Swarm workers pick up tasks from the kanban board or delegated by ops/ingest when parallel investigation is needed. No fixed schedule.

### Typical assignments this week
- [ ] **swarm1:** Parallel PDF diagnosis — process bottom 25 of 73 stuck PDFs, report error patterns
- [ ] **swarm10:** County source verification — check websites for 5 broken counties, report any structural changes
- [ ] **swarm11:** Log analysis — grep all `*.log` files for ERROR/FATAL since May 11, deduplicate, rank by frequency

### Invocation triggers
- Kanban dispatcher claims a task
- `delegate_task` from blotter-ops or blotter-ingest with `tasks: [...]` batch
- Manual assignment via `hermes kanban assign <task> <profile>`

---

## Cross-Agent Coordination Map

| Time (MT) | Agent | Action | Handoff to |
|-----------|-------|--------|------------|
| 00:00 | ops | Log rotation, cleanup cron | — |
| 02:00 | ops | DB backup verification | ingest (if backup fails) |
| 06:50 | ops, ingest, dev, civic | Daily digest generation | aggregator → Jon |
| 07:00 | ops | Morning briefing trigger | Scribe (review) |
| 07:15 | ops | Daily blog trigger | Scribe (review) |
| Every 15m | ingest | Email worker check | — |
| Every 5m | ops | Site probe | ingest (if down) |
| Continuous | scraper | Source scraping | ingest (verify) → parser (process) |
| Continuous | parser | PDF parsing | ingest (re-queue if fail) → dev (if code issue) |
| As flagged | dev | Parser patch draft | Jon review |
| As drafted | civic | Outreach email draft | Jon send-or-discard |
| 18:50 Sun | all | Weekly digest raw sections | aggregator → Jon |

---

## Current Week Priorities (W21)

1. **Restore backup chain** (ops, Yellow — overdue)
2. **Diagnose and fix Gallatin scraper** (ingest + scraper + dev — ongoing since May 11)
3. **Diagnose and fix court calendar scrapers** (ingest + scraper + dev — broken May 17)
4. **Clear stuck PDF backlog** (ingest + parser — 73 files)
5. **Create civic contact roster** (civic — foundational, unblocks outreach)
6. **Investigate ingest silence since May 20 19:10** (ingest — possible source outage or pipeline break)
7. **Address elevated SSH brute-force** (ops — monitor, draft hardening proposal if needed)

---

## Queue Aging Watch

| Queue | Open | Aged >7d | Action |
|-------|------|----------|--------|
| ops | 1 | 0 | Monitor |
| ingest | 2 | 0 | Active work |
| dev | 2 | 0 | Active work |
| civic | 1 | 0 | Awaiting roster |
| red-tier | 0 | 0 | — |

*If any queue grows past 20 items, lower thresholds or increase swarm delegation.*
