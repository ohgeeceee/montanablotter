# Missing Persons Team — mtblotter.com

Tracks Montana missing persons daily from the Montana Missing Persons Index (DOJ),
posts them to mtblotter.com, and marks individuals as found when removed from the index.

## Roles

| Role | Owner | Responsibility |
|------|-------|----------------|
| Source Lead | Jon | Source URL verification, approval of new data fields |
| Fetcher | blotter-ingest | Daily scrape/pull of Montana Missing Persons Index |
| Parser/normalizer | blotter-dev | Parse entries, dedup, upsert into `missing_persons` table |
| Reconciliation | blotter-ingest | Detect removals from index → mark `status='found'` with found_date |
| Publisher | blotter-ops | Public pages, alerts, digest inclusion |
| QA | blotter-dev | Tests for fetch/parse/reconcile, regression |

## Daily flow

1. Fetch current Montana Missing Persons Index listing.
2. Parse each entry: name, age, date missing, last seen location, photo URL, case number, agency.
3. Upsert into `missing_persons` (dedup by case number / name+date).
4. Reconcile: any previously-active record not present in today's fetch → set status='found', found_date=today, source='removed_from_index'.
5. Publish to site + include in daily digest.
6. Alert on newly added and newly found.

## Schema (proposed `missing_persons`)

- id, case_number UNIQUE, full_name, age, date_missing, last_seen_location
- agency, photo_url, details, status ('active'/'found'), found_date
- source_url, first_seen_at, last_seen_at, updated_at

## Backlog (initial)

1. Locate Montana Missing Persons Index source URL and format (HTML/PDF/API).
2. Design `missing_persons` table + migration.
3. Implement fetcher + parser.
4. Implement reconciliation (removed → found).
5. Add public listing page + detail page.
6. Add to daily digest and alerts.
7. Add tests for fetch/parse/reconcile.

## Working rules

- Never fabricate person details; only publish parsed index data.
- Mark found only when confirmed removed from index (or explicit update).
- Keep photos as links to source, don't hotlink without permission check.
- Red-tier: schema changes and new fetchers need Jon approval.
