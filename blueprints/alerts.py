"""Alert subscription routes for county-targeted immediate alerts."""
from __future__ import annotations

import json
import secrets
from html import escape

from flask import Blueprint, abort, jsonify, render_template, request, url_for

from db import get_db


alerts_bp = Blueprint('alerts', __name__)


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _normalize_email(email: str) -> str:
    return (email or '').strip().lower()


def _valid_county(conn, county: str) -> bool:
    row = conn.execute(
        '''
        SELECT 1 FROM (
            SELECT DISTINCT county AS county FROM posts WHERE county IS NOT NULL AND county != ''
            UNION
            SELECT DISTINCT county AS county FROM records WHERE county IS NOT NULL AND county != ''
        ) WHERE county = ?
        ''',
        (county,),
    ).fetchone()
    return row is not None


@alerts_bp.route('/alerts/subscribe', methods=['POST'])
def alert_subscribe():
    """Subscribe to immediate alerts for a county.

    Payload: {email, county, alert_types?}
    """
    payload = request.get_json(silent=True) or {}
    email = _normalize_email(payload.get('email'))
    county = (payload.get('county') or '').strip()
    alert_types = payload.get('alert_types') or ['all']

    if not email or '@' not in email:
        abort(400, description='Valid email is required.')
    if not county:
        abort(400, description='County is required.')

    conn = get_db()
    if not _valid_county(conn, county):
        conn.close()
        abort(400, description='Invalid county.')

    token = _generate_token()
    alert_types_json = json.dumps(alert_types if isinstance(alert_types, list) else [alert_types])

    conn.execute(
        '''
        INSERT INTO alert_subscriptions (email, county, alert_types, token, active, verified)
        VALUES (?, ?, ?, ?, 1, 0)
        ON CONFLICT(email, county) DO UPDATE SET
            alert_types = excluded.alert_types,
            active = 1,
            token = excluded.token,
            updated_at = datetime('now')
        ''',
        (email, county, alert_types_json, token),
    )
    conn.commit()
    conn.close()

    # TODO: send verification email
    verify_url = url_for('alerts.alert_verify', token=token, _external=True)

    return jsonify({
        'success': True,
        'message': 'Subscription created. Please check your email to verify.',
        'verify_url': verify_url,
        'token': token,
    }), 201


@alerts_bp.route('/alerts/verify/<token>', methods=['GET'])
def alert_verify(token: str):
    """Verify an alert subscription via token link."""
    conn = get_db()
    cur = conn.execute(
        'UPDATE alert_subscriptions SET verified = 1, updated_at = datetime("now") WHERE token = ?',
        (token,),
    )
    conn.commit()
    updated = cur.rowcount
    conn.close()

    if not updated:
        abort(404, description='Invalid or expired verification token.')

    return render_template('alerts/verified.html')


@alerts_bp.route('/alerts/unsubscribe/<token>', methods=['GET', 'POST'])
def alert_unsubscribe(token: str):
    """Unsubscribe from alerts using the token."""
    conn = get_db()
    cur = conn.execute(
        'UPDATE alert_subscriptions SET active = 0, updated_at = datetime("now") WHERE token = ?',
        (token,),
    )
    conn.commit()
    updated = cur.rowcount
    conn.close()

    if not updated:
        abort(404, description='Invalid or expired unsubscribe token.')

    return render_template('alerts/unsubscribed.html')


@alerts_bp.route('/alerts/subscriptions/<email>')
def alert_list_subscriptions(email: str):
    """List active alert subscriptions for an email (token-protected via query param)."""
    token = (request.args.get('token') or '').strip()
    if not token:
        abort(401, description='Token required.')

    conn = get_db()
    row = conn.execute(
        'SELECT 1 FROM alert_subscriptions WHERE email = ? AND token = ? LIMIT 1',
        (email.lower(), token),
    ).fetchone()
    if not row:
        conn.close()
        abort(403, description='Invalid token for this email.')

    subs = conn.execute(
        '''
        SELECT id, county, alert_types, active, verified, created_at
        FROM alert_subscriptions
        WHERE email = ?
        ORDER BY county
        ''',
        (email.lower(),),
    ).fetchall()
    conn.close()

    return jsonify({
        'email': email.lower(),
        'subscriptions': [
            {
                'id': r['id'],
                'county': r['county'],
                'alert_types': json.loads(r['alert_types']) if r['alert_types'] else ['all'],
                'active': bool(r['active']),
                'verified': bool(r['verified']),
                'created_at': r['created_at'],
            }
            for r in subs
        ],
    })
