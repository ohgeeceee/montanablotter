# Agent Queue

Working directory for the Montana Blotter agent fleet. Every drafted action, flagged issue, and Red-tier proposal lands here for Jon to review.

## Layout

- `ops/`, `ingest/`, `dev/`, `civic/` — items owned by the corresponding profile
- `new-counties/` — county additions Jon wants the fleet to start working
- `red-tier/` — Red-tier action proposals from any profile (code change, deploy, DB write, outbound message, dependency install, infra change)
- `archive/` — dispositioned items (kept for paper trail)
- `digests/` — daily and weekly reports
- `_template/` — canonical item structure; do not put real items here

- `bin/` — operational scripts (aggregator, digest runner, crontab snippet)

## Item naming

One directory per item:

```
YYYY-MM-DD-HHMM-<slug>/
  ITEM.md        # structured frontmatter + body (see _template/)
  attachments/   # optional supporting files
```

## Digest pipeline

Cron triggers `bin/run-digest.sh daily` at **6:50 AM MT** and `bin/run-digest.sh weekly` at **6:50 PM MT Sundays**.

```
run-digest.sh
  └── triggers each profile → writes digests/raw/<profile>/YYYY-MM-DD.md
  └── bin/aggregator.py daily|weekly
        └── reads raw sections, emits digests/daily/ or digests/weekly/
```

Log: `/var/log/montana-digest.log`

`aggregator.py` is stdlib-only and deterministic — no LLM, no network. Missing raw sections are flagged loudly in the output rather than silently skipped.

## Red-tier workflow

Profiles may not self-approve Red-tier actions. They must:
1. Create an item under `red-tier/` with a full proposal in `ITEM.md`
2. Wait for Jon to review and move or delete the item
3. Proceed only after explicit approval

## Disposition

Move completed or rejected items to `archive/` with a one-line outcome note appended to `ITEM.md`. Do not delete items.