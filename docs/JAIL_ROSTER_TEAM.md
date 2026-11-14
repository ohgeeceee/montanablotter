# Jail Roster Team — mtblotter.com

Companion to `docs/CODEMAPS/jail-bookings.md`. Defines the team that finds Montana jail rosters daily and publishes them to mtblotter.com.

## Roles

| Role | Owner | Responsibility |
|------|-------|----------------|
| Coverage Lead | Jon | Priorities, outreach approvals, Red-tier sign-off |
| Fetcher adapters | blotter-ingest | Per-county roster fetchers (55/56 live; Hill County has no online roster) |
| Parser/normalizer | blotter-dev | `transform_roster_record.py`, schema, dedup, hash backfill |
| Publisher | blotter-ops | Post rosters to site, daily digest, mobile API |
| Outreach | blotter-civic | Sheriff/records-officer contact, FOIA, gap closure |
| QA | blotter-dev | Roster search tests, public roster tests, regression |

## Daily flow

1. Fetch each county roster (adapters in `scrapers/` + `ingestion/`).
2. Normalize records via `transform_roster_record.py`.
3. Dedup/hash via `backfill_jail_bookings_hashes.py`.
4. Publish to `/jail-rosters` + mobile API.
5. Daily digest + alerts on gaps/failures.
6. Outreach for silent counties (>7 days).

## Backlog (initial)

1. Hill County: obtain roster via email/FOIA (only county without online roster).
2. Add smoke tests for all 55 fetcher adapters.
3. Add retry/backoff + alerting for failed fetches.
4. Mobile app jail roster screen QA.
5. Per-county roster freshness dashboard.
6. Backfill hashes for historical bookings.
7. Outreach automation for new counties.

## Working rules

- Fetcher changes are Red-tier: draft as PR, Jon merges.
- Never fabricate roster entries; only publish parsed data.
- Hash dedup prevents duplicate postings.
- Hill County gap tracked until resolved.
