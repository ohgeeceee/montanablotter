# Police Blotter Team — mtblotter.com

Companion to `docs/CODEMAPS/*`. Defines the team that gathers daily police blotters across Montana.

## Roles

| Role | Owner | Responsibility |
|------|-------|----------------|
| Coverage Lead | Jon | Source priorities, outreach, Red-tier sign-off |
| Fetchers | blotter-ingest | Per-agency scrapers (Whitefish, Bozeman, Missoula, Billings, etc.) |
| Parser/normalizer | blotter-dev | PDF/HTML/email parsers, dedup, schema |
| Publisher | blotter-ops | Public pages, digests, daily blog |
| Civic | blotter-civic | Agency outreach, records requests, gap closure |
| QA | blotter-dev | Parser tests, regression |

## Daily flow

1. Fetch each agency's daily blotter (PDF/HTML/email).
2. Parse into records (date, time, county, location, incident type, details).
3. Dedup + upsert into `records` table.
4. Publish to site + daily blog + digests.
5. Alert on gaps/stuck PDFs.

## Backlog (initial)

1. Coverage audit: which of 56 counties have blotter feeds.
2. Add parsers for agencies without coverage.
3. Improve PDF layout resilience (format drift detection).
4. Outreach for silent agencies (>7 days).
5. Add tests per parser.

## Working rules

- Parser changes are Red-tier: draft as PR, Jon merges.
- Never fabricate incidents; only publish parsed data.
- Idempotent slugs; re-runs skip duplicates.
