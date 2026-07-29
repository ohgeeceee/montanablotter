from __future__ import annotations

import json
from functools import wraps

from flask import Blueprint, abort, has_request_context, redirect, request, url_for
from flask_login import current_user

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES  # noqa: F401 — re-exported for sub-modules


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _client_ip() -> str:
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return (request.remote_addr or '').strip()


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


def register_admin_blueprint(app):
    """Register the admin blueprint and all sub-modules onto the Flask app."""
    # Side-effect imports: each module decorates routes onto admin_bp at import time.
    from blueprints.admin import ai_console  # noqa: F401
    from blueprints.admin import agents     # noqa: F401
    from blueprints.admin import audience   # noqa: F401
    from blueprints.admin import bail_ads   # noqa: F401
    from blueprints.admin import blog       # noqa: F401
    from blueprints.admin import donations  # noqa: F401
    from blueprints.admin import ingestion  # noqa: F401
    from blueprints.admin import mission_control  # noqa: F401
    from blueprints.admin import operations # noqa: F401
    from blueprints.admin import recovery_ads  # noqa: F401
    from blueprints.admin import attorney_ads  # noqa: F401
    from blueprints.admin import lawyer_ads  # noqa: F401
    from blueprints.admin import security   # noqa: F401
    from blueprints.admin import code_violations  # noqa: F401
    from blueprints.admin import license_sanctions  # noqa: F401
    from blueprints.admin import sex_offender  # noqa: F401
    from blueprints.admin import workspace  # noqa: F401
    from blueprints.admin import agency_contacts  # noqa: F401
    from blueprints.admin import command_center  # noqa: F401
    from blueprints.admin import corrections     # noqa: F401
    from blueprints.admin import social_shares   # noqa: F401
    from blueprints.admin import sponsored_digests  # noqa: F401
    from blueprints.admin import case_watch  # noqa: F401
    from blueprints.admin import outreach  # noqa: F401
    from blueprints.admin import for_the_record  # noqa: F401
    from blueprints.admin import civic_requests  # noqa: F401
    app.register_blueprint(admin_bp)
