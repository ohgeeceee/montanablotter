# Jail Booking Source Status

As of 2026-03-18 UTC.

## Verified And Automatable

| County | Source | Status | Notes |
| --- | --- | --- | --- |
| Missoula | `https://webapps.missoulacounty.us/jailroster/Inmates` | Live | Existing HTML adapter works. |
| Yellowstone | `https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp` | Live | Existing prompt + detail adapter works. |
| Flathead | `https://apps.flathead.mt.gov/jailroster/` | Live | Current-inmates HTML adapter added. |
| Jefferson | `https://jefferson-so-mt.zuercherportal.com/#/inmates` | Live | Zuercher JSON API adapter added. |
| Sanders | `https://sanders-mt.publiclogs.com/` | Live | PublicLogs adapter added; current host requires TLS verification disabled because the county certificate is expired. |

## Verified But Not Yet Automatable

| County | Source | Status | Blocker |
| --- | --- | --- | --- |
| Broadwater | `https://www.broadwatercountysheriff.org/roster.php` | Real Montana roster | Parser is drafted, but the host still times out from the ingest machine so live validation is blocked. |
| Pondera | `https://ponderacountyjail.org/inmate-search/` | Reachable | Cloudflare challenge blocks direct ingest. |
| Granite | `https://granitecountyjail.org/` | Reachable | Cloudflare challenge blocks direct ingest. |

## Temporarily Unavailable

| County | Source | Status | Blocker |
| --- | --- | --- | --- |
| Gallatin | `https://gallatin-so-mt.zuercherportal.com/#/inmates` | Official portal | Zuercher server returns maintenance mode. |

## Bad Or Unverified Source

| County | Previous Source | Status | Action Taken |
| --- | --- | --- | --- |
| Hill | `https://vinelink.vineapps.com/state/mt` | Statewide search, not county roster | Disabled as a jail-booking ingest source. |
| Custer | `https://www.custercountysheriff.com/inmate-search` | Wrong jurisdiction | Disabled as online roster source. |
| Lincoln | `http://inmateroster.lincolncountysheriff.us/` | Wrong jurisdiction | Disabled as online roster source. |
| Madison | `https://webportal.mcits.site/NewWorld.InmateInquiry/MadisonCountyJail` | Wrong jurisdiction | Disabled as online roster source. |

## Next Best Targets

1. Broadwater, if DNS resolves consistently from the ingest host.
2. Pondera or Granite, only with a browser-backed fetch path or a non-Cloudflare official endpoint.
3. Gallatin, once the official portal leaves maintenance mode.
