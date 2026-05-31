---
profile: ingest
created: 2026-05-19T08:30:00-06:00
tier: yellow
status: open
priority: high
related_county: "Gallatin"
related_files:
  - /root/montanablotter/services/ingestion/jail_bookings.py
  - /root/montanablotter/logs/jail_booking_ingest.log
---

# Summary

Gallatin County jail booking ingest has been failing since at least 2026-05-11 with a JSON parse error ("Expecting value: line 1 column 1 (char 0)"). This is a major county (top 3 by volume) and the failure means 0 new bookings are being captured.

# Observation

From the source coverage cron output (mb_source_coverage_daily, 2026-05-17):

```json
{
  "county_name": "Gallatin",
  "facility_name": "Gallatin County Detention Center",
  "source_type": "official_roster",
  "coverage_tier": "major",
  "is_enabled": 1,
  "last_checked_at": "2026-05-11 20:47:01",
  "last_success_at": null,
  "latest_error": "Expecting value: line 1 column 1 (char 0)",
  "notes": "network_error: Expecting value: line 1 column 1 (char 0)"
}
```

The error pattern suggests the upstream roster endpoint is returning HTML (e.g., cloudflare block, maintenance page, or redirect) instead of JSON. The scraper last succeeded before 2026-05-11.

# Proposed action

1. Run the Gallatin jail booking scraper manually with debug logging to capture the actual HTTP response body:
   ```bash
   cd /root/montanablotter
   /root/montanablotter/venv/bin/python3 services/ingestion/jail_bookings.py --county gallatin --debug 2>&1 | head -100
   ```
2. If the endpoint changed, update the request URL/headers in `services/ingestion/jail_bookings.py` or the county-specific adapter.
3. If the endpoint is behind a new anti-bot measure, evaluate whether a headless browser or session cookie is needed.
4. Re-queue any missing bookings from the outage window once the fix is deployed.

# Reasoning

Gallatin is a major county with high public interest. A 8+ day outage is significant. The JSON parse error at char 0 is almost always a non-JSON response (HTML error page). Ruling out: DB corruption (error is at ingest time, not storage), rate limiting (would typically be HTTP 429, not HTML), and schema drift (would parse partially then fail, not at char 0).

# Rollback

Revert any URL/header changes in `services/ingestion/jail_bookings.py` via git checkout. If a new dependency (e.g., selenium) is added, remove it from requirements.txt and the venv.

# Verification

After fix, `last_success_at` should update within 2 hours (the scraper runs every 2 hours at :20). Check via:
```bash
sqlite3 blotter.db "SELECT last_success_at FROM jail_booking_sources WHERE county_name='Gallatin';"
```
