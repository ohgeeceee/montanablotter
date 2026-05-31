---
profile: dev
created: 2026-05-19T08:45:00-06:00
tier: red
status: open
priority: high
related_county: ""
related_files:
  - /root/montanablotter/services/court/refresh.py
  - /root/montanablotter/services/alerts/court.py
---

# Summary

Two court calendar scrapers have been in error state since 2026-05-17: Montana Courts of Limited Jurisdiction (COLJ) Calendar and Montana District Court Calendar. Both are critical transparency sources.

# Observation

From source coverage cron output (mb_source_coverage_daily, 2026-05-17):

```json
[
  {
    "source_slug": "montana-colj-calendar",
    "alert_kind": "error",
    "summary": "Montana Courts of Limited Jurisdiction Calendar is error (scraped=2026-05-17 12:18:15, success=2026-05-17 03:18:21).",
    "first_detected_at": "2026-05-17 06:47:02",
    "last_detected_at": "2026-05-17 15:17:02"
  },
  {
    "source_slug": "montana-district-court-calendar",
    "alert_kind": "error",
    "summary": "Montana District Court Calendar is error (scraped=2026-05-17 12:18:11, success=2026-05-17 03:18:13).",
    "first_detected_at": "2026-05-17 06:47:02",
    "last_detected_at": "2026-05-17 15:17:02"
  }
]
```

The scrapers succeeded at ~03:18 but failed at ~12:18 on the same day, suggesting a transient outage or a site change that broke the parser.

# Proposed action

1. Run the court refresh manually in debug mode to capture the actual error:
   ```bash
   cd /root/montanablotter
   /root/montanablotter/venv/bin/python3 -m services.court.refresh --debug 2>&1 | tail -50
   ```
2. Check the court source websites directly for layout changes, new anti-bot measures, or endpoint moves.
3. If the HTML structure changed, update the CSS selectors or parsing logic in `services/court/refresh.py`.
4. If the site added rate limiting, add backoff/retry logic.
5. Open a draft PR with the parser patch.

# Reasoning

Both sources broke simultaneously around midday on 2026-05-17. This timing pattern (03:18 success, 12:18 failure) strongly suggests a scheduled site update or maintenance window that changed the page structure, rather than a network issue. The fix is likely a selector update.

# Rollback

```bash
git checkout -- services/court/refresh.py
```

If new dependencies were added, revert `requirements.txt` and rebuild the venv.

# Verification

After fix, the court source alerts should clear within one `court_refresh` cycle (runs every 3 hours at :18). Verify via:
```bash
sqlite3 blotter.db "SELECT source_slug, alert_kind, last_detected_at FROM source_alerts WHERE source_slug LIKE '%court-calendar%' AND alert_kind='error';"
```
Expected: 0 rows.
