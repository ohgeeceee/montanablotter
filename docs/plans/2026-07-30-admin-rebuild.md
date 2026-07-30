# Admin Panel Rebuild — Plan (v1, draft for approval)

**Date:** 2026-07-30
**Owner:** Jon + Hermes
**Status:** AWAITING APPROVAL — do not implement until sign-off
**Scope:** `/admin/*` surface only. Public site, public templates, public CSS, public routes, and auth flow are out of scope and must not be modified.

---

## 1. What "simplify" actually means here

The admin panel is **not** visually broken. It already has a coherent dark "command-console" design system: `base.html` injects `--adm-*` tokens, `anav-*` nav classes, breadcrumb, brand mark, and a sticky top nav. **Three** templates (`admin_dashboard.html`, `admin_hub.html`, `admin_agency_contacts.html`) use that system correctly.

The pain is structural, not visual:

1. **The design system lives inline in `base.html`** (lines 13–79). Every consumer inherits a 67-line `<style>` block plus a 45-line `<nav>` block plus a 17-line breadcrumb plus a 17-line CSRF-injection script. Total chrome = ~150 lines per page render, duplicated mentally across templates that may never use it.
2. **52 admin templates extend `base.html` directly**, not an `admin_base.html`. That means the design system is "shared" only by template-extends — not by component reuse. Any change to the nav or tokens requires editing `base.html` (which is also used by public-facing templates if any route renders it).
3. **The Command Center is its own full-DOCTYPE document** (1952 lines, 85 KB). It has a custom topbar + sidebar + amber-on-dark design that ignores the `--adm-*` tokens from `base.html`. So we have **two parallel admin design systems** — the "command-console" one in `base.html` and the "operations console" one inlined in `admin_command_center.html`.
4. **Three "home" pages**: `/admin` (redirects to command center), `/admin/dashboard` (the new dark-dashboard, modern), `/admin/hub` (VPS hub launcher, modern), `/admin/command-center` (the 1952-line monster). All linked from the nav.
5. **Duplicate ad products**: `/admin/attorney-ads` (free tier on existing `/attorneys`) and `/admin/lawyer-ads` (paid tier on new `/lawyers`). Both have full admin UIs, both have public intake. Intentional per `docs/plans/2026-07-28-montana-lawyer-advertising-plan.md` — **do not merge** without rewriting that plan.
6. **No shared form/table/modal components.** Every page hand-rolls buttons, inputs, tables, status pills, and flash messages. ~38 forms across templates, ~24 tables, 0 modals (everyone uses inline forms or a separate edit page).
7. **No reusable stat-tile, card, list-row, or filter-bar component.** Same `.adm-stat` markup is hand-pasted into `admin_dashboard.html`, `admin_hub.html`, `admin_mission_control.html`, `admin_agency_contacts.html`.
8. **CSRF token is auto-injected by a global script in `base.html`** — fine, but it means any admin template that doesn't extend `base.html` is CSRF-broken. Check `admin_command_center.html` — it has its own `<body>`, no CSRF meta, no auto-injection. Any POST form in there is broken. (Verified via grep: 0 `csrf_token` references in command_center.)
9. **Sticky breadcrumbs at line 150–167** are computed by string-splitting `request.path`. Works for most pages, breaks on URL params, breaks on non-`/admin/...` paths inside the admin (e.g. `/hermes/` link in nav uses a different breadcrumb path).

---

## 2. Goals (in priority order)

| # | Goal | Why |
|---|------|-----|
| G1 | **One admin design system, defined once.** Move `--adm-*` tokens + `.adm-*` / `.anav-*` classes to `static/admin.css`. No inline `<style>` blocks in templates longer than 30 lines. | Eliminates duplication. Future visual changes happen in one place. |
| G2 | **One admin layout (`templates/admin_base.html`)** with sidebar + topbar + breadcrumb + flash region. All admin templates extend it. | Every admin page gets the same nav, same flash handling, same breadcrumb, same CSRF. |
| G3 | **Component primitives** for the things that repeat: button, status pill, stat tile, card, list row, form field, table. Macros, not Jinja `include`s. | 30+ templates stop hand-pasting the same markup. |
| G4 | **Consolidate the three "home" pages** into one role-aware landing. `/admin` redirects based on user role: super_admin → command-center; ops → dashboard; everyone else → dashboard. Remove `/admin/hub` (it duplicates dashboard). | One entry point, less nav noise. |
| G5 | **Make `admin_command_center.html` use the same design system** as everything else — or split it out into a self-contained `/admin/mission-control` page that no longer lives inside the same template. | Currently 85 KB of inline CSS in one template. Either promote it to `admin_mission_control.html` as a standalone page, or rewrite it on the shared system. |
| G6 | **No feature loss.** Every existing route still works. Every existing test still passes. No schema changes. | This is a UX/rebuild, not a refactor. |

---

## 3. Non-goals

- No consolidation of `attorney_ads` vs `lawyer_ads` — that's a product decision covered by the existing lawyer-ads plan.
- No removal of the 3D office iframe from the dashboard. It's a legitimate operational surface (`/admin/office/3d`).
- No public-site changes. Public templates, `public-redesign.css`, `mb-newspaper.css`, and `?newspaper=1` are off-limits.
- No auth changes. `MB_REQUIRE_SIGNIN`, role checks, and CSRF middleware stay as-is.
- No removal of the existing `test_admin_dashboard.py` / `test_admin_command_center.py` / `test_admin_ai_console.py` regression suite. They must pass unmodified.

---

## 4. Proposed architecture

```
templates/
├── admin_base.html              ← NEW: single admin layout
├── admin_base_v2.html           ← stays if anyone imports it; rename alias to admin_base
├── admin/                       ← NEW: directory for admin-only partials/macros
│   ├── _macros.html             ← NEW: button, stat, card, pill, table-row, form-field macros
│   ├── _flash.html              ← NEW: extracted flash-message block
│   └── _breadcrumb.html         ← NEW: extracted from base.html lines 150–167
├── admin.html                   ← STAYS, but rewritten to extend admin_base.html
├── admin_command_center.html    ← REDUCED: ~85 KB → ~10 KB, uses admin_base.html + macros
├── admin_dashboard.html         ← REFACTORED: uses admin_base.html + macros
├── admin_hub.html               ← MERGED into admin_dashboard.html or admin.html; old file redirects
├── admin_*.html                 ← All 55 templates: change `{% extends "base.html" %}` → `{% extends "admin_base.html" %}`
│
static/
├── admin.css                    ← NEW: extracted design tokens + component classes
│                                    (token block + nav + breadcrumb + cards + forms + tables + status pills)
├── public-redesign.css          ← untouched
└── mb-newspaper.css             ← untouched

templates/base.html              ← CLEANED: remove inline admin CSS (lines 13–79), admin nav (84–148),
│                                    admin breadcrumb (150–167). Keep public-flash + CSRF-inject + body shell.
│                                    Public routes that previously saw admin nav no longer will.
```

### 4.1 Layout split (`base.html` vs `admin_base.html`)

| Element | `base.html` (public-leaning) | `admin_base.html` |
|---------|-------------------------------|---------------------|
| `<head>` chrome | Playfair + IBM Plex Mono, public meta tags | same fonts + admin.css + admin-favicon if any |
| Body class | `font-sans` | `font-sans admin-shell` |
| Top nav | none (public templates add their own) | sticky `.anav` bar with brand + grouped nav + logout |
| Sidebar | none | collapsible, only on `/admin/*` (optional v2 — see §4.4) |
| Breadcrumb | none | computed from `request.path`, with override via `{% set breadcrumbs = [...] %}` |
| Flash region | public styling | `.adm-flash` block, scoped to admin |
| CSRF auto-inject | yes (keep) | yes (keep — already in base.html) |
| Footer / scripts | OGC network bar | OGC network bar + admin.js (new) |

### 4.2 Component macros (`templates/admin/_macros.html`)

```jinja
{% macro stat(label, value, accent='default', warn=0) %} ... {% endmacro %}
{% macro card(title, body, footer=None, accent='default') %} ... {% endmacro %}
{% macro status_pill(status, kind='auto') %} ... {% endmacro %}
{% macro btn(label, href=None, type='button', variant='primary', icon=None) %} ... {% endmacro %}
{% macro form_field(name, label, type='text', value='', help=None, required=False) %} ... {% endmacro %}
{% macro list_row(items, field, href=None) %} ... {% endmacro %}
```

Usage example:

```jinja
{% from 'admin/_macros.html' import stat, card, status_pill, btn %}

{% call card('Recent Records') %}
  {% for r in records %}
    {{ list_row(r, ['county', 'incident_date', 'incident_type']) }}
  {% endfor %}
{% endcall %}
```

### 4.3 New `admin.css` outline (~400 lines, single source of truth)

```
:root { --adm-bg, --adm-surface, --adm-text, --adm-amber, ... }   ← 30 lines
.anav, .anav-link, .anav-group, .anav-logout                       ← 60 lines
.anav-breadcrumb                                                    ← 20 lines
.adm-card, .adm-stat, .adm-stat__label, .adm-stat__value           ← 30 lines
.adm-cmd, .adm-cmd--amber/blue/green/purple/sky/dim                ← 25 lines
.adm-btn, .adm-btn--primary/ghost/danger                            ← 30 lines
.adm-form, .adm-input, .adm-select, .adm-textarea, .adm-help        ← 40 lines
.adm-table, .adm-thead, .adm-row, .adm-row--alt                     ← 35 lines
.adm-pill, .adm-pill--green/red/amber/blue/muted                    ← 30 lines
.adm-flash, .adm-flash--error/warn/success                          ← 25 lines
.adm-modal (placeholder for future), .adm-tabs                       ← 40 lines
.layout-* utilities (sidebar collapsed, topbar compact)              ← 30 lines
```

Total ~400 lines. Single source of truth. Edit once, propagates.

### 4.4 Sidebar: yes or no?

The Command Center template has a custom sidebar (`<nav class="sidebar">` at line 1293). The rest of admin uses a topbar-only nav. Two paths:

- **Path A (recommended):** Keep topbar-only nav. Rewrite Command Center to fit. Lose some screen real estate on that one page but gain consistency.
- **Path B:** Add a collapsible left sidebar to `admin_base.html`, gated by a `{% block sidebar %}`. Command Center gets a sidebar; other pages inherit the empty sidebar (still collapsible).

**Recommendation: Path A.** Sidebar-everywhere adds 240px of wasted space on data-heavy pages like `/admin/bail-ads` or `/admin/audience/email-ops`. Topbar dropdowns (already in `.anav-group` with hover-expand) are enough.

### 4.5 `/admin/hub` and the three-home-page problem

Current state:
- `/admin` → redirects to command center
- `/admin/dashboard` → modern dark dashboard
- `/admin/hub` → VPS hub launcher
- `/admin/command-center` → 1952-line Command Center with own design

Proposed state:
- `/admin` → role-aware redirect:
  - super_admin → `/admin/command-center`
  - ops / editor → `/admin/dashboard`
- `/admin/dashboard` → keep, becomes the default landing for non-super-admins
- `/admin/hub` → DELETE the route, redirect to `/admin/dashboard`. Hub content merges into dashboard's existing "Surface launcher" section.
- `/admin/command-center` → rewrite on shared design system; loses 85 KB of inline CSS; sidebar becomes topbar.

---

## 5. Phased rollout (5 phases, each shippable & revertable)

### Phase 1 — Extract `admin.css` + `admin_base.html` (no behavior change)
**Deliverable:** `static/admin.css` (~400 lines) + `templates/admin_base.html` + `templates/admin/_macros.html` (skeleton). Three templates (`admin_dashboard.html`, `admin_hub.html`, `admin_agency_contacts.html`) switch to extending `admin_base.html`. They render pixel-identical. Old `base.html` admin styling kept as `<style>` block but `<link>` of `admin.css` is no-op until later phases.
**Tests:** existing dashboard/command-center/agency-contacts tests pass.
**Risk:** low. Pure refactor.
**Est. time:** 2–3 hours.

### Phase 2 — Migrate remaining 49 admin templates
**Deliverable:** change `{% extends "base.html" %}` → `{% extends "admin_base.html" %}` in every `templates/admin_*.html` that currently extends `base.html`. Confirm no template accidentally pulls in public chrome.
**Tests:** full pytest suite + visual smoke on `/admin/`, `/admin/dashboard`, `/admin/bail-ads`, `/admin/audience/subscribers`, `/admin/blog`.
**Risk:** medium. Some templates may depend on `base.html`-provided blocks (e.g. `extra_head`). Mitigate by re-exposing `{% block extra_head %}` and `{% block extra_scripts %}` in `admin_base.html`.
**Est. time:** 1 day.

### Phase 3 — Rewrite `admin_command_center.html` on shared system
**Deliverable:** `admin_command_center.html` reduced from 1952 lines / 85 KB to ~400 lines / 12 KB. Topbar/sidebar from inline styles replaced by `admin_base.html` + macros. Live feed, pipeline panel, health tile, agent registry all kept.
**Tests:** `test_admin_command_center.py` passes; visual smoke confirms live feed polling still hits `/admin/api/command-center/feed`.
**Risk:** medium-high. This template has the most custom JS. Need to preserve `cs-spark-fly` animation and the `toggleSidebar()` function (or replace with topbar-only nav).
**Est. time:** 2 days.

### Phase 4 — Consolidate `/admin/hub` into `/admin/dashboard`, redirect `/admin`
**Deliverable:**
- `/admin/hub` route returns 301 → `/admin/dashboard`.
- `templates/admin_hub.html` deleted (after visual diff confirms dashboard surfaces the same content).
- `/admin` route handler gains role-aware redirect.
**Tests:** `test_admin_dashboard.py` updated to assert hub content now lives at `/admin/dashboard`. Add test for role-aware `/admin` redirect.
**Risk:** low. Redirects are reversible.
**Est. time:** 0.5 day.

### Phase 5 — Component primitives fill-out + dead-route audit
**Deliverable:**
- `_macros.html` fleshed out: stat, card, status_pill, btn, form_field, list_row, table.
- Apply macros to the top-10 most-used templates (bail_ads, attorney_ads, lawyer_ads, audience/subscribers, blog, ingestion, sources, blotters, donations, users).
- Audit: list every route in `blueprints/admin/*.py` + `blueprints/lawyer_ads.py` + `blueprints/attorney_ads.py`. Flag any with zero inbound links from nav + zero hits in `tests/` over the last 6 months. Present list to user for kill-or-keep decision.
**Tests:** new `tests/test_admin_macros.py` — render each macro standalone, assert HTML output structure.
**Risk:** low. Macro changes are mechanical.
**Est. time:** 2–3 days.

---

## 6. Migration risks & mitigations

| Risk | Mitigation |
|------|-----------|
| `admin_command_center.html` CSRF breakage (it has its own body, no CSRF meta) | Phase 3 forces it onto `admin_base.html` which restores the auto-inject. Phase 1 adds a smoke test for command_center POST endpoints. |
| Template extends `base.html` and depends on a block that `admin_base.html` doesn't expose | Phase 2 inventories every block override in admin templates before migration. Add missing blocks (`extra_head`, `extra_scripts`, `body_class`, `content_wrapper_class`) to `admin_base.html`. |
| CSS extraction shifts 1–2px on dashboards | Phase 1 is pixel-identical by construction (same CSS, just in a `<link>` instead of inline `<style>`). Visual diff via Playwright before/after on `/admin/dashboard`. |
| Hidden users of admin_base_v2.html (memory references it but file doesn't exist on disk) | Grep for the string before Phase 2. Memory recall is stale; trust `git grep`. |
| `anav-group` mobile collapse behavior | Preserve verbatim in `admin.css`. Tested at viewport widths 375 / 768 / 1280 / 1920. |
| Flash messages lose categories when migrating away from `base.html` | `_flash.html` partial explicitly checks for `(category, message)` tuples; same shape as `base.html` lines 6–19. |
| OGC network bar (line 192 of base.html) is a public-site feature | Keep in `admin_base.html` too. It's a network-wide footer; belongs everywhere. |

---

## 7. What I'm asking for

Approve Phase 1 only, then proceed phase-by-phase with checkpoints. Before I touch anything:

1. Confirm Path A (no sidebar) over Path B.
2. Confirm role-aware `/admin` redirect rules in §4.5 (super_admin → command-center, else → dashboard).
3. Confirm `/admin/hub` is safe to delete (its content merges into dashboard).
4. Confirm the 5-phase split is the right granularity (do you want bigger chunks to ship faster, or smaller chunks to keep risk bounded?).
5. Flag any admin template I should NOT touch (some pages may be load-bearing for ops in ways I can't see).

Once approved, I start Phase 1: extract `admin.css`, create `admin_base.html`, migrate the three templates already on the design system, ship as one PR.

---

## 8. Out of scope (for this plan)

- Consolidating `attorney_ads` vs `lawyer_ads`
- Removing the 3D office iframe
- Removing the Command Center (it's kept, just rewritten on shared system)
- Any public-site changes
- Any auth / RBAC / MFA changes
- Any DB / schema / migration changes

---

**Approval block — fill in:**

- [ ] Approve Phase 1 (extract admin.css + admin_base.html, no behavior change)
- [ ] Path A (no sidebar) or Path B (collapsible sidebar)
- [ ] Role-aware /admin redirect confirmed
- [ ] /admin/hub deletion confirmed
- [ ] 5-phase split confirmed (or revised to: ___)
- [ ] Date / commit hash of go-ahead: ____________

**Signed:** ____________
