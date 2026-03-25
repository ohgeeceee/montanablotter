# Blueprint Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Incrementally split `app.py` (16,530 lines) into Flask Blueprints across 6 phases, one PR per phase, without downtime or breaking changes.

**Architecture:** One Blueprint per domain (admin, api, auth, payments, public). Phase 0 extracts shared utilities first so blueprints never import from `app.py`. Admin uses a single `admin_bp` object extended via side-effect imports from 7 sub-files.

**Tech Stack:** Python 3.12, Flask, Flask-Login, Flask-Bcrypt, SQLite via `db.py`

---

## File Map

**Created:**
- `utils/__init__.py` — empty, makes utils a package
- `utils/auth_constants.py` — all role tuples
- `utils/app_settings.py` — `_app_setting_*` and `_save_app_setting`
- `blueprints/admin/__init__.py` — `admin_bp`, `require_role`, `enforce_admin_csrf`, `_log_admin_action`, `register_admin_blueprint`
- `blueprints/admin/security.py` — `/admin/login`, `/admin/logout`, `/admin/security/*`
- `blueprints/admin/operations.py` — `/admin`, `/admin/operations/*`, `/admin/facebook`, `/admin/settings`
- `blueprints/admin/ingestion.py` — `/admin/ingestion`, `/admin/upload`, `/admin/blotters`, `/admin/post/*`
- `blueprints/admin/audience.py` — `/admin/audience/*`, `/admin/emails`
- `blueprints/admin/donations.py` — `/admin/donations/*`
- `blueprints/admin/bail_ads.py` — `/admin/bail-ads/*`
- `blueprints/admin/blog.py` — `/admin/blog/*`
- `blueprints/api.py` — `/api/*` endpoints
- `blueprints/auth.py` — `/login`, `/register`, `/dashboard`, `/account`, `/logout`
- `blueprints/payments.py` — `/donate`, `/webhooks/stripe`, `/advertise/bail-bonds/*`
- `blueprints/public.py` — `/`, `/arrests`, `/county/*`, `/city/*`, `/blog/*`, `/laws/*`, and remaining public routes

**Modified:**
- `db.py` — add `get_db()` function
- `app.py` — remove extracted functions/routes, add registration calls, update `login_manager.login_view`

---

## Phase 0 — Pre-flight Utilities

### Task 1: Add `get_db` to `db.py`

**Files:**
- Modify: `db.py`
- Modify: `app.py:1062-1063`

`get_db` is currently a 2-line wrapper in `app.py`. Moving it to `db.py` gives every future blueprint a single import point with no circular dependency risk.

- [ ] **Step 1: Add `get_db` to the end of `db.py`**

```python
def get_db() -> sqlite3.Connection:
    return connect_db()
```

- [ ] **Step 2: Verify the import works**

```bash
cd /root/montanablotter && source venv/bin/activate
python -c "from db import get_db; conn = get_db(); print('OK', conn)"
```
Expected: `OK <sqlite3.Connection object ...>`

- [ ] **Step 3: Update `app.py` to import from `db` instead of defining its own**

In `app.py`, find line 1062:
```python
def get_db():
    return connect_db()
```
Replace with:
```python
from db import get_db  # moved to db.py
```

- [ ] **Step 4: Verify app still starts**

```bash
python -c "from app import app; print('OK')"
```
Expected: `OK` with no errors

- [ ] **Step 5: Commit**

```bash
git add db.py app.py
git commit -m "refactor: move get_db to db.py"
```

---

### Task 2: Create `utils/auth_constants.py`

**Files:**
- Create: `utils/__init__.py`
- Create: `utils/auth_constants.py`
- Modify: `app.py:803-815`

Role tuples are referenced by both admin routes and the `User` class. They must live in a neutral module that both `app.py` and any blueprint can import without circular dependencies.

- [ ] **Step 1: Create `utils/__init__.py`**

```python
```
(Empty file — makes `utils` a package)

- [ ] **Step 2: Create `utils/auth_constants.py`**

Copy the exact values from `app.py` lines 803–815:
```python
ADMIN_ACCESS_ROLES = ('super_admin', 'ops', 'editor', 'revenue', 'read_only')
ADMIN_MANAGEMENT_ROLES = ('super_admin',)
EMAIL_OPS_SEND_ROLES = ('super_admin', 'ops', 'revenue')
OPERATIONS_ROLES = ('super_admin', 'ops')
CONTENT_REVIEW_ROLES = ('super_admin', 'ops', 'editor')
AUDIENCE_MANAGEMENT_ROLES = ('super_admin', 'ops', 'revenue')
ROLE_LABELS = {
    'super_admin': 'Super Admin',
    'ops': 'Operations',
    'editor': 'Editor',
    'revenue': 'Revenue',
    'read_only': 'Read Only',
}
```

- [ ] **Step 3: Verify the import works**

```bash
python -c "from utils.auth_constants import ADMIN_ACCESS_ROLES; print(ADMIN_ACCESS_ROLES)"
```
Expected: `('super_admin', 'ops', 'editor', 'revenue', 'read_only')`

- [ ] **Step 4: Update `app.py` to import from `utils.auth_constants`**

Find the block at `app.py` lines 803–815 and replace with:
```python
from utils.auth_constants import (  # moved to utils/auth_constants.py
    ADMIN_ACCESS_ROLES,
    ADMIN_MANAGEMENT_ROLES,
    EMAIL_OPS_SEND_ROLES,
    OPERATIONS_ROLES,
    CONTENT_REVIEW_ROLES,
    AUDIENCE_MANAGEMENT_ROLES,
    ROLE_LABELS,
)
```

- [ ] **Step 5: Verify app still starts**

```bash
python -c "from app import app; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add utils/__init__.py utils/auth_constants.py app.py
git commit -m "refactor: move role constants to utils/auth_constants.py"
```

---

### Task 3: Create `utils/app_settings.py`

**Files:**
- Create: `utils/app_settings.py`
- Modify: `app.py:1436-1500`

The `_app_setting_*` functions are used by both admin settings routes and public banner rendering. They must live in `utils/` so both domains can import them independently.

- [ ] **Step 1: Create `utils/app_settings.py`**

Cut the following functions verbatim from `app.py` (lines 1436–1500) and paste into the new file. Add the necessary imports at the top:

```python
from __future__ import annotations

import sqlite3

from db import get_db


def _app_setting_raw(key: str, default=None, conn=None):
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        row = conn.execute(
            'SELECT value FROM app_settings WHERE key = ?',
            (key,),
        ).fetchone()
        if not row or row['value'] is None or row['value'] == '':
            return default
        return row['value']
    except sqlite3.Error:
        return default
    finally:
        if own_conn:
            conn.close()


def _app_setting_bool(key: str, default: bool = False, conn=None) -> bool:
    raw = _app_setting_raw(key, default=None, conn=conn)
    if raw is None:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _app_setting_int(key: str, default: int, minimum: int | None = None, maximum: int | None = None, conn=None) -> int:
    raw = _app_setting_raw(key, default=None, conn=conn)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _app_setting_text(key: str, default: str = '', max_length: int | None = None, conn=None) -> str:
    raw = _app_setting_raw(key, default=None, conn=conn)
    value = str(default if raw is None else raw).strip()
    if max_length is not None:
        value = value[:max_length]
    return value


def _save_app_setting(conn, key: str, value) -> None:
    if isinstance(value, bool):
        stored_value = '1' if value else '0'
    else:
        stored_value = str(value).strip()
    conn.execute(
        '''
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        ''',
        (key, stored_value),
    )
```

- [ ] **Step 2: Verify the import works**

```bash
python -c "from utils.app_settings import _app_setting_bool; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Replace the functions in `app.py` with an import**

Find the original function definitions in `app.py` (lines ~1436–1500) and replace them with:
```python
from utils.app_settings import (  # moved to utils/app_settings.py
    _app_setting_raw,
    _app_setting_bool,
    _app_setting_int,
    _app_setting_text,
    _save_app_setting,
)
```

- [ ] **Step 4: Verify app still starts**

```bash
python -c "from app import app; print('OK')"
```
Expected: `OK`

- [ ] **Step 5: Smoke-test Phase 0 checklist**

```bash
python -c "from db import get_db; print(get_db())"
python -c "from utils.auth_constants import ADMIN_ACCESS_ROLES; print(ADMIN_ACCESS_ROLES)"
python -c "from utils.app_settings import _app_setting_bool; print('OK')"
python -c "from app import app; print('OK')"
```
All four must succeed without errors.

- [ ] **Step 6: Commit**

```bash
git add utils/app_settings.py app.py
git commit -m "refactor: move app_settings helpers to utils/app_settings.py"
```

---

## Phase 1 — Admin Blueprint

### Task 4: Create `blueprints/admin/__init__.py`

**Files:**
- Create: `blueprints/admin/__init__.py`

This file defines the single `admin_bp` Blueprint object and the shared admin utilities (`require_role`, `enforce_admin_csrf`, `_log_admin_action`) that all admin sub-files depend on.

**Key decisions baked in:**
- `require_role` uses `redirect(url_for('admin.admin_login'))` instead of `login_manager.unauthorized()` — avoids circular import since `login_manager` lives in `app.py`
- `enforce_admin_csrf` stays as an `@app.before_request` hook in `app.py` until ALL admin routes are extracted (see Task 12)
- `_log_admin_action` moves here because it is only called by admin route functions

- [ ] **Step 1: Copy (do not cut) `require_role` from `app.py` lines 5900–5912 and `_log_admin_action` from lines 5915–5942**

Copy these functions into the new file in the next step. Leave the originals in `app.py` untouched — they will be deleted in Task 12 all at once when the swap is made. Deleting them now would break `app.py` during Tasks 5–11 while the old routes still live there.

- [ ] **Step 2: Create `blueprints/admin/__init__.py`**

```python
from __future__ import annotations

import json
from functools import wraps

from flask import Blueprint, abort, has_request_context, jsonify, redirect, request, url_for
from flask_login import current_user, logout_user

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def require_role(*allowed_roles):
    """Decorator that enforces admin authentication and role access."""
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


def _log_admin_action(action: str, target_type: str = '', target_id=None, metadata=None, user_id=None, conn=None):
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        actor_id = user_id
        if actor_id is None and getattr(current_user, 'is_authenticated', False):
            actor_id = current_user.id
        conn.execute(
            '''
            INSERT INTO audit_logs (user_id, action, target_type, target_id, ip_address, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                actor_id,
                (action or '').strip()[:120],
                (target_type or '').strip()[:80] or None,
                str(target_id)[:120] if target_id is not None else None,
                _client_ip()[:128] if has_request_context() else None,
                json.dumps(metadata or {}, sort_keys=True)[:4000] if metadata is not None else None,
            ),
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def _client_ip() -> str:
    """Return best-effort client IP for audit logging."""
    return (
        request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        or request.remote_addr
        or ''
    )


def register_admin_blueprint(app):
    """Register the admin blueprint and all sub-modules onto the Flask app."""
    # Side-effect imports: each module decorates routes onto admin_bp at import time.
    from blueprints.admin import audience   # noqa: F401
    from blueprints.admin import bail_ads   # noqa: F401
    from blueprints.admin import blog       # noqa: F401
    from blueprints.admin import donations  # noqa: F401
    from blueprints.admin import ingestion  # noqa: F401
    from blueprints.admin import operations # noqa: F401
    from blueprints.admin import security   # noqa: F401
    app.register_blueprint(admin_bp)
```

**Note:** `_client_ip` is a short private helper used only by `_log_admin_action`. Copy its full implementation from `app.py` line 1343 if it differs from the version above.

- [ ] **Step 3: Verify the package imports cleanly**

```bash
python -c "from blueprints.admin import admin_bp, require_role; print('OK')"
```
Expected: `OK` (no ImportError)

- [ ] **Step 4: Commit**

```bash
git add blueprints/admin/__init__.py
git commit -m "refactor: add blueprints/admin package with admin_bp and shared decorators"
```

---

### Task 5: Create `blueprints/admin/security.py`

**Files:**
- Create: `blueprints/admin/security.py`

Routes: `/admin/login` (GET/POST), `/admin/logout`, `/admin/security/users` (GET), `/admin/security/users/<id>/role` (POST), `/admin/security/users/<id>/status` (POST), `/admin/security/audit` (GET)

These are the routes at `app.py` lines 12631–12700 and 13662–13885.

- [ ] **Step 1: Identify the exact line ranges in `app.py`**

```bash
grep -n "^@app.route.*admin/login\|^@app.route.*admin/logout\|^@app.route.*admin/security\|^def admin_login\|^def admin_logout\|^def admin_security" /root/montanablotter/app.py
```

- [ ] **Step 2: Create `blueprints/admin/security.py`**

Structure (copy actual implementations from `app.py` for each route):
```python
from __future__ import annotations

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, ADMIN_MANAGEMENT_ROLES, ROLE_LABELS
from blueprints.admin import admin_bp, require_role, _log_admin_action


@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    # ... copy from app.py verbatim ...


@admin_bp.route('/logout')
@login_required
def admin_logout():
    # ... copy from app.py verbatim ...


@admin_bp.route('/security/users')
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_users():
    # ... copy from app.py verbatim ...


@admin_bp.route('/security/users/<int:user_id>/role', methods=['POST'])
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_user_role(user_id):
    # ... copy from app.py verbatim ...


@admin_bp.route('/security/users/<int:user_id>/status', methods=['POST'])
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_user_status(user_id):
    # ... copy from app.py verbatim ...


@admin_bp.route('/security/audit')
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_audit_log():
    # ... copy from app.py verbatim ...
```

**Important:** Keep the route functions in `app.py` for now — do NOT remove them yet. Removal happens in Task 12 all at once.

- [ ] **Step 3: Verify the module imports cleanly in isolation**

```bash
python -c "
import sys
# Simulate the blueprint being loaded without registering
from blueprints.admin import admin_bp
from blueprints.admin import security
print('security routes:', [str(r) for r in admin_bp.deferred_functions[:3]])
print('OK')
"
```
Expected: No ImportError. (Route count will be 0 at this stage since `register_admin_blueprint` hasn't been called yet.)

- [ ] **Step 4: Commit**

```bash
git add blueprints/admin/security.py
git commit -m "refactor: add admin/security.py (login, logout, users, audit routes)"
```

---

### Task 6: Create `blueprints/admin/operations.py`

**Files:**
- Create: `blueprints/admin/operations.py`

Routes: `/admin` (dashboard), `/admin/operations/sources`, `/admin/operations/courts`, `/admin/operations/meetings`, `/admin/operations/jail-bookings`, `/admin/operations/jail-bookings/create`, `/admin/operations/jail-bookings/<id>/status`, `/admin/facebook`, `/admin/settings`

These are at `app.py` lines 12700–12976 and 14213–14628.

Private helpers that move with this file: `_build_source_coverage_dashboard`, `_humanize_source_type`, `_build_ingestion_health_dashboard`, `_settings_form_values`, `_coerce_setting_value`, `_apply_runtime_app_settings` (check each for usage outside admin — if used elsewhere, leave in `app.py` and import from there temporarily).

- [ ] **Step 1: Check if any of the operations helpers are called outside `/admin/*` routes**

```bash
grep -n "_build_source_coverage_dashboard\|_build_ingestion_health_dashboard\|_settings_form_values\|_apply_runtime_app_settings" /root/montanablotter/app.py | grep -v "^def "
```
Any hits outside the 12700–14628 line range must stay in `app.py` for now.

- [ ] **Step 2: Create `blueprints/admin/operations.py`**

```python
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, OPERATIONS_ROLES
from utils.app_settings import _app_setting_bool, _app_setting_text, _save_app_setting
from blueprints.admin import admin_bp, require_role, _log_admin_action

# --- Private helpers (admin-only) ---
# Copy verbatim from app.py: _build_source_coverage_dashboard, _humanize_source_type,
# _build_ingestion_health_dashboard, _settings_form_values, _coerce_setting_value,
# _apply_runtime_app_settings

# --- Routes ---
@admin_bp.route('/', strict_slashes=False)
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_dashboard():
    # ... copy from app.py verbatim ...

# ... remaining routes ...
```

- [ ] **Step 3: Verify import**

```bash
python -c "from blueprints.admin import operations; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add blueprints/admin/operations.py
git commit -m "refactor: add admin/operations.py (dashboard, sources, facebook, settings)"
```

---

### Task 7: Create `blueprints/admin/ingestion.py`

**Files:**
- Create: `blueprints/admin/ingestion.py`

Routes: `/admin/ingestion` (GET), `/admin/ingestion/<job_id>/retry` (POST), `/admin/upload` (GET/POST), `/admin/blotters` (GET), `/admin/blotter/<id>/delete` (POST), `/admin/post/<id>/redact` (GET/POST), `/admin/post/<id>/status` (POST), `/admin/operations/redaction` (GET), `/admin/operations/redaction/<id>/approve` (POST), `/admin/operations/redaction/<id>/reset` (POST)

Lines ~13885–14215 and ~13052–13290 in `app.py`.

Private helpers that move here: check for any `_ingest_*`, `_redaction_*` helpers defined near those routes.

- [ ] **Step 1: Identify private helpers adjacent to ingestion routes**

```bash
grep -n "^def _" /root/montanablotter/app.py | awk -F: '$2 >= 13800 && $2 <= 14220'
```

- [ ] **Step 2: Create `blueprints/admin/ingestion.py`**

```python
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, CONTENT_REVIEW_ROLES, OPERATIONS_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action

# Copy any private helpers adjacent to these routes from app.py

@admin_bp.route('/ingestion')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_ingestion():
    # ... copy from app.py verbatim ...

# ... remaining routes ...
```

- [ ] **Step 3: Verify import**

```bash
python -c "from blueprints.admin import ingestion; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add blueprints/admin/ingestion.py
git commit -m "refactor: add admin/ingestion.py (ingestion, upload, blotters, redaction)"
```

---

### Task 8: Create `blueprints/admin/audience.py`

**Files:**
- Create: `blueprints/admin/audience.py`

Routes (lines ~13288–13626 and ~14421–14530): `/admin/audience/subscribers`, `/admin/audience/alerts`, `/admin/audience/alerts/<id>/deactivate`, `/admin/audience/alerts/watch/<id>/deactivate`, `/admin/audience/comments`, `/admin/audience/comments/<id>/status`, `/admin/audience/subscribers/<id>/status`, `/admin/audience/subscribers/<id>/counties`, `/admin/audience/subscribers/<id>/notes`, `/admin/audience/subscribers/export.csv`, `/admin/audience/email-ops`, `/admin/audience/email-ops/send-test`, `/admin/audience/email-ops/send-now`, `/admin/audience/email-ops/retry-failures`, `/admin/emails`, `/admin/emails/template/<type>`

Private helpers that move: `_subscriber_admin_context`, `_build_email_ops_preview`, `_digest_target_date`, `_digest_support_email`, `_normalize_email_ops_recipient`, `_record_digest_run`, `_record_digest_run_recipient`, `_finish_digest_run` — verify each is admin-only with grep before moving.

- [ ] **Step 1: Verify helpers are admin-only**

```bash
grep -n "_subscriber_admin_context\|_build_email_ops_preview\|_record_digest_run" /root/montanablotter/app.py | grep -v "^[0-9]*:def "
```
Hits outside line range 13000–14640 must stay in `app.py`.

- [ ] **Step 2: Create `blueprints/admin/audience.py`**

```python
from __future__ import annotations

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, AUDIENCE_MANAGEMENT_ROLES, EMAIL_OPS_SEND_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action

# Copy private helpers + routes verbatim from app.py
```

- [ ] **Step 3: Verify import**

```bash
python -c "from blueprints.admin import audience; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add blueprints/admin/audience.py
git commit -m "refactor: add admin/audience.py (subscribers, alerts, comments, email-ops)"
```

---

### Task 9: Create `blueprints/admin/donations.py`

**Files:**
- Create: `blueprints/admin/donations.py`

Routes (lines ~14984–15320): `/admin/donations`, `/admin/donations/preflight`, `/admin/donations/reconcile`, `/admin/donations/export.csv`

Private helpers near those lines: `_donation_launch_snapshot`, `_apply_stripe_event`, `_apply_stripe_bail_ad_event` — check usage:

```bash
grep -n "_donation_launch_snapshot\|_apply_stripe_event\|_apply_stripe_bail_ad_event" /root/montanablotter/app.py | grep -v "^[0-9]*:def "
```

**Warning:** `_apply_stripe_event` and `_apply_stripe_bail_ad_event` are also called from `/webhooks/stripe` (a public route). Do NOT move these — they will be extracted in Phase 4 with `payments.py`. Only move `_donation_launch_snapshot` if it is admin-only.

- [ ] **Step 1: Create `blueprints/admin/donations.py`**

```python
from __future__ import annotations

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, OPERATIONS_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action

@admin_bp.route('/donations')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_donations():
    # ... copy from app.py verbatim ...

# ... remaining 3 routes ...
```

- [ ] **Step 2: Verify import**

```bash
python -c "from blueprints.admin import donations; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add blueprints/admin/donations.py
git commit -m "refactor: add admin/donations.py"
```

---

### Task 10: Create `blueprints/admin/bail_ads.py`

**Files:**
- Create: `blueprints/admin/bail_ads.py`

Routes (lines ~15320–16270): `/admin/bail-ads`, `/admin/bail-ads/agencies`, `/admin/bail-ads/simulator`, `/admin/bail-ads/agencies/create`, `/admin/bail-ads/agencies/<id>/update`, `/admin/bail-ads/agencies/<id>/delete`, `/admin/bail-ads/attribution/export.csv`, `/admin/bail-ads/<id>/status`, `/admin/bail-ads/creatives/<id>/status`, `/admin/content/seo`

**Warning:** Many `_bail_ad_*` helpers in `app.py` are shared between admin routes AND the public `/advertise/bail-bonds/*` routes. Do NOT move those shared helpers. Only move helpers that are exclusively called from `/admin/bail-ads/*` routes.

- [ ] **Step 1: Identify admin-only bail helpers**

```bash
# For each candidate helper, check all call sites:
grep -n "_bail_advertiser_attribution_30d\|_bail_agency_dedupe_key\|_ensure_bail_agency_outreach_schema\|_log_bail_agency_email\|_seed_bail_agency_outreach\|_bail_agency_default_templates\|_render_bail_template\|_bail_agency_rendered_templates" /root/montanablotter/app.py | grep -v "^[0-9]*:def "
```
Only move helpers whose call sites are all within the `/admin/bail-ads/*` route functions.

- [ ] **Step 2: Create `blueprints/admin/bail_ads.py`**

```python
from __future__ import annotations

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, OPERATIONS_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action

# Copy admin-only bail helpers + all /admin/bail-ads/* routes verbatim from app.py
```

- [ ] **Step 3: Verify import**

```bash
python -c "from blueprints.admin import bail_ads; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add blueprints/admin/bail_ads.py
git commit -m "refactor: add admin/bail_ads.py"
```

---

### Task 11: Create `blueprints/admin/blog.py`

**Files:**
- Create: `blueprints/admin/blog.py`

Routes (lines ~12203–12350): `/admin/blog`, `/admin/blog/workflow`, `/admin/blog/new`, `/admin/blog/<id>/edit`, `/admin/blog/<id>/delete`

- [ ] **Step 1: Create `blueprints/admin/blog.py`**

```python
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, CONTENT_REVIEW_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action


@admin_bp.route('/blog')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_blog():
    # ... copy from app.py verbatim ...


@admin_bp.route('/blog/workflow', methods=['GET', 'POST'])
@login_required
@require_role(*CONTENT_REVIEW_ROLES)
def admin_blog_workflow():
    # ... copy from app.py verbatim ...


@admin_bp.route('/blog/new', methods=['GET', 'POST'])
@login_required
@require_role(*CONTENT_REVIEW_ROLES)
def admin_blog_new():
    # ... copy from app.py verbatim ...


@admin_bp.route('/blog/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
@require_role(*CONTENT_REVIEW_ROLES)
def admin_blog_edit(post_id):
    # ... copy from app.py verbatim ...


@admin_bp.route('/blog/<int:post_id>/delete', methods=['POST'])
@login_required
@require_role(*CONTENT_REVIEW_ROLES)
def admin_blog_delete(post_id):
    # ... copy from app.py verbatim ...
```

- [ ] **Step 2: Verify import**

```bash
python -c "from blueprints.admin import blog; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add blueprints/admin/blog.py
git commit -m "refactor: add admin/blog.py"
```

---

### Task 12: Register admin blueprint in `app.py` and cut the old routes

**Files:**
- Modify: `app.py` — remove ~50 admin routes + their private helpers, add registration call, update `login_manager.login_view`

This is the atomic swap. Do it in one commit.

- [ ] **Step 1: Add the registration call to `app.py`**

Find the comment block in `app.py` just before the first `@app.route('/admin/...')` decorator (around line 12200) and insert before it:
```python
# ==========================================
# ADMIN BLUEPRINT
# ==========================================
from blueprints.admin import register_admin_blueprint
register_admin_blueprint(app)
```

- [ ] **Step 2: Update `login_manager.login_view`**

At `app.py` line 801, change:
```python
login_manager.login_view = 'admin_login'
```
to:
```python
login_manager.login_view = 'admin.admin_login'
```

- [ ] **Step 3: Update `enforce_admin_csrf` in `app.py`**

In `enforce_admin_csrf` (line ~1006), update the redirect target:
```python
# Before:
return redirect(url_for('admin_login'))
# After:
return redirect(url_for('admin.admin_login'))
```
Also update the `url_for('admin_dashboard')` reference on the same block:
```python
# Before:
return redirect(request.referrer or url_for('admin_dashboard'))
# After:
return redirect(request.referrer or url_for('admin.admin_dashboard'))
```

- [ ] **Step 4: Verify import — this is the critical check**

```bash
python -c "from app import app; print('OK')"
```
If this raises `AssertionError: View function mapping is overwriting...`, a route was left in both `app.py` and the blueprint. Fix by removing it from `app.py`.

If this raises `ImportError`, a helper function referenced in a blueprint sub-file was not moved. Add it to the appropriate sub-file.

- [ ] **Step 5: Remove the old admin route functions from `app.py`**

Delete all `@app.route('/admin/...')` decorated functions and their private helpers from `app.py`. Line ranges to remove:
- Lines ~12203–16270 (all admin routes)
- The `require_role` function at lines ~5900–5912 (now in admin `__init__.py`)
- The `_log_admin_action` function at lines ~5915–5942 (now in admin `__init__.py`)
- Private helpers that were moved with their routes

Keep in `app.py`:
- `enforce_admin_csrf` and `enforce_admin_access` (still `@app.before_request`, converted after all routes are out)
- `_active_super_admin_count` (used by security routes — verify it moved to `security.py` already)

- [ ] **Step 6: Verify import again after deletion**

```bash
python -c "from app import app; print('OK')"
```
Expected: `OK` with no errors

- [ ] **Step 7: Run smoke test checklist**

```bash
# Start a test server in the background:
source venv/bin/activate
python app.py &
APP_PID=$!
sleep 2

# Check each endpoint (adjust host/port as needed):
curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/admin/login
# Expected: 200

curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/admin
# Expected: 302 (redirect to login since unauthenticated)

kill $APP_PID
```

Then run the full Phase 1 smoke test checklist from the spec in your browser (requires real login):
- `/admin/login` renders the login form
- Login with valid credentials → redirects to `/admin`
- `/admin`, `/admin/blotters`, `/admin/ingestion`, `/admin/audience/subscribers` all render without 500
- `/admin/bail-ads`, `/admin/donations`, `/admin/settings` all render
- Unauthenticated `/admin` request → redirects to `/admin/login`
- Check `tail -50 /root/montanablotter/gunicorn.log` for tracebacks
- Check `tail -20 /root/montanablotter/mail.log` — email worker still running

- [ ] **Step 8: Deploy to production**

```bash
sudo systemctl restart montanablotter
sudo systemctl status montanablotter
```
Expected: `active (running)` — if it shows `failed`, the import crashed. Check `journalctl -u montanablotter -n 50`.

- [ ] **Step 9: Commit**

```bash
git add app.py blueprints/admin/
git commit -m "refactor(phase-1): register admin blueprint, remove ~4000 lines from app.py"
```

---

## Phase 2 — API Blueprint

### Task 13: Create `blueprints/api.py` and register it

**Files:**
- Create: `blueprints/api.py`
- Modify: `app.py`

Routes (lines ~7032–7300): `/api/pattern-click`, `/api/subscribe-event`, `/api/donate-event`, `/api/bail-ads/event`, `/api/bail-ads/simulator-event`, `/api/bail-ads/simulator-upload`, `/api/bail-leads/event`, `/api/donate/create-checkout-session`, `/api/docs`, `/developers/api`

- [ ] **Step 1: Create `blueprints/api.py`**

```python
from __future__ import annotations

from flask import Blueprint, jsonify, request

from db import get_db

api_bp = Blueprint('api', __name__)


@api_bp.route('/api/pattern-click', methods=['POST'])
def api_pattern_click():
    # ... copy from app.py verbatim ...

# ... remaining routes ...
```

- [ ] **Step 2: Verify import**

```bash
python -c "from blueprints.api import api_bp; print('OK')"
```

- [ ] **Step 3: Register in `app.py`**

Add near the top of the route registrations:
```python
from blueprints.api import api_bp
app.register_blueprint(api_bp)
```
Remove the old `/api/*` route functions from `app.py`.

- [ ] **Step 4: Verify and smoke test**

```bash
python -c "from app import app; print('OK')"
# Start dev server, test: GET /api/counties, POST /api/subscribe-event
```

- [ ] **Step 5: Deploy and commit**

```bash
sudo systemctl restart montanablotter
git add blueprints/api.py app.py
git commit -m "refactor(phase-2): extract API routes to blueprints/api.py"
```

---

## Phase 3 — Auth Blueprint

### Task 14: Create `blueprints/auth.py` and register it

**Files:**
- Create: `blueprints/auth.py`
- Modify: `app.py`

Routes (lines ~8239–8693): `/register`, `/login`, `/logout`, `/dashboard`, `/account`, `/comments`, `/subscribe`, `/unsubscribe`, `/alerts`, `/alerts/unsubscribe`, `/alerts/name-watch/cancel`

Helpers that move: `_safe_next_url`, `_all_subscription_counties`, `_selected_counties_from_form`, `_upsert_digest_subscription`, `_load_public_user`, `_get_public_user`, `_set_public_user_session`, `_clear_public_user_session`, `_public_comment_target_exists`, `_public_comments_context`, `_public_comment_target_path` — verify each is auth-only before moving.

The `PublicUser` class (line 1076) and `User` class (line 5856) stay in `app.py` until a dedicated auth refactor — they are tightly coupled to `login_manager`.

- [ ] **Step 1: Audit helper usage**

```bash
grep -n "_safe_next_url\|_load_public_user\|_get_public_user\|_upsert_digest_subscription" /root/montanablotter/app.py | grep -v "^[0-9]*:def "
```
Only move helpers whose call sites are all within auth routes.

- [ ] **Step 2: Create `blueprints/auth.py`**

```python
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES

auth_bp = Blueprint('auth', __name__)

# Copy private helpers + routes verbatim from app.py
```

- [ ] **Step 3: Register, verify, deploy, commit**

```bash
# In app.py:
from blueprints.auth import auth_bp
app.register_blueprint(auth_bp)

python -c "from app import app; print('OK')"
sudo systemctl restart montanablotter
git add blueprints/auth.py app.py
git commit -m "refactor(phase-3): extract auth routes to blueprints/auth.py"
```

---

## Phase 4 — Payments Blueprint

### Task 15: Create `blueprints/payments.py` and register it

**Files:**
- Create: `blueprints/payments.py`
- Modify: `app.py`

Routes: `/donate`, `/donate/success`, `/donate/cancel`, `/webhooks/stripe`, `/advertise`, `/advertise/bail-bonds`, `/advertise/bail-bonds/checkout`, `/advertise/bail-bonds/checkout/success`, `/advertise/bail-bonds/checkout/cancel`, `/advertise/bail-bonds/contract`, `/advertise/bail-bonds/onboarding/<token>`, `/advertise/bail-bonds/control-panel/<token>`, `/bail-bonds`, `/bail-bonds/<county_slug>`, `/bail-bonds/intake`

Shared helpers that move here: `_apply_stripe_event`, `_apply_stripe_bail_ad_event`, `_bail_ad_*` public helpers. These can finally leave `app.py` now that admin's bail routes are already in `bail_ads.py`.

- [ ] **Step 1: Audit `_apply_stripe_event` and `_apply_stripe_bail_ad_event` call sites**

```bash
grep -n "_apply_stripe_event\|_apply_stripe_bail_ad_event" /root/montanablotter/app.py | grep -v "^[0-9]*:def "
```
Confirm all callers are either `/webhooks/stripe` or `/admin/donations/*` (which is now in `donations.py`).

- [ ] **Step 2: Create `blueprints/payments.py`** with all shared bail/stripe helpers and payment routes.

- [ ] **Step 3: Update `blueprints/admin/donations.py` import (mandatory)**

`donations.py` was written in Phase 1 with `_apply_stripe_event` and `_apply_stripe_bail_ad_event` still in `app.py`. Now that they move to `payments.py`, add an explicit import at the top of `donations.py`:
```python
from blueprints.payments import _apply_stripe_event, _apply_stripe_bail_ad_event
```
Verify: `python -c "from blueprints.admin import donations; print('OK')"`

- [ ] **Step 4: Register, verify, deploy, commit**

```bash
python -c "from app import app; print('OK')"
sudo systemctl restart montanablotter
git add blueprints/payments.py blueprints/admin/donations.py app.py
git commit -m "refactor(phase-4): extract payment and advertiser routes to blueprints/payments.py"
```

---

## Phase 5 — Public Blueprint

### Task 16: Create `blueprints/public.py` and register it

**Files:**
- Create: `blueprints/public.py`
- Modify: `app.py`

Remaining routes: `/`, `/arrests`, `/counties`, `/county/<slug>`, `/cities`, `/city/<slug>`, `/patterns`, `/patterns/<slug>`, `/patterns/<slug>/<county>`, `/warrants`, `/warrants/<slug>`, `/laws`, `/laws/charge/<slug>`, `/blog`, `/blog/<slug>`, `/trends`, `/corrections`, `/guides`, `/guides/<slug>`, `/annual-roundups`, `/annual-roundups/<year>`, `/courts`, `/court-hearings`, `/court-case/<slug>`, all `/sitemap-*.xml`, `/feed.xml`, `/robots.txt`, `/sitemap.xml`, static pages (`/terms`, `/privacy`, etc.)

This is the largest single extraction (~7,000 lines). SEO helper functions (`_iso_lastmod`, `_build_like_clause`, `_pattern_clause`, etc.) move here alongside the routes that use them.

**Do NOT move `inject_public_nav` or `track_page_view`.** These are `@app.context_processor` and `@app.before_request` hooks registered on the `app` object, not on any blueprint. Moving them to `public.py` would scope them only to public blueprint requests, breaking context injection on admin, auth, and payment pages. They stay in `app.py` permanently.

- [ ] **Step 1: Identify all remaining routes in `app.py`**

```bash
grep -c "^@app.route" /root/montanablotter/app.py
```
After Phase 4, this should be ~40. After Phase 5, it should be 0 (excluding PWA routes that may stay permanently).

- [ ] **Step 2: Create `blueprints/public.py`** with all remaining public routes and their private helpers.

- [ ] **Step 3: Move `enforce_admin_csrf` and `enforce_admin_access` to `admin_bp.before_request`**

Now that all `/admin/*` routes are on `admin_bp`, these hooks can be tightened:

In `blueprints/admin/__init__.py`, add:
```python
@admin_bp.before_request
def enforce_admin_csrf_hook():
    # Same body as app.py enforce_admin_csrf, but remove the startswith('/admin') guard
    # since this only fires for admin blueprint requests
    ...

@admin_bp.before_request
def enforce_admin_access_hook():
    # Same body, remove startswith('/admin') guard
    ...
```

Remove both `@app.before_request` hooks from `app.py`.

- [ ] **Step 4: Register, verify, deploy, commit**

```bash
python -c "from app import app; print('OK')"
# Count remaining routes in app.py — should be near zero
grep -c "^@app.route" /root/montanablotter/app.py
sudo systemctl restart montanablotter
git add blueprints/public.py blueprints/admin/__init__.py app.py
git commit -m "refactor(phase-5): extract public routes, finalize admin before_request hooks"
```

---

## Final Verification

After all phases are complete:

- [ ] `grep -c "^@app.route" /root/montanablotter/app.py` — should output `0` or near 0 (only PWA/static routes remain)
- [ ] `wc -l /root/montanablotter/app.py` — should be under 1,000 lines (down from 16,530)
- [ ] `python -c "from app import app; print('OK')"` — clean import
- [ ] Full smoke test: visit every section of the site, verify no 500 errors
- [ ] `sudo systemctl status montanablotter` — `active (running)`
- [ ] Check `tail -100 /root/montanablotter/gunicorn.log` — no tracebacks
- [ ] Check `tail -20 /root/montanablotter/mail.log` — email worker still ingesting normally
