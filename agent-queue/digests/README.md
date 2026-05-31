# Digests

Two cadences:

- `daily/<YYYY-MM-DD>.md` — generated nightly at 06:55 MT, delivered 07:00 MT
- `weekly/<YYYY-WW>.md` — generated Sunday 18:55 MT, delivered 19:00 MT

## How a digest is built

1. At 06:50 MT (daily) or Sunday 18:50 MT (weekly), each profile writes its raw section to `raw/<profile>/<date>.md` using the per-profile schema below.
2. An aggregator (cron job or one profile designated for this) reads the four raw files and produces `daily/<date>.md` or `weekly/<week>.md` using the aggregate template.
3. The aggregate is delivered through whatever channel is wired up (email, Slack, Telegram).

If a profile fails to write its raw section in time, the aggregate notes "ops section missing" rather than silently omitting it. A missing section is itself information.

## Per-profile raw digest template

Save as `digests/raw/<profile>/<date>.md` when generated. Example for ops:

```markdown
---
profile: blotter-ops
date: 2026-05-15
generated_at: 2026-05-15T06:50:00-06:00
status: green
---

## Health
- Public site: 200 OK | 24h uptime: 99.97%
- Service: montanablotter active (running) | restarts 24h: 0
- Disk: / 42% | /root 38%
- TLS cert: 67 days until expiry
- Backups: 7/7 days present | latest: 6h ago

## Yellow-tier actions (last 24h)
- (none)

## Open queue
- Total open: 0 | New today: 0 | Aged >7d: 0

## Notes
(blank unless something's worth flagging that doesn't fit above)
```

Profile-specific health fields:

- ingest: counties on schedule (X/56), counties drifting, stuck PDFs, parse success rate trailing 7d, queue depth in uploads/
- dev: open draft PRs, dependency advisories, coverage delta vs last week, lint/test status
- civic: outreach drafts pending send, silent feeds (>7d), press mentions, roster changes

## Daily aggregate template

Save as `digests/daily/<date>.md`. The aggregator fills this in from the four raw files.

```markdown
# Montana Blotter — Daily Digest — 2026-05-15

**Overall: GREEN** • Open queue items: 3 • Yellow actions (24h): 1 • Red-tier proposals open: 0

## ops — GREEN
Site 200. Disk 42%/38%. Cert 67d. Backups current.
Yellow actions: 1 (worker restart 03:42 after 47min silence; recovered).

## ingest — YELLOW
53/56 counties on schedule. 3 flagged: cascade (stuck PDF, format drift suspected), madison (silent 6d), powell (record count 22% of median).
Queue: 2 new items.

## dev — GREEN
No drafts pending review. 1 draft PR open >3 days: #47 parser refactor.

## civic — GREEN
No outreach drafts pending send. Madison check-in draft will be queued tomorrow if no recovery.

## Action items for Jon
1. Review `agent-queue/ingest/2026-05-15-0342-cascade-stuck-pdf/` — format drift candidate, parser draft attached
2. Disposition draft PR #47 in `agent-queue/dev/` or extend review window
3. (informational) Madison feed silent 6d — no action yet, civic will draft check-in tomorrow

## 7-day trend
- Ingest success rate: 98.1% → 97.4% → 96.9% (slow drift, watch)
- Stuck PDF queue depth: 0 → 0 → 1
- Public site uptime: 100% → 99.97% → 99.97%
```

## Weekly aggregate template

Save as `digests/weekly/<YYYY-WW>.md`. Same shape, longer trend window, plus:

```markdown
# Montana Blotter — Weekly Digest — Week 2026-W20 (May 11–17)

[overall + per-profile sections like daily, but covering the week]

## Dev queue review
- Draft PRs ready for disposition: list with one-line summaries
- Dependency advisories noted (no action taken): list
- Coverage gaps drafted as new tests: list

## Civic queue review
- Outreach drafts ready for send-or-discard: list with recipient + one-line purpose
- Roster changes: new contacts, departures, role changes
- Press mentions this week: list with one-line summary and source

## County roster snapshot
- Cooperating, on schedule: X
- Cooperating, drifting: Y (list)
- Silent / unknown: Z (list)
- New outreach targets this week: list

## Promotion candidates
Any Yellow-tier workflow that ran ≥10 times this week with zero issues. These are candidates for tier promotion at Jon's discretion — proposed promotions go in `red-tier/` (since they're changes to MISSION.md, which is itself a Red-tier change).

## Action items for Jon
1. Disposition any items aged >7d
2. Review promotion candidates if any
3. Confirm or revise next week's outreach plan
```

A note on the aggregator: simplest path is a tiny shell or Python script run by cron, not a profile. Have it read the four raw files and concatenate them into the aggregate template. Keeping the aggregator dumb (no LLM in the loop) means the digest is deterministic — if you don't see your ops section, ops didn't write it, full stop. That's a useful property when you're trying to figure out why the digest looks weird.

Once you have a week or two of real digests, the format will want tuning — fields you never read, fields that should have been there, etc. Treat the templates as v0.1, not load-bearing.
