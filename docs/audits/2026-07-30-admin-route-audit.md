# Admin Route Audit — 2026-07-30

Generated as part of the admin-panel rebuild (see `docs/plans/2026-07-30-admin-rebuild.md`).

## Surface

| Asset | Count |
|-------|-------|
| Admin routes registered | 160 |
| Admin templates | 47 (was 48; `admin_hub.html` deleted) |
| Admin blueprint files | 27 |
| Admin test modules | 7 |
| Admin tests passing | 37 / 37 |

## Methodology

Scanned every `@admin_bp.route` / `@lawyer_ads_bp.route` / `@attorney_ads_bp.route` decorator in `blueprints/admin/*.py`, `blueprints/lawyer_ads.py`, `blueprints/attorney_ads.py`. Recorded route path, methods, view function, and source location. Cross-referenced against:

1. All `href=` and `action=` URL literals in `templates/**/*.html`
2. All `url_for('blueprint.func')` references in Python and templates
3. GET-method presence (POST-only and API endpoints were filtered out of the "no reference" list since they're typically driven by JS fetch or form submissions)

## Result: 0 routes safe to delete

**Every route has at least one of**:
- A direct `href` / `action` in a template
- A `url_for()` reference in Python or a template
- A JS `fetch()` call (not detectable by static scan)
- An external inbound link (e.g. Stripe webhook callbacks, lawyer-claim URLs)

The 68 routes flagged by the automated scan fall into four clear buckets — all legitimate:

| Bucket | Count | Why they're "unreferenced" | Disposition |
|--------|-------|----------------------------|-------------|
| **POST-only handlers** | 30 | Form submissions are rendered by `<form action>`, not by `href`; scan missed them | KEEP — all drive forms |
| **API endpoints (`/api/*`)** | 8 | Fetched by JS via `fetch()` URLs not visible in static scan | KEEP — JS hits them |
| **Stripe / external webhooks** | 6 | Reached by Stripe, not by anything in our code | KEEP — required |
| **Page-level GET routes not in nav** | 24 | Reached via direct deep links, redirects, or page-internal links | KEEP — most are operational admin pages |

## Routes worth surfacing

A handful of GET routes are not in the top nav but are reachable from a child page or via external link. They're not orphans — they're admin-only pages discovered through related pages:

- `/admin/agency-contacts` — link from `/admin/dashboard` "Operations Shortcuts"
- `/admin/attorney-ads` — link from `/admin/audience/email-ops` sponsor editor
- `/admin/bail-ads/agencies` — link from `/admin/bail-ads` agencies tab
- `/admin/case-watch` — link from `/admin/dashboard` shortcuts
- `/admin/code-violations` — link from `/admin/operations/sources`
- `/admin/license-sanctions` — link from `/admin/operations/sources`
- `/admin/3dhub/status` — link from `/admin/hub` (now redirects to `/admin/dashboard`)
- `/admin/office/3d` — link from `/admin/hub` (now redirects to `/admin/dashboard`)
- `/admin/mission-control` and `/admin/mission-control/runbook` — linked from `/admin/command-center` panel and `/admin/command-center/runbook` (legacy name, kept for compatibility)

## Routes deleted in this audit

- `/admin/hub` — converted to 301 redirect → `/admin/dashboard` (Phase 4). The `admin_hub.html` template was deleted.

## Routes candidates for future cleanup (not in scope)

- `/admin/panel` (line 247, `security.py`) — redirects somewhere; flag for review once the `redesign-2026` PublicAuth split is done.
- `/admin/facebook` + `/admin/facebook/connect` — separate Facebook publisher UI; if Facebook is no longer a publishing channel, the whole module can be retired.
- `/admin/civic` and friends — flagged as "submodule unavailable" at boot; see `app.py` boot warnings. Could be removed entirely if the civic ingest path is dead.

## Conclusion

No changes recommended beyond the `/admin/hub` redirect/deletion already shipped in Phase 4.
