from __future__ import annotations

import hashlib
import json
import os
import secrets

import stripe
from flask import Blueprint, abort, current_app, jsonify, render_template, request
from werkzeug.utils import secure_filename

from db import get_db


api_bp = Blueprint('api', __name__)


def register_api_blueprint(app):
    """Register the api blueprint onto the Flask app."""
    app.register_blueprint(api_bp)


# ---------------------------------------------------------------------------
# Private helpers (thin wrappers that delegate to app.py helpers)
# ---------------------------------------------------------------------------

def _app():
    import app as _app_module
    return _app_module


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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


@api_bp.route('/api/posts')
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
        SELECT id, title, summary, county, agency_name, agency_type, incident_date,
               incident_type, created_at
        FROM posts
        WHERE {where_sql}
        ORDER BY incident_date DESC, created_at DESC, id DESC
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
def api_post_detail(post_id: int):
    conn = get_db()
    row = conn.execute(
        '''
        SELECT id, title, summary, county, agency_name, agency_type, incident_date,
               incident_type, created_at
        FROM posts
        WHERE id = ?
          AND COALESCE(audit_status, 'pending') = 'clean'
        LIMIT 1
        ''',
        (post_id,),
    ).fetchone()
    conn.close()
    if not row:
        abort(404)
    return jsonify(_post_payload(row))


@api_bp.route('/api/counties')
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

    mode = (payload.get('mode') or 'one_time').strip().lower()
    if mode not in {'one_time', 'monthly'}:
        return jsonify({'error': 'Invalid donation mode'}), 400

    try:
        amount_cents = int(payload.get('amount_cents'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid donation amount'}), 400

    min_cents = m._donation_min_cents()
    max_cents = m._donation_max_cents()
    if amount_cents < min_cents or amount_cents > max_cents:
        return jsonify({'error': 'Donation amount out of allowed range'}), 400

    public_user = m._get_public_user()
    source = (payload.get('source') or 'donate_page').strip()[:80]
    donor_name = (payload.get('name') or '').strip()[:120]
    email = (payload.get('email') or '').strip().lower()
    if not donor_name and public_user:
        donor_name = (public_user.display_name or '').strip()[:120]
    if not email and public_user:
        email = (public_user.email or '').strip().lower()
    if email and '@' not in email:
        email = ''

    currency = m._donation_currency()
    stripe_keys = m._stripe_keys()
    stripe.api_key = stripe_keys['secret_key']

    line_item = {
        'price_data': {
            'currency': currency,
            'product_data': {'name': 'Montana Blotter Donation'},
            'unit_amount': amount_cents,
        },
        'quantity': 1,
    }
    if mode == 'monthly':
        line_item['price_data']['recurring'] = {'interval': 'month'}

    checkout_params = {
        'mode': 'subscription' if mode == 'monthly' else 'payment',
        'line_items': [line_item],
        'success_url': f'{m.BASE_URL}/donate/success?session_id={{CHECKOUT_SESSION_ID}}',
        'cancel_url': f'{m.BASE_URL}/donate/cancel',
        'billing_address_collection': 'auto',
        'allow_promotion_codes': True,
        'metadata': {
            'source': source,
            'mode': mode,
            'amount_cents': str(amount_cents),
            'donor_name': donor_name,
            'public_user_id': str(public_user.id) if public_user else '',
            'feature_gate': 'bondsman_command_center' if m._is_bondsman_subscription_source(source) else '',
        },
    }
    if email:
        checkout_params['customer_email'] = email

    try:
        checkout_session = stripe.checkout.Session.create(**checkout_params)
    except Exception:
        return jsonify({'error': 'Unable to start secure checkout'}), 502

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO donations (
                provider, mode, status, amount_cents, currency, email_hash, donor_name,
                source, provider_session_id, provider_payment_intent_id, provider_subscription_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_session_id) DO UPDATE SET
                mode = excluded.mode,
                status = excluded.status,
                amount_cents = excluded.amount_cents,
                currency = excluded.currency,
                email_hash = excluded.email_hash,
                donor_name = excluded.donor_name,
                source = excluded.source,
                provider_payment_intent_id = excluded.provider_payment_intent_id,
                provider_subscription_id = excluded.provider_subscription_id,
                updated_at = datetime('now')
            ''',
            (
                'stripe',
                mode,
                'pending',
                amount_cents,
                currency,
                m._donation_email_hash(email),
                donor_name,
                source,
                checkout_session.get('id'),
                checkout_session.get('payment_intent'),
                checkout_session.get('subscription'),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    m._record_donation_event('checkout_start', source=source, page_path='/donate', amount_cents=amount_cents)
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
