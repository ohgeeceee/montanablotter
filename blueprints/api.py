from __future__ import annotations

import datetime
import hmac
import hashlib
import json
import os
import secrets
import sqlite3

import stripe
from flask import Blueprint, abort, current_app, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from db import get_db
from blueprints.payments import (
    build_donation_checkout_payload,
    create_donation_checkout_session,
    persist_donation_checkout,
)
from services.api.auth import require_api_key, validate_request
import services.mobile_auth as mobile_auth
from services.disposition.lookup import lookup_disposition
from services.push.expo_sender import (
    broadcast_expo_push,
    deactivate_mobile_push_token,
    register_mobile_push_token,
)

import functools


# ---------------------------------------------------------------------------
# Server-side response cache helper
# ---------------------------------------------------------------------------

def _api_cache():
    """Return the Flask-Caching instance attached to the app, if any."""
    app_module = _app()
    return getattr(app_module, 'api_cache', None)


def cached_api(timeout=300):
    """Lightweight response cache keyed by request path + query string."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            cache = _api_cache()
            if cache is None or request.method != 'GET':
                return view(*args, **kwargs)
            cache_key = f"api:{request.full_path}"
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
            response = view(*args, **kwargs)
            cache.set(cache_key, response, timeout=timeout)
            return response
        return wrapper
    return decorator


api_bp = Blueprint('api', __name__)

# Geo/map endpoints are first-party UI calls — give them a generous anonymous
# quota so Crime Atlas and Crime Map don't hit the default 100 req/day limit.
_GEO_QUOTA = {"window_seconds": 86400, "max_requests": 2000}


def register_api_blueprint(app):
    """Register the api blueprint onto the Flask app."""
    app.register_blueprint(api_bp)

@api_bp.before_request
def enforce_public_user_csrf():
    """
    CSRF protection for cookie-authenticated "me" endpoints.

    These endpoints rely on the Flask session (`public_user_id`). If a request is
    authenticated via an API key, CSRF does not apply.
    """
    from flask import session

    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if not request.path.startswith("/api/me/"):
        return None
    if not session.get("public_user_id"):
        return None

    # If an API key is present and valid, treat it as non-cookie auth.
    try:
        conn = get_db()
        if validate_request(conn) is not None:
            conn.close()
            return None
        conn.close()
    except Exception:
        # Fail closed to avoid bypassing CSRF due to auth lookup issues.
        return jsonify({"error": "csrf_validation_failed"}), 400

    session_token = session.get("_csrf_token")
    submitted_token = (request.headers.get("X-CSRF-Token") or "").strip()
    if not session_token or not submitted_token or not hmac.compare_digest(session_token, submitted_token):
        return jsonify({"error": "csrf_validation_failed"}), 400
    return None


def _json_error_response(error):
    """Return a JSON error response for a Werkzeug HTTP exception."""
    response = jsonify({
        "error": error.name.replace(" ", "_").lower(),
        "message": error.description,
    })
    response.status_code = error.code
    return response


# Register JSON handlers for common HTTP status codes so that API routes return
# machine-readable errors instead of Flask's default HTML error pages.
for _code in (400, 401, 403, 404, 405, 429, 500, 503):
    api_bp.register_error_handler(_code, _json_error_response)


@api_bp.errorhandler(HTTPException)
def handle_api_http_exception(error):
    """Fallback JSON error handler for any remaining HTTP exceptions."""
    return _json_error_response(error)


# ---------------------------------------------------------------------------
# Private helpers (thin wrappers that delegate to app.py helpers)
# ---------------------------------------------------------------------------

def _app():
    import app as _app_module
    return _app_module


def _slugify(text: str) -> str:
    import re as _re
    text = (text or '').lower().strip()
    text = _re.sub(r'[^\w\s-]', '', text)
    text = _re.sub(r'[\s_-]+', '-', text)
    return text[:80]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _source_pdf_name(row):
    if not row:
        return None

    file_path = (row['file_path'] or '').strip() if 'file_path' in row.keys() else ''
    if file_path:
        return os.path.basename(file_path)

    filename = ''
    for key in ('blotter_filename', 'filename'):
        if key in row.keys() and (row[key] or '').strip():
            filename = row[key].strip()
            break
    return os.path.basename(filename) if filename else None


def _post_payload(row):
    return {
        'id': int(row['id']),
        'title': row['title'] or '',
        'summary': row['summary'] or '',
        'county': row['county'] or '',
        'agency_name': row['agency_name'] or '',
        'agency_type': row['agency_type'] or '',
        'incident_date': row['incident_date'] or '',
        'incident_type': row['incident_type'] or '',
        'created_at': row['created_at'] or '',
        'source_pdf_name': _source_pdf_name(row),
    }


def _blog_payload(row, *, include_body: bool = True):
    payload = {
        'id': int(row['id']),
        'title': row['title'] or '',
        'slug': row['slug'] or '',
        'excerpt': row['excerpt'] or '',
        'author': row['author'] or '',
        'created_at': row['created_at'] or '',
    }
    if include_body:
        payload['body'] = row['body'] or ''
    return payload


# ---------------------------------------------------------------------------
# Premium alert profiles
# ---------------------------------------------------------------------------

@api_bp.route('/api/me/alert-profiles', methods=['GET'])
@require_api_key(allow_anonymous=True)
def api_my_alert_profiles():
    user = _current_public_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM user_alert_profiles WHERE user_id=? AND is_active=1 ORDER BY created_at DESC',
        (user['id'],),
    ).fetchall()
    conn.close()
    return jsonify({'profiles': [_alert_profile_payload(r) for r in rows]})


@api_bp.route('/api/me/alert-profiles', methods=['POST'])
@require_api_key(allow_anonymous=True)
def api_create_alert_profile():
    user = _current_public_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    data = request.get_json(force=True, silent=True) or {}
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO user_alert_profiles (
            user_id, name, alert_types, counties, cities, neighborhoods,
            radius_miles, center_lat, center_lng, severity_threshold,
            frequency, delivery_channel, webhook_url, slack_channel, teams_webhook
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            user['id'],
            (data.get('name') or 'New Alert').strip()[:120],
            json.dumps(data.get('alert_types') or ['all']),
            json.dumps(data.get('counties')) if data.get('counties') else None,
            json.dumps(data.get('cities')) if data.get('cities') else None,
            json.dumps(data.get('neighborhoods')) if data.get('neighborhoods') else None,
            data.get('radius_miles'),
            data.get('center_lat'),
            data.get('center_lng'),
            (data.get('severity_threshold') or 'all').strip()[:20],
            (data.get('frequency') or 'immediate').strip()[:20],
            (data.get('delivery_channel') or 'email').strip()[:20],
            (data.get('webhook_url') or '').strip()[:500] or None,
            (data.get('slack_channel') or '').strip()[:120] or None,
            (data.get('teams_webhook') or '').strip()[:500] or None,
        ),
    )
    conn.commit()
    profile_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': profile_id, 'message': 'Profile created'}), 201


@api_bp.route('/api/me/alert-profiles/<int:profile_id>', methods=['PUT'])
@require_api_key(allow_anonymous=True)
def api_update_alert_profile(profile_id):
    user = _current_public_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    data = request.get_json(force=True, silent=True) or {}
    conn = get_db()
    existing = conn.execute(
        'SELECT id FROM user_alert_profiles WHERE id=? AND user_id=?',
        (profile_id, user['id']),
    ).fetchone()
    if not existing:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    fields = []
    params = []
    if 'name' in data:
        fields.append('name=?')
        params.append((data['name'] or 'New Alert').strip()[:120])
    if 'alert_types' in data:
        fields.append('alert_types=?')
        params.append(json.dumps(data['alert_types']))
    if 'counties' in data:
        fields.append('counties=?')
        params.append(json.dumps(data['counties']) if data['counties'] else None)
    if 'cities' in data:
        fields.append('cities=?')
        params.append(json.dumps(data['cities']) if data['cities'] else None)
    if 'neighborhoods' in data:
        fields.append('neighborhoods=?')
        params.append(json.dumps(data['neighborhoods']) if data['neighborhoods'] else None)
    if 'radius_miles' in data:
        fields.append('radius_miles=?')
        params.append(data['radius_miles'])
    if 'center_lat' in data:
        fields.append('center_lat=?')
        params.append(data['center_lat'])
    if 'center_lng' in data:
        fields.append('center_lng=?')
        params.append(data['center_lng'])
    if 'severity_threshold' in data:
        fields.append('severity_threshold=?')
        params.append(data['severity_threshold'].strip()[:20])
    if 'frequency' in data:
        fields.append('frequency=?')
        params.append(data['frequency'].strip()[:20])
    if 'delivery_channel' in data:
        fields.append('delivery_channel=?')
        params.append(data['delivery_channel'].strip()[:20])
    if 'webhook_url' in data:
        fields.append('webhook_url=?')
        params.append(data['webhook_url'].strip()[:500] or None)
    if 'slack_channel' in data:
        fields.append('slack_channel=?')
        params.append(data['slack_channel'].strip()[:120] or None)
    if 'teams_webhook' in data:
        fields.append('teams_webhook=?')
        params.append(data['teams_webhook'].strip()[:500] or None)
    if 'is_active' in data:
        fields.append('is_active=?')
        params.append(1 if data['is_active'] else 0)
    if not fields:
        conn.close()
        return jsonify({'message': 'No changes'}), 200
    params.append(profile_id)
    conn.execute(
        f"UPDATE user_alert_profiles SET {', '.join(fields)}, updated_at=datetime('now') WHERE id=?",
        tuple(params),
    )
    conn.commit()
    conn.close()
    return jsonify({'message': 'Profile updated'})


@api_bp.route('/api/me/alert-profiles/<int:profile_id>', methods=['DELETE'])
@require_api_key(allow_anonymous=True)
def api_delete_alert_profile(profile_id):
    user = _current_public_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    conn = get_db()
    conn.execute(
        'DELETE FROM user_alert_profiles WHERE id=? AND user_id=?',
        (profile_id, user['id']),
    )
    conn.commit()
    conn.close()
    return jsonify({'message': 'Profile deleted'})


@api_bp.route('/api/me/watchlist', methods=['GET'])
@require_api_key(allow_anonymous=True)
def api_get_watchlist():
    user = _current_public_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    conn = get_db()
    rows = conn.execute(
        '''
        SELECT p.*
        FROM posts p
        JOIN public_user_saved_posts s ON s.post_id = p.id
        WHERE s.public_user_id = ? AND p.audit_status = 'clean'
        ORDER BY s.created_at DESC
        ''',
        (user['id'],),
    ).fetchall()
    conn.close()
    return jsonify({'posts': [_post_payload(r) for r in rows]})


@api_bp.route('/api/me/watchlist', methods=['POST'])
@require_api_key(allow_anonymous=True)
def api_add_watchlist_item():
    user = _current_public_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    data = request.get_json(force=True, silent=True) or {}
    post_id = data.get('post_id')
    if not post_id:
        return jsonify({'error': 'post_id required'}), 400
    conn = get_db()
    conn.execute(
        '''
        INSERT OR IGNORE INTO public_user_saved_posts (public_user_id, post_id)
        VALUES (?, ?)
        ''',
        (user['id'], int(post_id)),
    )
    conn.commit()
    conn.close()
    return jsonify({'message': 'Saved'}), 201


@api_bp.route('/api/me/watchlist/<int:post_id>', methods=['DELETE'])
@require_api_key(allow_anonymous=True)
def api_remove_watchlist_item(post_id: int):
    user = _current_public_user()
    if not user:
        return jsonify({'error': 'Authentication required'}), 401
    conn = get_db()
    conn.execute(
        'DELETE FROM public_user_saved_posts WHERE public_user_id = ? AND post_id = ?',
        (user['id'], post_id),
    )
    conn.commit()
    conn.close()
    return jsonify({'message': 'Removed'})


def _alert_profile_payload(row):
    return {
        'id': row['id'],
        'name': row['name'],
        'alert_types': json.loads(row['alert_types']) if row['alert_types'] else ['all'],
        'counties': json.loads(row['counties']) if row['counties'] else None,
        'cities': json.loads(row['cities']) if row['cities'] else None,
        'neighborhoods': json.loads(row['neighborhoods']) if row['neighborhoods'] else None,
        'radius_miles': row['radius_miles'],
        'center_lat': row['center_lat'],
        'center_lng': row['center_lng'],
        'severity_threshold': row['severity_threshold'],
        'frequency': row['frequency'],
        'delivery_channel': row['delivery_channel'],
        'webhook_url': row['webhook_url'],
        'slack_channel': row['slack_channel'],
        'teams_webhook': row['teams_webhook'],
        'is_active': bool(row['is_active']),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def _current_public_user():
    """Return the currently authenticated public user from session or mobile token."""
    from flask import g, session

    cached = getattr(g, '_public_user', None)
    if cached is not None:
        return cached

    user_id = session.get('public_user_id')
    if user_id:
        conn = get_db()
        row = conn.execute('SELECT * FROM public_users WHERE id=? AND is_active=1', (user_id,)).fetchone()
        conn.close()
        user = dict(row) if row else None
        g._public_user = user
        return user

    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token_str = auth_header[7:].strip()
        conn = get_db()
        token = mobile_auth.get_token_by_plaintext(conn, token_str)
        if token:
            user = mobile_auth.load_public_user(conn, token.public_user_id)
            if user:
                if not getattr(g, '_mobile_token_touched', False):
                    mobile_auth.touch_mobile_user_token(conn, token.id)
                    g._mobile_token_touched = True
                g._public_user = user
                conn.close()
                return user
        conn.close()

    g._public_user = None
    return None


# ---------------------------------------------------------------------------
# Crime mapping geo-API
# ---------------------------------------------------------------------------

@api_bp.route('/api/geo/incidents')
@require_api_key(allow_anonymous=True, anonymous_quota=_GEO_QUOTA)
@cached_api(timeout=120)
def api_geo_incidents():
    bounds = request.args.get('bounds', '').strip()
    county = (request.args.get('county') or '').strip()[:60]
    city = (request.args.get('city') or '').strip()[:60]
    incident_type = (request.args.get('incident_type') or '').strip()[:60]
    date_from = (request.args.get('date_from') or '').strip()[:10]
    date_to = (request.args.get('date_to') or '').strip()[:10]
    limit = max(1, min(5000, request.args.get('limit', 500, type=int)))
    where = ['g.lat IS NOT NULL', 'g.lng IS NOT NULL']
    params = []
    if bounds:
        try:
            sw_lat, sw_lng, ne_lat, ne_lng = [float(x) for x in bounds.split(',')]
            where.append('g.lat BETWEEN ? AND ? AND g.lng BETWEEN ? AND ?')
            params.extend([sw_lat, ne_lat, sw_lng, ne_lng])
        except Exception:
            pass
    if county:
        where.append('g.county=?')
        params.append(county)
    if city:
        where.append('g.city=?')
        params.append(city)
    if incident_type:
        where.append('r.incident_type=?')
        params.append(incident_type)
    if date_from:
        where.append('r.date>=?')
        params.append(date_from)
    if date_to:
        where.append('r.date<=?')
        params.append(date_to)
    conn = get_db()
    sql = (
        'SELECT r.id, r.date, r.time, r.incident, r.incident_type, r.location, r.county, r.officer, '
        'g.lat, g.lng, g.neighborhood '
        'FROM incident_geocodes g JOIN records r ON r.id=g.record_id '
        f"WHERE {' AND '.join(where)} ORDER BY r.date DESC, r.time DESC LIMIT ?"
    )
    rows = conn.execute(sql, tuple(params + [limit])).fetchall()
    conn.close()
    return jsonify({'incidents': [dict(r) for r in rows]})


@api_bp.route('/api/geo/heatmap')
@require_api_key(allow_anonymous=True, anonymous_quota=_GEO_QUOTA)
def api_geo_heatmap():
    county = (request.args.get('county') or '').strip()[:60]
    date_from = (request.args.get('date_from') or '').strip()[:10]
    date_to = (request.args.get('date_to') or '').strip()[:10]
    where = ['g.lat IS NOT NULL', 'g.lng IS NOT NULL']
    params = []
    if county:
        where.append('g.county=?')
        params.append(county)
    if date_from:
        where.append('r.date>=?')
        params.append(date_from)
    if date_to:
        where.append('r.date<=?')
        params.append(date_to)
    conn = get_db()
    sql = (
        'SELECT g.lat, g.lng, COUNT(*) as weight FROM incident_geocodes g '
        'JOIN records r ON r.id=g.record_id '
        f"WHERE {' AND '.join(where)} GROUP BY ROUND(g.lat,3), ROUND(g.lng,3)"
    )
    rows = conn.execute(sql, tuple(params)).fetchall()
    conn.close()
    return jsonify({'points': [{'lat': r['lat'], 'lng': r['lng'], 'weight': r['weight']} for r in rows]})


# ---------------------------------------------------------------------------
# Safety scorecards
# ---------------------------------------------------------------------------

@api_bp.route('/api/scorecards/<area_type>/<area_slug>')
@require_api_key(allow_anonymous=True)
def api_scorecard(area_type, area_slug):
    period = (request.args.get('period') or '30d').strip()[:10]
    conn = get_db()
    row = conn.execute(
        'SELECT * FROM safety_scorecards WHERE area_type=? AND area_slug=? ORDER BY computed_at DESC LIMIT 1',
        (area_type, area_slug),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'No scorecard available'}), 404
    return jsonify({
        'area_type': row['area_type'],
        'area_slug': row['area_slug'],
        'area_name': row['area_name'],
        'county': row['county'],
        'population': row['population'],
        'score': row['score'],
        'percentile_state': row['percentile_state'],
        'percentile_national': row['percentile_national'],
        'methodology_version': row['methodology_version'],
        'metrics': json.loads(row['metrics_json']) if row['metrics_json'] else {},
        'trends': json.loads(row['trends_json']) if row['trends_json'] else {},
        'factors': json.loads(row['factors_json']) if row['factors_json'] else {},
        'period_start': row['period_start'],
        'period_end': row['period_end'],
        'computed_at': row['computed_at'],
    })


@api_bp.route('/api/scorecards')
@require_api_key(allow_anonymous=True)
def api_scorecards_list():
    area_type = (request.args.get('area_type') or '').strip()[:20]
    county = (request.args.get('county') or '').strip()[:60]
    conn = get_db()
    where = ['1=1']
    params = []
    if area_type:
        where.append('area_type=?')
        params.append(area_type)
    if county:
        where.append('county=?')
        params.append(county)
    rows = conn.execute(
        f"SELECT area_slug, area_name, county, score, percentile_state, computed_at FROM safety_scorecards WHERE {' AND '.join(where)} ORDER BY score DESC",
        tuple(params),
    ).fetchall()
    conn.close()
    return jsonify({'scorecards': [dict(r) for r in rows]})


@api_bp.route('/api/posts')
@require_api_key(allow_anonymous=True)
def api_posts():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = max(1, min(100, request.args.get('per_page', 20, type=int)))
    params = []
    where = ["COALESCE(audit_status, 'pending') = 'clean'"]

    ids = (request.args.get('ids') or '').strip()
    if ids:
        parsed_ids = []
        for raw_id in ids.split(','):
            try:
                parsed_ids.append(int(raw_id.strip()))
            except (TypeError, ValueError):
                continue
        if parsed_ids:
            placeholders = ','.join('?' for _ in parsed_ids)
            where.append(f'id IN ({placeholders})')
            params.extend(parsed_ids)
        else:
            where.append('0=1')

    county = (request.args.get('county') or '').strip()
    if county:
        where.append('county = ?')
        params.append(county)

    agency_type = (request.args.get('agency_type') or '').strip()
    if agency_type:
        where.append('agency_type = ?')
        params.append(agency_type)

    date_from = (request.args.get('date_from') or '').strip()
    if date_from:
        where.append('incident_date >= ?')
        params.append(date_from)

    date_to = (request.args.get('date_to') or '').strip()
    if date_to:
        where.append('incident_date <= ?')
        params.append(date_to)

    search = (request.args.get('search') or request.args.get('q') or '').strip()
    if search:
        term = f'%{search}%'
        where.append('(title LIKE ? OR summary LIKE ? OR agency_name LIKE ? OR county LIKE ?)')
        params.extend([term, term, term, term])

    where_sql = ' AND '.join(where)
    offset = (page - 1) * per_page
    conn = get_db()
    total = conn.execute(f'SELECT COUNT(*) FROM posts WHERE {where_sql}', params).fetchone()[0]
    rows = conn.execute(
        f'''
        SELECT posts.id, posts.title, posts.summary, posts.county, posts.agency_name,
               posts.agency_type, posts.incident_date, posts.incident_type, posts.created_at,
               blotters.file_path AS file_path, blotters.filename AS blotter_filename
        FROM posts
        LEFT JOIN blotters ON blotters.id = posts.blotter_id
        WHERE {where_sql}
        ORDER BY incident_date DESC, created_at DESC, posts.id DESC
        LIMIT ? OFFSET ?
        ''',
        [*params, per_page, offset],
    ).fetchall()
    conn.close()
    return jsonify({
        'posts': [_post_payload(row) for row in rows],
        'total': int(total),
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (int(total) + per_page - 1) // per_page),
    })


@api_bp.route('/api/posts/<int:post_id>')
@require_api_key(allow_anonymous=True)
def api_post_detail(post_id: int):
    conn = get_db()
    row = conn.execute(
        '''
        SELECT posts.id, posts.title, posts.summary, posts.county, posts.agency_name,
               posts.agency_type, posts.incident_date, posts.incident_type, posts.created_at,
               blotters.file_path AS file_path, blotters.filename AS blotter_filename
        FROM posts
        LEFT JOIN blotters ON blotters.id = posts.blotter_id
        WHERE posts.id = ?
          AND COALESCE(posts.audit_status, 'pending') = 'clean'
        LIMIT 1
        ''',
        (post_id,),
    ).fetchone()
    conn.close()
    if not row:
        abort(404)
    return jsonify(_post_payload(row))


@api_bp.route('/api/counties')
@require_api_key(allow_anonymous=True)
def api_counties():
    conn = get_db()
    rows = conn.execute(
        '''
        SELECT p.county AS county,
               COUNT(DISTINCT p.id) AS post_count,
               COUNT(DISTINCT r.id) AS record_count
        FROM posts p
        LEFT JOIN records r ON r.blotter_id = p.blotter_id
        WHERE p.county IS NOT NULL
          AND p.county != ''
          AND COALESCE(p.audit_status, 'pending') = 'clean'
        GROUP BY p.county
        ORDER BY p.county
        '''
    ).fetchall()
    conn.close()
    return jsonify({
        'counties': [
            {
                'county': row['county'],
                'post_count': int(row['post_count'] or 0),
                'record_count': int(row['record_count'] or 0),
            }
            for row in rows
        ]
    })


@api_bp.route('/api/agencies')
@require_api_key(allow_anonymous=True)
def api_agencies():
    conn = get_db()
    rows = conn.execute(
        '''
        SELECT agency_name, agency_type, MAX(county) AS county, COUNT(*) AS post_count
        FROM posts
        WHERE agency_name IS NOT NULL
          AND agency_name != ''
          AND COALESCE(audit_status, 'pending') = 'clean'
        GROUP BY agency_name, agency_type
        ORDER BY agency_name
        '''
    ).fetchall()
    conn.close()
    return jsonify({
        'agencies': [
            {
                'agency_name': row['agency_name'] or '',
                'agency_type': row['agency_type'] or '',
                'county': row['county'] or '',
                'post_count': int(row['post_count'] or 0),
            }
            for row in rows
        ]
    })


@api_bp.route('/api/stats')
@require_api_key(allow_anonymous=True)
def api_stats():
    conn = get_db()
    totals = conn.execute(
        '''
        SELECT
            (SELECT COUNT(*) FROM posts WHERE COALESCE(audit_status, 'pending') = 'clean') AS total_posts,
            (SELECT COUNT(DISTINCT county) FROM posts WHERE county IS NOT NULL AND county != '' AND COALESCE(audit_status, 'pending') = 'clean') AS total_counties,
            (SELECT COUNT(DISTINCT agency_name) FROM posts WHERE agency_name IS NOT NULL AND agency_name != '' AND COALESCE(audit_status, 'pending') = 'clean') AS total_agencies,
            (SELECT COUNT(*) FROM blotters) AS total_blotters
        '''
    ).fetchone()
    total_records = conn.execute(
        '''
        SELECT COUNT(DISTINCT r.id)
        FROM records r
        JOIN posts p ON p.blotter_id = r.blotter_id
        WHERE COALESCE(p.audit_status, 'pending') = 'clean'
        '''
    ).fetchone()[0]
    latest_blotter = conn.execute(
        'SELECT county, upload_date FROM blotters ORDER BY upload_date DESC, id DESC LIMIT 1'
    ).fetchone()
    date_range = conn.execute(
        '''
        SELECT MIN(incident_date) AS earliest, MAX(incident_date) AS latest
        FROM posts
        WHERE COALESCE(audit_status, 'pending') = 'clean'
        '''
    ).fetchone()
    top_counties = conn.execute(
        '''
        SELECT county, COUNT(*) AS count
        FROM posts
        WHERE county IS NOT NULL
          AND county != ''
          AND COALESCE(audit_status, 'pending') = 'clean'
        GROUP BY county
        ORDER BY count DESC, county ASC
        LIMIT 10
        '''
    ).fetchall()
    top_incident_types = conn.execute(
        '''
        SELECT COALESCE(NULLIF(incident_type, ''), 'Incident') AS incident_type,
               COUNT(*) AS count
        FROM posts
        WHERE COALESCE(audit_status, 'pending') = 'clean'
        GROUP BY COALESCE(NULLIF(incident_type, ''), 'Incident')
        ORDER BY count DESC, incident_type ASC
        LIMIT 10
        '''
    ).fetchall()
    conn.close()
    return jsonify({
        'total_records': int(total_records or 0),
        'total_posts': int(totals['total_posts'] or 0),
        'total_blotters': int(totals['total_blotters'] or 0),
        'total_counties': int(totals['total_counties'] or 0),
        'total_agencies': int(totals['total_agencies'] or 0),
        'latest_blotter': dict(latest_blotter) if latest_blotter else None,
        'date_range': {
            'earliest': date_range['earliest'] if date_range else None,
            'latest': date_range['latest'] if date_range else None,
        },
        'top_counties': [
            {'county': row['county'], 'count': int(row['count'] or 0)}
            for row in top_counties
        ],
        'top_incident_types': [
            {'incident_type': row['incident_type'], 'count': int(row['count'] or 0)}
            for row in top_incident_types
        ],
    })


@api_bp.route('/api/blog')
@require_api_key(allow_anonymous=True)
def api_blog_posts():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = max(1, min(100, request.args.get('per_page', 20, type=int)))
    offset = (page - 1) * per_page
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) FROM blog_posts WHERE published = 1').fetchone()[0]
    rows = conn.execute(
        '''
        SELECT id, title, slug, excerpt, body, author, created_at
        FROM blog_posts
        WHERE published = 1
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        ''',
        (per_page, offset),
    ).fetchall()
    conn.close()
    return jsonify({
        'posts': [_blog_payload(row, include_body=False) for row in rows],
        'total': int(total),
        'page': page,
        'total_pages': max(1, (int(total) + per_page - 1) // per_page),
    })


@api_bp.route('/api/blog/<slug>')
@require_api_key(allow_anonymous=True)
def api_blog_post_detail(slug: str):
    conn = get_db()
    row = conn.execute(
        '''
        SELECT id, title, slug, excerpt, body, author, created_at
        FROM blog_posts
        WHERE slug = ?
          AND published = 1
        LIMIT 1
        ''',
        (slug,),
    ).fetchone()
    conn.close()
    if not row:
        abort(404)
    return jsonify(_blog_payload(row))


@api_bp.route('/api/blog/posts', methods=['POST'])
@require_api_key()
def api_blog_post_create():
    """Create a new blog post. Intended for the autonomous content pipeline."""
    data = request.get_json(force=True, silent=True) or {}

    title = (data.get('title') or '').strip()
    body = (data.get('body') or '').strip()
    excerpt = (data.get('excerpt') or '').strip()
    author = (data.get('author') or 'blotter.host').strip()[:120]
    published = bool(data.get('published', True))
    source_url = (data.get('source_url') or '').strip()[:500]
    primary_category = (data.get('primary_category') or '').strip()[:120]

    if not title or not body:
        return jsonify({'error': 'title and body are required'}), 400

    slug = _slugify(data.get('slug', '') or title)
    if not slug:
        return jsonify({'error': 'Could not generate a valid slug'}), 400

    # Append source link to body if provided and not already present
    if source_url and source_url not in body:
        body += f"\n\n<p><a href=\"{source_url}\" target=\"_blank\" rel=\"noopener\">Read original source &rarr;</a></p>"

    # Optional tags stored as JSON in a metadata column if it exists; harmless otherwise
    tags_json = None
    tags = data.get('tags') or []
    if isinstance(tags, list) and tags:
        tags_json = json.dumps(tags)

    conn = get_db()
    try:
        # Ensure metadata column exists for pipeline posts
        try:
            conn.execute('ALTER TABLE blog_posts ADD COLUMN source_url TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute('ALTER TABLE blog_posts ADD COLUMN primary_category TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute('ALTER TABLE blog_posts ADD COLUMN tags_json TEXT')
        except sqlite3.OperationalError:
            pass

        cursor = conn.execute(
            '''
            INSERT INTO blog_posts (title, slug, body, excerpt, author, published, source_url, primary_category, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                title[:255],
                slug,
                body,
                excerpt,
                author,
                1 if published else 0,
                source_url,
                primary_category,
                tags_json,
            ),
        )
        conn.commit()
        post_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'A post with this slug already exists'}), 409
    except Exception as exc:
        conn.close()
        return jsonify({'error': str(exc)}), 500
    finally:
        if conn:
            conn.close()

    return jsonify({
        'id': post_id,
        'slug': slug,
        'title': title,
        'published': published,
        'message': 'Post published' if published else 'Post saved as draft',
    }), 201


@api_bp.route('/api/pattern-click', methods=['POST'])
def track_pattern_click():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    placement = (payload.get('placement') or '').strip()[:80]
    meta = _app()._pattern_target_meta(payload.get('target_path') or '')
    if not placement or not meta:
        return ('', 204)

    ip_hash = hashlib.sha256((request.remote_addr or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    source_path = (payload.get('source_path') or request.headers.get('X-Source-Path') or '')[:255]

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO pattern_clicks (
                pattern_slug, county_slug, target_path, placement, source_path, ip_hash, referrer
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                meta['pattern_slug'],
                meta['county_slug'],
                meta['target_path'],
                placement,
                source_path,
                ip_hash,
                referrer,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return ('', 204)


@api_bp.route('/api/subscribe-event', methods=['POST'])
def track_subscribe_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip()
    if event_type not in {'cta_click', 'form_submit', 'nav_click'}:
        return ('', 204)

    source = payload.get('source') or ''
    page_path = payload.get('page_path') or request.path
    _app()._record_subscribe_event(event_type, source=source, page_path=page_path)
    return ('', 204)


@api_bp.route('/api/donate-event', methods=['POST'])
def track_donate_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip()
    if event_type not in {'donate_view', 'cta_click', 'checkout_start', 'checkout_success', 'checkout_cancel'}:
        return ('', 204)

    source = payload.get('source') or ''
    page_path = payload.get('page_path') or request.path
    amount_cents = payload.get('amount_cents')
    _app()._record_donation_event(event_type, source=source, page_path=page_path, amount_cents=amount_cents)
    return ('', 204)


@api_bp.route('/api/bail-ads/event', methods=['POST'])
def track_bail_ad_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip().lower()
    if event_type not in {'impression', 'click', 'lead', 'call', 'text'}:
        return ('', 204)

    try:
        order_id = int(payload.get('order_id') or 0)
    except (TypeError, ValueError):
        order_id = 0
    try:
        slot_id = int(payload.get('slot_id') or 0)
    except (TypeError, ValueError):
        slot_id = 0
    county = (payload.get('county') or '').strip()[:80]
    source = (payload.get('source') or '').strip()[:80]
    ip_hash = hashlib.sha256((_app()._client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO bail_ad_events (order_id, slot_id, event_type, county, source, ip_hash, referrer)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                order_id if order_id > 0 else None,
                slot_id if slot_id > 0 else None,
                event_type,
                county,
                source,
                ip_hash,
                referrer,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return ('', 204)


@api_bp.route('/api/bail-ads/simulator-event', methods=['POST'])
def track_bail_ad_simulator_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip().lower()
    allowed = {
        'page_view',
        'logo_upload',
        'view_switch',
        'mobile_toggle',
        'county_switch',
        'inquiry_sync',
        'checkout_click',
        'share_link',
        'public_preview_open',
    }
    if event_type not in allowed:
        return ('', 204)

    try:
        conn = get_db()
        _app()._record_bail_ad_simulator_event(
            conn,
            event_type=event_type,
            source=(payload.get('source') or '').strip()[:80],
            sim_view=(payload.get('sim_view') or '').strip()[:24],
            county=(payload.get('county') or '').strip()[:80],
            agency_name=(payload.get('agency_name') or '').strip()[:120],
            asset_path=(payload.get('asset_path') or '').strip()[:500],
            share_url=(payload.get('share_url') or '').strip()[:500],
            internal_mode=bool(payload.get('internal_mode')),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return ('', 204)


@api_bp.route('/api/bail-ads/simulator-upload', methods=['POST'])
def upload_bail_ad_simulator_asset():
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'Image file is required.'}), 400
    if not _app()._bail_ad_allowed_asset(file.filename):
        return jsonify({'error': 'Logo file must be PNG, JPG, JPEG, WEBP, or GIF.'}), 400

    content_length = request.content_length or 0
    if content_length > 5 * 1024 * 1024:
        return jsonify({'error': 'Logo file must be 5MB or smaller.'}), 413

    safe_name = secure_filename(file.filename)
    if not safe_name:
        return jsonify({'error': 'Invalid file name.'}), 400

    token = secrets.token_urlsafe(12)
    from datetime import datetime
    storage_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{token}_{safe_name}"
    sim_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'bail_ads_simulator')
    os.makedirs(sim_dir, exist_ok=True)
    abs_path = os.path.join(sim_dir, storage_name)
    file.save(abs_path)
    asset_url = f"/uploads/bail_ads_simulator/{storage_name}"

    try:
        conn = get_db()
        _app()._record_bail_ad_simulator_event(
            conn,
            event_type='logo_upload',
            source=(request.form.get('source') or 'ad_simulator').strip()[:80],
            sim_view=(request.form.get('sim_view') or '').strip()[:24],
            county=(request.form.get('county') or '').strip()[:80],
            agency_name=(request.form.get('agency_name') or '').strip()[:120],
            asset_path=asset_url,
            internal_mode=(request.form.get('internal_mode') or '').strip().lower() in {'1', 'true', 'yes'},
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return jsonify({'ok': True, 'asset_url': asset_url})


@api_bp.route('/api/bail-leads/event', methods=['POST'])
def track_bail_consumer_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip().lower()
    if event_type not in {'directory_view', 'form_view', 'form_submit', 'call_click', 'text_click', 'chat_click'}:
        return ('', 204)

    m = _app()
    county = m._normalize_bail_county((payload.get('county') or '').strip()[:80])
    source = (payload.get('source') or '').strip()[:80]
    conn = None
    try:
        conn = get_db()
        m._ensure_bail_consumer_lead_schema(conn)
        m._record_bail_consumer_event(conn, event_type, county=county, source=source)
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    return ('', 204)


@api_bp.route('/api/donate/create-checkout-session', methods=['POST'])
def donate_create_checkout_session():
    m = _app()
    if not m._donations_enabled():
        return jsonify({'error': 'Donations are currently unavailable'}), 503
    if not m._stripe_ready_for_checkout():
        return jsonify({'error': 'Payment provider is not configured'}), 503

    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict() if request.form else {}

    parsed = build_donation_checkout_payload(payload)
    if 'error' in parsed:
        return jsonify({'error': parsed['error']}), parsed['status']

    try:
        checkout_session = create_donation_checkout_session(parsed)
    except Exception:
        return jsonify({'error': 'Unable to start secure checkout'}), 502

    persist_donation_checkout(parsed, checkout_session)
    return jsonify({
        'checkout_url': checkout_session.get('url'),
        'session_id': checkout_session.get('id'),
    })


@api_bp.route('/developers/api')
@api_bp.route('/api/docs')
def developers_api():
    m = _app()
    return render_template(
        'api_docs.html',
        base_url=m.BASE_URL.rstrip('/'),
        active_nav='api',
        current_year=__import__('datetime').datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Raw Records Endpoints (for dashboard Live Feed)
# ---------------------------------------------------------------------------

@api_bp.route('/api/records')
@require_api_key(allow_anonymous=True)
def api_records():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = max(1, min(100, request.args.get('per_page', 50, type=int)))
    offset = (page - 1) * per_page

    conn = get_db()
    cur = conn.cursor()

    where = ["1=1"]
    params = []

    county = (request.args.get('county') or '').strip()
    if county:
        where.append("county = ?")
        params.append(county)

    incident_type = (request.args.get('type') or '').strip()
    if incident_type:
        where.append("incident_type = ?")
        params.append(incident_type)

    status = (request.args.get('status') or '').strip()
    if status:
        where.append("status = ?")
        params.append(status)

    search = (request.args.get('q') or '').strip()
    if search:
        where.append("(location LIKE ? OR incident LIKE ? OR summary LIKE ?)")
        params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

    where_sql = ' AND '.join(where)

    # Total count
    cur.execute(f"SELECT COUNT(*) as total FROM records WHERE {where_sql}", params)
    total = cur.fetchone()['total']

    # Records
    cur.execute(
        f"SELECT id, date, time, location, county, incident_type, incident, status, summary, details, officer, charge_category, created_at FROM records WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [per_page, offset]
    )
    records = [dict(r) for r in cur.fetchall()]
    conn.close()

    return jsonify({
        'records': records,
        'total': int(total),
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (int(total) + per_page - 1) // per_page)
    })


@api_bp.route('/api/records/recent')
@require_api_key(allow_anonymous=True)
def api_records_recent():
    conn = get_db()
    cur = conn.cursor()

    # Get 50 most recent records
    cur.execute(
        "SELECT id, date, time, location, county, incident_type, incident, status, summary, details, officer, charge_category, created_at FROM records ORDER BY id DESC LIMIT 50"
    )
    records = [dict(r) for r in cur.fetchall()]
    conn.close()

    return jsonify({'records': records})


@api_bp.route('/api/records/timeline')
@require_api_key(allow_anonymous=True)
def api_records_timeline():
    conn = get_db()
    cur = conn.cursor()

    # Daily counts for last 30 days
    cur.execute(
        "SELECT date, COUNT(*) as count FROM records WHERE date >= date('now', '-30 days') GROUP BY date ORDER BY date"
    )
    timeline = [dict(r) for r in cur.fetchall()]
    conn.close()

    return jsonify({'timeline': timeline})


# ---------------------------------------------------------------------------
# API Key Management (admin only)
# ---------------------------------------------------------------------------

from services.api.auth import (
    create_client,
    revoke_client,
    list_clients,
    MB_API_ADMIN_SECRET,
)


def _require_admin_secret():
    """Abort 403 if the X-Admin-Secret header doesn't match."""
    # Read the secret via the source module each time so tests can patch
    # services.api.auth.MB_API_ADMIN_SECRET without also having to patch the
    # local re-export in this blueprint module.
    import services.api.auth as _auth_module
    provided = (request.headers.get("X-Admin-Secret") or "").strip()
    secret = (getattr(_auth_module, "MB_API_ADMIN_SECRET", "") or "").strip()
    if not secret:
        abort(503, description="API admin secret not configured.")
    if not secrets.compare_digest(provided, secret):
        abort(403, description="Invalid admin secret.")


@api_bp.route("/api/auth/keys", methods=["POST"])
def api_create_key():
    """Admin endpoint to create a new API key."""
    _require_admin_secret()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    tier = (payload.get("tier") or "free").strip().lower()
    if not name:
        abort(400, description="Field 'name' is required.")
    if tier not in {"free", "pro", "enterprise"}:
        abort(400, description="Field 'tier' must be one of: free, pro, enterprise.")
    conn = get_db()
    plaintext, client_id = create_client(conn, name, tier)
    conn.close()
    return jsonify({
        "id": client_id,
        "name": name,
        "tier": tier,
        "api_key": plaintext,
        "message": "Store this key now — it will not be shown again.",
    }), 201


@api_bp.route("/api/auth/keys", methods=["GET"])
def api_list_keys():
    """Admin endpoint to list all API clients (hashes only, no plaintext)."""
    _require_admin_secret()
    conn = get_db()
    clients = list_clients(conn)
    conn.close()
    return jsonify({
        "clients": [
            {
                "id": c.id,
                "name": c.name,
                "tier": c.tier,
                "is_active": bool(c.is_active),
                "created_at": c.created_at,
                "revoked_at": c.revoked_at,
            }
            for c in clients
        ]
    })


@api_bp.route("/api/auth/keys/<int:client_id>", methods=["DELETE"])
def api_revoke_key(client_id: int):
    """Admin endpoint to revoke an API key."""
    _require_admin_secret()
    conn = get_db()
    ok = revoke_client(conn, client_id)
    conn.close()
    if not ok:
        abort(404, description="Client not found.")
    return jsonify({"revoked": True, "client_id": client_id})


@api_bp.route("/api/auth/whoami")
def api_whoami():
    """Return the authenticated client's metadata (useful for debugging tiers)."""
    from services.api.auth import validate_request
    conn = get_db()
    client = validate_request(conn)
    conn.close()
    if client is None:
        abort(401, description="No valid API key provided.")
    return jsonify({
        "id": client.id,
        "name": client.name,
        "tier": client.tier,
        "is_active": bool(client.is_active),
    })


# ---------------------------------------------------------------------------
# Mobile user authentication — JSON endpoints for the Expo app
# ---------------------------------------------------------------------------

def _public_user_payload(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "subscription_counties": user.get("subscription_counties") or "",
        "subscribe_digest": bool(user.get("subscribe_digest")),
        "is_active": bool(user.get("is_active", 1)),
        "created_at": user.get("created_at"),
        "last_login_at": user.get("last_login_at"),
    }


@api_bp.route("/api/v1/auth/register", methods=["POST"])
def api_v1_auth_register():
    """Create a public user account and return a long-lived bearer token."""
    data = request.get_json(force=True, silent=True) or {}
    display_name = (data.get("display_name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    errors = []
    if len(display_name) < 2:
        errors.append("Choose a display name with at least 2 characters.")
    if not email or "@" not in email:
        errors.append("Enter a valid email address.")
    if len(password) < 8:
        errors.append("Use a password with at least 8 characters.")
    if errors:
        return jsonify({"error": "validation_failed", "messages": errors}), 400

    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM public_users WHERE email = ?",
        (email,),
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "email_already_registered"}), 409

    app_module = _app()
    password_hash = app_module.bcrypt.generate_password_hash(password).decode("utf-8")
    cursor = conn.execute(
        """
        INSERT INTO public_users (
            email, password_hash, display_name, subscription_counties, subscribe_digest
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (email, password_hash, display_name[:120], "", 0),
    )
    user_id = cursor.lastrowid
    conn.commit()

    plaintext_token, _token_id = mobile_auth.create_mobile_user_token(
        conn, user_id, name="mobile"
    )
    user = mobile_auth.load_public_user(conn, user_id)
    conn.close()
    return jsonify({"user": _public_user_payload(user), "token": plaintext_token}), 201


@api_bp.route("/api/v1/auth/login", methods=["POST"])
def api_v1_auth_login():
    """Authenticate a public user and return a long-lived bearer token."""
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "email_and_password_required"}), 400

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM public_users WHERE email = ?",
        (email,),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "invalid_credentials"}), 401

    app_module = _app()
    password_valid = app_module.bcrypt.check_password_hash(row["password_hash"], password)
    if not password_valid or not row["is_active"]:
        conn.close()
        return jsonify({"error": "invalid_credentials"}), 401

    user_id = row["id"]
    conn.execute(
        "UPDATE public_users SET last_login_at = datetime('now') WHERE id = ?",
        (user_id,),
    )
    conn.commit()

    plaintext_token, _token_id = mobile_auth.create_mobile_user_token(
        conn, user_id, name="mobile"
    )
    user = mobile_auth.load_public_user(conn, user_id)
    conn.close()
    return jsonify({"user": _public_user_payload(user), "token": plaintext_token})


@api_bp.route("/api/v1/auth/me", methods=["GET"])
def api_v1_auth_me():
    """Return the authenticated public user's profile."""
    user = _current_public_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    return jsonify({"user": _public_user_payload(user)})


@api_bp.route("/api/v1/auth/logout", methods=["POST"])
def api_v1_auth_logout():
    """Revoke the bearer token used for this request."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Authentication required"}), 401

    token_str = auth_header[7:].strip()
    conn = get_db()
    token = mobile_auth.get_token_by_plaintext(conn, token_str)
    if token:
        mobile_auth.revoke_mobile_user_token(conn, token.id)
    conn.close()
    return jsonify({"message": "Signed out"})


@api_bp.route("/api/v1/auth/forgot-password", methods=["POST"])
def api_v1_auth_forgot_password():
    """Request a password reset email (identical behaviour to the HTML form)."""
    from blueprints.auth import (
        _generate_reset_token,
        _hash_reset_token,
        _is_rate_limited,
        _record_reset_request,
        _send_reset_email,
    )

    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "valid_email_required"}), 400

    conn = get_db()
    row = conn.execute(
        "SELECT id, email, is_active FROM public_users WHERE email = ?",
        (email,),
    ).fetchone()

    if row and row["is_active"]:
        public_user_id = row["id"]
        if _is_rate_limited(conn, public_user_id):
            conn.close()
            return jsonify({"error": "rate_limited"}), 429

        raw_token = _generate_reset_token()
        token_hash = _hash_reset_token(raw_token)
        _record_reset_request(conn, public_user_id, token_hash, request.remote_addr or "")
        conn.commit()

        reset_url = f"{config.BASE_URL.rstrip('/')}/reset-password/{raw_token}"
        try:
            _send_reset_email(row["email"], reset_url)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Failed to send password reset email to %s", email)

    conn.close()
    return jsonify({
        "message": "If an account exists for that email, a reset link has been sent."
    })


@api_bp.route("/api/v1/auth/reset-password", methods=["POST"])
def api_v1_auth_reset_password():
    """Reset a password using a token from a reset email."""
    from blueprints.auth import _hash_reset_token

    data = request.get_json(force=True, silent=True) or {}
    raw_token = (data.get("token") or "").strip()
    new_password = data.get("new_password") or ""

    if not raw_token:
        return jsonify({"error": "token_required"}), 400
    if len(new_password) < 8:
        return jsonify({"error": "password_too_short"}), 400

    conn = get_db()
    token_hash = _hash_reset_token(raw_token)
    row = conn.execute(
        """
        SELECT t.id, t.public_user_id, t.expires_at, t.used_at
        FROM password_reset_tokens t
        WHERE t.token_hash = ?
        """,
        (token_hash,),
    ).fetchone()

    from datetime import datetime
    now = datetime.utcnow()
    is_valid = bool(
        row
        and row["used_at"] is None
        and datetime.fromisoformat(row["expires_at"]) > now
    )
    if not is_valid:
        conn.close()
        return jsonify({"error": "invalid_or_expired_token"}), 400

    app_module = _app()
    new_hash = app_module.bcrypt.generate_password_hash(new_password).decode("utf-8")
    conn.execute(
        "UPDATE public_users SET password_hash = ? WHERE id = ?",
        (new_hash, row["public_user_id"]),
    )
    conn.execute(
        "UPDATE password_reset_tokens SET used_at = ? WHERE id = ?",
        (now.isoformat(), row["id"]),
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Password updated. You can now log in."})


@api_bp.route("/api/v1/purchases/verify", methods=["POST"])
def api_v1_verify_purchase():
    """Verify a RevenueCat purchase and activate premium server-side."""
    from services.monetization import revenuecat

    user = _current_public_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401

    data = request.get_json(force=True, silent=True) or {}
    app_user_id = (data.get("app_user_id") or "").strip()
    if not app_user_id:
        return jsonify({"error": "app_user_id_required"}), 400

    subscriber = revenuecat.fetch_subscriber(app_user_id)
    is_premium = revenuecat.has_premium_entitlement(subscriber)

    conn = get_db()
    if is_premium:
        conn.execute(
            "UPDATE public_users SET subscriber_plan = 'insider' WHERE id = ?",
            (user["id"],),
        )
    conn.commit()
    conn.close()
    return jsonify({"is_premium": is_premium})


# ---------------------------------------------------------------------------
# Maps & Data Expansion — new endpoints (2026)
# ---------------------------------------------------------------------------

# Handles both MM/DD/YY (8-char) and YYYY-MM-DD (10-char) date storage formats
_ISO = (
    "(CASE WHEN length(r.date)=8 "
    "THEN '20'||substr(r.date,7,2)||'-'||substr(r.date,1,2)||'-'||substr(r.date,4,2) "
    "ELSE r.date END)"
)


@api_bp.route('/api/geo/choropleth')
@require_api_key(allow_anonymous=True, anonymous_quota=_GEO_QUOTA)
def api_geo_choropleth():
    """County-level incident aggregates for choropleth map coloring."""
    from datetime import datetime, timedelta
    days = max(1, min(730, request.args.get('days', 90, type=int)))
    mode = (request.args.get('mode') or 'per_capita').strip().lower()
    category = (request.args.get('category') or '').strip().lower()[:40]
    date_from = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    where = [f"{_ISO} >= ?", "r.county IS NOT NULL", "r.county != ''"]
    params = [date_from]
    if mode == 'category' and category:
        where.append("r.charge_category = ?")
        params.append(category)

    conn = get_db()
    rows = conn.execute(
        f"SELECT r.county, COUNT(*) AS n FROM records r "
        f"WHERE {' AND '.join(where)} GROUP BY r.county ORDER BY r.county",
        params,
    ).fetchall()
    conn.close()

    from data.mt_county_populations import MT_COUNTY_POPULATIONS
    features = []
    for row in rows:
        name = (row['county'] or '').strip().title()
        count = int(row['n'] or 0)
        pop = MT_COUNTY_POPULATIONS.get(name, 0)
        features.append({
            'county': row['county'],
            'incident_count': count,
            'per_capita_per_1k': round(count / pop * 1000, 2) if pop else None,
            'population': pop,
        })
    return jsonify({'features': features, 'days': days, 'mode': mode,
                    'category': category, 'date_from': date_from})


@api_bp.route('/api/geo/county-detail')
@require_api_key(allow_anonymous=True, anonymous_quota=_GEO_QUOTA)
def api_geo_county_detail():
    """Incident breakdown for a single county — charge categories, sparkline, agencies."""
    from datetime import datetime, timedelta
    from collections import defaultdict
    county = (request.args.get('county') or '').strip()[:80]
    days = max(1, min(365, request.args.get('days', 90, type=int)))
    if not county:
        return jsonify({'error': 'county required'}), 400
    date_from = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')
    spark_from = (datetime.utcnow() - timedelta(weeks=12)).strftime('%Y-%m-%d')

    conn = get_db()
    top_types = conn.execute(
        f"SELECT charge_category, COUNT(*) AS n FROM records r "
        f"WHERE r.county=? AND {_ISO}>=? AND r.charge_category IS NOT NULL "
        f"GROUP BY r.charge_category ORDER BY n DESC LIMIT 6",
        (county, date_from),
    ).fetchall()
    spark = conn.execute(
        f"SELECT strftime('%Y-W%W', {_ISO}) AS wk, COUNT(*) AS n FROM records r "
        f"WHERE r.county=? AND {_ISO}>=? GROUP BY wk ORDER BY wk",
        (county, spark_from),
    ).fetchall()
    agencies = conn.execute(
        "SELECT b.county AS agency, COUNT(r.id) AS n FROM records r "
        "JOIN blotters b ON b.id=r.blotter_id WHERE r.county=? "
        "GROUP BY b.county ORDER BY n DESC LIMIT 5",
        (county,),
    ).fetchall()
    conn.close()

    slug = county.lower().replace(' ', '-').replace("'", '')
    return jsonify({
        'county': county,
        'top_charge_categories': [{'category': r['charge_category'], 'count': int(r['n'])} for r in top_types],
        'sparkline': [{'week': r['wk'], 'count': int(r['n'])} for r in spark],
        'agency_breakdown': [{'agency': r['agency'], 'count': int(r['n'])} for r in agencies],
        'county_page_url': f'/county/{slug}',
    })


@api_bp.route('/api/stats/crime-calendar')
@require_api_key(allow_anonymous=True)
def api_stats_crime_calendar():
    """Daily incident counts for a given year — used by the crime calendar heatmap."""
    from datetime import datetime
    year = request.args.get('year', datetime.utcnow().year, type=int)
    year = max(2018, min(datetime.utcnow().year + 1, year))
    date_from = f'{year}-01-01'
    date_to = f'{year}-12-31'

    conn = get_db()
    rows = conn.execute(
        f"SELECT {_ISO} AS iso_date, COUNT(*) AS n FROM records r "
        f"WHERE {_ISO} >= ? AND {_ISO} <= ? GROUP BY iso_date ORDER BY iso_date",
        (date_from, date_to),
    ).fetchall()
    conn.close()
    return jsonify({
        'year': year,
        'days': [{'date': r['iso_date'], 'count': int(r['n'] or 0)} for r in rows
                 if r['iso_date'] and len(r['iso_date']) == 10],
    })


@api_bp.route('/api/stats/charge-trends')
@require_api_key(allow_anonymous=True)
def api_stats_charge_trends():
    """Monthly charge category counts for stacked area chart."""
    from datetime import datetime, timedelta
    from collections import defaultdict
    months = max(1, min(24, request.args.get('months', 12, type=int)))
    date_from = (datetime.utcnow() - timedelta(days=months * 31)).strftime('%Y-%m-%d')

    conn = get_db()
    rows = conn.execute(
        f"SELECT strftime('%Y-%m', {_ISO}) AS mo, charge_category, COUNT(*) AS n "
        f"FROM records r WHERE {_ISO}>=? AND charge_category IS NOT NULL "
        f"GROUP BY mo, charge_category ORDER BY mo, charge_category",
        (date_from,),
    ).fetchall()
    conn.close()

    result = defaultdict(lambda: defaultdict(int))
    categories = set()
    for row in rows:
        if not row['mo']:
            continue
        result[row['mo']][row['charge_category']] += int(row['n'] or 0)
        categories.add(row['charge_category'])

    sorted_months = sorted(result.keys())
    return jsonify({
        'months': sorted_months,
        'categories': sorted(categories),
        'data': {mo: dict(counts) for mo, counts in sorted(result.items())},
    })


@api_bp.route('/api/stats/agency-activity')
@require_api_key(allow_anonymous=True)
def api_stats_agency_activity():
    """Per-county blotter filing frequency — most active reporting counties."""
    from datetime import datetime, timedelta
    days = max(7, min(365, request.args.get('days', 90, type=int)))
    date_from = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')

    conn = get_db()
    rows = conn.execute(
        "SELECT county, COUNT(*) AS blotter_count, "
        "SUM(COALESCE(incident_count,0)) AS total_incidents, MAX(upload_date) AS last_upload "
        "FROM blotters WHERE upload_date>=? AND county IS NOT NULL AND county!='' "
        "GROUP BY county ORDER BY blotter_count DESC LIMIT 30",
        (date_from,),
    ).fetchall()
    conn.close()
    return jsonify({'agencies': [
        {'county': r['county'], 'blotter_count': int(r['blotter_count'] or 0),
         'total_incidents': int(r['total_incidents'] or 0), 'last_upload': r['last_upload']}
        for r in rows
    ]})


@api_bp.route('/api/geo/zip-location')
@require_api_key(allow_anonymous=True, anonymous_quota=_GEO_QUOTA)
def api_geo_zip_location():
    """Resolve a Montana zip code to lat/lng for the 'Near Me' map feature."""
    zip_code = (request.args.get('zip') or '').strip()[:5]
    if len(zip_code) != 5 or not zip_code.isdigit():
        return jsonify({'error': 'Invalid zip code'}), 400
    from services.geo.zip_geocode import zip_to_latlon
    result = zip_to_latlon(zip_code)
    if result:
        return jsonify({'lat': result[0], 'lng': result[1], 'zip': zip_code})
    return jsonify({'error': 'Zip code not found'}), 404


@api_bp.route('/api/geo/sex-offender-county-counts')
@require_api_key(allow_anonymous=True, anonymous_quota=_GEO_QUOTA)
def api_geo_sex_offender_county_counts():
    """County-level aggregate sex offender counts — no individual locations exposed."""
    conn = get_db()
    rows = conn.execute(
        "SELECT address_county AS county, COUNT(*) AS count FROM sex_offenders "
        "WHERE status='active' AND address_county IS NOT NULL AND address_county!='' "
        "GROUP BY address_county ORDER BY address_county"
    ).fetchall()
    conn.close()
    return jsonify({'counts': [{'county': r['county'], 'count': int(r['count'])} for r in rows]})


# ---------------------------------------------------------------------------
# Health / ops
# ---------------------------------------------------------------------------

@api_bp.route('/api/health')
def api_health():
    """Public health check for load balancers and monitoring."""
    db_ok = False
    try:
        conn = get_db()
        conn.execute('SELECT 1').fetchone()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    return jsonify({
        'status': 'ok' if db_ok else 'degraded',
        'database': 'ok' if db_ok else 'error',
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'),
    }), 200 if db_ok else 503


# ---------------------------------------------------------------------------
# Mobile push notifications
# ---------------------------------------------------------------------------

@api_bp.route('/api/v1/push/register', methods=['POST'])
@require_api_key(allow_anonymous=True)
def api_v1_push_register():
    """Register an Expo push token for the authenticated or anonymous user."""
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get('expo_push_token') or '').strip()
    if not token or not token.startswith('ExponentPushToken['):
        return jsonify({'error': 'valid_expo_push_token_required'}), 400

    user = _current_public_user()
    platform = (data.get('platform') or request.headers.get('X-Platform', '') or '').strip()[:40]
    device_id = (data.get('device_id') or '').strip()[:255]
    county_filter = (data.get('county_filter') or '').strip()[:80]
    alert_types = data.get('alert_types') or ['all']

    conn = get_db()
    try:
        token_id = register_mobile_push_token(
            conn,
            expo_push_token=token,
            public_user_id=user['id'] if user else None,
            platform=platform,
            device_id=device_id,
            county_filter=county_filter,
            alert_types=alert_types if isinstance(alert_types, list) else ['all'],
        )
    except ValueError as exc:
        conn.close()
        return jsonify({'error': str(exc)}), 400
    finally:
        conn.close()

    return jsonify({'registered': True, 'token_id': token_id})


@api_bp.route('/api/v1/push/unregister', methods=['POST'])
@require_api_key(allow_anonymous=True)
def api_v1_push_unregister():
    """Deactivate an Expo push token."""
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get('expo_push_token') or '').strip()
    if not token:
        return jsonify({'error': 'expo_push_token_required'}), 400

    conn = get_db()
    try:
        deactivate_mobile_push_token(conn, token)
    finally:
        conn.close()

    return jsonify({'unregistered': True})


@api_bp.route('/api/v1/push/broadcast', methods=['POST'])
def api_v1_push_broadcast():
    """Admin-only endpoint to broadcast a push notification to mobile devices."""
    # Restrict to admin API key or authenticated admin user.
    conn = get_db()
    try:
        api_user = validate_request(conn)
        user = _current_public_user()
        if not api_user and (not user or user.get('role') not in {'admin', 'superuser', 'owner'}):
            return jsonify({'error': 'Forbidden'}), 403

        data = request.get_json(force=True, silent=True) or {}
        title = (data.get('title') or '').strip()[:120]
        body = (data.get('body') or '').strip()[:240]
        if not title or not body:
            return jsonify({'error': 'title and body required'}), 400

        push_data = data.get('data') or {}
        county_filter = (data.get('county_filter') or '').strip()[:80] or None
        result = broadcast_expo_push(conn, title, body, push_data, county_filter=county_filter)
    finally:
        conn.close()

    return jsonify(result)


# ---------------------------------------------------------------------------
# Court / disposition lookup
# ---------------------------------------------------------------------------

@api_bp.route('/api/v1/court/lookup')
@require_api_key(allow_anonymous=True)
@cached_api(timeout=300)
def api_v1_court_lookup():
    """Search criminal court cases by person name and/or case number."""
    name = (request.args.get('name') or '').strip()[:160]
    county = (request.args.get('county') or '').strip()[:80]
    case_number = (request.args.get('case_number') or '').strip()[:80]
    include_bookings = request.args.get('include_bookings', '1').strip() != '0'
    limit = max(1, min(50, request.args.get('limit', 25, type=int)))

    conn = get_db()
    try:
        result = lookup_disposition(
            conn,
            name=name or None,
            county=county or None,
            case_number=case_number or None,
            include_bookings=include_bookings,
            limit=limit,
        )
    finally:
        conn.close()

    return jsonify(result)


# ---------------------------------------------------------------------------
# Warrants
# ---------------------------------------------------------------------------

def _warrant_payload(row: sqlite3.Row) -> dict:
    return {
        'id': row['id'],
        'source_record_id': row['source_record_id'],
        'county': row['county'],
        'city': row['city'],
        'person_name': row['person_name'],
        'dob': row['dob'],
        'warrant_type': row['warrant_type'],
        'charges_text': row['charges_text'],
        'issued_by': row['issued_by'],
        'issue_date': row['issue_date'],
        'bond_amount': row['bond_amount'],
        'bond_type': row['bond_type'],
        'status': row['status'],
        'source_url': row['source_url'],
        'mugshot_url': row['mugshot_url'],
        'photo_url': row['photo_url'],
        'first_seen_at': row['first_seen_at'],
        'updated_at': row['updated_at'],
    }


@api_bp.route('/api/v1/warrants')
@require_api_key(allow_anonymous=True)
@cached_api(timeout=120)
def api_v1_warrants():
    """List active warrant records with optional county and search filters."""
    county = (request.args.get('county') or '').strip()[:80]
    q = (request.args.get('q') or '').strip()[:120]
    status = (request.args.get('status') or 'active').strip().lower()[:20]
    warrant_type = (request.args.get('warrant_type') or '').strip()[:40]
    limit = max(1, min(200, request.args.get('limit', 50, type=int)))

    where = ['1 = 1']
    params: list = []
    if status:
        where.append('status = ?')
        params.append(status)
    if county:
        where.append('LOWER(county) = LOWER(?)')
        params.append(county)
    if warrant_type:
        where.append('warrant_type = ?')
        params.append(warrant_type)
    if q:
        like = f'%{q}%'
        where.append(
            '(person_name LIKE ? OR charges_text LIKE ? OR city LIKE ? OR issued_by LIKE ?)'
        )
        params.extend([like, like, like, like])

    conn = get_db()
    try:
        rows = conn.execute(
            f'''
            SELECT *
            FROM warrants
            WHERE {' AND '.join(where)}
            ORDER BY datetime(COALESCE(updated_at, first_seen_at)) DESC, id DESC
            LIMIT ?
            ''',
            params + [limit],
        ).fetchall()
        total_row = conn.execute(
            'SELECT COUNT(*) FROM warrants WHERE status = ?',
            (status or 'active',),
        ).fetchone()
    finally:
        conn.close()

    return jsonify({
        'warrants': [_warrant_payload(r) for r in rows],
        'filters': {
            'county': county or None,
            'q': q or None,
            'status': status,
            'warrant_type': warrant_type or None,
        },
        'total': total_row[0] if total_row else 0,
    })


# ---------------------------------------------------------------------------
# Missing persons
# ---------------------------------------------------------------------------

def _missing_person_payload(item: dict) -> dict:
    """Select mobile-safe fields from a decorated missing-person record."""
    return {
        'id': item.get('id'),
        'full_name': item.get('full_name'),
        'slug': item.get('slug'),
        'status': item.get('status'),
        'status_label': item.get('status_label'),
        'age': item.get('age'),
        'age_missing': item.get('age_missing'),
        'gender': item.get('gender'),
        'race': item.get('race'),
        'hair_color': item.get('hair_color'),
        'eye_color': item.get('eye_color'),
        'height_label': item.get('height_label'),
        'weight_lbs': item.get('weight_lbs'),
        'height_weight': item.get('height_weight'),
        'missing_from': item.get('missing_from'),
        'last_seen_location': item.get('last_seen_location'),
        'city': item.get('city'),
        'county': item.get('county'),
        'date_last_seen': item.get('date_last_seen'),
        'last_seen_at_label': item.get('last_seen_at_label'),
        'summary': item.get('summary'),
        'case_number': item.get('case_number'),
        'investigating_agency': item.get('investigating_agency'),
        'photo_url': item.get('photo_url'),
        'photos': item.get('photos', []),
        'is_active': item.get('is_active'),
        'is_indigenous': item.get('is_indigenous'),
        'is_child': item.get('is_child'),
        'public_href': item.get('public_href'),
        'created_at': item.get('created_at'),
        'updated_at': item.get('updated_at'),
    }


@api_bp.route('/api/v1/missing-persons')
@require_api_key(allow_anonymous=True)
@cached_api(timeout=120)
def api_v1_missing_persons():
    """List missing person records with optional status, county, and search filters."""
    from services.persons.missing import (
        STATUS_MISSING,
        STATUS_LOCATED,
        _fetch_missing_person_rows,
        _decorate_person_row,
        ensure_missing_person_schema,
    )

    status = (request.args.get('status') or '').strip().lower()[:20]
    county = (request.args.get('county') or '').strip()[:80]
    q = (request.args.get('q') or '').strip()[:120]
    sort = (request.args.get('sort') or 'updated_desc').strip()[:32]
    limit = max(1, min(200, request.args.get('limit', 50, type=int)))

    if status not in {STATUS_MISSING, STATUS_LOCATED, ''}:
        status = ''

    conn = get_db()
    try:
        ensure_missing_person_schema(conn)
        rows = _fetch_missing_person_rows(
            conn,
            status=status or None,
            q=q,
            county=county,
            sort=sort,
            limit=limit,
        )
        people = [_missing_person_payload(_decorate_person_row(r)) for r in rows]
        total_active = conn.execute(
            "SELECT COUNT(*) FROM missing_persons WHERE status = ?",
            (STATUS_MISSING,),
        ).fetchone()[0]
    finally:
        conn.close()

    return jsonify({
        'people': people,
        'filters': {
            'status': status or None,
            'county': county or None,
            'q': q or None,
            'sort': sort,
        },
        'total_active': total_active,
    })
