---
profile: ingest
created: 2026-05-27T06:45:01
tier: green
status: open
priority: med
related_county: ""
related_files: []
---

# Daily Parse Quality Audit

## Parse success rate — run this query

```sql
SELECT
    LOWER(county) AS county,
    COUNT(*) AS total,
    SUM(CASE WHEN parse_error IS NULL THEN 1 ELSE 0 END) AS ok,
    ROUND(100.0 * SUM(CASE WHEN parse_error IS NULL THEN 1 ELSE 0 END)
          / COUNT(*), 1) AS pct
FROM blotters
WHERE created_at >= datetime('now', '-7 days')
GROUP BY LOWER(county)
HAVING pct < 95 OR total = 0
ORDER BY pct ASC;
```

Flag any county below 95% to blotter-dev immediately.

## Record spot-check

For 5 random counties, inspect 3 recent records each:
- Name field: proper format, no special chars or truncation.
- Charges: not empty, not garbled.
- Date: within the last 90 days.
- Agency: normalized county/city name.

Alert on: empty charges, garbled names, dates >3 months ago.

## Image blotter errors (Havre PD)

- `[email_image_blotter] Skipping bounce email: Mail delivery failed: returning message to sender`
- `[email_image_blotter] Processing: Mail delivery failed: returning message to sender from Mail Delivery System <mailer-daemon@perfora.net>`
- `[email_image_blotter] Skipping bounce email: Mail delivery failed: returning message to sender`
