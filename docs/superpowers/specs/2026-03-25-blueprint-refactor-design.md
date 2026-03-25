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
    __init__.py         ← Blueprint object, shared decorators, registration
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
```

---

## Extraction Order

Each phase is one PR. Ship and verify in production before starting the next.

| Phase | Target | Approx lines removed from app.py | Routes |
|-------|--------|----------------------------------|--------|
| 1 | `blueprints/admin/` | ~4,000 | ~50 |
| 2 | `blueprints/api.py` | ~250 | ~8 |
| 3 | `blueprints/auth.py` | ~300 | ~6 |
| 4 | `blueprints/payments.py` | ~1,500 | ~15 |
| 5 | `blueprints/public.py` | ~7,000 | ~40+ |

---

## Dependency Strategy

**Rule:** Nothing in a Blueprint imports from `app.py`. Shared utilities go in `utils/` first.

### Already have a home
- `get_db()` → `db.py` — blueprints `from db import get_db`
- `login_required` → Flask-Login, imported directly
- `connect_db` → `db.py`

### Move into `blueprints/admin/__init__.py`
- `require_role()` decorator
- `enforce_admin_csrf()`
- `_log_admin_action()`
- All admin-only helpers: `_build_source_coverage_dashboard()`, `_build_ingestion_health_dashboard()`, `_analytics_hub_context()`, `_subscriber_admin_context()`, `_build_email_ops_preview()`

### Extract to `utils/app_settings.py` (before Phase 1)
- `_app_setting_raw()`, `_app_setting_bool()`, `_app_setting_int()`, `_app_setting_text()`
- `_save_app_setting()`
- These are used by both admin settings routes and public banner rendering, so they cannot live in either blueprint.

### Stay in `app.py` temporarily
- `_bail_ad_*` helpers — used by both admin and public advertiser routes; extracted together in Phase 4
- `inject_public_nav()`, `track_page_view()` — Flask hooks, stay permanently
- `PublicUser`, `User`, `login_manager` — stay until Phase 3 (auth blueprint)

---

## Admin Blueprint Detail (Phase 1)

### `blueprints/admin/__init__.py`
```python
from flask import Blueprint

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Decorators and shared helpers moved from app.py:
def require_role(*roles): ...
def enforce_admin_csrf(): ...
def _log_admin_action(...): ...

def register_admin_blueprint(app):
    from .blog import blog_bp       # noqa: F401 (registers routes on admin_bp)
    from .ingestion import ingestion_bp
    from .audience import audience_bp
    from .bail_ads import bail_ads_bp
    from .donations import donations_bp
    from .security import security_bp
    from .operations import operations_bp
    app.register_blueprint(admin_bp)
```

### Each sub-file imports only what it needs
```python
from flask_login import login_required
from db import get_db
from utils.app_settings import _app_setting_bool
from blueprints.admin import admin_bp, require_role, enforce_admin_csrf
```

### Registration in `app.py` (replaces ~4,000 lines)
```python
from blueprints.admin import register_admin_blueprint
register_admin_blueprint(app)
```

---

## Pattern for Each Phase

Every extraction follows the same steps:

1. Create the Blueprint file
2. Move route functions and their private helpers (functions used only by those routes)
3. Add imports — `from db import get_db`, Flask-Login, `utils/` modules as needed
4. Replace the removed routes in `app.py` with a single registration call
5. Run `python -c "from app import app"` to verify no import errors
6. Manual smoke test of affected routes in production
7. Merge PR, monitor logs for 24 hours before starting next phase

---

## What Does NOT Change

- `blueprints/detention.py` — left exactly as-is
- Template files — no changes to any `.html` file
- Database schema — no changes
- URL structure — all routes keep identical paths and methods
- Cron jobs and worker scripts — unchanged
- `config.py`, `db.py`, `init_db.py` — unchanged
- All external integrations (Stripe, Claude API, Facebook, SMTP) — unchanged

---

## Success Criteria

- All 120+ routes return the same responses before and after
- `app.py` reduced by ~13,000 lines across all 5 phases
- No circular imports (`python -c "from app import app"` exits cleanly)
- Zero downtime during any extraction
- Each phase deployable and independently revertable
