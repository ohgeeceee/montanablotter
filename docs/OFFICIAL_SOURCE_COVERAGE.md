# Official Source Coverage

Checked: March 9, 2026

This file tracks Montana city, county, sheriff, and police official public-source coverage
for montanablotter ingestion.

Search focus:
- `police blotter`
- `call log`
- `crime log`
- `weekly crime journal`
- `blotter police`

Scope notes:
- This is limited to official government-hosted or clearly government-linked public sources.
- Third-party news sites, aggregators, and social feeds are excluded.
- "Covered" means an active ingest exists in this repo.
- "Candidate" means a source exists but is not yet suitable or not yet integrated.
- "No source found" means no qualifying public blotter/log page was found during the sweep.

## Covered

| Agency | Source | Notes |
| --- | --- | --- |
| Whitefish Police Department | https://www.cityofwhitefish.gov/688/Police-Blotter | Official posted blotter. Ingested by `whitefish_blotter_fetcher.py`. |
| Bozeman Police Department | https://www.bozeman.net/departments/police/crime-information/police-call-logs/30-day-call-log | Official calls-for-service source. Ingested by `bozeman_police_fetcher.py --dataset calls`. |
| Bozeman Police Department | https://bozeman.maps.arcgis.com/apps/dashboards/38247556995340e6b796a9e53c15ae1f | Official city-linked crime dashboard. Ingested by `bozeman_police_fetcher.py --dataset crime`. |
| Missoula County public report feed | https://webapps.missoulacounty.us/dailypublicreport/ | Official public incident report source. Ingested by `missoula_public_report_fetcher.py`. |

## Candidate / Not Yet Usable

| Agency | Source | Status | Notes |
| --- | --- | --- | --- |
| Big Horn County Sheriff's Office | https://www.bighorncountymt.gov/176/Sheriff | Not ingestable | Official sheriff page links to a CrimeGraphics/CitizenRIMS portal, but public incident/case/log features are disabled for that tenant as of March 9, 2026. |
| Billings Police Department | https://billingsmt.gov/1773/Crime-Statistics | Stale / not added | Official dashboard exists, but the page indicates the offenses dashboard is currently from January 2024 onward and was not treated as a current rolling blotter feed. |
| Great Falls Police Department | https://greatfallsmt.net/police/welcome-gfpd-message-chief | Ambiguous / not added | Official site references crime statistics, but no directly posted blotter/call-log/crime-log page was confirmed in this sweep. |

## No Qualifying Public Log Found

| Agency | Official page checked | Result |
| --- | --- | --- |
| Helena Police Department | https://www.helenamt.gov/Departments/Police-Department/Support-Services-Records | Records are available by request, but no posted public blotter/log page was found. |
| Kalispell Police Department | https://www.kalispell.com/260/Police | No qualifying blotter/log page found. |
| Belgrade Police Department | https://www.belgrademt.gov/158/Police | No qualifying blotter/log page found. |
| Laurel Police Department | https://cityoflaurelmontana.com/police/custom-contact-page/police-contact-information | No qualifying blotter/log page found. |
| Yellowstone County Sheriff's Office | https://www.yellowstonecountymt.gov/Sheriff/ | No qualifying blotter/log page found. |
| Cascade County Sheriff's Office | https://www.cascadecountymt.gov/283/Sheriffs-Office | No qualifying blotter/log page found. |
| Flathead County Sheriff's Office | https://flatheadcounty.gov/department-directory/sheriffs-office | No qualifying blotter/log page found. |
| Gallatin County Sheriff's Office | https://www.gallatinmt.gov/patrol-division/links/crime-reporting | Reporting page only; no posted public log was found. |
| Missoula County Sheriff's Office | https://www.missoulacounty.gov/departments/sheriffs-office/ | No separate posted sheriff blotter/log page was found. |

## Existing Non-Official-Vendor Coverage Already In Repo

These are active ingest sources already covered by the project, but they are not official
government-hosted blotter pages:

| Agency group | Source type | Notes |
| --- | --- | --- |
| Billings PD, Great Falls PD, Flathead County Sheriff, MSU Police, Carbon County Sheriff, Red Lodge PD, Bridger PD, Chouteau County Sheriff | CrimeMapping | Covered by `crimemapping_fetcher.py`. |

## Next Review Targets

- Re-check Big Horn County Sheriff's Office if their CitizenRIMS public features are enabled later.
- Re-check Billings Police Department if the official dashboard becomes a current rolling feed rather than a stale historical snapshot.
- Periodically sweep smaller Montana city police pages for newly published blotter/log dashboards.

## Jail Roster Coverage (added 2026-08-31)

Daily jail-roster ingest covers ~22 of 56 Montana counties. Tracked in
`jail_booking_sources` + `TRACKED_SOURCES` (services/ingestion/jail_bookings.py).
Browser-rendered rosters (dmxAppConnect / ASP.NET GridView) use
`services/ingestion/fetchers/playwright_mt_inmate.py` (system Chromium).

Maintenance watch-items:
- **Chouteau County** — Wix-hosted site; the jail roster PDF URL rotates on every
  publish. The fetcher re-discovers the current link from the landing page each
  run (scheduled daily 06:15 in crontab.txt). If Chouteau moves off Wix or stops
  publishing the PDF, this breaks silently — re-scout.
- **Carter County** — roster renders via dmxAppConnect cards; the per-inmate
  detail pages (`inmate.php?bookingid=`) are broken server-side (PHP warning,
  "inmate not found"), so only list-card name + booked-at are captured (no
  charges). If the county fixes detail pages, extend the fetcher to pull charges.
- **Broadwater County** — roster URL is reachable from a normal browser but
  TCP-blocked from the ingest VPS; parser ready, needs an egress/proxy fix.
- **Powder River / Wibaux (Dawson contract)** — Cloudflare 403 even after
  headless render; deferred (low ROI vs. CAPTCHA-solve cost).
- **11 no-roster counties** (Blaine, Daniels, Golden Valley, McCone, Musselshell,
  Petroleum, Sheridan, Sweet Grass, Teton, Toole, Treasure) — no public roster;
  FOIA/outreach plan in `docs/jail_coverage_foia_draft.md`.
- See `docs/jail_tier2_assessment.md` for the full broken-source triage.
