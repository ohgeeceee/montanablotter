# Session: Paywall audit vs HANDOFF-paywall.md

- **Date:** 2026-11-19
- **Agent:** kimi (chat session, GitHub read-only)
- **Scope:** `services/monetization/paywall.py`, `blueprints/detention.py`, `blueprints/payments.py`, `blueprints/bail_bond_ads.py`, `blueprints/api.py`, `app.py`, `templates/pricing.html`, `templates/crime_map.html`

## Goal
Audit the paywall implementation against the `HANDOFF-paywall.md` acceptance criteria.

## Done
- Full audit written: `docs/audits/2026-11-19-paywall-audit.md`.
- Verdict: paywall shipped and largely works. 9 of 11 criteria pass at code level.
- July audit H1 (`/api/v1/warrants` open) confirmed FIXED. H2 (`/record/<id>/source` PDF bypass) confirmed STILL OPEN.

## Not done / blockers
- 4 owner decisions logged in the audit: tier name/price truth source, map popup gating,
  warrant-access scope conflict (docs say separate add-on, code grants it to plus/pro),
  and the `/record/<id>/source` bypass.
- No runtime verification (no server access this session) — recommend one live
  signup → checkout → webhook → cancel round-trip.

## State changes
- None (docs-only commit).

## Handoff notes
- If owner confirms the bypass fix: pattern is one decorator call — copy
  `preview_allowed(resource_type='incident', resource_id=record_id)` usage from
  `public_record_detail` (app.py ~10423) into `public_record_source` (app.py:10467).
- Gotchas updated with the duplicate `/pricing` route and the three-way price drift.
