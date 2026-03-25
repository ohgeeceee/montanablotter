# Blueprint Refactor Design
**Date:** 2026-03-25
**Status:** Approved
**Goal:** Incrementally split `app.py` (16,530 lines, ~120 routes) into Flask Blueprints without downtime or risky big-bang changes.

---

## Context

`app.py` has grown to 16,530 lines and is the single biggest maintenance and reliability risk in the codebase. A syntax error, unhandled exception, or merge conflict in any part of the file affects every route. The existing `blueprints/detention.py` proves the pattern works — this refactor extends it across the entire app.

**Drivers:**
- Maintenance pain: file is too large to navigate and edit safely
- Deployment reliability: isolate failures so a bug in one domain doesn't affect others
- New feature preparation: clean boundaries before future development

**Constraints:**
- Site is live and actively ingesting from 13+ sources
- Incremental only — one Blueprint per PR, proven in production before the next
- Nothing in a Blueprint imports from `app.py` (no circular dependencies)

---

## Target Structure

```
blueprints/
  detention.py          ← already done, leave untouched
  admin/
    __init__.py         ← admin_bp object, shared decorators, side-effect imports
    blog.py             ← /admin/blog/* (5 routes)
    ingestion.py        ← /admin/ingestion, /admin/upload, /admin/blotters,
                           /admin/post/*, /admin/operations/redaction (8 routes)
    audience.py         ← /admin/audience/*, /admin/emails (11 routes)
    bail_ads.py         ← /admin/bail-ads/* (8 routes)
    donations.py        ← /admin/donations/* (4 routes)
    security.py         ← /admin/login, /admin/logout, /admin/security/* (6 routes)
    operations.py       ← /admin/, /admin/operations/*, /admin/facebook,
                           /admin/settings (10 routes)
  api.py                ← /api/* endpoints (8 routes)
  auth.py               ← /login, /register, /dashboard, /account, /logout
  payments.py           ← /donate, /webhooks/stripe, /advertise/bail-bonds/*
  public.py             ← /, /arrests, /county/*, /city/*, /blog/*, /laws/*, etc.

utils/
  app_settings.py       ← _app_setting_raw/bool/int/text(), _save_app_setting()
  auth_constants.py     ← ADMIN_ACCESS_ROLES, OPERATIONS_ROLES, and all role tuples
```

---

## Extraction Order

Each phase is one PR. Ship and verify in production before starting the next.

| Phase | Target | Approx lines removed from app.py | Routes |
|-------|--------|----------------------------------|--------|
| 0 | Pre-flight: move `get_db` to `db.py`, role constants to `utils/auth_constants.py` | ~25 | 0 |
| 1 | `blueprints/admin/` | ~4,000 | ~50 |
| 2 | `blueprints/api.py` | ~250 | ~8 |
| 3 | `blueprints/auth.py` | ~300 | ~6 |
| 4 | `blueprints/payments.py` | ~1,500 | ~15 |
| 5 | `blueprints/public.py` | ~7,000 | ~40+ |

---

## Dependency Strategy

**Rule:** Nothing in a Blueprint imports from `app.py`. Shared utilities go in `utils/` first.

### Pre-flight moves (Phase 0 — before any blueprint work)

**Move `get_db` into `db.py`:**
`get_db()` is currently defined in `app.py` at line 1062 as a thin wrapper around `connect_db`. Move it to `db.py` so all blueprints can `from db import get_db`. Update the existing call sites in `app.py` (which already uses it internally) — no behavior change.

**Move role constants to `utils/auth_constants.py`:**
The following constants are defined at lines 803–808 of `app.py` and referenced by both admin routes and the `User` class:
```python
ADMIN_ACCESS_ROLES = ('super_admin', 'ops', 'editor', 'revenue', 'read_only')
ADMIN_MANAGEMENT_ROLES = ('super_admin',)
OPERATIONS_ROLES = ('super_admin', 'ops')
CONTENT_REVIEW_ROLES = ('super_admin', 'ops', 'editor')
AUDIENCE_MANAGEMENT_ROLES = ('super_admin', 'ops', 'revenue')
```
Move these to `utils/auth_constants.py`. Update `app.py` to `from utils.auth_constants import *`. Blueprints import from `utils.auth_constants` directly — no circular dependency.

### Already have a home (no changes needed)
- `connect_db` → `db.py`
- `get_db` → `db.py` after Phase 0
- `login_required` → Flask-Login, imported directly
- Role constants → `utils/auth_constants.py` after Phase 0

### Move into `blueprints/admin/__init__.py`
- `require_role()` decorator — see note on `login_manager` below
- `enforce_admin_csrf()`
- `_log_admin_action()`
- All admin-only helpers: `_build_source_coverage_dashboard()`, `_build_ingestion_health_dashboard()`, `_analytics_hub_context()`, `_subscriber_admin_context()`, `_build_email_ops_preview()`

**`require_role` and `login_manager`:**
`require_role` currently calls `login_manager.unauthorized()` when the user is not authenticated (line 5905). `login_manager` is bound to `app` in `app.py` and cannot be imported by a blueprint without creating a circular dependency. Fix: replace `login_manager.unauthorized()` with `redirect(url_for('admin.admin_login'))`. This is semantically equivalent — unauthenticated users are sent to the admin login page. This change is made when moving `require_role` to `blueprints/admin/__init__.py`.

### Extract to `utils/app_settings.py` (before Phase 1)
- `_app_setting_raw()`, `_app_setting_bool()`, `_app_setting_int()`, `_app_setting_text()`
- `_save_app_setting()`
- Used by both admin settings routes and public banner rendering; cannot live in either blueprint.

### `before_request` hooks during transition
`enforce_admin_csrf` and `enforce_admin_access` are currently `@app.before_request` hooks that check `if not request.path.startswith('/admin')` to self-scope. **During Phases 1–5, keep them as `@app.before_request` hooks in `app.py`.** Do NOT move them to `admin_bp.before_request` until all `/admin/*` routes have been extracted (end of Phase 1). If moved to `admin_bp.before_request` while some routes still live in `app.py`, those remaining routes lose CSRF and access enforcement silently. After Phase 1 is complete and all admin routes are on `admin_bp`, convert both hooks to `@admin_bp.before_request` and remove the `startswith('/admin')` guard.

### `login_manager.login_view` update (required during Phase 1)
`login_manager.login_view` is currently set to `'admin_login'` (line 801). When `admin_login` moves to `blueprints/admin/security.py` and is registered on a Blueprint named `'admin'`, Flask changes its endpoint name to `'admin.admin_login'`. Flask-Login uses this string for redirects. **As part of Phase 1, update line 801 to:**
```python
login_manager.login_view = 'admin.admin_login'
```
Failure to do this causes a redirect loop or 404 for all unauthenticated admin access after Phase 1.

### Stay in `app.py` temporarily
- `_bail_ad_*` helpers — used by both admin and public advertiser routes; extracted together in Phase 4
- `inject_public_nav()`, `track_page_view()` — Flask hooks, stay permanently
- `PublicUser`, `User`, `login_manager` — stay until Phase 3 (auth blueprint)

---

## Admin Blueprint Detail (Phase 1)

### Route registration pattern: side-effect imports onto a single Blueprint

All admin sub-files (`blog.py`, `ingestion.py`, etc.) import `admin_bp` from the package and decorate routes directly onto it. There is one Blueprint object. Sub-files have no Blueprint of their own — they are route modules that extend `admin_bp` via import side effects.

**`blueprints/admin/__init__.py`:**
```python
from flask import Blueprint, abort, redirect, url_for
from flask_login import current_user, login_required
from functools import wraps
from utils.auth_constants import ADMIN_ACCESS_ROLES

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def require_role(*allowed_roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('admin.admin_login'))
            if not getattr(current_user, 'can_access_admin', False):
                abort(403)
            if allowed_roles and getattr(current_user, 'role', '') not in allowed_roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def enforce_admin_csrf(): ...      # moved from app.py, unchanged
def _log_admin_action(...): ...    # moved from app.py, unchanged


def register_admin_blueprint(app):
    # Side-effect imports: each module decorates routes onto admin_bp at import time.
    from blueprints.admin import blog        # noqa: F401
    from blueprints.admin import ingestion   # noqa: F401
    from blueprints.admin import audience    # noqa: F401
    from blueprints.admin import bail_ads    # noqa: F401
    from blueprints.admin import donations   # noqa: F401
    from blueprints.admin import security    # noqa: F401
    from blueprints.admin import operations  # noqa: F401
    app.register_blueprint(admin_bp)
```

**Each sub-file (e.g. `blueprints/admin/blog.py`):**
```python
from flask import render_template, request, redirect, url_for
from flask_login import login_required
from db import get_db
from utils.app_settings import _app_setting_bool
from utils.auth_constants import ADMIN_ACCESS_ROLES, CONTENT_REVIEW_ROLES
from blueprints.admin import admin_bp, require_role, enforce_admin_csrf, _log_admin_action


@admin_bp.route('/blog')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_blog():
    ...
```

**Registration in `app.py` (replaces ~4,000 lines):**
```python
from blueprints.admin import register_admin_blueprint
register_admin_blueprint(app)
```

---

## Pattern for Each Phase

Every extraction follows the same steps:

1. Create the Blueprint file(s)
2. Move route functions and their private helpers (functions only used by those routes)
3. Add imports — `from db import get_db`, Flask-Login, `utils/` modules as needed
4. Replace the removed routes in `app.py` with a single registration call
5. Verify: `python -c "from app import app; print('OK')"` — must exit cleanly, no ImportError
6. Run smoke test checklist (see below)
7. Deploy: `systemctl restart montanablotter`
8. Monitor `gunicorn.log` and `cron.log` for 24 hours
9. Merge PR, then start next phase

---

## Smoke Test Checklist (run after each phase deploy)

Minimum verification per phase. Check each in browser and confirm HTTP 200 (or expected redirect):

**Phase 0 (pre-flight):**
- [ ] `python -c "from db import get_db; print(get_db())"` — returns connection
- [ ] `python -c "from utils.auth_constants import ADMIN_ACCESS_ROLES; print(ADMIN_ACCESS_ROLES)"`
- [ ] App starts: `python -c "from app import app; print('OK')"`

**Phase 1 (admin blueprint):**
- [ ] `/admin/login` — renders login form
- [ ] Admin login with valid credentials — redirects to `/admin`
- [ ] `/admin` — dashboard loads with no errors
- [ ] `/admin/blotters` — blotter list renders
- [ ] `/admin/ingestion` — pipeline status renders
- [ ] `/admin/audience/subscribers` — subscriber list renders
- [ ] `/admin/bail-ads` — ad dashboard renders
- [ ] `/admin/donations` — donation list renders
- [ ] `/admin/settings` — settings form renders
- [ ] Unauthenticated request to `/admin` — redirects to `/admin/login` (not 500)
- [ ] Wrong-role user — gets 403, not 500
- [ ] Check `gunicorn.log` for any tracebacks
- [ ] Check `tail -20 /root/montanablotter/mail.log` — email worker still ingesting

**Phases 2–5:** verify 3–5 representative routes from the extracted domain plus `python -c "from app import app"`.

---

## Rollback Procedure

If a phase deploy causes errors:

```bash
git revert HEAD --no-edit
systemctl restart montanablotter
```

Then verify `python -c "from app import app; print('OK')"` and re-run smoke tests.

**Note on duplicate endpoints:** Flask raises `AssertionError: View function mapping is overwriting an existing endpoint function` at startup if a route endpoint name collides. This is caught immediately on `systemctl restart` — the service will fail to start and the old workers continue serving traffic. A duplicate endpoint is therefore a safe failure mode, not a silent one.

---

## What Does NOT Change

- `blueprints/detention.py` — left exactly as-is
- Template files — no changes to any `.html` file
- Database schema — no changes
- URL paths — all routes keep identical paths and HTTP methods
- Cron jobs and worker scripts — unchanged
- `config.py`, `init_db.py` — unchanged
- All external integrations (Stripe, Claude API, Facebook, SMTP) — unchanged

---

## Success Criteria

- All 120+ routes return the same responses before and after each phase
- `app.py` reduced by ~13,000 lines across all 5 phases
- `python -c "from app import app"` exits cleanly after every phase
- Zero downtime during any extraction
- Each phase independently revertable via `git revert HEAD` + service restart
- Smoke test checklist passes after each phase
