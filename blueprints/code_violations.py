from __future__ import annotations

import re
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request, url_for


code_violations_bp = Blueprint('code_violations', __name__)

_get_db = None


def register_code_violations_blueprint(app, *, get_db):
    global _get_db
    _get_db = get_db
    app.register_blueprint(code_violations_bp)


def _slugify(text: str) -> str:
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def _load_violations_context(
    *,
    city: str = '',
    violation_type: str = '',
    status: str = '',
    q: str = '',
    page: int = 1,
    per_page: int = 50,
):
    conn = _get_db()
    try:
        where_clauses = ['1=1']
        params: list = []

        if city:
            where_clauses.append('pa.city = ?')
            params.append(city)
        if violation_type:
            where_clauses.append('cv.violation_type = ?')
            params.append(violation_type)
        if status:
            where_clauses.append('cv.status = ?')
            params.append(status)
        if q:
            where_clauses.append('(pa.street LIKE ? OR cv.violation_type LIKE ? OR cv.owner_name LIKE ?)')
            like = f'%{q}%'
            params.extend([like, like, like])

        where_sql = ' AND '.join(where_clauses)

        count_row = conn.execute(
            f'''
            SELECT COUNT(*) AS total
            FROM code_violations cv
            LEFT JOIN property_addresses pa ON cv.property_address_id = pa.id
            WHERE {where_sql}
            ''',
            params,
        ).fetchone()
        total = count_row['total'] if count_row else 0

        rows = conn.execute(
            f'''
            SELECT
                cv.id,
                cv.violation_type,
                cv.status,
                cv.date_issued,
                cv.date_resolved,
                cv.owner_name,
                cv.fine_amount,
                cv.raw_address,
                pa.address_slug,
                pa.street,
                pa.city,
                pa.state,
                pa.zip,
                pa.county,
                cvs.display_name AS source_name
            FROM code_violations cv
            LEFT JOIN property_addresses pa ON cv.property_address_id = pa.id
            LEFT JOIN code_violation_sources cvs ON cv.source_id = cvs.id
            WHERE {where_sql}
            ORDER BY cv.date_issued DESC, cv.id DESC
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        # Facets
        cities = [r['city'] for r in conn.execute(
            'SELECT DISTINCT city FROM property_addresses ORDER BY city'
        ).fetchall() if r['city']]
        types = [r['violation_type'] for r in conn.execute(
            'SELECT DISTINCT violation_type FROM code_violations ORDER BY violation_type'
        ).fetchall() if r['violation_type']]

        return {
            'rows': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'cities': cities,
            'violation_types': types,
            'city_filter': city,
            'type_filter': violation_type,
            'status_filter': status,
            'q': q,
        }
    finally:
        conn.close()


@code_violations_bp.route('/code-violations')
def code_violations_index():
    context = _load_violations_context(
        city=request.args.get('city', ''),
        violation_type=request.args.get('type', ''),
        status=request.args.get('status', ''),
        q=request.args.get('q', ''),
        page=int(request.args.get('page', 1)),
    )
    return render_template('code_violations.html', **context)


@code_violations_bp.route('/property/<address_slug>')
def property_detail(address_slug):
    conn = _get_db()
    try:
        prop = conn.execute(
            'SELECT * FROM property_addresses WHERE address_slug = ?',
            (address_slug,),
        ).fetchone()
        if not prop:
            abort(404)

        violations = conn.execute(
            '''
            SELECT
                cv.*,
                cvs.display_name AS source_name
            FROM code_violations cv
            LEFT JOIN code_violation_sources cvs ON cv.source_id = cvs.id
            WHERE cv.property_address_id = ?
            ORDER BY cv.date_issued DESC
            ''',
            (prop['id'],),
        ).fetchall()

        # Cross-link: recent jail bookings at same address (naive text match)
        bookings = conn.execute(
            '''
            SELECT id, person_name, county_name, booking_at, charges_summary
            FROM jail_bookings
            WHERE raw_json LIKE ? OR person_name LIKE ?
            ORDER BY booking_at DESC
            LIMIT 10
            ''',
            (f'%"address": "{prop["street"]}%', f'%{prop["street"]}%'),
        ).fetchall()

        # Cross-link: recent records (arrests/incidents) mentioning the street
        records = conn.execute(
            '''
            SELECT id, incident, location, date, county
            FROM records
            WHERE location LIKE ?
            ORDER BY date DESC
            LIMIT 10
            ''',
            (f'%{prop["street"]}%',),
        ).fetchall()

        return render_template(
            'property_detail.html',
            prop=prop,
            violations=violations,
            bookings=bookings,
            records=records,
            page_title=f"{prop['street']}, {prop['city']}, {prop['state']} — Property Violations",
            meta_description=f"Code enforcement violations for {prop['street']}, {prop['city']}, {prop['state']}. View violation history, cross-linked jail bookings, and incident records.",
        )
    finally:
        conn.close()


@code_violations_bp.route('/api/code-violations')
def api_code_violations():
    context = _load_violations_context(
        city=request.args.get('city', ''),
        violation_type=request.args.get('type', ''),
        status=request.args.get('status', ''),
        q=request.args.get('q', ''),
        page=int(request.args.get('page', 1)),
        per_page=min(int(request.args.get('per_page', 50)), 100),
    )
    return jsonify({
        'violations': [dict(r) for r in context['rows']],
        'total': context['total'],
        'page': context['page'],
        'pages': context['pages'],
        'filters': {
            'city': context['city_filter'] or None,
            'type': context['type_filter'] or None,
            'status': context['status_filter'] or None,
            'q': context['q'] or None,
        },
    })


@code_violations_bp.route('/api/property/<address_slug>')
def api_property_detail(address_slug):
    conn = _get_db()
    try:
        prop = conn.execute(
            'SELECT * FROM property_addresses WHERE address_slug = ?',
            (address_slug,),
        ).fetchone()
        if not prop:
            return jsonify({'error': 'Not found'}), 404

        violations = conn.execute(
            '''
            SELECT cv.*, cvs.display_name AS source_name
            FROM code_violations cv
            LEFT JOIN code_violation_sources cvs ON cv.source_id = cvs.id
            WHERE cv.property_address_id = ?
            ORDER BY cv.date_issued DESC
            ''',
            (prop['id'],),
        ).fetchall()

        return jsonify({
            'property': dict(prop),
            'violations': [dict(v) for v in violations],
        })
    finally:
        conn.close()


@code_violations_bp.route('/api/embed/violations')
def api_embed_violations():
    address_slug = request.args.get('address')
    if not address_slug:
        return jsonify({'error': 'address required'}), 400

    conn = _get_db()
    try:
        prop = conn.execute(
            'SELECT * FROM property_addresses WHERE address_slug = ?',
            (address_slug,),
        ).fetchone()
        if not prop:
            return jsonify({'violations': [], 'property': None})

        violations = conn.execute(
            '''
            SELECT violation_type, status, date_issued, date_resolved, description
            FROM code_violations
            WHERE property_address_id = ?
            ORDER BY date_issued DESC
            ''',
            (prop['id'],),
        ).fetchall()

        return jsonify({
            'property': {
                'street': prop['street'],
                'city': prop['city'],
                'state': prop['state'],
                'zip': prop['zip'],
            },
            'violations': [dict(v) for v in violations],
            'count': len(violations),
            'open_count': sum(1 for v in violations if v['status'] == 'open'),
        })
    finally:
        conn.close()


@code_violations_bp.after_request
def _add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response
