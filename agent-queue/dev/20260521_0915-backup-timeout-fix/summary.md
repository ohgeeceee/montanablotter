---
profile: dev
created: 2026-05-21T09:15:00-06:00
tier: red
status: open
priority: medium
related_files:
  - /root/montanablotter/backup_db.sh
  - /root/montanablotter/crontab.txt
---

# Summary

The nightly DB backup times out intermittently because a 13 GB SQLite copy at idle I/O priority (`ionice -c3 nice -n 19`) with `pages=8192, sleep=0.5` can exceed the 12-hour cron timeout.

## Problem

- **May 17 backup:** timed out after 43200s, leaving no fresh backup
- **May 18 backup:** skipped because lock file still held by timed-out May 17 process
- Current backup (07:26 today) has been running ~2h and is ~65% done
- At this rate, total runtime is ~3–4h, but under I/O contention it can exceed 12h

## Proposed change

### Option A — Optimize backup throughput (recommended)

Edit `backup_db.sh`:

```bash
# Before
src.backup(dst, pages=8192, sleep=0.5)

# After
src.backup(dst, pages=32768, sleep=0.1)
```

This reduces sleep overhead from ~200s to ~10s total and quadruples batch size, cutting runtime by ~30–50% without significantly impacting web traffic.

### Option B — Increase cron timeout

Edit `crontab.txt`:

```bash
# Before
--timeout 43200

# After
--timeout 64800
```

### Option C — Both

Apply Option A + Option B for maximum headroom.

## Rollback

Revert `backup_db.sh` and `crontab.txt` from git.

## Verification

After change, next backup should complete in <6h and produce a `.db.gz` file in `db_backups/`.
