from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request, url_for


license_sanctions_bp = Blueprint('license_sanctions', __name__)

_get_db = None


def register_license_sanctions_blueprint(app, *, get_db):
    global _get_db
    _get_db = get_db
    app.register_blueprint(license_sanctions_bp)


def _load_sanctions_context(
    *,
    board: str = '',
    county: str = '',
    action: str = '',
    q: str = '',
    date_from: str = '',
    date_to: str = '',
    page: int = 1,
    per_page: int = 50,
):
    conn = _get_db()
    try:
        where_clauses = ['is_active = 1']
        params: list = []

        if board:
            where_clauses.append('board = ?')
            params.append(board)
        if county:
            where_clauses.append('county = ?')
            params.append(county)
        if action:
            where_clauses.append('action_taken = ?')
            params.append(action)
        if date_from:
            where_clauses.append('effective_date >= ?')
            params.append(date_from)
        if date_to:
            where_clauses.append('effective_date <= ?')
            params.append(date_to)
        if q:
            where_clauses.append('(name LIKE ? OR license_number LIKE ? OR violation_type LIKE ?)')
            like = f'%{q}%'
            params.extend([like, like, like])

        where_sql = ' AND '.join(where_clauses)
        count_row = conn.execute(
            f'SELECT COUNT(*) AS total FROM license_sanctions WHERE {where_sql}',
            params,
        ).fetchone()
        total = count_row['total'] if count_row else 0

        rows = conn.execute(
            f'''
            SELECT
                id, name, name_slug, license_number, board,
                violation_type, action_taken, effective_date, county,
                description, source_url, created_at
            FROM license_sanctions
            WHERE {where_sql}
            ORDER BY effective_date DESC, id DESC
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        boards = [r['board'] for r in conn.execute(
            'SELECT DISTINCT board FROM license_sanctions WHERE is_active = 1 ORDER BY board'
        ).fetchall() if r['board']]
        counties = [r['county'] for r in conn.execute(
            'SELECT DISTINCT county FROM license_sanctions WHERE is_active = 1 AND county IS NOT NULL ORDER BY county'
        ).fetchall() if r['county']]
        actions = [r['action_taken'] for r in conn.execute(
            'SELECT DISTINCT action_taken FROM license_sanctions WHERE is_active = 1 AND action_taken IS NOT NULL ORDER BY action_taken'
        ).fetchall() if r['action_taken']]

        return {
            'rows': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'boards': boards,
            'counties': counties,
            'actions': actions,
            'board_filter': board,
            'county_filter': county,
            'action_filter': action,
            'date_from': date_from,
            'date_to': date_to,
            'q': q,
        }
    finally:
        conn.close()


@license_sanctions_bp.route('/license-sanctions')
def license_sanctions_index():
    context = _load_sanctions_context(
        board=request.args.get('board', ''),
        county=request.args.get('county', ''),
        action=request.args.get('action', ''),
        q=request.args.get('q', ''),
        date_from=request.args.get('date_from', ''),
        date_to=request.args.get('date_to', ''),
        page=int(request.args.get('page', 1)),
    )
    return render_template('license_sanctions.html', **context)


@license_sanctions_bp.route('/license-sanctions/<slug>')
def license_sanction_detail(slug):
    conn = _get_db()
    try:
        row = conn.execute(
            '''
            SELECT
                ls.*,
                lss.board_name AS source_board_name,
                lss.board_url AS source_board_url
            FROM license_sanctions ls
            LEFT JOIN license_sanction_sources lss ON ls.source_id = lss.id
            WHERE ls.name_slug = ? AND ls.is_active = 1
            ORDER BY ls.effective_date DESC
            LIMIT 1
            ''',
            (slug,),
        ).fetchone()
        if not row:
            abort(404)

        all_sanctions = conn.execute(
            '''
            SELECT * FROM license_sanctions
            WHERE name_slug = ? AND is_active = 1
            ORDER BY effective_date DESC
            ''',
            (slug,),
        ).fetchall()

        bookings = conn.execute(
            '''
            SELECT id, person_name, county_name, booking_at, charges_summary
            FROM jail_bookings
            WHERE person_name LIKE ?
            ORDER BY booking_at DESC
            LIMIT 10
            ''',
            (f'%{row["name"]}%',),
        ).fetchall()

        records = conn.execute(
            '''
            SELECT id, incident, location, date, county
            FROM records
            WHERE (incident LIKE ? OR location LIKE ?)
            ORDER BY date DESC
            LIMIT 10
            ''',
            (f'%{row["name"]}%', f'%{row["name"]}%'),
        ).fetchall()

        page_title = f"{row['name']} | Montana License Sanctions"
        meta_description = (
            f"{row['name']} disciplinary actions in Montana. "
            f"Board: {row['board']}. Action: {row['action_taken'] or 'Unknown'}."
        )
        return render_template(
            'license_sanction_detail.html',
            sanction=row,
            all_sanctions=all_sanctions,
            bookings=bookings,
            records=records,
            page_title=page_title,
            meta_description=meta_description,
            canonical_url=url_for('license_sanctions.license_sanction_detail', slug=slug, _external=True),
        )
    finally:
        conn.close()


@license_sanctions_bp.route('/api/license-sanctions')
def api_license_sanctions():
    context = _load_sanctions_context(
        board=request.args.get('board', ''),
        county=request.args.get('county', ''),
        action=request.args.get('action', ''),
        q=request.args.get('q', ''),
        date_from=request.args.get('date_from', ''),
        date_to=request.args.get('date_to', ''),
        page=int(request.args.get('page', 1)),
        per_page=min(int(request.args.get('per_page', 50)), 100),
    )
    return jsonify({
        'sanctions': [dict(r) for r in context['rows']],
        'total': context['total'],
        'page': context['page'],
        'pages': context['pages'],
        'filters': {
            'board': context['board_filter'] or None,
            'county': context['county_filter'] or None,
            'action': context['action_filter'] or None,
            'date_from': context['date_from'] or None,
            'date_to': context['date_to'] or None,
            'q': context['q'] or None,
        },
    })


@license_sanctions_bp.route('/api/license-sanctions/<slug>')
def api_license_sanction_detail(slug):
    conn = _get_db()
    try:
        row = conn.execute(
            'SELECT * FROM license_sanctions WHERE name_slug = ? AND is_active = 1 ORDER BY effective_date DESC LIMIT 1',
            (slug,),
        ).fetchone()
        if not row:
            return jsonify({'error': 'Not found'}), 404
        all_sanctions = conn.execute(
            'SELECT * FROM license_sanctions WHERE name_slug = ? AND is_active = 1 ORDER BY effective_date DESC',
            (slug,),
        ).fetchall()
        return jsonify({
            'sanction': dict(row),
            'all_sanctions': [dict(r) for r in all_sanctions],
        })
    finally:
        conn.close()
