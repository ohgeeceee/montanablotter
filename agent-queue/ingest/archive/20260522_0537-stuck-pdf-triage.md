---
profile: ingest
created: 2026-05-22T05:37:43
tier: yellow
status: open
priority: high
related_county: ""
related_files: []
---

# Stuck PDF Batch Triage — 62 Files

Batch-diagnose by age. Prioritize major counties (Gallatin, Missoula, Yellowstone).

## Top 10 oldest

- `2-26 media log.pdf` — 2030.1h old
- `Calls_in_Whitehall_area_2026-03-02_08.00.10.pdf` — 1914.2h old
- `0304 log.pdf` — 1888.1h old
- `0309 log.pdf` — 1768.6h old
- `Calls_in_Whitehall_area_2026-03-09_08.00.12.pdf` — 1767.4h old
- `Weekly_Central_and_North_JeffCo_CFS_2026-03-09_08.00.12.pdf` — 1767.4h old
- `G145417169.pdf` — 1753.6h old
- `3112026.pdf` — 1721.4h old
- `3122026.pdf` — 1696.4h old
- `31329016.pdf` — 1673.4h old

## Steps

1. Identify county from filename pattern.
2. Dry-run parse: `python3 processor.py --dry-run --file <path>`
3. Classify: corrupt PDF / format change / parser bug / encoding issue.
4. Success → back up + re-queue (Yellow). Failure → escalate to blotter-dev.
