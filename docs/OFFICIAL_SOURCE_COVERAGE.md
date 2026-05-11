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
