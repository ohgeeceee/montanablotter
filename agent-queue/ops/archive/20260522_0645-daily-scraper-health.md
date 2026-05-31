---
profile: ops
created: 2026-05-22T06:45:01
tier: green
status: open
priority: high
related_county: ""
related_files: []
---

# Daily Scraper Health Check

## Active scraper status

- ⚠️  Jail rosters (all counties) (expect every 4h): 20 errors
- ⚠️  Missoula public report (expect every 1h): 20 errors
- ⚠️  CrimeMapping 8 MT agencies (expect every 12h): 20 errors
- ⚠️  Whitefish PD blotter (expect every 6h): 20 errors
- ⚠️  MHP crash news releases (expect every 24h): 8 errors
- ⚠️  MT sex/violent offender registry (expect every 8h): 20 errors
- ⚠️  MT missing persons watch (expect every 1h): 20 errors
- ⚠️  Bozeman PD calls-for-service (expect every 1h): 20 errors

## Gallatin Zuercher recovery check

URL: https://gallatin-so-mt.zuercherportal.com/api/inmates
Status (2026-05-11): maintenance mode (in SKIPPED_SOURCES).
Action: HTTP GET to API endpoint. If HTTP 200 + valid JSON array:
  → Write recovery item to agent-queue/ingest/ for blotter-dev to re-enable.

## Court calendar recovery check

URL: https://coljportal.pubcourts.mt.gov/fullcourtweb/start.do
Status (2026-05-17): WAF blocking — all courts return 'Request Rejected'.
Action: attempt HEAD request. If no longer blocking, note in agent-queue/ingest/.
If still blocked: confirm status, escalate to blotter-dev for user-agent fix.
