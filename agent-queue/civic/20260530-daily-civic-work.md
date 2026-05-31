---
profile: civic
created: 2026-05-30T06:45:01
tier: green
status: open
priority: med
related_county: ""
related_files: []
---

# Daily Civic Work

No counties silent >7 days. ✓

## Source expansion research

Research the following for jail rosters / blotter PDFs / CrimeMapping feeds:
- Beaverhead County: beaverheadcountymt.gov/departments/sheriff/
- Big Horn County: bighorncountymt.gov/239/Detention
- Chouteau County: research sheriff page
- Glacier County: glaciercounty.org sheriff page
- Hill County / Havre PD: expand beyond image-based emails

For each: document findings in agent-queue/civic/. If source found, draft
source entry for blotter-dev/blotter-scraper.

## Roster maintenance

Review agent-queue/civic/_roster.yaml:
- Flag contacts with no outreach in >60 days for refresh.
- Add newly discovered sheriff/PIO contacts.
- Update last_contact_at for any counties contacted this week.
