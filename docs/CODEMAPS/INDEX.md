# Montana Blotter Codemaps

**Last Updated:** 2026-05-29

This directory contains architectural maps for Montana Blotter's key subsystems. Each codemap documents module structure, data flow, dependencies, and integration points.

## Overview

Montana Blotter is a full-stack news site covering public records in Montana: police blotters, jail bookings, court cases, missing persons, sex offender registry, code violations, and more.

**Stack:** Python 3.12 + Flask + SQLite + Anthropic Claude API (claude-sonnet-4-6)
**Deployment:** nginx → gunicorn (3 workers) + systemd on Ubuntu 22.04
**Database:** SQLite with WAL mode, 80+ tables, ~10GB active data

## Codemaps

| Area | Purpose | Entry Points |
|------|---------|--------------|
| [blotter-ingest](blotter-ingest.md) | Police blotter PDF/text ingestion, parsing, record normalization | `email_worker.py`, `processor.py`, `services/blotter/` |
| [court-tracking](court-tracking.md) | Court case discovery, outcome scraping, hearing alerts | `services/court/tracker.py`, `outcome_scraper.py`, `refresh.py` |
| [jail-bookings](jail-bookings.md) | Jail roster ingestion (50+ MT counties), booking tracking, release monitoring | `services/ingestion/jail_bookings.py`, `services/detention/` |
| [publishing-newsroom](publishing-newsroom.md) | Autonomous news generation, story candidate review, publication workflow | `services/publishing/`, `services/summarizer/`, `news_planner.py` |
| [persons-tracking](persons-tracking.md) | Missing persons alerts, sex offender registry sync, delta detection | `services/persons/missing.py`, `sex_offender_scraper.py` |
| [monetization](monetization.md) | Bail ad orders/slots/creatives, recovery center ads, paywall, donations | `services/monetization/`, `recovery_ads_bp`, `bail_ad_*` tables |
| [database](database.md) | Schema, migrations, core tables, relationships, indexes | `init_db.py`, `migrate()` |
| [api-auth](api-auth.md) | REST API tokens, request authentication, rate limiting | `blueprints/api/`, `services/api/auth.py` |

## Related Docs

- `/root/montanablotter/CLAUDE.md` — Architecture overview and conventions
- `/root/montanablotter/docs/IMPLEMENTATION_SUMMARY.md` — Feature timeline
- `/root/montanablotter/docs/AGENTS.md` — Autonomous agent reference
- `/root/montanablotter/docs/openclaw-setup.md` — OpenClaw integration (deprecated)

## Key Principles

1. **Single source of truth** — Codemaps generated from actual code, not speculation
2. **Module boundaries** — Each map covers one logical subsystem
3. **Data flow focus** — Shows how data moves through the system
4. **External deps** — Lists package versions and integration points
5. **Freshness** — Updated whenever major changes land

## Navigation Tips

- Start with [blotter-ingest.md](blotter-ingest.md) for the core data pipeline
- Check [database.md](database.md) for table schema and relationships
- See [publishing-newsroom.md](publishing-newsroom.md) for autonomous agents
- Use [court-tracking.md](court-tracking.md) for litigation discovery features

---

**Generated:** 2026-05-29 | **Generator:** Claude Codemap Tool
