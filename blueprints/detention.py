from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request, url_for


detention_bp = Blueprint('detention', __name__)

_get_db = None
_booking_context_loader = None
_roster_directory_loader = None


def register_detention_blueprint(
    app,
    *,
    get_db,
    booking_context_loader,
    roster_directory_loader,
) -> None:
    global _get_db, _booking_context_loader, _roster_directory_loader
    _get_db = get_db
    _booking_context_loader = booking_context_loader
    _roster_directory_loader = roster_directory_loader
    app.register_blueprint(detention_bp)


def _load_booking_context(*, county_filter='', status_filter='current', q='', county_page=False):
    conn = _get_db()
    try:
        return _booking_context_loader(
            conn,
            county_filter=county_filter,
            status_filter=status_filter,
            q=q,
            county_page=county_page,
        )
    finally:
        conn.close()


@detention_bp.route('/detention')
@detention_bp.route('/jail-rosters')
def jail_rosters():
    booking_context = _load_booking_context(status_filter='recent')
    roster_directory = _roster_directory_loader()
    return render_template(
        'detention_hub.html',
        booking_context=booking_context,
        roster_directory=roster_directory,
        current_year=datetime.now().year,
    )


@detention_bp.route('/jail-bookings')
def jail_bookings():
    context = _load_booking_context(
        county_filter=request.args.get('county'),
        status_filter=request.args.get('status'),
        q=request.args.get('q'),
    )
    return render_template('jail_bookings.html', **context)


@detention_bp.route('/jail-bookings/<county_slug>')
def jail_bookings_county(county_slug):
    context = _load_booking_context(
        county_filter=county_slug,
        status_filter=request.args.get('status'),
        q=request.args.get('q'),
        county_page=True,
    )
    if not context.get('selected_source'):
        abort(404)
    return render_template('jail_bookings.html', **context)


@detention_bp.route('/api/jail-bookings')
def api_jail_bookings():
    context = _load_booking_context(
        county_filter=request.args.get('county'),
        status_filter=request.args.get('status'),
        q=request.args.get('q'),
    )
    return jsonify({
        'bookings': context['rows'],
        'filters': {
            'county': context['county_filter'] or None,
            'status': context['status_filter'],
            'q': context['q'] or None,
        },
        'summary': context['summary'],
    })


@detention_bp.route('/api/jail-bookings/<county_slug>')
def api_jail_bookings_county(county_slug):
    context = _load_booking_context(
        county_filter=county_slug,
        status_filter=request.args.get('status'),
        q=request.args.get('q'),
        county_page=True,
    )
    if not context.get('selected_source'):
        abort(404)
    return jsonify({
        'bookings': context['rows'],
        'filters': {
            'county': context['county_filter'] or None,
            'status': context['status_filter'],
            'q': context['q'] or None,
        },
        'summary': context['summary'],
        'county_page': True,
        'official_roster_href': context['selected_source'].get('roster_url'),
        'public_href': context['selected_source'].get('public_href') or url_for('detention.jail_bookings_county', county_slug=county_slug),
    })
