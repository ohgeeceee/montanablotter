"""
Paywall / subscription enforcement for Montana Blotter.

Tier hierarchy:
  free (level 0) — public access, limited history, ads, basic alerts
  plus (level 1) — $5.99/mo or $59/yr, 12mo history, 5 counties, watchlists
  pro  (level 2) — $19.99/mo or $199/yr, full archive, statewide, unlimited

Feature lookup: has_feature(plan, feature_name) checks if the plan's level
meets or exceeds the feature's minimum plan level.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from flask_login import current_user

from db import connect_page_views, get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES

# ---------------------------------------------------------------------------
# Plan hierarchy (level = access power)
# ---------------------------------------------------------------------------
PLAN_HIERARCHY = {
    'free': 0,
    'plus': 1,
    'pro': 2,
}

PLAN_LABELS = {
    'free': 'Free',
    'plus': 'Plus',
    'pro': 'Pro',
}

# ---------------------------------------------------------------------------
# Feature matrix
# Each feature maps to the minimum plan level required.
# ---------------------------------------------------------------------------
FEATURES = {
    # Records & search
    'warrant_access':      0,     # free gets warrant search
    'records_7day':        0,     # free sees 7 days of history
    'records_12mo':        1,     # plus sees 12 months
    'records_full':        2,     # pro sees full archive
    'advanced_filters':    1,     # date range, charge category, etc.

    # Alerts & watches
    'county_alerts':       0,     # 1 county basic alert
    'alerts_5_counties':   1,     # plus: up to 5 counties
    'alerts_statewide':    2,     # pro: all counties
    'name_watchlists':     1,     # plus: up to 5 name/keyword watches
    'name_watchlists_unlimited': 2,  # pro: unlimited
    'saved_searches':      1,     # plus: 10 saved searches
    'saved_searches_unlimited': 2, # pro: unlimited
    'daily_digest':        1,     # plus: daily digest email

    # Case tracking
    'case_tracking':       1,     # plus: track 5 cases
    'case_tracking_unlimited': 2, # pro: unlimited

    # Experience
    'ad_free':             1,     # plus+: no ads
    'priority_support':    2,     # pro only

    # Exports & API
    'pdf_export':          1,     # plus: limited PDF exports
    'csv_export':          2,     # pro: CSV + PDF
    'data_exports':        2,     # pro: bulk data exports
    'api_access':          2,     # pro: API key
    'webhook_alerts':      2,     # pro: webhook notifications

    # Analytics
    'county_analytics':    1,     # plus: per-county trends
    'statewide_analytics': 2,     # pro: statewide comparisons
}

# Backward compat: plans that unlock warrant pages
WARRANT_PLANS = {'plus', 'pro', 'warrant_access'}

PREVIEW_LIMITS = {
    'day': 3,
    'week': 5,
}

# Feature-level limits by plan
PLAN_LIMITS = {
    'free': {
        'saved_searches': 0,
        'name_watchlists': 0,
        'tracked_cases': 0,
        'alert_counties': 1,
    },
    'plus': {
        'saved_searches': 10,
        'name_watchlists': 5,
        'tracked_cases': 5,
        'alert_counties': 5,
    },
    'pro': {
        'saved_searches': 9999,
        'name_watchlists': 9999,
        'tracked_cases': 9999,
        'alert_counties': 9999,
    },
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
    if current_user.is_authenticated:
        return ('admin', str(current_user.id))
    public_user_id = session.get('public_user_id')
    if public_user_id:
        return ('public_user', str(int(public_user_id)))
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
    if current_user.is_authenticated:
        return 'pro'
    public_user_id = session.get('public_user_id')
    if public_user_id:
        conn = get_db()
        try:
            row = conn.execute(
                'SELECT subscriber_plan, subscription_status FROM public_users WHERE id = ?',
                (int(public_user_id),),
            ).fetchone()
        finally:
            conn.close()
        if row:
            plan = (row['subscriber_plan'] or 'free').strip().lower()
            status = (row['subscription_status'] or '').strip().lower()
            # Warrant access is sold as its own Stripe product but is treated as
            # the 'plus' tier for feature/access purposes (see app.py webhook
            # remap warrant_access -> plus). Resolve it so the gate and feature
            # matrix see a recognized plan.
            if plan == 'warrant_access' and status in ('active', 'trialing'):
                return 'plus'
            if plan in PLAN_HIERARCHY and status in ('active', 'trialing'):
                return plan
    return 'free'

def get_plan_level(plan: str) -> int:
    return PLAN_HIERARCHY.get(plan, 0)

# ---------------------------------------------------------------------------
# Feature gates
# ---------------------------------------------------------------------------

def has_feature(feature_name: str) -> bool:
    """Return True if the current user's plan includes the named feature."""
    return get_plan_level(get_user_plan()) >= FEATURES.get(feature_name, 999)

def get_feature_limit(feature_name: str) -> int:
    """Return the numeric limit for a capped feature on the user's plan."""
    plan = get_user_plan()
    return PLAN_LIMITS.get(plan, {}).get(feature_name, 0)

def plan_has_access(user_plan: str, min_plan: str) -> bool:
    return get_plan_level(user_plan) >= get_plan_level(min_plan)

def user_has_access(min_plan: str = 'plus') -> bool:
    return plan_has_access(get_user_plan(), min_plan)

# ---------------------------------------------------------------------------
# Backward compat
# ---------------------------------------------------------------------------

def user_has_warrant_access() -> bool:
    """Return True if the current user can access warrant pages.
    
    Free users with an active ad-unlock grant also get access.
    """
    if current_user.is_authenticated and getattr(current_user, 'role', None) in ADMIN_ACCESS_ROLES:
        return True
    plan = get_user_plan()
    if plan in WARRANT_PLANS:
        return True
    return user_has_ad_unlocked_warrant()

def user_has_ad_unlocked_warrant() -> bool:
    public_user_id = session.get('public_user_id')
    if not public_user_id:
        return False
    return get_ad_unlock_remaining_seconds(int(public_user_id)) > 0

def get_ad_unlock_remaining_seconds(public_user_id: int) -> int:
    try:
        conn = get_db()
        row = conn.execute(
            '''SELECT MAX(expires_at) AS latest_expiry
               FROM ad_unlock_grants
              WHERE public_user_id = ? AND expires_at > datetime('now')''',
            (public_user_id,),
        ).fetchone()
        conn.close()
    except sqlite3.OperationalError:
        return 0
    if not row or not row['latest_expiry']:
        return 0
    try:
        expiry = datetime.strptime(row['latest_expiry'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    now = datetime.now(timezone.utc)
    return max(0, int((expiry - now).total_seconds()))

def record_ad_unlock(
    public_user_id: int, watch_seconds: int, ip_address: str | None = None,
    user_agent: str | None = None, ad_id: str | None = None,
    duration_hours: int = 24, provider: str = 'youtube',
) -> str:
    duration_hours = max(1, int(duration_hours))
    provider_tag = (provider or 'youtube').strip().lower()[:32] or 'youtube'
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=duration_hours)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    try:
        conn.execute(
            '''INSERT INTO ad_unlock_grants
               (public_user_id, expires_at, ad_id, watch_seconds, ip_address, user_agent, provider)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (public_user_id, expires_at, ad_id or None, int(watch_seconds),
             ip_address or None, user_agent or None, provider_tag),
        )
        conn.commit()
    finally:
        conn.close()
    return expires_at

def count_recent_ad_unlocks_by_ip(ip_address: str, hours: int = 1) -> int:
    if not ip_address:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = get_db()
        row = conn.execute(
            'SELECT COUNT(*) AS c FROM ad_unlock_grants WHERE ip_address = ? AND granted_at >= ?',
            (ip_address, cutoff),
        ).fetchone()
        conn.close()
    except sqlite3.OperationalError:
        return 0
    return int(row['c'] or 0) if row else 0

# ---------------------------------------------------------------------------
# Preview counting (unchanged)
# ---------------------------------------------------------------------------

def get_preview_counts(viewer_type: str, viewer_id: str) -> dict[str, int]:
    conn = _page_views_conn()
    try:
        now = datetime.now(timezone.utc)
        day_ago = (now - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        day_count = conn.execute(
            'SELECT COUNT(*) AS c FROM preview_views WHERE viewer_type = ? AND viewer_id = ? AND viewed_at > ?',
            (viewer_type, viewer_id, day_ago),
        ).fetchone()['c']
        week_count = conn.execute(
            'SELECT COUNT(*) AS c FROM preview_views WHERE viewer_type = ? AND viewer_id = ? AND viewed_at > ?',
            (viewer_type, viewer_id, week_ago),
        ).fetchone()['c']
        return {'day': day_count, 'week': week_count}
    finally:
        conn.close()

def record_preview_view(resource_type: str = 'incident', resource_id: int | None = None) -> dict[str, int]:
    viewer_type, viewer_id = _viewer_identity()
    if viewer_type == 'admin':
        return {'day': 0, 'week': 0}
    conn = _page_views_conn()
    try:
        conn.execute(
            'INSERT INTO preview_views (viewer_type, viewer_id, resource_type, resource_id) VALUES (?, ?, ?, ?)',
            (viewer_type, viewer_id, resource_type, resource_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_preview_counts(viewer_type, viewer_id)

def check_preview_eligibility() -> tuple[bool, dict[str, int]]:
    viewer_type, viewer_id = _viewer_identity()
    if viewer_type == 'admin':
        return True, {'day': 0, 'week': 0}
    counts = get_preview_counts(viewer_type, viewer_id)
    allowed = counts['day'] < PREVIEW_LIMITS['day'] and counts['week'] < PREVIEW_LIMITS['week']
    return allowed, counts

def preview_allowed(resource_type: str = 'incident', resource_id: int | None = None) -> tuple[bool, dict[str, int]]:
    if user_has_access('plus'):
        return True, {'day': 0, 'week': 0}
    allowed, counts = check_preview_eligibility()
    if allowed:
        counts = record_preview_view(resource_type, resource_id)
        allowed = counts['day'] <= PREVIEW_LIMITS['day'] and counts['week'] <= PREVIEW_LIMITS['week']
    return allowed, counts

# ---------------------------------------------------------------------------
# Decorator / helper for routes
# ---------------------------------------------------------------------------

def gated_detail(min_plan: str = 'plus'):
    def wrapper(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            if user_has_access(min_plan):
                return fn(*args, **kwargs)
            allowed, counts = preview_allowed()
            if allowed:
                return fn(*args, **kwargs)
            g._paywall_blocked = True
            g._paywall_counts = counts
            g._paywall_min_plan = min_plan
            return fn(*args, **kwargs)
        return decorated
    return wrapper

def require_subscription(min_plan: str = 'plus', json=False):
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
