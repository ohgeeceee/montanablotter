---
profile: ingest
created: 2026-05-28T07:06:28
tier: yellow
status: open
priority: high
related_county: ""
related_files: []
---

# Stuck PDF Batch Triage — 65 Files

Batch-diagnose by age. Prioritize major counties (Gallatin, Missoula, Yellowstone).

## Top 10 oldest

- `2-26 media log.pdf` — 2175.6h old
- `Calls_in_Whitehall_area_2026-03-02_08.00.10.pdf` — 2059.7h old
- `0304 log.pdf` — 2033.6h old
- `0309 log.pdf` — 1914.1h old
- `Calls_in_Whitehall_area_2026-03-09_08.00.12.pdf` — 1912.9h old
- `Weekly_Central_and_North_JeffCo_CFS_2026-03-09_08.00.12.pdf` — 1912.9h old
- `G145417169.pdf` — 1899.1h old
- `3112026.pdf` — 1866.9h old
- `3122026.pdf` — 1841.9h old
- `31329016.pdf` — 1818.9h old

## Steps

1. Identify county from filename pattern.
2. Dry-run parse: `python3 processor.py --dry-run --file <path>`
3. Classify: corrupt PDF / format change / parser bug / encoding issue.
4. Success → back up + re-queue (Yellow). Failure → escalate to blotter-dev.
