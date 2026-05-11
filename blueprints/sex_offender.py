from __future__ import annotations

import re

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

sex_offender_bp = Blueprint('sex_offender', __name__)
_get_db = None


def register_sex_offender_blueprint(app, *, get_db):
    global _get_db
    _get_db = get_db
    app.register_blueprint(sex_offender_bp)


def _geocode_address(street: str, city: str, state: str = 'MT', zip_code: str = '') -> tuple[float | None, float | None]:
    """Simple geocoding via Nominatim (OpenStreetMap)."""
    try:
        import requests
        query = f"{street}, {city}, {state} {zip_code}"
        resp = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': query, 'format': 'json', 'limit': 1},
            headers={'User-Agent': 'MontanaBlotter/1.0'},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception:
        pass
    return None, None


def _load_updates_context(
    *,
    county: str = '',
    city: str = '',
    change_type: str = '',
    page: int = 1,
    per_page: int = 50,
):
    conn = _get_db()
    try:
        where_clauses = ['1=1']
        params: list = []

        if county:
            where_clauses.append('so.address_county = ?')
            params.append(county)
        if city:
            where_clauses.append('so.address_city = ?')
            params.append(city)
        if change_type:
            where_clauses.append('soc.change_type = ?')
            params.append(change_type)

        where_sql = ' AND '.join(where_clauses)

        count_row = conn.execute(
            f'''
            SELECT COUNT(*) AS total
            FROM sex_offender_changes soc
            JOIN sex_offenders so ON soc.offender_id = so.id
            WHERE {where_sql}
            ''',
            params,
        ).fetchone()
        total = count_row['total'] if count_row else 0

        rows = conn.execute(
            f'''
            SELECT
                soc.id,
                soc.change_type,
                soc.change_note,
                soc.created_at,
                so.registry_id,
                so.full_name,
                so.address_street,
                so.address_city,
                so.address_county,
                so.lat,
                so.lon,
                so.photo_url
            FROM sex_offender_changes soc
            JOIN sex_offenders so ON soc.offender_id = so.id
            WHERE {where_sql}
            ORDER BY soc.created_at DESC, soc.id DESC
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        counties = [r['address_county'] for r in conn.execute(
            "SELECT DISTINCT address_county FROM sex_offenders WHERE status = 'active' ORDER BY address_county"
        ).fetchall() if r['address_county']]

        types = [r['change_type'] for r in conn.execute(
            'SELECT DISTINCT change_type FROM sex_offender_changes ORDER BY change_type'
        ).fetchall() if r['change_type']]

        return {
            'rows': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'counties': counties,
            'change_types': types,
            'county_filter': county,
            'city_filter': city,
            'type_filter': change_type,
        }
    finally:
        conn.close()


@sex_offender_bp.route('/sex-offender-updates')
def sex_offender_updates():
    context = _load_updates_context(
        county=request.args.get('county', ''),
        city=request.args.get('city', ''),
        change_type=request.args.get('type', ''),
        page=int(request.args.get('page', 1)),
    )
    return render_template('sex_offender_updates.html', **context)


@sex_offender_bp.route('/sex-offender-updates/<county_slug>')
def sex_offender_county(county_slug):
    conn = _get_db()
    try:
        county = county_slug.replace('-', ' ').title()
        offenders = conn.execute(
            '''
            SELECT * FROM sex_offenders
            WHERE address_county = ? AND status = 'active'
            ORDER BY address_city, full_name
            ''',
            (county,),
        ).fetchall()

        cities = sorted({r['address_city'] for r in offenders if r['address_city']})

        return render_template(
            'sex_offender_county.html',
            county=county,
            offenders=offenders,
            cities=cities,
            page_title=f'{county} County Sex Offender Registry — Montana Blotter',
            meta_description=f'Current sex offender registrants in {county} County, Montana. View address-level map and recent changes.',
        )
    finally:
        conn.close()


@sex_offender_bp.route('/api/sex-offender-updates')
def api_sex_offender_updates():
    context = _load_updates_context(
        county=request.args.get('county', ''),
        city=request.args.get('city', ''),
        change_type=request.args.get('type', ''),
        page=int(request.args.get('page', 1)),
        per_page=min(int(request.args.get('per_page', 50)), 100),
    )
    return jsonify({
        'changes': [dict(r) for r in context['rows']],
        'total': context['total'],
        'page': context['page'],
        'pages': context['pages'],
        'filters': {
            'county': context['county_filter'] or None,
            'city': context['city_filter'] or None,
            'type': context['type_filter'] or None,
        },
    })


@sex_offender_bp.route('/api/sex-offenders/geojson')
def api_sex_offenders_geojson():
    """Return GeoJSON for Leaflet map."""
    conn = _get_db()
    try:
        county = request.args.get('county', '')
        params = []
        where = "status = 'active' AND lat IS NOT NULL AND lon IS NOT NULL"
        if county:
            where += ' AND address_county = ?'
            params.append(county)

        rows = conn.execute(
            f'SELECT * FROM sex_offenders WHERE {where}',
            params,
        ).fetchall()

        features = []
        for r in rows:
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [r['lon'], r['lat']],
                },
                'properties': {
                    'name': r['full_name'],
                    'address': f"{r['address_street']}, {r['address_city']}, MT {r['address_zip'] or ''}",
                    'tier': r['tier'],
                    'risk_level': r['risk_level'],
                    'offense': r['offense_description'],
                },
            })

        return jsonify({'type': 'FeatureCollection', 'features': features})
    finally:
        conn.close()


@sex_offender_bp.route('/sex-offender-alerts', methods=['GET', 'POST'])
def sex_offender_alerts():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        address = request.form.get('address', '').strip()
        radius = float(request.form.get('radius', 5))

        lat, lon = _geocode_address(address, '', 'MT')
        if lat is None:
            flash('Could not geocode address. Please try again.', 'error')
            return redirect(url_for('sex_offender.sex_offender_alerts'))

        conn = _get_db()
        try:
            conn.execute(
                '''
                INSERT INTO sex_offender_alert_subscriptions (email, lat, lon, radius_miles)
                VALUES (?, ?, ?, ?)
                ''',
                (email, lat, lon, radius),
            )
            conn.commit()
            flash('Alert subscription created successfully.', 'success')
        finally:
            conn.close()
        return redirect(url_for('sex_offender.sex_offender_alerts'))

    return render_template('sex_offender_alerts.html')
