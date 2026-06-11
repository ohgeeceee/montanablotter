"""
Ad-watched warrant unlock — free-for-attention path for /wanted access.

Flow:
  1. GET  /ad/watch                  — login required, issues a single-use
                                         session nonce + renders the player.
  2. POST /api/ad-unlock/complete    — validates the nonce + watch seconds
                                         and records a time-bounded grant in
                                         ad_unlock_grants (see init_db.py).

The paywall check (services.monetization.paywall.user_has_warrant_access)
treats an active grant as equivalent to a paid warrant_access subscription.
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

import config
from services.monetization.paywall import (
    count_recent_ad_unlocks_by_ip,
    get_ad_unlock_remaining_seconds,
    record_ad_unlock,
)

log = logging.getLogger(__name__)


warrant_unlock_bp = Blueprint('warrant_unlock', __name__)


def register_warrant_unlock_blueprint(app):
    """Register the warrant-unlock blueprint onto the Flask app."""
    app.register_blueprint(warrant_unlock_bp)


def _client_ip() -> str:
    """Best-effort client IP. Prefers X-Forwarded-For when present."""
    fwd = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    if fwd:
        return fwd
    return (request.remote_addr or '').strip()


def _issue_nonce() -> tuple[str, float]:
    """Issue a single-use nonce + the page-load timestamp; return (nonce, issued_at)."""
    nonce = secrets.token_urlsafe(24)
    issued_at = time.time()
    session['_ad_unlock_nonce'] = nonce
    session['_ad_unlock_nonce_issued_at'] = issued_at
    return nonce, issued_at


@warrant_unlock_bp.route('/ad/watch', methods=['GET'])
def ad_watch():
    """Render the ad-player page. Login required."""
    public_user_id = session.get('public_user_id')
    if not public_user_id:
        flash('Please log in or create an account to watch an ad.', 'info')
        return redirect(url_for('auth.public_login', next='/ad/watch'))

    video_id = (getattr(config, 'WARRANT_UNLOCK_YOUTUBE_VIDEO_ID', '') or '').strip()
    monetag_zone_id = (getattr(config, 'WARRANT_UNLOCK_MONETAG_ZONE_ID', '') or '').strip()
    # Provider priority: Monetag (real ad revenue) > YouTube (no revenue).
    provider = 'monetag' if monetag_zone_id else 'youtube'
    ad_id = monetag_zone_id or video_id
    min_watch_seconds = int(getattr(config, 'WARRANT_UNLOCK_MIN_WATCH_SECONDS', 15))
    duration_hours = int(getattr(config, 'WARRANT_UNLOCK_DURATION_HOURS', 24))
    rate_limit = int(getattr(config, 'WARRANT_UNLOCK_RATE_LIMIT_PER_HOUR', 5))

    # Clear any consumed nonce from a previous successful claim so the user
    # can re-watch and stack a new grant.
    session.pop('_ad_unlock_nonce', None)
    session.pop('_ad_unlock_nonce_issued_at', None)
    nonce, issued_at = _issue_nonce()

    remaining_seconds = get_ad_unlock_remaining_seconds(int(public_user_id))
    remaining_human = _humanize_seconds(remaining_seconds)
    recent_count = count_recent_ad_unlocks_by_ip(_client_ip(), hours=1)
    rate_limited = recent_count >= rate_limit

    return render_template(
        'ad_watch.html',
        active_nav='wanted',
        page_title='Watch Ad to Unlock Warrants',
        meta_description='Watch a short ad to earn 24 hours of full Montana warrant access.',
        canonical_url=f'{_base_url()}/ad/watch',
        current_year=datetime.now().year,
        provider=provider,
        youtube_video_id=video_id,
        monetag_zone_id=monetag_zone_id,
        nonce=nonce,
        min_watch_seconds=min_watch_seconds,
        duration_hours=duration_hours,
        remaining_seconds=remaining_seconds,
        remaining_human=remaining_human,
        has_active_grant=remaining_seconds > 0,
        rate_limited=rate_limited,
        rate_limit=rate_limit,
        video_missing=not bool(ad_id),
    )


@warrant_unlock_bp.route('/api/ad-unlock/complete', methods=['POST'])
def ad_unlock_complete():
    """Validate nonce + watch seconds, then record a grant. Returns JSON."""
    public_user_id = session.get('public_user_id')
    if not public_user_id:
        return jsonify({'ok': False, 'error': 'login_required'}), 401

    video_id = (getattr(config, 'WARRANT_UNLOCK_YOUTUBE_VIDEO_ID', '') or '').strip()
    monetag_zone_id = (getattr(config, 'WARRANT_UNLOCK_MONETAG_ZONE_ID', '') or '').strip()
    if not (video_id or monetag_zone_id):
        return jsonify({'ok': False, 'error': 'config_missing_ad'}), 503

    min_watch_seconds = int(getattr(config, 'WARRANT_UNLOCK_MIN_WATCH_SECONDS', 15))
    duration_hours = int(getattr(config, 'WARRANT_UNLOCK_DURATION_HOURS', 24))
    rate_limit = int(getattr(config, 'WARRANT_UNLOCK_RATE_LIMIT_PER_HOUR', 5))
    nonce_ttl = int(getattr(config, 'WARRANT_UNLOCK_NONCE_TTL_SECONDS', 600))

    payload = request.get_json(silent=True) or {}
    submitted_nonce = (payload.get('nonce') or '').strip()
    watch_seconds = payload.get('watch_seconds')
    submitted_provider = (payload.get('provider') or '').strip().lower()[:32]

    if not submitted_nonce or not isinstance(watch_seconds, (int, float)):
        return jsonify({'ok': False, 'error': 'bad_request'}), 400

    session_nonce = session.pop('_ad_unlock_nonce', None)
    issued_at = session.pop('_ad_unlock_nonce_issued_at', None)
    if not session_nonce or not issued_at:
        return jsonify({'ok': False, 'error': 'no_active_nonce'}), 400
    if not secrets.compare_digest(submitted_nonce, session_nonce):
        return jsonify({'ok': False, 'error': 'nonce_mismatch'}), 400
    if (time.time() - float(issued_at)) > nonce_ttl:
        return jsonify({'ok': False, 'error': 'nonce_expired'}), 400

    watch_int = int(watch_seconds)
    if watch_int < min_watch_seconds:
        return jsonify({
            'ok': False,
            'error': 'insufficient_watch',
            'min_watch_seconds': min_watch_seconds,
            'received_watch_seconds': watch_int,
        }), 400

    client_ip = _client_ip()
    if count_recent_ad_unlocks_by_ip(client_ip, hours=1) >= rate_limit:
        return jsonify({'ok': False, 'error': 'rate_limited'}), 429

    # Trust the client-declared provider only when its config is actually
    # present on the server; otherwise attribute to whatever the server has.
    if submitted_provider == 'monetag' and monetag_zone_id:
        provider_tag, ad_id = 'monetag', monetag_zone_id
    elif submitted_provider == 'youtube' and video_id:
        provider_tag, ad_id = 'youtube', video_id
    else:
        # Fall back to the server-side default (Monetag if configured, else YouTube).
        provider_tag = 'monetag' if monetag_zone_id else 'youtube'
        ad_id = monetag_zone_id or video_id

    try:
        expires_at = record_ad_unlock(
            public_user_id=int(public_user_id),
            watch_seconds=watch_int,
            ip_address=client_ip or None,
            user_agent=(request.headers.get('User-Agent') or '')[:512] or None,
            ad_id=ad_id,
            duration_hours=duration_hours,
            provider=provider_tag,
        )
    except Exception:
        log.exception('ad-unlock: failed to record grant for user %s', public_user_id)
        return jsonify({'ok': False, 'error': 'server_error'}), 500

    log.info(
        'ad-unlock: granted %sh to user %s (provider=%s, ip=%s, watch=%ds)',
        duration_hours, public_user_id, provider_tag, client_ip, watch_int,
    )
    return jsonify({
        'ok': True,
        'expires_at': expires_at,
        'duration_hours': duration_hours,
        'provider': provider_tag,
        'remaining_seconds': get_ad_unlock_remaining_seconds(int(public_user_id)),
    })


def _base_url() -> str:
    """Best-effort base URL for canonical/redirect use."""
    base = (getattr(config, 'BASE_URL', '') or '').strip()
    if base:
        return base.rstrip('/')
    return request.host_url.rstrip('/')


def _humanize_seconds(total: int) -> str:
    """Format seconds as a short human string like '18h 24m' or '42m' or '12s'."""
    total = max(0, int(total))
    if total <= 0:
        return '0m'
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours >= 1 and minutes >= 1:
        return f'{hours}h {minutes}m'
    if hours >= 1:
        return f'{hours}h'
    if minutes >= 1:
        return f'{minutes}m'
    return f'{total}s'
