---
profile: growth
created: 2026-05-30T06:46:04
tier: green
status: open
priority: high
related_county: ""
related_files: []
---

# Daily Growth Intelligence

## Traffic snapshot (last 7 days)

*(DB unavailable — run queries manually)*

## Top pages (last 7 days)

- Run: `SELECT path, COUNT(*) FROM page_views WHERE created_at >= datetime('now','-7 days') GROUP BY path ORDER BY 2 DESC LIMIT 10;`

## Top referrers (last 7 days)

- Run: `SELECT referrer, COUNT(*) FROM page_views WHERE created_at >= datetime('now','-7 days') AND referrer != '' GROUP BY referrer ORDER BY 2 DESC LIMIT 10;`

## Today's growth tasks

1. Identify the single top-performing page above and check: Is there a clear CTA, share button, or email signup? If not, draft a Yellow-tier improvement proposal.
2. Look at the top referrer — is it a community or site we should engage with more? If yes, note in weekly link-building research.
3. Flag any path with unusually high traffic today for social media promotion (Red tier).
