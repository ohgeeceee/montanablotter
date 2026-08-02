"""LEA API authentication middleware.

Decorator-based: `@require_lea_api_token` validates Authorization: Bearer <token>
against the lea_api_tokens table (SHA256 hash lookup), setting g.lea_agency_id
and g.lea_user_id on success.
"""
import functools
import logging
import sqlite3

from flask import g, jsonify, request

import config
from services.lea_auth.api_tokens import hash_token

logger = logging.getLogger(__name__)


def require_lea_api_token(f):
    """Decorator that requires a valid LEA API token.

    Extracts the Bearer token from the Authorization header, SHA256-hashes it,
    looks it up in lea_api_tokens, and sets ``g.lea_agency_id``,
    ``g.lea_user_id`` for downstream use.

    Returns 401 JSON on failure.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')

        if not auth_header.startswith('Bearer '):
            return jsonify({
                'error': 'Missing or malformed Authorization header',
                'code': 'MISSING_AUTHORIZATION',
            }), 401

        raw_token = auth_header[7:]  # Strip "Bearer "
        if not raw_token:
            return jsonify({
                'error': 'Empty token',
                'code': 'EMPTY_TOKEN',
            }), 401

        token_hash = hash_token(raw_token)

        try:
            conn = sqlite3.connect(config.DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT id, agency_id, user_id, is_revoked, expires_at
                   FROM lea_api_tokens
                   WHERE token_hash = ?""",
                (token_hash,),
            ).fetchone()
            conn.close()
        except Exception as exc:
            logger.error("lea_api token lookup failed: %s", exc)
            return jsonify({
                'error': 'Internal authentication error',
                'code': 'AUTH_ERROR',
            }), 500

        if row is None:
            return jsonify({
                'error': 'Invalid token',
                'code': 'INVALID_TOKEN',
            }), 401

        if row['is_revoked']:
            return jsonify({
                'error': 'Token has been revoked',
                'code': 'TOKEN_REVOKED',
            }), 401

        # Check expiry
        if row['expires_at']:
            from datetime import datetime, timezone
            try:
                expiry = datetime.fromisoformat(row['expires_at'])
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expiry:
                    return jsonify({
                        'error': 'Token has expired',
                        'code': 'TOKEN_EXPIRED',
                    }), 401
            except (ValueError, TypeError):
                pass  # Malformed expiry — allow (fail soft)

        # Update last_used_at
        try:
            conn2 = sqlite3.connect(config.DB_PATH)
            conn2.execute(
                "UPDATE lea_api_tokens SET last_used_at = datetime('now') WHERE id = ?",
                (row['id'],),
            )
            conn2.commit()
            conn2.close()
        except Exception:
            pass  # Non-critical — don't break the request

        g.lea_agency_id = row['agency_id']
        g.lea_user_id = row['user_id']
        return f(*args, **kwargs)

    return decorated
