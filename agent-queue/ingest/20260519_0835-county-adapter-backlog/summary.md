---
profile: ingest
created: 2026-05-19T08:35:00-06:00
tier: green
status: open
priority: med
related_county: "Multiple"
related_files:
  - /root/montanablotter/services/ingestion/jail_bookings.py
---

# Summary

18 Montana counties have no automated jail booking adapter. Their roster entries show "No automated county adapter has been added yet" and `last_checked_at` dates frozen at 2026-03-18. This is a backlog expansion task, not an outage.

# Observation

From source coverage data (2026-05-17), counties with zero automation:

- Beaverhead
- Big Horn
- Carbon
- Cascade (enabled but no adapter)
- Dawson
- Fergus
- Glacier
- Granite
- Lewis and Clark
- Mineral
- Park
- Phillips
- Pondera
- Powell
- Ravalli
- Silver Bow
- Valley

Additional flags:
- Broadwater: "Official roster host is timing out from the ingest machine."
- Custer, Hill, Lincoln, Madison: `is_enabled: 0` (roster unavailable or unverified)

# Proposed action

1. Rank counties by population + public records accessibility.
2. For the top 5, research whether the county publishes a roster online (sheriff's website, jail website, or third-party service).
3. Draft a `jail_bookings.py` adapter skeleton for each reachable source.
4. Queue adapter drafts in `agent-queue/dev/` for review.
5. For counties with no online roster, flag for `blotter-civic` outreach (public records request).

# Reasoning

Automating the top 5 by population would increase jail booking coverage by an estimated 15-20%. Lewis and Clark, Cascade, and Ravalli are the largest counties in this group. Trying to build all 18 at once would create a review bottleneck; batching is safer.

# Rollback

No deployable code is created at this stage (Green tier — research and draft only). If a drafted adapter is rejected, delete the draft file.

# Verification

A successful research pass produces:
- 1 markdown file per county listing: roster URL, update frequency, format (HTML table, PDF, API), and feasibility score (easy/medium/hard/unreachable).
- Count of counties flagged for civic outreach.
