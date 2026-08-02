"""LEA REST API blueprint — /api/v1/lea/ prefix.

Endpoints:
  POST /auth/token          — exchange username+password for JWT
  POST /blotter/publish     — submit single incident draft
  POST /blotter/batch       — submit multiple incidents
  GET  /blotter/batch/<id>/status — check batch processing status
  POST /roster/sync         — submit jail roster snapshot
  GET  /audit               — retrieve agency audit log
"""
import json
import logging
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

import config
from services.lea_auth.user_auth import verify_password
from services.lea_auth.api_tokens import create_jwt
from services.api.lea_auth import require_lea_api_token

logger = logging.getLogger(__name__)

api_lea = Blueprint('api_lea', __name__, url_prefix='/api/v1/lea')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Open a connection to the main database."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _json_error(message: str, code: str, status: int = 400) -> tuple:
    """Return a standardised JSON error response."""
    return jsonify({'error': message, 'code': code}), status


def _log_audit(conn: sqlite3.Connection, agency_id: int, user_id: int | None,
               action: str, resource_type: str | None = None,
               resource_id: str | None = None, change_summary: str | None = None) -> None:
    """Insert a row into lea_audit_log."""
    conn.execute(
        """INSERT INTO lea_audit_log
           (agency_id, user_id, actor_ip, action, resource_type, resource_id, change_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (agency_id, user_id,
         request.headers.get('X-Forwarded-For', request.remote_addr or ''),
         action, resource_type, resource_id, change_summary),
    )
    conn.commit()


def _generate_batch_id() -> str:
    """Generate a unique batch identifier."""
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    suffix = secrets.token_hex(4)
    return f"batch_{ts}_{suffix}"


def _generate_sync_id() -> str:
    """Generate a unique sync identifier."""
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    suffix = secrets.token_hex(4)
    return f"sync_{ts}_{suffix}"


# ---------------------------------------------------------------------------
# CORS – apply to every response
# ---------------------------------------------------------------------------

@api_lea.after_request
def add_cors_headers(response):
    """Set CORS headers on all API responses."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Max-Age'] = '86400'
    return response


@api_lea.route('/auth/token', methods=['POST', 'OPTIONS'])
def token():
    """Exchange username + password for a JWT access token.

    Request (JSON):
        {"grant_type": "password", "username": "...", "password": "..."}

    Response (200):
        {"access_token": "eyJ...", "token_type": "Bearer", "expires_in": 2592000}

    Errors: 400 (bad request), 401 (invalid credentials)
    """
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}
    grant_type = (data.get('grant_type') or '').lower()

    if grant_type != 'password':
        return _json_error('Unsupported grant_type. Only "password" is supported.',
                           'UNSUPPORTED_GRANT_TYPE', 400)

    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return _json_error('username and password are required.',
                           'MISSING_CREDENTIALS', 400)

    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT id, agency_id, password_hash, is_active
               FROM lea_users WHERE username = ?""",
            (username,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return _json_error('invalid_credentials', 'INVALID_CREDENTIALS', 401)

    if not row['is_active']:
        return _json_error('ACCOUNT_DISABLED', 'ACCOUNT_DISABLED', 401)

    if not verify_password(password, row['password_hash']):
        return _json_error('invalid_credentials', 'INVALID_CREDENTIALS', 401)

    user_id = row['id']
    agency_id = row['agency_id']

    jwt_secret = getattr(config, 'LEA_JWT_SECRET', config.SECRET_KEY)
    expiry_hours = 720  # 30 days

    access_token = create_jwt(
        payload={
            'sub': str(user_id),
            'agency_id': agency_id,
            'user_id': user_id,
            'scopes': ['blotter.publish', 'blotter.batch', 'roster.sync', 'audit.read'],
        },
        secret=jwt_secret,
        expiry_hours=expiry_hours,
    )

    return jsonify({
        'access_token': access_token,
        'token_type': 'Bearer',
        'expires_in': expiry_hours * 3600,
    }), 200


# ---------------------------------------------------------------------------
# Blotter: Single Incident Publish
# ---------------------------------------------------------------------------

@api_lea.route('/blotter/publish', methods=['POST', 'OPTIONS'])
@require_lea_api_token
def publish_incident():
    """Submit a single incident for publication.

    Request (JSON):
        {
            "incident_date": "2026-08-02",
            "incident_time": "14:30",
            "cad_number": "2026-1234",
            "location": "300 BLK MAIN ST",
            "charges": ["45-5-202", "45-5-206"],
            "narrative": "Officers responded to..."
        }

    Required fields: incident_date, location
    """
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}

    # Validate required fields
    incident_date = (data.get('incident_date') or '').strip()
    location = (data.get('location') or '').strip()
    cad_number = (data.get('cad_number') or '').strip()
    incident_time = (data.get('incident_time') or '').strip()
    charges = data.get('charges') or []
    narrative = (data.get('narrative') or '').strip()

    if not incident_date:
        return _json_error('incident_date is required.', 'MISSING_INCIDENT_DATE', 400)
    if not location:
        return _json_error('location is required.', 'MISSING_LOCATION', 400)

    charges_json = json.dumps(charges) if charges else '[]'

    conn = _get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO lea_blotter_drafts
               (agency_id, submitted_by_user_id, incident_date, incident_time,
                cad_number, incident_location_block, public_narrative,
                charges_json, submission_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft')""",
            (g.lea_agency_id, g.lea_user_id,
             incident_date, incident_time or None,
             cad_number or None, location,
             narrative or None, charges_json),
        )
        draft_id = cursor.lastrowid

        _log_audit(conn, g.lea_agency_id, g.lea_user_id,
                   'api_blotter_publish', 'blotter_draft', str(draft_id),
                   f"Published incident {cad_number or 'N/A'} on {incident_date}")
    finally:
        conn.close()

    return jsonify({
        'draft_id': draft_id,
        'status': 'draft',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'review_url': f'/lea/submission/{draft_id}',
    }), 201


# ---------------------------------------------------------------------------
# Blotter: Batch Upload
# ---------------------------------------------------------------------------

@api_lea.route('/blotter/batch', methods=['POST', 'OPTIONS'])
@require_lea_api_token
def batch_publish():
    """Submit multiple incidents in one request.

    Request (JSON):
        {"incidents": [{...}, {...}]}

    Response (202):
        {
            "batch_id": "batch_20260802_143000_a1b2",
            "status": "processing",
            "records_queued": 15,
            "status_url": "/api/v1/lea/blotter/batch/batch_.../status"
        }
    """
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}
    incidents = data.get('incidents')

    if not isinstance(incidents, list) or len(incidents) == 0:
        return _json_error('incidents must be a non-empty array.',
                           'MISSING_INCIDENTS', 400)

    batch_id = _generate_batch_id()
    conn = _get_db()
    inserted = 0
    errors = []

    try:
        for idx, incident in enumerate(incidents):
            inc_date = (incident.get('incident_date') or '').strip()
            inc_location = (incident.get('location') or '').strip()

            if not inc_date or not inc_location:
                errors.append({'index': idx, 'error': 'missing incident_date or location'})
                continue

            inc_charges = json.dumps(incident.get('charges') or [])
            inc_narrative = (incident.get('narrative') or '').strip()
            inc_cad = (incident.get('cad_number') or '').strip()
            inc_time = (incident.get('incident_time') or '').strip()

            raw_json_payload = json.dumps({"batch_id": batch_id})

            conn.execute(
                """INSERT INTO lea_blotter_drafts
                   (agency_id, submitted_by_user_id, incident_date, incident_time,
                    cad_number, incident_location_block, public_narrative,
                    charges_json, submission_status, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'batch_pending', ?)""",
                (g.lea_agency_id, g.lea_user_id,
                 inc_date, inc_time or None,
                 inc_cad or None, inc_location,
                 inc_narrative or None, inc_charges,
                 raw_json_payload),
            )
            inserted += 1

        _log_audit(conn, g.lea_agency_id, g.lea_user_id,
                   'api_batch_submit', 'batch', batch_id,
                   json.dumps({'total': len(incidents), 'inserted': inserted,
                               'errors': len(errors)}))
    finally:
        conn.close()

    return jsonify({
        'batch_id': batch_id,
        'status': 'processing',
        'records_queued': inserted,
        'status_url': f'/api/v1/lea/blotter/batch/{batch_id}/status',
    }), 202


# ---------------------------------------------------------------------------
# Blotter: Batch Status
# ---------------------------------------------------------------------------

@api_lea.route('/blotter/batch/<batch_id>/status', methods=['GET', 'OPTIONS'])
@require_lea_api_token
def batch_status(batch_id: str):
    """Return processing status for a batch submission."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    # Look up drafts tagged with this batch_id via raw_json
    batch_pattern = f'%{batch_id}%'
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT submission_status, COUNT(*) as cnt
               FROM lea_blotter_drafts
               WHERE agency_id = ? AND raw_json LIKE ?
               GROUP BY submission_status""",
            (g.lea_agency_id, batch_pattern),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return _json_error(f'Batch {batch_id} not found.', 'BATCH_NOT_FOUND', 404)

    total = sum(r['cnt'] for r in rows)
    status_map = {r['submission_status']: r['cnt'] for r in rows}

    return jsonify({
        'batch_id': batch_id,
        'total': total,
        'status': status_map,
    }), 200


# ---------------------------------------------------------------------------
# Roster Sync
# ---------------------------------------------------------------------------

@api_lea.route('/roster/sync', methods=['POST', 'OPTIONS'])
@require_lea_api_token
def sync_roster():
    """Submit a jail roster snapshot.

    Request (JSON):
        {
            "sync_type": "full" | "incremental",
            "facility_name": "Test County Detention",
            "snapshot_date": "2026-08-02T14:30:00Z",
            "inmates": [{...}]
        }

    Response (202):
        {
            "sync_id": "sync_...",
            "status": "processing",
            "records_received": 45,
            "status_url": "..."
        }
    """
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    data = request.get_json(silent=True) or {}

    sync_type = (data.get('sync_type') or 'incremental').strip()
    facility_name = (data.get('facility_name') or '').strip()
    snapshot_date = (data.get('snapshot_date') or '').strip()
    inmates = data.get('inmates') or []

    if not facility_name:
        return _json_error('facility_name is required.', 'MISSING_FACILITY_NAME', 400)
    if not snapshot_date:
        return _json_error('snapshot_date is required.', 'MISSING_SNAPSHOT_DATE', 400)
    if not isinstance(inmates, list) or len(inmates) == 0:
        return _json_error('inmates must be a non-empty array.', 'MISSING_INMATES', 400)

    if sync_type not in ('full', 'incremental'):
        sync_type = 'incremental'

    sync_id = _generate_sync_id()
    roster_json = json.dumps({
        'sync_type': sync_type,
        'facility_name': facility_name,
        'snapshot_date': snapshot_date,
        'inmates': inmates,
        'sync_id': sync_id,
    })

    conn = _get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO lea_roster_snapshots
               (agency_id, submitted_by_user_id, snapshot_date, sync_type,
                roster_json, total_inmates, ingestion_status)
               VALUES (?, ?, ?, ?, ?, ?, 'staged')""",
            (g.lea_agency_id, g.lea_user_id,
             snapshot_date, sync_type,
             roster_json, len(inmates)),
        )
        snapshot_id = cursor.lastrowid

        _log_audit(conn, g.lea_agency_id, g.lea_user_id,
                   'api_roster_sync', 'roster_snapshot', str(snapshot_id),
                   json.dumps({'sync_id': sync_id, 'sync_type': sync_type,
                               'inmates': len(inmates), 'facility': facility_name}))
    finally:
        conn.close()

    return jsonify({
        'sync_id': sync_id,
        'status': 'processing',
        'records_received': len(inmates),
        'status_url': f'/api/v1/lea/roster/sync/{sync_id}/status',
    }), 202


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@api_lea.route('/audit', methods=['GET', 'OPTIONS'])
@require_lea_api_token
def audit_log():
    """Retrieve the agency's own audit log entries.

    Query params (all optional):
        action  — filter by action type (e.g. "api_blotter_publish")
        days    — only entries within the last N days (default 90)

    Response (200):
        {"entries": [{...}], "total": N}
    """
    if request.method == 'OPTIONS':
        return jsonify({}), 200

    action_filter = (request.args.get('action') or '').strip()
    days_str = (request.args.get('days') or '').strip()

    try:
        days = max(1, min(365, int(days_str)))
    except (ValueError, TypeError):
        days = 90

    conn = _get_db()
    try:
        query = """SELECT id, agency_id, user_id, actor_ip, action,
                          resource_type, resource_id, change_summary, timestamp
                   FROM lea_audit_log
                   WHERE agency_id = ?
                     AND timestamp >= datetime('now', ? || ' days')"""
        params: list = [g.lea_agency_id, f'-{days}']

        if action_filter:
            query += ' AND action = ?'
            params.append(action_filter)

        query += ' ORDER BY timestamp DESC LIMIT 500'

        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    entries = [dict(r) for r in rows]
    # Convert timestamps to ISO strings
    for entry in entries:
        if isinstance(entry.get('timestamp'), str):
            # Already a string from SQLite
            pass

    return jsonify({
        'entries': entries,
        'total': len(entries),
    }), 200


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_api_lea(app) -> None:
    """Register the LEA API blueprint onto the Flask app."""
    app.register_blueprint(api_lea)
