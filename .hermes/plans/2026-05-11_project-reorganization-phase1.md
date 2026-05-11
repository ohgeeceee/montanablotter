# Montana Blotter Project Reorganization — Phase 1

> **Goal:** Reorganize root-level modules into logical packages for immediate navigability wins. Zero functional changes. All tests must pass.

## Architecture

Move modules into packages by domain. Update imports. Keep `app.py` untouched for now (Phase 2).

---

## Task 1: Move docs to docs/

**Objective:** Move all 17 `.md` files from root into `docs/` subdirectories.

**Files:**
- Move: `AGENDAS_PROVIDER_ARCHITECTURE.md` → `docs/architecture/`
- Move: `AGENDAS_SUBDOMAIN_BLUEPRINT.md` → `docs/architecture/`
- Move: `AGENTS.md` → `docs/`
- Move: `BAIL_BONDS_AD_PLAN.md` → `docs/plans/`
- Move: `BAIL_OUTBOUND_PLAYBOOK.md` → `docs/runbooks/`
- Move: `CASE_JOURNEY_HOMEPAGE_SPEC.md` → `docs/specs/`
- Move: `CLAUDE.md` → `docs/`
- Move: `DONATE_ADDON_PLAN.md` → `docs/plans/`
- Move: `DONATIONS_LAUNCH_RUNBOOK.md` → `docs/runbooks/`
- Move: `FIX_502.md` → `docs/runbooks/`
- Move: `IMPLEMENTATION_SUMMARY.md` → `docs/`
- Move: `OFFICIAL_SOURCE_COVERAGE.md` → `docs/`
- Move: `README.md` → keep at root (update refs if any)
- Move: `SEARCH_CONSOLE_RUNBOOK.md` → `docs/runbooks/`
- Move: `SUPABASE_MEETING_SEARCH_PIPELINE.md` → `docs/architecture/`
- Move: `notes.md` → `docs/`
- Move: `security_best_practices_report.md` → `docs/`

**Commands:**
```bash
mkdir -p docs/architecture docs/plans docs/runbooks docs/specs
git mv AGENDAS_PROVIDER_ARCHITECTURE.md docs/architecture/
git mv AGENDAS_SUBDOMAIN_BLUEPRINT.md docs/architecture/
git mv AGENTS.md docs/
git mv BAIL_BONDS_AD_PLAN.md docs/plans/
git mv BAIL_OUTBOUND_PLAYBOOK.md docs/runbooks/
git mv CASE_JOURNEY_HOMEPAGE_SPEC.md docs/specs/
git mv CLAUDE.md docs/
git mv DONATE_ADDON_PLAN.md docs/plans/
git mv DONATIONS_LAUNCH_RUNBOOK.md docs/runbooks/
git mv FIX_502.md docs/runbooks/
git mv IMPLEMENTATION_SUMMARY.md docs/
git mv OFFICIAL_SOURCE_COVERAGE.md docs/
git mv SEARCH_CONSOLE_RUNBOOK.md docs/runbooks/
git mv SUPABASE_MEETING_SEARCH_PIPELINE.md docs/architecture/
git mv notes.md docs/
git mv security_best_practices_report.md docs/
```

**Verify:** `ls *.md` at root should only show `README.md`.

---

## Task 2: Create services/agents/ package

**Objective:** Move agent orchestration modules into a package.

**Files:**
- Create dir: `services/agents/`
- Move: `agent_events_bus.py` → `services/agents/events_bus.py`
- Move: `agent_events_service.py` → `services/agents/events_service.py`
- Move: `agent_dashboard.py` → `services/agents/dashboard.py`
- Move: `agent_mission_control.py` → `services/agents/mission_control.py`
- Move: `agent_status.py` → `services/agents/status.py`
- Move: `openclaw_heartbeat.py` → `services/agents/heartbeat.py`
- Create: `services/agents/__init__.py` with re-exports

**Import updates:**
- `app.py`: `from agent_events_bus import ...` → `from services.agents.events_bus import ...`
- `app.py`: `from agent_dashboard import register_agent_dashboard` → `from services.agents.dashboard import register_agent_dashboard`
- `app.py`: `from agent_mission_control import ...` → `from services.agents.mission_control import ...`
- `app.py`: `from agent_status import ...` → `from services.agents.status import ...`
- `tests/test_agent_events_bus.py`: `from agent_events_bus import ...` → `from services.agents.events_bus import ...`
- `tests/test_agent_events_service.py`: `from agent_events_service import ...` → `from services.agents.events_service import ...`
- `tests/test_agent_mission_control.py`: `from agent_mission_control import ...` → `from services.agents.mission_control import ...`
- `tests/test_agent_monitoring_dashboard.py`: `from agent_dashboard import ...` → `from services.agents.dashboard import ...`
- `tests/test_agent_status_services.py`: `from agent_status import ...` → `from services.agents.status import ...`
- Any other files importing these modules.

**Commands:**
```bash
mkdir -p services/agents
git mv agent_events_bus.py services/agents/events_bus.py
git mv agent_events_service.py services/agents/events_service.py
git mv agent_dashboard.py services/agents/dashboard.py
git mv agent_mission_control.py services/agents/mission_control.py
git mv agent_status.py services/agents/status.py
git mv openclaw_heartbeat.py services/agents/heartbeat.py
```

**Verify:** `pytest tests/test_agent_events_bus.py tests/test_agent_events_service.py tests/test_agent_mission_control.py tests/test_agent_monitoring_dashboard.py tests/test_agent_status_services.py -v`

---

## Task 3: Create services/ingestion/fetchers/ package

**Objective:** Move root-level fetchers into a package.

**Files:**
- Create dir: `services/ingestion/fetchers/`
- Move: `bozeman_police_fetcher.py` → `services/ingestion/fetchers/bozeman.py`
- Move: `missoula_public_report_fetcher.py` → `services/ingestion/fetchers/missoula.py`
- Move: `whitefish_blotter_fetcher.py` → `services/ingestion/fetchers/whitefish.py`
- Move: `mhp_crash_fetcher.py` → `services/ingestion/fetchers/mhp_crashes.py`
- Move: `crimemapping_fetcher.py` → `services/ingestion/fetchers/crime_mapping.py`
- Move: `mt_blotter_scraper.py` → `services/ingestion/fetchers/mt_blotter.py`
- Move: `fwp_scraper.py` → `services/ingestion/fetchers/fwp.py`
- Create: `services/ingestion/__init__.py`
- Create: `services/ingestion/fetchers/__init__.py` with re-exports

**Import updates:** Search for all imports of these modules and update.

**Commands:**
```bash
mkdir -p services/ingestion/fetchers
git mv bozeman_police_fetcher.py services/ingestion/fetchers/bozeman.py
git mv missoula_public_report_fetcher.py services/ingestion/fetchers/missoula.py
git mv whitefish_blotter_fetcher.py services/ingestion/fetchers/whitefish.py
git mv mhp_crash_fetcher.py services/ingestion/fetchers/mhp_crashes.py
git mv crimemapping_fetcher.py services/ingestion/fetchers/crime_mapping.py
git mv mt_blotter_scraper.py services/ingestion/fetchers/mt_blotter.py
git mv fwp_scraper.py services/ingestion/fetchers/fwp.py
```

**Verify:** `pytest tests/ -v -k "ingestion or fetcher or bozeman or missoula or whitefish or mhp or crime_mapping or mt_blotter or fwp"`

---

## Task 4: Consolidate email ingestion

**Objective:** Merge email-related ingestion modules into `services/ingestion/email.py`.

**Files:**
- Create: `services/ingestion/email.py` (merge `email_blotter_ingest.py`, `email_image_blotter.py`, `email_worker.py`)
- Keep: `fetch_mail.py` at root (utility, not ingestion-specific)
- Delete after merge: `email_blotter_ingest.py`, `email_image_blotter.py`, `email_worker.py`

**Approach:**
- Read all three files.
- Create `services/ingestion/email.py` with all classes/functions.
- Handle naming conflicts (prefix with domain if needed).
- Update all imports.

**Import updates:** Search for imports of `email_blotter_ingest`, `email_image_blotter`, `email_worker`.

**Verify:** `pytest tests/test_email_ops.py -v` (or relevant tests)

---

## Task 5: Consolidate Facebook publishing

**Objective:** Merge Facebook modules into `services/publishing/facebook.py`.

**Files:**
- Create dir: `services/publishing/`
- Create: `services/publishing/facebook.py` (merge `facebook_publisher.py`, `facebook_worker.py`, `facebook_page_manager.py`)
- Keep: `facebook_page_manager_daemon.py` at root or move to `services/publishing/`
- Delete after merge: `facebook_publisher.py`, `facebook_worker.py`, `facebook_page_manager.py`

**Verify:** `pytest tests/ -v -k "facebook"`

---

## Task 6: Consolidate blog/newsroom

**Objective:** Merge blog/newsroom modules into `services/publishing/blog.py` or `services/publishing/newsroom.py`.

**Files:**
- Create: `services/publishing/blog.py` (merge `daily_blog_worker.py`, `duplicate_blog_checker.py`, `news_writer_agent.py`, `news_planner.py`, `news_editor_agent.py`)
- Keep: `duplicate_blog_checker_daemon.py` as daemon wrapper
- Keep: `news_source_registry.py` at root (registry, not worker)
- Delete after merge: `daily_blog_worker.py`, `duplicate_blog_checker.py`, `news_writer_agent.py`, `news_planner.py`, `news_editor_agent.py`

**Verify:** `pytest tests/ -v -k "blog or news"`

---

## Task 7: Consolidate alerts

**Objective:** Merge alert modules into `services/alerts/`.

**Files:**
- Create dir: `services/alerts/`
- Create: `services/alerts/engine.py` (merge `alerting.py`, `alert_engine.py`)
- Move: `alert_dispatcher.py` → `services/alerts/dispatcher.py`
- Move: `ingestion_alerts.py` → `services/alerts/ingestion.py`
- Move: `incident_notifications.py` → `services/alerts/incidents.py`
- Move: `sex_offender_alerts.py` → `services/alerts/sex_offender.py`
- Move: `court_source_alerts.py` → `services/alerts/court.py`
- Move: `meeting_source_alerts.py` → `services/alerts/meetings.py`
- Move: `bail_bonds_alerts.py` → `services/alerts/bail_bonds.py`
- Move: `weekly_county_digest.py` → `services/alerts/weekly_digest.py`
- Move: `weekly_safety_report.py` → `services/alerts/weekly_safety.py`
- Move: `weekly_snapshot.py` → `services/alerts/weekly_snapshot.py`
- Move: `watchdog_digest.py` → `services/alerts/watchdog.py`
- Create: `services/alerts/__init__.py`

**Verify:** `pytest tests/ -v -k "alert or digest or weekly"`

---

## Task 8: Move scripts

**Objective:** Move shell scripts into `scripts/ops/`.

**Files:**
- Move: `deploy.sh` → `scripts/ops/deploy.sh`
- Move: `upload.sh` → `scripts/ops/upload.sh`
- Move: `crontab.txt` → `scripts/ops/crontab.txt`

**Verify:** Check no hardcoded paths reference these files from root.

---

## Task 9: Create core/ package

**Objective:** Move shared utilities into `core/`.

**Files:**
- Create dir: `core/`
- Move: `dedupe.py` → `core/dedupe.py`
- Move: `sanitize.py` → `core/sanitize.py`
- Move: `pipeline_state.py` → `core/pipeline_state.py`
- Move: `queue_config.py` → `core/queue_config.py`
- Move: `queue_helpers.py` → `core/queue_helpers.py`
- Move: `tasks.py` → `core/tasks.py`
- Move: `source_registry.py` → `core/source_registry.py`
- Move: `agency_normalization.py` → `core/agency_normalization.py`
- Create: `core/__init__.py`

**Verify:** `pytest tests/ -v -k "dedupe or agency or source_registry"`

---

## Task 10: Move remaining services

**Objective:** Move other business logic modules into `services/`.

**Files:**
- Create dirs: `services/blotter/`, `services/court/`, `services/meetings/`, `services/persons/`, `services/geo/`, `services/summarizer/`, `services/analytics/`
- Move: `pdf_parser.py` → `services/blotter/parser.py`
- Move: `processor.py` → `services/blotter/processor.py`
- Move: `blotter_auditor.py` → `services/blotter/auditor.py`
- Move: `blotter_analytics.py` → `services/blotter/analytics.py`
- Move: `summarizer.py` → `services/summarizer/engine.py`
- Move: `historical_context.py` → `services/summarizer/historical_context.py`
- Move: `court_tracker.py` → `services/court/tracker.py`
- Move: `court_ingest.py` → `services/court/ingest.py`
- Move: `court_refresh.py` → `services/court/refresh.py`
- Move: `court_source_adapters.py` → `services/court/source_adapters.py`
- Move: `public_meetings.py` → `services/meetings/public.py`
- Move: `agendas_ingest.py` → `services/meetings/agendas_ingest.py`
- Move: `missing_persons.py` → `services/persons/missing.py`
- Move: `missing_person_watch.py` → `services/persons/watch.py`
- Move: `sex_offender_scraper.py` → `services/persons/sex_offender_scraper.py`
- Move: `sex_offender_delta.py` → `services/persons/sex_offender_delta.py`
- Move: `geocode_pipeline.py` → `services/geo/pipeline.py`
- Move: `salary_ingest.py` → `services/ingestion/salaries.py`
- Move: `code_violation_ingest.py` → `services/ingestion/code_violations.py`
- Move: `jail_booking_ingest.py` → `services/ingestion/jail_bookings.py`

**Verify:** Run full test suite.

---

## Task 11: Move admin_ai and related

**Objective:** Move admin AI modules.

**Files:**
- Move: `admin_ai.py` → `services/admin/ai.py`
- Move: `kimi_sqlite_agent.py` → `services/admin/sqlite_agent.py`
- Move: `charge_explainer_worker.py` → `services/admin/charge_explainer.py`
- Move: `case_journeys.py` → `services/admin/case_journeys.py`

---

## Task 12: Move bondsman and monetization

**Objective:** Move bondsman and revenue modules.

**Files:**
- Create dir: `services/monetization/`
- Move: `bondsman_command_center.py` → `services/monetization/bondsman.py`
- Move: `bondsman_watch_worker.py` → `services/monetization/bondsman_watch.py`
- Move: `bail_bonds_alerts.py` → `services/monetization/bail_alerts.py` (if not already moved)
- Move: `resend_bounced.py` → `services/monetization/resend_bounced.py`

---

## Task 13: Move monitoring and ops

**Objective:** Move monitoring/ops modules.

**Files:**
- Create dir: `services/ops/`
- Move: `script_watchdog.py` → `services/ops/watchdog.py`
- Move: `ingestion_monitoring.py` → `services/ops/ingestion_monitor.py`
- Move: `ingestion_smoke_check.py` → `services/ops/smoke_check.py`
- Move: `anthropic_credit_monitor.py` → `services/ops/credit_monitor.py`

---

## Task 14: Move API/auth infrastructure

**Objective:** Move API/auth modules.

**Files:**
- Create dir: `services/api/`
- Move: `api_auth.py` → `services/api/auth.py`
- Move: `api_routes.py` → `services/api/routes.py`

---

## Task 15: Move remaining misc

**Objective:** Move remaining root modules.

**Files:**
- Move: `config.py` → `core/config.py` (or keep at root — it's imported everywhere)
- Keep at root: `app.py`, `db.py`, `init_db.py` (core infrastructure)
- Move: `setup.py` → `scripts/setup.py`
- Move: `seed_admin.py` → `scripts/seed_admin.py`
- Move: `example_agent.py` → `services/agents/example.py`
- Move: `news_source_registry.py` → `core/news_source_registry.py`
- Move: `check_montana_orgs.py` → `services/ops/check_orgs.py`
- Move: `morning_briefing.py` → `services/publishing/morning_briefing.py`

---

## Final Verification

```bash
# Full test suite
python -m pytest tests/ -v

# Check no broken imports
python -m py_compile app.py
python -m py_compile config.py
python -m py_compile db.py

# Check root is clean
ls *.py | wc -l  # should be ~3-5 files
```

## Risks

- **Import cycles:** Moving modules may reveal hidden cycles. Fix by extracting shared interfaces.
- **App.py size:** Still 11K lines. Phase 2 will split routes into blueprints.
- **Test imports:** Tests may import moved modules directly. Update all test files.
- **Cron/scripts:** Shell scripts may hardcode Python module paths. Verify after move.
