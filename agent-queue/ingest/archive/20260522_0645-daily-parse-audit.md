---
profile: ingest
created: 2026-05-22T06:45:01
tier: green
status: open
priority: high
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

## RQ parsing errors (last 24h)

- `22:31:38 Worker b06012afc63647f6b6edfe3194205734: job cf62d470-39fe-4065-9843-f2f61cb73159 failed (Work-horse terminated unexpectedly; waitpid returned 9 (signal 9); )`
- `22:32:33 Worker c8d1ac3340474515955134473cf9dcf6: job 4a3da5f6-225a-4bc2-8c11-5b71863c47e8 failed (Work-horse terminated unexpectedly; waitpid returned 9 (signal 9); )`

## Image blotter errors (Havre PD)

- `[email_image_blotter] Processing: Mail delivery failed: returning message to sender from Mail Delivery System <mailer-daemon@perfora.net>`
- `[email_image_blotter] Skipping bounce email: Mail delivery failed: returning message to sender`
- `[email_image_blotter] Processing: Mail delivery failed: returning message to sender from Mail Delivery System <mailer-daemon@perfora.net>`
