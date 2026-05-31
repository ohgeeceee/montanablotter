"""
Paywall / sneak-preview enforcement for Montana Blotter.

Tracks free detailed views per anonymous session and per public_user,
enforces 3/day and 5/week rolling limits, and provides subscription
plan helpers for gating content.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from flask_login import current_user

from db import connect_page_views, get_db

# ---------------------------------------------------------------------------
# Plan hierarchy
# ---------------------------------------------------------------------------
PLAN_HIERARCHY = {
    'scout': 0,
    'insider': 1,
    'professional': 2,
}

PLAN_LABELS = {
    'scout': 'Scout',
    'insider': 'Insider',
    'professional': 'Professional',
}

PREVIEW_LIMITS = {
    'day': 3,
    'week': 5,
}

# ---------------------------------------------------------------------------
# Schema helpers (page_views.db)
# ---------------------------------------------------------------------------

def _ensure_preview_schema() -> None:
    conn = connect_page_views()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS preview_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                viewer_type TEXT NOT NULL DEFAULT 'anonymous',
                viewer_id TEXT NOT NULL,
                resource_type TEXT NOT NULL DEFAULT 'incident',
                resource_id INTEGER,
                viewed_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_preview_views_lookup
            ON preview_views(viewer_type, viewer_id, viewed_at)
        ''')
        conn.commit()
    finally:
        conn.close()


def _page_views_conn():
    _ensure_preview_schema()
    return connect_page_views()


# ---------------------------------------------------------------------------
# Viewer identity
# ---------------------------------------------------------------------------

def _viewer_identity() -> tuple[str, str]:
    """Return (viewer_type, viewer_id) for the current request."""
    # Admin users bypass tracking entirely, but we still need an identity
    if current_user.is_authenticated:
        return ('admin', str(current_user.id))

    # Check public user session
    public_user_id = session.get('public_user_id')
    if public_user_id:
        return ('public_user', str(int(public_user_id)))

    # Anonymous: use session id + IP hash for stability
    session_id = session.get('mb_session_id')
    if not session_id:
        session_id = _generate_session_id()
        session['mb_session_id'] = session_id
        session.permanent = True

    ip = request.headers.get('X-Forwarded-For', request.remote_addr) or ''
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
    return ('anonymous', f"{session_id}:{ip_hash}")


def _generate_session_id() -> str:
    import secrets
    return secrets.token_urlsafe(16)


# ---------------------------------------------------------------------------
# Plan resolution
# ---------------------------------------------------------------------------

def get_user_plan() -> str:
    """Return the effective plan for the current requester."""
    # Admin users are treated as professional (full access)
    if current_user.is_authenticated:
        return 'professional'

    # Public users
    public_user_id = session.get('public_user_id')
    if public_user_id:
        conn = get_db()
        try:
            row = conn.execute(
                '''
                SELECT subscriber_plan, subscription_status
                FROM public_users
                WHERE id = ?
                ''',
                (int(public_user_id),),
            ).fetchone()
        finally:
            conn.close()

        if row:
            plan = (row['subscriber_plan'] or 'scout').strip().lower()
            status = (row['subscription_status'] or '').strip().lower()
            if plan in PLAN_HIERARCHY and status in ('active', 'trialing'):
                return plan

    return 'scout'


def plan_has_access(user_plan: str, min_plan: str) -> bool:
    return PLAN_HIERARCHY.get(user_plan, 0) >= PLAN_HIERARCHY.get(min_plan, 0)


def user_has_access(min_plan: str = 'insider') -> bool:
    return plan_has_access(get_user_plan(), min_plan)


# ---------------------------------------------------------------------------
# Preview counting
# ---------------------------------------------------------------------------

def get_preview_counts(viewer_type: str, viewer_id: str) -> dict[str, int]:
    """Return {'day': int, 'week': int} counts for the viewer."""
    conn = _page_views_conn()
    try:
        now = datetime.now(timezone.utc)
        day_ago = (now - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')

        day_count = conn.execute(
            '''
            SELECT COUNT(*) AS c FROM preview_views
            WHERE viewer_type = ? AND viewer_id = ? AND viewed_at > ?
            ''',
            (viewer_type, viewer_id, day_ago),
        ).fetchone()['c']

        week_count = conn.execute(
            '''
            SELECT COUNT(*) AS c FROM preview_views
            WHERE viewer_type = ? AND viewer_id = ? AND viewed_at > ?
            ''',
            (viewer_type, viewer_id, week_ago),
        ).fetchone()['c']

        return {'day': day_count, 'week': week_count}
    finally:
        conn.close()


def record_preview_view(resource_type: str = 'incident', resource_id: int | None = None) -> dict[str, int]:
    """Record a preview view and return the updated counts."""
    viewer_type, viewer_id = _viewer_identity()

    # Admins don't consume previews
    if viewer_type == 'admin':
        return {'day': 0, 'week': 0}

    conn = _page_views_conn()
    try:
        conn.execute(
            '''
            INSERT INTO preview_views (viewer_type, viewer_id, resource_type, resource_id)
            VALUES (?, ?, ?, ?)
            ''',
            (viewer_type, viewer_id, resource_type, resource_id),
        )
        conn.commit()
    finally:
        conn.close()

    return get_preview_counts(viewer_type, viewer_id)


def check_preview_eligibility() -> tuple[bool, dict[str, int]]:
    """Return (allowed, counts) for the current requester."""
    viewer_type, viewer_id = _viewer_identity()

    if viewer_type == 'admin':
        return True, {'day': 0, 'week': 0}

    counts = get_preview_counts(viewer_type, viewer_id)
    allowed = counts['day'] < PREVIEW_LIMITS['day'] and counts['week'] < PREVIEW_LIMITS['week']
    return allowed, counts


def preview_allowed(resource_type: str = 'incident', resource_id: int | None = None) -> tuple[bool, dict[str, int]]:
    """Combined check: if user has subscription, allow; else check preview limits and record."""
    if user_has_access('insider'):
        return True, {'day': 0, 'week': 0}

    allowed, counts = check_preview_eligibility()
    if allowed:
        counts = record_preview_view(resource_type, resource_id)
        # Re-check after recording
        allowed = counts['day'] <= PREVIEW_LIMITS['day'] and counts['week'] <= PREVIEW_LIMITS['week']
    return allowed, counts


# ---------------------------------------------------------------------------
# Decorator / helper for routes
# ---------------------------------------------------------------------------

def gated_detail(min_plan: str = 'insider'):
    """Decorator for detail routes. Returns a dict with gating info instead of redirecting."""
    # This is meant to be called inside route handlers, not as a decorator,
    # because we want to render a teaser page rather than a 403.
    def wrapper(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            if user_has_access(min_plan):
                return fn(*args, **kwargs)

            allowed, counts = preview_allowed()
            if allowed:
                return fn(*args, **kwargs)

            # Over limit — render the same route but mark it as paywalled
            # We pass _paywalled=True through kwargs or g
            g._paywall_blocked = True
            g._paywall_counts = counts
            g._paywall_min_plan = min_plan
            return fn(*args, **kwargs)
        return decorated
    return wrapper


def require_subscription(min_plan: str = 'insider', json=False):
    """Decorator that blocks entirely (returns 403 or JSON error)."""
    def wrapper(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            if user_has_access(min_plan):
                return fn(*args, **kwargs)
            if json:
                from flask import jsonify
                return jsonify({'error': 'Subscription required', 'plan_required': min_plan}), 403
            abort(403)
        return decorated
    return wrapper
