"""
Self-serve API key management for Pro subscribers.

Pro subscribers can generate, list, and revoke API keys from their
account page. The keys use the existing api_clients table and grant
'pro' tier rate limits (1000 req/day).
"""

from __future__ import annotations

import json
from datetime import datetime

from flask import (
    Blueprint,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from db import get_db
from init_db import ensure_api_auth_schema
from services.api.auth import (
    create_client,
    list_clients,
    revoke_client,
    ApiClient,
)

api_keys_bp = Blueprint('api_keys', __name__, url_prefix='/account')


# ---------------------------------------------------------------------------
# Require authenticated public user + Pro subscription
# ---------------------------------------------------------------------------

def _require_pro_user():
    """Return public_user_id or redirect to login."""
    public_user_id = session.get('public_user_id')
    if not public_user_id:
        return None, redirect(url_for('auth.public_register', next='/account/api-keys'))
    conn = get_db()
    user = conn.execute(
        'SELECT id, subscriber_plan, is_subscribed FROM public_users WHERE id = ? AND is_active = 1',
        (int(public_user_id),),
    ).fetchone()
    conn.close()
    if not user:
        return None, redirect(url_for('auth.register'))
    if not user['is_subscribed'] or user['subscriber_plan'] != 'pro':
        return None, redirect('/pricing?required=pro')
    return int(public_user_id), None


# ---------------------------------------------------------------------------
# Page: list API keys
# ---------------------------------------------------------------------------

@api_keys_bp.route('/api-keys')
def api_keys_page():
    user_id, redirect_resp = _require_pro_user()
    if redirect_resp:
        return redirect_resp

    conn = get_db()
    ensure_api_auth_schema(conn)

    try:
        # Get all keys associated with this user_id via the name field
        keys = conn.execute('''
            SELECT id, name, tier, key_hash, is_active, created_at, revoked_at
            FROM api_clients
            WHERE name LIKE ? AND is_active = 1
            ORDER BY created_at DESC
        ''', (f'pro_user_{user_id}_%',)).fetchall()
    finally:
        conn.close()

    return render_template(
        'api_keys.html',
        keys=[dict(r) for r in keys],
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# API: create a new key
# ---------------------------------------------------------------------------

@api_keys_bp.route('/api-keys/create', methods=['POST'])
def create_api_key():
    user_id, redirect_resp = _require_pro_user()
    if redirect_resp:
        return redirect_resp

    label = (request.form.get('label') or '').strip()[:50]
    if not label:
        return jsonify({'error': 'Label is required.'}), 400

    conn = get_db()
    ensure_api_auth_schema(conn)

    try:
        # Count existing active keys for this user
        count = conn.execute(
            'SELECT COUNT(*) FROM api_clients WHERE name LIKE ? AND is_active = 1',
            (f'pro_user_{user_id}_%',),
        ).fetchone()[0]
        if count >= 5:
            return jsonify({'error': 'Maximum 5 active API keys.'}), 400

        plaintext, client_id = create_client(
            conn,
            name=f'pro_user_{user_id}_{label}',
            tier='pro',
        )
    finally:
        conn.close()

    return jsonify({'plaintext_key': plaintext, 'client_id': client_id})


# ---------------------------------------------------------------------------
# API: revoke a key
# ---------------------------------------------------------------------------

@api_keys_bp.route('/api-keys/<int:key_id>/revoke', methods=['POST'])
def revoke_api_key(key_id):
    user_id, redirect_resp = _require_pro_user()
    if redirect_resp:
        return redirect_resp

    conn = get_db()
    ensure_api_auth_schema(conn)

    try:
        key = conn.execute(
            'SELECT id, name FROM api_clients WHERE id = ?',
            (key_id,),
        ).fetchone()
        if not key or not key['name'].startswith(f'pro_user_{user_id}_'):
            return jsonify({'error': 'Key not found.'}), 404

        revoke_client(conn, key_id)
    finally:
        conn.close()

    return jsonify({'revoked': True})