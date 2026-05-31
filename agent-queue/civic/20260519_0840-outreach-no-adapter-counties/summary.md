---
profile: civic
created: 2026-05-19T08:40:00-06:00
tier: green
status: open
priority: med
related_county: "Multiple"
related_files: []
---

# Summary

Draft polite outreach emails to sheriff's offices in counties with no automated jail booking adapter, requesting a direct feed or publication of roster data in a machine-readable format.

# Observation

From source coverage data (2026-05-17), the following counties have no automated adapter and may benefit from a direct relationship:

- Beaverhead, Big Horn, Carbon, Cascade, Dawson, Fergus, Glacier, Granite, Lewis and Clark, Mineral, Park, Phillips, Pondera, Powell, Ravalli, Silver Bow, Valley

Additionally, these counties are disabled due to unavailable/unverified rosters:
- Custer, Hill, Lincoln, Madison

# Proposed action

1. For the top 5 counties by population (Lewis and Clark, Cascade, Ravalli, Silver Bow, Hill), draft a personalized outreach email to the sheriff's office records officer or PIO.
2. Use the `county-outreach-email` skill if available; otherwise follow this structure:
   - Intro: who Montana Blotter is and what we do
   - Ask: whether the county publishes jail roster data online or via email
   - Offer: to build and maintain a free, automated parser that respects their publication schedule
   - Close: invitation to reply or schedule a 10-minute call
3. Save drafts as `civic/<county>-outreach-<date>.md` in the agent queue.
4. Update the contact roster with last-contact dates.

# Reasoning

A direct feed or even a consistent email/PDF publication is often easier for a records officer to set up than maintaining a public website. Montana's public records law supports this request. Starting with the largest counties maximizes coverage impact.

# Rollback

No emails are sent in Green tier. If Jon rejects a draft, delete the file.

# Verification

- 5 draft emails exist in `agent-queue/civic/`
- Contact roster updated with draft dates
- No outbound emails sent without Jon approval
