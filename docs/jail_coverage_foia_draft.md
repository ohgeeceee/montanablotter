# Jail Roster Coverage — FOIA / Agency Outreach Plan

Generated: 2026-08-31. Montana Blotter currently ingests daily jail rosters for
~22 of 56 Montana counties. The 11 counties below publish NO public inmate roster
on their sheriff/county site (only landing pages, permits, or SVOR offender search).
This document is the outreach kit to request they publish a machine-readable roster.

## Legal basis (Montana)

- Montana Constitution, Article II, Section 9 — right to know / inspect public
  documents.
- Montana Code Ann. § 2-6-101 et seq. — Uniform Administrative Rules of
  Montana (UARM) / public records requests. No statutory fee for inspection;
  agencies may charge actual copying costs. Response expected within a
  "reasonable time" (agencies commonly acknowledge in 10 business days).
- Sheriff's office jail roster is a public record; booking data (name, charges,
  booking date) is routinely published by peer counties and is not exempt.

## Request template (reuse per county)

```
To: <Sheriff / County Clerk>  <COUNTY> County, MT
Subject: Public Records Request — Current Jail Booking Roster

Under Article II, Section 9 of the Montana Constitution and Mont. Code Ann.
§ 2-6-101 et seq., I request a copy of the current in-custody jail booking
roster for <COUNTY> County Sheriff's Office, including:

  - Inmate name
  - Booking date/time
  - Charge(s) / statute
  - Bond (if listed)

Preferred format: structured (CSV / JSON / daily PDF). If a daily or weekly
export is already produced for any internal or public purpose, please provide
the public-facing copy or a link. If published on a web page, the public URL
is sufficient.

I operate Montana Blotter (montanablotter.com), a free public-records
aggregator for all 56 Montana counties. This request is for non-commercial
public transparency; no fee is expected for inspection.

Please acknowledge receipt and provide a timeline. Thank you.
```

## Per-county targets (sheriff site confirmed via earlier curl; addresses NOT verified)

| County | Sheriff / county site | Mailing address | Records contact | Notes |
|--------|----------------------|-----------------|-----------------|-------|
| Blaine | https://blainecounty-mt.gov/sheriff-coroner/ | VERIFY | VERIFY | Landing page only; no roster link |
| Daniels | https://www.danielscountymt.gov/sheriff | VERIFY | VERIFY | Landing page only |
| Golden Valley | https://www.goldenvalleycountysheriffsoffice.org/ | VERIFY | VERIFY | Landing page only |
| McCone | https://mcconecountymt.gov/departments/sheriff-coroner | VERIFY | VERIFY | Landing page only |
| Musselshell | https://musselshellcounty.gov/sheriffs-office/ | VERIFY | VERIFY | Landing page only |
| Petroleum | https://petroleumcountymt.gov/departments/sheriffs-department/ | VERIFY | VERIFY | Landing page only |
| Sheridan | https://www.sheridancountymt.gov/sheriff | VERIFY | VERIFY | Landing page only |
| Sweet Grass | https://sgcountymt.gov/government-departments/county-govt/sheriff/ | VERIFY | VERIFY | Landing page only (alt .org Cloudflare) |
| Teton | https://www.tetoncountysheriffmt.org/ | VERIFY | VERIFY | Landing page only |
| Toole | https://toolecountymt.gov/sheriffs-office/ | VERIFY | VERIFY | Landing page only |
| Treasure | https://www.treasurecountymt.gov/tcsheriff | VERIFY | VERIFY | Landing page only |

## Near-miss sources (scrape instead of FOIA if still live)

None of the 11 above expose a roster, PDF, or Facebook booking log at scan time.
If any later adds one, route it through `services/ingestion/fetchers/generic_mt_inmate.py`
(Playwright variant if JS-rendered).

## Next steps

1. Pull verified mailing addresses + public-records officers from each county's
   site or Montana State Directory before sending.
2. Send the template; log responses in agent-queue/civic/ or a tracking sheet.
3. For denials, cite MCA § 2-6-102 and offer the narrowest workable format.
4. For counties that agree, add a `jail_booking_sources` row + TRACKED_SOURCES
   entry and wire a fetcher (see jail_bookings.py dispatcher).
