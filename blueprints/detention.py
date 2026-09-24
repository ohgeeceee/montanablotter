from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from services.monetization.paywall import preview_allowed, user_has_access
from services.monetization.name_suppression import redact_person_name, redact_text

import config as config

detention_bp = Blueprint('detention', __name__)

_get_db = None
_booking_context_loader = None
_roster_directory_loader = None


JAIL_BOOKING_STATUS_LABELS = {
    'current': 'Current',
    'released': 'Released',
    'transferred': 'Transferred',
    'archived': 'Archived',
}


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
    """Keep old links working while presenting one canonical booking hub."""
    return redirect(url_for('detention.jail_bookings'), code=301)


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
    # The Hill County slug gets a dedicated daily-roster layout: HPD does
    # not publish a public online roster, so the page is built from the
    # daily email HPD sends Montana Blotter.
    if county_slug == "hill":
        conn = _get_db()
        try:
            context = _havre_roster_context(conn)
        finally:
            conn.close()
        if not context.get("hpd_source"):
            abort(404)
        return render_template('havre_daily_roster.html', **context)

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


def _havre_roster_context(conn) -> dict:
    """Build the context dict the ``havre_daily_roster.html`` template
    expects. The function is read-only: it does not mutate any row.

    Returns an empty ``hpd_source`` (so the route returns 404) if the
    jail_booking_sources row for ``hill`` has not been seeded yet — that
    should never happen at runtime because ``ingest_havre_pdf`` calls
    ``_ensure_tracked_sources`` on its first run, but we guard against it
    so a fresh DB doesn't 500 on the public page.
    """
    source = conn.execute(
        "SELECT * FROM jail_booking_sources WHERE county_slug = ?",
        ("hill",),
    ).fetchone()

    if source is None:
        return {"hpd_source": None}

    source_id = source["id"]
    last_success_at = source["last_success_at"] or source["last_checked_at"]
    freshness_label, freshness_state, last_run_at = _havre_freshness(last_success_at)

    currently_booked = conn.execute(
        '''
        SELECT id, person_name, age, booking_at, charges_summary
        FROM jail_bookings
        WHERE county_slug = 'hill' AND COALESCE(is_current, 1) = 1
        ORDER BY COALESCE(booking_at, first_seen_at) DESC, id DESC
        ''',
    ).fetchall()

    recent_new_entries = conn.execute(
        '''
        SELECT id, person_name, booking_at, charges_summary
        FROM jail_bookings
        WHERE county_slug = 'hill'
          AND booking_at >= datetime('now', '-7 days')
        ORDER BY booking_at DESC
        LIMIT 50
        ''',
    ).fetchall()

    raw_diff = conn.execute(
        '''
        SELECT date(completed_at) AS day,
               COALESCE(SUM(new_count), 0) AS new_count,
               COALESCE(SUM(missing_count), 0) AS released_count
        FROM jail_booking_runs
        WHERE source_id = ?
          AND completed_at >= datetime('now', '-7 days')
        GROUP BY date(completed_at)
        ORDER BY day DESC
        ''',
        (source_id,),
    ).fetchall()

    today = datetime.now(timezone.utc).date()
    daily_diff: list[dict] = []
    diff_by_day = {row["day"]: row for row in raw_diff}
    for offset in range(7):
        day = today - timedelta(days=offset)
        day_str = day.isoformat()
        row = diff_by_day.get(day_str)
        daily_diff.append({
            "label": day.strftime("%a %-m/%-d"),
            "date": day_str,
            "new_count": int(row["new_count"]) if row else 0,
            "released_count": int(row["released_count"]) if row else 0,
        })

    return {
        "hpd_source": source,
        "hpd_public_url": source["roster_url"] or "https://www.havremt.gov/police",
        "hpd_phone": source["phone"] or "406-265-4397",
        "currently_booked": [dict(r) for r in currently_booked],
        "recent_new_entries": [dict(r) for r in recent_new_entries],
        "daily_diff": daily_diff,
        "freshness_label": freshness_label,
        "freshness_state": freshness_state,
        "last_run_at": last_run_at,
        "current_year": datetime.now().year,
    }


def _havre_freshness(last_success_at: str | None) -> tuple[str, str, str | None]:
    """Render the freshness badge: label, state, and an absolute timestamp.

    State is one of: ``fresh`` (≤ 36h), ``stale`` (≤ 7 days), ``empty``.
    """
    if not last_success_at:
        return ("Awaiting first HPD email", "empty", None)
    try:
        # SQLite stores ISO-8601 strings; tolerate both naive and 'Z'-suffixed.
        cleaned = last_success_at.replace("Z", "+00:00")
        last_dt = datetime.fromisoformat(cleaned)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return (f"Last updated: {last_success_at}", "stale", last_success_at)
    delta = datetime.now(timezone.utc) - last_dt
    hours = int(delta.total_seconds() // 3600)
    if hours < 0:
        rel = "just now"
    elif hours < 1:
        rel = "less than an hour ago"
    elif hours == 1:
        rel = "1 hour ago"
    elif hours < 36:
        rel = f"{hours} hours ago"
    elif hours < 48:
        rel = "yesterday"
    else:
        days = hours // 24
        rel = f"{days} days ago"
    state = "fresh" if hours < 36 else "stale"
    absolute = last_dt.strftime("%Y-%m-%d %H:%M UTC")
    return (f"Last updated: {rel}", state, absolute)


def _slugify_name(name: str) -> str:
    """Create a URL-friendly slug from a person's name."""
    import re
    slug = (name or 'unknown').lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or 'unknown'


def _display_dt(value):
    """Minimal display datetime for the search results."""
    if not value:
        return 'Unknown'
    from app import _display_jail_booking_datetime
    return _display_jail_booking_datetime(value)


def _jail_roster_search_context(*, q='', county_filter='', status_filter='current', limit=100):
    """Build the context for the jail roster search page. Returns dict with
    ``rows`` (decorated), ``total_matches``, and ``preview_limit``.

    Preview limit caps how many results anonymous/Free users see before the
    paywall upsell kicks in. This is a display cap — the query still counts the
    full match set so the template can say "Showing 3 of 47 results"."""
    PREVIEW_LIMIT = 3  # anonymous/Free preview rows before upsell

    conn = _get_db()
    try:
        where = ['1 = 1']
        params = []

        if county_filter:
            where.append('jb.county_slug = ?')
            params.append(county_filter)

        status_filter = (status_filter or 'current').strip().lower()
        if status_filter == 'current':
            where.append('COALESCE(jb.is_current, 1) = 1')
        elif status_filter == 'recent':
            where.append("datetime(COALESCE(jb.booking_at, jb.first_seen_at, jb.created_at)) >= datetime('now', '-24 hours')")
        elif status_filter == 'released':
            where.append("(COALESCE(jb.is_current, 1) = 0 OR jb.booking_status = 'released')")
        # 'all' adds no status filter

        if q:
            like = f'%{q}%'
            where.append(
                '('
                'jb.person_name LIKE ? OR '
                'COALESCE(jb.charges_summary, \'\') LIKE ? OR '
                'COALESCE(jb.booking_number, \'\') LIKE ? OR '
                'COALESCE(jb.arresting_agency, \'\') LIKE ? OR '
                'COALESCE(jb.county_name, \'\') LIKE ?'
                ')'
            )
            params.extend([like, like, like, like, like])

        total_row = conn.execute(
            f'SELECT COUNT(*) AS c FROM jail_bookings jb WHERE {" AND ".join(where)}',
            params,
        ).fetchone()
        total_matches = int(total_row['c']) if total_row else 0

        rows = conn.execute(
            f'''
            SELECT
                jb.*,
                s.roster_url AS roster_url,
                s.phone AS source_phone,
                s.last_success_at,
                s.is_featured
            FROM jail_bookings jb
            LEFT JOIN jail_booking_sources s ON s.id = jb.source_id
            WHERE {' AND '.join(where)}
            ORDER BY datetime(COALESCE(jb.booking_at, jb.first_seen_at, jb.created_at)) DESC, jb.id DESC LIMIT ?
            ''',
            params + [limit],
        ).fetchall()

        # Apply paid privacy suppression and decorate each row
        decorated = []
        for row in rows:
            item = dict(row)
            # Reuse the same redaction as the existing booking detail route
            county = item.get('county_name') or item.get('county_slug')
            if item.get('person_name'):
                item['person_name'] = redact_person_name(item['person_name'], county)
            if item.get('charges_summary'):
                item['charges_summary'] = redact_text(item['charges_summary'], county)

            # Build display labels mirroring _decorate_jail_booking_row
            booking_val = item.get('booking_at') or item.get('first_seen_at') or item.get('created_at')
            item['booking_at_label'] = _display_dt(booking_val)
            item['last_seen_label'] = _display_dt(item.get('last_seen_at') or item.get('updated_at') or item.get('created_at'))
            booking_status = (item.get('booking_status') or 'current').strip().lower()
            item['booking_status'] = booking_status if booking_status in ('current', 'released', 'transferred', 'archived') else 'current'
            item['booking_status_label'] = JAIL_BOOKING_STATUS_LABELS.get(item['booking_status'], 'Current')
            item['charges_summary'] = (item.get('charges_summary') or '').strip()
            decorated.append(item)

        return {
            'rows': decorated,
            'total_matches': total_matches,
            'preview_limit': PREVIEW_LIMIT,
        }
    finally:
        conn.close()


@detention_bp.route('/jail-roster-search')
def jail_roster_search():
    """Dedicated jail roster search page with full-text search across all
    Montana county rosters. Anonymous visitors see limited preview results
    with a paywall upsell; subscribers see the full result set."""
    q = (request.args.get('q') or '').strip()[:120]
    county = (request.args.get('county') or '').strip().lower()
    status = (request.args.get('status') or 'current').strip().lower()[:20]

    context = _jail_roster_search_context(
        q=q,
        county_filter=county,
        status_filter=status,
    )

    # Paywall gating: subscribers get everything; anonymous/Free users get
    # a limited preview and see upsell CTAs when there are more results
    # than the preview limit.
    paywall_allowed, paywall_counts = preview_allowed(resource_type='jail_roster_search')
    user_is_subscriber = user_has_access('plus')
    paywall_blocked = not user_is_subscriber and context['total_matches'] > context['preview_limit']

    context.update({
        'page_title': f'Jail Roster Search: {q or "Montana"}' if q else 'Jail Roster Search',
        'meta_description': (
            'Search current and recent jail bookings across Montana. '
            'Find who is in custody by name, charge, agency, or county.'
        ),
        'canonical_url': f'{config.BASE_URL}/jail-roster-search?q={q}' if q else f'{config.BASE_URL}/jail-roster-search',
        'og_title': 'Jail Roster Search | Montana Blotter',
        'og_description': 'Search Montana jail rosters by name, charge, booking number, or county.',
        'active_nav': 'jail_bookings',
        'paywall_allowed': paywall_allowed,
        'paywall_counts': paywall_counts,
        'paywall_blocked': paywall_blocked,
        'preview_limit': context['preview_limit'],
        'showing_count': min(context['total_matches'], context['preview_limit']) if paywall_blocked else context['total_matches'],
        'user_is_subscribed': user_is_subscriber,
    })
    return render_template('jail_roster_search.html', **context)


@detention_bp.route('/api/jail-roster-search')
def api_jail_roster_search():
    """JSON endpoint for jail roster search. Same paywall gating as the HTML page."""
    q = (request.args.get('q') or '').strip()[:120]
    county = (request.args.get('county') or '').strip().lower()
    status = (request.args.get('status') or 'current').strip().lower()[:20]

    paywall_allowed, paywall_counts = preview_allowed(resource_type='jail_roster_search')
    if not paywall_allowed:
        return jsonify({
            'ok': False,
            'error': 'preview_limit_reached',
            'detail': 'You have reached your free search preview limit. Subscribe for unlimited jail roster search.',
            'subscribe_url': '/pricing',
            'preview_counts': paywall_counts,
        }), 403

    context = _jail_roster_search_context(
        q=q,
        county_filter=county,
        status_filter=status,
    )
    return jsonify({
        'ok': True,
        'query': q,
        'county_filter': county or None,
        'status_filter': status,
        'total_matches': context['total_matches'],
        'results': context['rows'],
    })


@detention_bp.route('/booking/<int:booking_id>')
def booking_detail(booking_id):
    """Individual jail booking detail page with rich SEO structured data."""
    conn = _get_db()
    try:
        booking_row = conn.execute(
            """
            SELECT
                jb.*,
                jbs.county_name,
                jbs.facility_name,
                jbs.roster_url AS source_roster_url
            FROM jail_bookings jb
            LEFT JOIN jail_booking_sources jbs ON jb.source_id = jbs.id
            WHERE jb.id = ?
            """,
            (booking_id,),
        ).fetchone()
        if not booking_row:
            abort(404)
        booking = dict(booking_row)

        # Parse charges_json for structured data
        charges = []
        if booking['charges_json']:
            try:
                import json
                charges = json.loads(booking['charges_json'])
                if not isinstance(charges, list):
                    charges = [charges]
            except Exception:
                charges = []

        # Build charges summary if JSON empty
        charges_summary = booking['charges_summary'] or ''
        if not charges and charges_summary:
            charges = [{'description': charges_summary}]
        county = booking['county_name'] or booking['county_slug'] or 'Unknown'

        # Paid privacy suppression: redact the person's name and any embedded
        # name mentions without deleting the underlying public record.
        person_name = redact_person_name(booking['person_name'] or 'Unknown', county)
        charges_summary = redact_text(charges_summary, county)
        booking['charges_summary'] = charges_summary
        for charge in charges:
            if not isinstance(charge, dict):
                continue
            for key in ('description', 'charge'):
                if charge.get(key):
                    charge[key] = redact_text(charge[key], county)

        # Meta description
        facility = booking['facility_name'] or 'Unknown facility'
        meta_desc = (
            f"{person_name} booking record for {county} County, Montana. "
            f"Booked at {facility}"
        )
        if booking['booking_at']:
            meta_desc += f" on {booking['booking_at'][:10]}"
        if charges_summary:
            meta_desc += f". Charges: {charges_summary[:60]}"
        meta_desc += ". View jail roster details, bond info, and charges."
        if len(meta_desc) > 160:
            meta_desc = meta_desc[:157] + '...'

        # Page title
        page_title = f"{person_name} — {county} County Jail Booking"

        paywall_allowed, paywall_counts = preview_allowed(resource_type='booking', resource_id=booking_id)
        paywall_blocked = not paywall_allowed

        # Load sponsored listings for this county
        sponsored_ads = []
        if booking['county_slug']:
            from blueprints.sponsored_listings import get_sponsored_for_county, record_impression
            sponsored_ads = get_sponsored_for_county(conn, booking['county_slug'])
            # Record impressions per active listing
            for ad in sponsored_ads:
                try:
                    record_impression(conn, ad['id'])
                except Exception:
                    pass

        return render_template(
            'booking_detail.html',
            booking=booking,
            charges=charges,
            person_name=person_name,
            county=county,
            facility=facility,
            meta_description=meta_desc,
            page_title=page_title,
            canonical_url=f"https://montanablotter.com/booking/{booking_id}",
            paywall_blocked=paywall_blocked,
            paywall_counts=paywall_counts,
            sponsored_ads=sponsored_ads,
            name_removal_amount_label=config.NAME_SUPPRESS_AMOUNT_LABEL,
        )
    finally:
        conn.close()
