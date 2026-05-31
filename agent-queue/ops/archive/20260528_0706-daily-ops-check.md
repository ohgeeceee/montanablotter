---
profile: ops
created: 2026-05-28T07:06:28
tier: green
status: open
priority: high
related_county: ""
related_files: []
---

# Daily Ops Health Check

## Required actions

1. Probe montanablotter.com — verify HTTP 200, response time <2s.
2. Check all systemd units:
   `systemctl is-active montanablotter nginx fb-page-manager blog-dup-checker`
3. Disk usage: `df -h / /root` — alert if >80%.
4. TLS cert: check expiry with openssl — alert if <21 days.
5. Backup chain: verify 7 daily .bak files exist in db_backups/.
   If latest is missing or >24h stale, trigger Yellow-tier snapshot.

## Known issues

- SSH brute-force sustained elevation (~11k/day) — check fail2ban counts.
- Court calendar WAF block (pubcourts.mt.gov) since 2026-05-17 — note if recovered.

## Backup log anomalies

- `upload failed: db_backups/blotter_20260430_020001.db.gz to s3://montanablotter-backups/blotter_20260430_020001.db.gz Unable to locate credentials`
- `[2026-04-30 02:02:08] Backup failed with exit code 1.`
- `upload failed: db_backups/blotter_20260501_020001.db.gz to s3://montanablotter-backups/blotter_20260501_020001.db.gz Unable to locate credentials`
