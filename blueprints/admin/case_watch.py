"""Admin routes for the disposition case-watch dashboard.

Surfaces jail-booking → court-case links (created by the disposition watcher)
and pending outcome-change notifications that the admin hasn't dismissed yet.

GET  /admin/case-watch                       — overview: stats, pending, recent links
POST /admin/case-watch/mark-notified         — dismiss one or more pending notifications
POST /admin/case-watch/refresh               — run refresh_outcome_data inline
"""
from __future__ import annotations

import json

from flask import flash, redirect, render_template, request, url_for

from db import get_db

from services.disposition import watcher as disposition_watcher

from . import admin_bp, require_role


def _format_match_type(mt: str) -> str:
    """Render match_type as a human label (e.g. 'last_only' -> 'Last name')."""
    if not mt:
        return ''
    return {
        'exact_slug': 'Exact slug',
        'last_first': 'Last, First',
        'last_only': 'Last name',
        'case_number': 'Case #',
    }.get(mt, mt.replace('_', ' ').title())


def _parse_link_ids(raw: str) -> list[int]:
    """Pull a list[int] of link ids from a form value (comma-separated or repeat)."""
    out: list[int] = []
    for chunk in (raw or '').split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except (TypeError, ValueError):
            continue
    # Form may also submit multiple `link_id` fields — merge those in too.
    for v in request.form.getlist('link_id'):
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    # Dedupe while preserving order.
    seen, deduped = set(), []
    for i in out:
        if i not in seen:
            seen.add(i)
            deduped.append(i)
    return deduped


@admin_bp.route('/content/case-watch', methods=['GET'])
@require_role()
def case_watch_list():
    """Overview dashboard for booking → court-case links."""
    only_pending = request.args.get('pending') == '1'
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 50
    offset = (page - 1) * per_page

    conn = get_db()
    try:
        stats_row = conn.execute(
            '''
            SELECT
                COUNT(*) AS total_links,
                SUM(CASE WHEN has_outcome = 1 THEN 1 ELSE 0 END) AS with_outcome,
                SUM(CASE WHEN has_outcome = 1 AND notified_admin_at IS NULL
                         THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN last_checked_at IS NULL THEN 1 ELSE 0 END) AS never_checked
            FROM booking_case_links
            '''
        ).fetchone()

        pending_rows = disposition_watcher.find_pending_notifications(conn, limit=200)
        # Decorate for template.
        for r in pending_rows:
            r['match_type_label'] = _format_match_type(r.get('match_type'))
            snap = r.get('last_outcome_snapshot')
            if snap:
                try:
                    r['snapshot'] = json.loads(snap)
                except (TypeError, ValueError):
                    r['snapshot'] = {}
            else:
                r['snapshot'] = {}

        where = ''
        params: list = []
        if only_pending:
            where = 'WHERE bcl.has_outcome = 1 AND bcl.notified_admin_at IS NULL'

        total = conn.execute(
            f'SELECT COUNT(*) AS n FROM booking_case_links bcl {where}', params
        ).fetchone()['n']

        rows = conn.execute(
            f'''
            SELECT bcl.id, bcl.booking_id, bcl.court_case_id, bcl.match_type,
                   bcl.confidence, bcl.linked_at, bcl.last_checked_at,
                   bcl.has_outcome, bcl.notified_admin_at,
                   jb.person_name, jb.county_name, jb.booking_at,
                   cc.case_number, cc.charges_text, cc.disposition,
                   cc.sentence_text, cc.sentence_date, cc.sentencing_judge,
                   cc.outcome_scraped_at
            FROM booking_case_links bcl
            JOIN jail_bookings jb ON jb.id = bcl.booking_id
            JOIN court_cases cc ON cc.id = bcl.court_case_id
            {where}
            ORDER BY bcl.linked_at DESC, bcl.id DESC
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, offset],
        ).fetchall()
        # Convert to dicts so the template can use .get() for optional fields.
        rows = [dict(r) for r in rows]

        for r in rows:
            r['match_type_label'] = _format_match_type(r.get('match_type'))
            r['is_pending'] = bool(r['has_outcome'] and not r['notified_admin_at'])
    finally:
        conn.close()

    return render_template(
        'admin_case_watch.html',
        stats=stats_row,
        pending_rows=pending_rows,
        rows=rows,
        total=total,
        page=page,
        per_page=per_page,
        only_pending=only_pending,
    )


@admin_bp.route('/content/case-watch/mark-notified', methods=['POST'])
@require_role()
def case_watch_mark_notified():
    """Dismiss pending notifications by stamping notified_admin_at."""
    ids = _parse_link_ids(request.form.get('ids', ''))
    if not ids:
        flash('No link ids provided to mark-notified.', 'error')
        return redirect(url_for('admin.case_watch_list'))
    conn = get_db()
    try:
        updated = disposition_watcher.mark_notified(conn, ids)
    finally:
        conn.close()
    flash(f'Marked {updated} notification(s) as read.', 'success')
    return redirect(url_for('admin.case_watch_list'))


@admin_bp.route('/content/case-watch/refresh', methods=['POST'])
@require_role()
def case_watch_refresh():
    """Trigger refresh_outcome_data inline so admins don't have to wait 15 min."""
    conn = get_db()
    try:
        stats = disposition_watcher.refresh_outcome_data(conn)
    finally:
        conn.close()
    flash(
        f"Refresh done in this process — checked {stats.get('checked', 0)} links, "
        f"found {stats.get('new_outcomes', 0)} new outcomes "
        f"({stats.get('errors', 0)} errors).",
        'success',
    )
    return redirect(url_for('admin.case_watch_list'))
