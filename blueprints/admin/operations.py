from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import config
from services.court.tracker import court_admin_context, ensure_court_tracker_schema
from db import get_db
from facebook_publisher import (
    load_facebook_settings,
    mask_token,
    publish_queue_item,
    queue_post,
    queue_recent_posts,
    run_facebook_queue,
    save_facebook_settings,
)
from services.persons.missing import (
    create_missing_person,
    dispatch_missing_person_alerts,
    get_missing_person_by_id,
    missing_person_admin_context,
    sync_official_missing_persons,
    update_missing_person,
    update_missing_person_status,
)
from services.persons.warrants_admin import (
    clear_warrant_staff_photo,
    get_warrant_by_id,
    update_warrant_photo_fields,
    warrant_admin_context,
)
from services.meetings.public import (
    ensure_public_meeting_schema,
    meeting_admin_context,
    review_duplicate_meeting_group,
)
from services.datasets.admin import build_data_center_ops_summary
from utils.auth_constants import ADMIN_ACCESS_ROLES, ADMIN_MANAGEMENT_ROLES, OPERATIONS_ROLES
from utils.app_settings import _save_app_setting
from blueprints.admin import admin_bp, require_role, _log_admin_action

# ---------------------------------------------------------------------------
# Lazy imports from app — kept here to avoid circular imports at module load.
# These helpers live in app.py and will be moved out in a later phase.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_bp.route('/operations', strict_slashes=False)
@login_required
def admin_root():
    """Role-aware landing.

    - super_admin → command center (live ops, system pulse)
    - everyone else → operations dashboard (intake, alerts, coverage)
    """
    role = getattr(current_user, 'role', '') or ''
    if role == 'super_admin':
        return redirect(url_for('admin.admin_command_center'))
    return redirect(url_for('admin.admin_dashboard'))


@admin_bp.route('/operations/dashboard')
@admin_bp.route('/dashboard')
@login_required
def admin_dashboard():
    """Merged operations dashboard — intake stats, alerts, source coverage, shortcuts."""
    import app as _app_module
    from flask import current_app

    conn = get_db()

    # Get statistics
    total_records = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    total_blotters = conn.execute('SELECT COUNT(*) FROM blotters').fetchone()[0]
    total_counties = conn.execute('SELECT COUNT(DISTINCT county) FROM records').fetchone()[0]
    failed_ingestions = conn.execute(
        "SELECT COUNT(*) FROM ingestion_jobs WHERE status = 'failed'"
    ).fetchone()[0]

    # Get recent blotters
    recent_blotters = conn.execute('''
        SELECT * FROM blotters
        ORDER BY upload_date DESC
        LIMIT 10
    ''').fetchall()

    # Get county breakdown
    county_stats = conn.execute('''
        SELECT county, COUNT(*) as count
        FROM records
        GROUP BY county
        ORDER BY count DESC
    ''').fetchall()

    source_coverage = _app_module._build_source_coverage_dashboard(conn)

    # County inventory refresh + summary
    try:
        from services.ops.county_inventory_persistence import refresh_county_inventory
        refresh_county_inventory(conn)
        ci_rows = conn.execute(
            "SELECT * FROM county_inventory ORDER BY population_rank, county"
        ).fetchall()
        ci_rows = [dict(r) for r in ci_rows]
        ci_summary = {}
        for s in ["active", "partial", "configured", "not_covered"]:
            ci_summary[s] = [r for r in ci_rows if r["status"] == s]
        ci_counts = {s: len(v) for s, v in ci_summary.items()}
        ci_rows_sorted = sorted(ci_rows, key=lambda r: (r["population_rank"], r["county"]))
    except Exception as _e:
        current_app.logger.exception("county inventory refresh failed")
        ci_rows_sorted = []
        ci_counts = {}

    active_admin_users = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role IN (?, ?, ?, ?, ?) AND COALESCE(is_active, 1) = 1",
        ADMIN_ACCESS_ROLES,
    ).fetchone()[0]
    recent_audit_actions = conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE timestamp >= datetime('now', '-7 days')"
    ).fetchone()[0]
    alert_rollup = {
        'ingestion': conn.execute(
            "SELECT COUNT(*) FROM ingestion_source_alerts WHERE state = 'open'"
        ).fetchone()[0],
        'courts': conn.execute(
            "SELECT COUNT(*) FROM court_source_alerts WHERE state = 'open'"
        ).fetchone()[0],
        'meetings': conn.execute(
            "SELECT COUNT(*) FROM meeting_source_alerts WHERE state = 'open'"
        ).fetchone()[0],
    }
    alert_rollup['total'] = (
        alert_rollup['ingestion']
        + alert_rollup['courts']
        + alert_rollup['meetings']
    )

    conn.close()

    return render_template('admin_dashboard.html',
                         total_records=total_records,
                         total_blotters=total_blotters,
                         total_counties=total_counties,
                         failed_ingestions=failed_ingestions,
                         active_admin_users=active_admin_users,
                         recent_audit_actions=recent_audit_actions,
                         alert_rollup=alert_rollup,
                         source_coverage=source_coverage,
                         recent_blotters=recent_blotters,
                         county_stats=county_stats,
                         county_inventory_rows=ci_rows_sorted,
                         county_inventory_counts=ci_counts)


@admin_bp.route('/operations/sources')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_sources():
    import app as _app_module

    q = (request.args.get('q') or '').strip()[:120]
    category_filter = (request.args.get('category') or 'all').strip().lower()
    health_filter = (request.args.get('health') or 'all').strip().lower()
    if category_filter not in {'all', 'covered', 'candidate', 'no_source'}:
        category_filter = 'all'
    if health_filter not in {'all', 'live', 'stale', 'failing'}:
        health_filter = 'all'

    conn = get_db()
    health_dashboard = _app_module._build_ingestion_health_dashboard(conn)
    conn.close()

    official_sources = []
    for item in health_dashboard['official_sources']:
        health_state = 'live'
        if item.get('latest_job_status') == 'failed' or item.get('failed_count', 0):
            health_state = 'failing'
        elif item.get('freshness_tone') in {'amber', 'red'} and item.get('category') == 'covered':
            health_state = 'stale'

        searchable = ' '.join([
            item.get('agency') or '',
            item.get('source_type') or '',
            item.get('source_url') or '',
            item.get('notes') or '',
        ]).lower()
        if q and q.lower() not in searchable:
            continue
        if category_filter != 'all' and item.get('category') != category_filter:
            continue
        if health_filter != 'all' and health_state != health_filter:
            continue
        enriched = dict(item)
        enriched['health_state'] = health_state
        official_sources.append(enriched)

    return render_template(
        'admin_sources.html',
        health_dashboard=health_dashboard,
        official_sources=official_sources,
        q=q,
        category_filter=category_filter,
        health_filter=health_filter,
    )


@admin_bp.route('/operations/courts')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_courts():
    conn = get_db()
    ensure_court_tracker_schema(conn)
    context = court_admin_context(conn)
    conn.close()
    return render_template('admin_courts.html', **context)


@admin_bp.route('/operations/meetings')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_meetings():
    conn = get_db()
    ensure_public_meeting_schema(conn)
    context = meeting_admin_context(conn)
    conn.close()
    return render_template('admin_meetings.html', **context)


@admin_bp.route('/operations/meetings/review', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_meeting_duplicate_review():
    action = (request.form.get('action') or '').strip().lower()
    source_slug = (request.form.get('source_slug') or '').strip()
    meeting_date = (request.form.get('meeting_date') or '').strip()
    meeting_time = (request.form.get('meeting_time') or '').strip()
    meeting_ids = []
    for value in request.form.getlist('meeting_id'):
        raw = (value or '').strip()
        if raw.isdigit():
            meeting_ids.append(int(raw))
    keeper_raw = (request.form.get('keeper_meeting_id') or '').strip()
    duplicate_raw = (request.form.get('duplicate_meeting_id') or '').strip()

    if action not in {'keep_both', 'merge'}:
        flash('Choose a valid meeting review action.', 'error')
        return redirect(url_for('admin.admin_meetings'))
    if not source_slug or not meeting_date or len(meeting_ids) < 2:
        flash('Meeting review request was incomplete.', 'error')
        return redirect(url_for('admin.admin_meetings'))

    keeper_meeting_id = int(keeper_raw) if keeper_raw.isdigit() else None
    duplicate_meeting_id = int(duplicate_raw) if duplicate_raw.isdigit() else None

    conn = get_db()
    ensure_public_meeting_schema(conn)
    try:
        result = review_duplicate_meeting_group(
            conn,
            source_slug=source_slug,
            meeting_date=meeting_date,
            meeting_time=meeting_time,
            meeting_ids=meeting_ids,
            action=action,
            keeper_meeting_id=keeper_meeting_id,
            duplicate_meeting_id=duplicate_meeting_id,
            decided_by_user_id=getattr(current_user, 'id', None),
        )
        _log_admin_action(
            f'meetings.duplicate_review.{action}',
            target_type='meeting_duplicate_review',
            target_id=result.get('review_key'),
            metadata={
                'source_slug': source_slug,
                'meeting_date': meeting_date,
                'meeting_time': meeting_time,
                'meeting_ids': meeting_ids,
                'keeper_meeting_id': keeper_meeting_id,
                'duplicate_meeting_id': duplicate_meeting_id,
                'migrated_docs': result.get('migrated_docs', 0),
            },
            conn=conn,
        )
        conn.commit()
    except ValueError as exc:
        conn.close()
        flash(str(exc), 'error')
        return redirect(url_for('admin.admin_meetings'))

    conn.close()
    if action == 'keep_both':
        flash('Marked the same-slot pair as intentionally distinct.', 'success')
    else:
        flash(
            f"Merged duplicate meeting #{duplicate_meeting_id} into #{keeper_meeting_id}.",
            'success',
        )
    return redirect(url_for('admin.admin_meetings'))


@admin_bp.route('/operations/data-center')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_data_center():
    conn = get_db()
    context = build_data_center_ops_summary(conn)
    conn.close()
    return render_template('admin_data_center.html', **context)


@admin_bp.route('/operations/jail-bookings')
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_jail_bookings():
    import app as _app_module

    conn = get_db()
    context = _app_module._jail_booking_admin_context(
        conn,
        county_filter=request.args.get('county'),
        status_filter=request.args.get('status'),
        q=request.args.get('q'),
    )
    conn.close()
    return render_template('admin_jail_bookings.html', **context)


@admin_bp.route('/operations/jail-bookings/create', methods=['POST'])
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_jail_booking_create():
    import app as _app_module

    source_id_raw = (request.form.get('source_id') or '').strip()
    person_name = (request.form.get('person_name') or '').strip()
    booking_status = _app_module._normalize_jail_booking_status(request.form.get('booking_status'))
    booking_number = (request.form.get('booking_number') or '').strip()[:120]
    charges_summary = (request.form.get('charges_summary') or '').strip()[:1200]
    arresting_agency = (request.form.get('arresting_agency') or '').strip()[:160]
    source_url = (request.form.get('source_url') or '').strip()[:500]
    notes = (request.form.get('notes') or '').strip()[:1000]
    booking_at = _app_module._normalize_jail_booking_datetime(request.form.get('booking_at'))
    age = None
    age_raw = (request.form.get('age') or '').strip()
    if age_raw:
        try:
            age = int(age_raw)
        except ValueError:
            flash('Age must be a whole number.', 'error')
            return redirect(url_for('admin.admin_jail_bookings'))

    if not source_id_raw.isdigit():
        flash('Choose a valid county source.', 'error')
        return redirect(url_for('admin.admin_jail_bookings'))
    if len(person_name) < 2:
        flash('Enter the booked person name.', 'error')
        return redirect(url_for('admin.admin_jail_bookings'))

    conn = get_db()
    _app_module._sync_jail_booking_sources(conn)
    source = conn.execute(
        'SELECT * FROM jail_booking_sources WHERE id = ? AND COALESCE(is_enabled, 1) = 1',
        (int(source_id_raw),),
    ).fetchone()
    if not source:
        conn.close()
        flash('Jail booking source not found.', 'error')
        return redirect(url_for('admin.admin_jail_bookings'))

    is_current = 0 if booking_status in {'released', 'archived'} else 1
    conn.execute(
        '''
        INSERT INTO jail_bookings (
            source_id,
            county_slug,
            county_name,
            facility_name,
            person_name,
            age,
            booking_number,
            booking_at,
            charges_summary,
            arresting_agency,
            source_url,
            booking_status,
            is_current,
            first_seen_at,
            last_seen_at,
            notes,
            created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?, datetime('now'), datetime('now'))
        ''',
        (
            source['id'],
            source['county_slug'],
            source['county_name'],
            source['facility_name'],
            person_name[:200],
            age,
            booking_number or None,
            booking_at,
            charges_summary,
            arresting_agency or None,
            source_url or source['roster_url'],
            booking_status,
            is_current,
            notes,
        ),
    )
    booking_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.execute(
        '''
        INSERT INTO jail_booking_runs (
            source_id,
            run_type,
            status,
            fetched_count,
            new_count,
            updated_count,
            missing_count,
            started_at,
            completed_at,
            notes
        ) VALUES (?, 'manual_entry', 'success', 1, 1, 0, 0, datetime('now'), datetime('now'), ?)
        ''',
        (source['id'], f'Manual booking entry for {person_name[:120]}'),
    )
    conn.execute(
        "UPDATE jail_booking_sources SET last_checked_at = datetime('now'), last_success_at = datetime('now') WHERE id = ?",
        (source['id'],),
    )
    _log_admin_action(
        'jail_booking.created',
        target_type='jail_booking',
        target_id=booking_id,
        metadata={
            'county_slug': source['county_slug'],
            'person_name': person_name[:120],
            'status': booking_status,
        },
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash(f'Added jail booking entry for {person_name}.', 'success')
    return redirect(url_for('admin.admin_jail_bookings'))


@admin_bp.route('/operations/jail-bookings/<int:booking_id>/status', methods=['POST'])
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_jail_booking_status(booking_id):
    import app as _app_module

    new_status = _app_module._normalize_jail_booking_status(request.form.get('booking_status'))
    conn = get_db()
    booking = conn.execute(
        '''
        SELECT
            jb.id,
            jb.person_name,
            jb.booking_status,
            jb.is_current,
            jb.release_at,
            jb.source_id,
            jb.county_slug
        FROM jail_bookings jb
        WHERE jb.id = ?
        ''',
        (booking_id,),
    ).fetchone()
    if not booking:
        conn.close()
        flash('Booking not found.', 'error')
        return redirect(url_for('admin.admin_jail_bookings'))

    new_is_current = 0 if new_status in {'released', 'archived'} else 1
    release_at = booking['release_at']
    if new_is_current == 0 and not release_at:
        release_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    if new_is_current == 1:
        release_at = None

    conn.execute(
        '''
        UPDATE jail_bookings
        SET booking_status = ?, is_current = ?, release_at = ?, last_seen_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
        ''',
        (new_status, new_is_current, release_at, booking_id),
    )
    conn.execute(
        '''
        INSERT INTO jail_booking_runs (
            source_id,
            run_type,
            status,
            fetched_count,
            new_count,
            updated_count,
            missing_count,
            started_at,
            completed_at,
            notes
        ) VALUES (?, 'status_update', 'success', 1, 0, 1, 0, datetime('now'), datetime('now'), ?)
        ''',
        (booking['source_id'], f'Status set to {new_status} for {booking["person_name"][:120]}'),
    )
    _log_admin_action(
        'jail_booking.status_changed',
        target_type='jail_booking',
        target_id=booking_id,
        metadata={
            'county_slug': booking['county_slug'],
            'person_name': booking['person_name'],
            'from': booking['booking_status'],
            'to': new_status,
        },
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash(
        f'Updated {booking["person_name"]} to '
        f'{_app_module.JAIL_BOOKING_STATUS_LABELS.get(new_status, new_status.title())}.',
        'success',
    )
    return redirect(url_for('admin.admin_jail_bookings'))


@admin_bp.route('/operations/missing-persons')
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_missing_persons():
    conn = get_db()
    context = missing_person_admin_context(
        conn,
        status_filter=request.args.get('status'),
        q=request.args.get('q'),
    )
    editing_person = None
    edit_id = request.args.get('edit', type=int)
    if edit_id:
        editing_person = get_missing_person_by_id(conn, edit_id)
    conn.close()
    return render_template(
        'admin_missing_persons.html',
        **context,
        editing_person=editing_person,
    )


@admin_bp.route('/operations/missing-persons/sync', methods=['POST'])
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_missing_person_sync():
    actor = getattr(current_user, 'username', '') or getattr(current_user, 'email', '') or 'admin'
    conn = get_db()
    try:
        result = sync_official_missing_persons(conn, actor=actor)
        _log_admin_action(
            'missing_person.synced',
            target_type='missing_person_sync',
            metadata={
                'active_total': result['active_total'],
                'created': result['created'],
                'updated': result['updated'],
                'reactivated': result['reactivated'],
                'resolved': result['resolved'],
                'official_last_updated': result['official_last_updated'],
            },
            conn=conn,
        )
        conn.commit()
        flash(
            'Official Montana DOJ sync completed: '
            f"{result['active_total']} active, {result['created']} new, "
            f"{result['reactivated']} reactivated, {result['resolved']} no longer listed.",
            'success',
        )
    except Exception as exc:
        conn.rollback()
        flash(f'Official missing-person sync failed: {exc}', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_missing_persons'))


@admin_bp.route('/operations/missing-persons/save', methods=['POST'])
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_missing_person_save():
    actor = getattr(current_user, 'username', '') or getattr(current_user, 'email', '') or 'admin'
    person_id_raw = (request.form.get('person_id') or '').strip()
    payload = {
        'full_name': request.form.get('full_name'),
        'age': request.form.get('age'),
        'city': request.form.get('city'),
        'county': request.form.get('county'),
        'last_seen_at': request.form.get('last_seen_at'),
        'last_seen_location': request.form.get('last_seen_location'),
        'summary': request.form.get('summary'),
        'physical_description': request.form.get('physical_description'),
        'contact_info': request.form.get('contact_info'),
        'source_name': request.form.get('source_name'),
        'source_url': request.form.get('source_url'),
        'photo_url': request.form.get('photo_url'),
        'status': request.form.get('status'),
        'resolution_summary': request.form.get('resolution_summary'),
    }

    conn = get_db()
    try:
        if person_id_raw.isdigit():
            person, should_notify = update_missing_person(conn, int(person_id_raw), payload, actor=actor)
            action_name = 'missing_person.updated'
            flash_message = f'Updated missing-person record for {person["full_name"]}.'
        else:
            person = create_missing_person(conn, payload, actor=actor)
            should_notify = person['status'] == 'missing'
            action_name = 'missing_person.created'
            flash_message = f'Created missing-person record for {person["full_name"]}.'

        email_stats = {'sent': 0, 'failed': 0, 'skipped': 0}
        if should_notify:
            email_stats = dispatch_missing_person_alerts(conn, person)
            flash_message += (
                f" Subscriber alerts: {email_stats['sent']} sent"
                f"{', ' + str(email_stats['failed']) + ' failed' if email_stats['failed'] else ''}."
            )

        _log_admin_action(
            action_name,
            target_type='missing_person',
            target_id=person['id'],
            metadata={
                'status': person['status'],
                'county': person.get('county'),
                'city': person.get('city'),
                'emails_sent': email_stats['sent'],
                'emails_failed': email_stats['failed'],
            },
            conn=conn,
        )
        conn.commit()
        flash(flash_message, 'success')
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_missing_persons'))


@admin_bp.route('/operations/missing-persons/<int:person_id>/status', methods=['POST'])
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_missing_person_status(person_id):
    actor = getattr(current_user, 'username', '') or getattr(current_user, 'email', '') or 'admin'
    new_status = request.form.get('status')
    resolution_summary = request.form.get('resolution_summary') or ''

    conn = get_db()
    try:
        person, should_notify = update_missing_person_status(
            conn,
            person_id,
            status=new_status,
            actor=actor,
            resolution_summary=resolution_summary,
        )
        email_stats = {'sent': 0, 'failed': 0, 'skipped': 0}
        if should_notify:
            email_stats = dispatch_missing_person_alerts(conn, person)
        _log_admin_action(
            'missing_person.status_changed',
            target_type='missing_person',
            target_id=person['id'],
            metadata={
                'status': person['status'],
                'emails_sent': email_stats['sent'],
                'emails_failed': email_stats['failed'],
            },
            conn=conn,
        )
        conn.commit()
        flash(
            f"{person['full_name']} marked {person['status_label'].lower()}."
            + (
                f" Subscriber alerts: {email_stats['sent']} sent."
                if should_notify else ''
            ),
            'success',
        )
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_missing_persons'))


@admin_bp.route('/operations/warrants')
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_warrants():
    conn = get_db()
    context = warrant_admin_context(
        conn,
        q=request.args.get('q'),
        county=request.args.get('county'),
        photo_filter=request.args.get('photo'),
    )
    editing_warrant = None
    edit_id = request.args.get('edit', type=int)
    if edit_id:
        editing_warrant = get_warrant_by_id(conn, edit_id)
    conn.close()
    return render_template(
        'admin_warrants.html',
        **context,
        editing_warrant=editing_warrant,
    )


@admin_bp.route('/operations/warrants/save', methods=['POST'])
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_warrant_photo_save():
    actor = getattr(current_user, 'username', '') or getattr(current_user, 'email', '') or 'admin'
    warrant_id_raw = (request.form.get('warrant_id') or '').strip()
    if not warrant_id_raw.isdigit():
        flash('Invalid warrant record.', 'error')
        return redirect(url_for('admin.admin_warrants'))

    run_ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    try:
        record = update_warrant_photo_fields(
            conn,
            int(warrant_id_raw),
            photo_url=request.form.get('photo_url') or '',
            social_profile_url=request.form.get('social_profile_url') or '',
            run_ts=run_ts,
        )
        if not record:
            flash('Warrant record not found.', 'error')
            return redirect(url_for('admin.admin_warrants'))

        _log_admin_action(
            'warrant.photo_updated',
            target_type='warrant',
            target_id=record['id'],
            metadata={
                'person_name': record['person_name'],
                'county': record['county'],
                'has_staff_photo': bool(record.get('photo_url')),
                'has_social_link': bool(record.get('social_profile_url')),
                'actor': actor,
            },
            conn=conn,
        )
        conn.commit()
        flash(
            f'Saved photo settings for {record["person_name"]}. '
            'Staff photos override official sheriff mugshots on /wanted.',
            'success',
        )
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_warrants', edit=int(warrant_id_raw)))


@admin_bp.route('/operations/warrants/<int:warrant_id>/clear-photo', methods=['POST'])
@login_required
@require_role(*OPERATIONS_ROLES)
def admin_warrant_photo_clear(warrant_id: int):
    run_ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    try:
        if not clear_warrant_staff_photo(conn, warrant_id, run_ts=run_ts):
            flash('Warrant record not found.', 'error')
        else:
            _log_admin_action(
                'warrant.photo_cleared',
                target_type='warrant',
                target_id=warrant_id,
                conn=conn,
            )
            conn.commit()
            flash('Cleared staff-approved photo and social profile link.', 'success')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_warrants', edit=warrant_id))


@admin_bp.route('/content/social', methods=['GET', 'POST'])
@login_required
def admin_facebook():
    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()

        if action == 'save_settings':
            settings = save_facebook_settings({
                'page_id': request.form.get('page_id', ''),
                'access_token': request.form.get('access_token', ''),
                'base_url': request.form.get('base_url', ''),
                'template': request.form.get('template', ''),
                'enabled': request.form.get('enabled'),
                'auto_enqueue_enabled': request.form.get('auto_enqueue_enabled'),
                'auto_publish_enabled': request.form.get('auto_publish_enabled'),
                'max_per_run': request.form.get('max_per_run', '3'),
            })
            flash(
                f"Facebook settings saved. "
                f"{'Enabled' if settings['enabled'] else 'Disabled'} / "
                f"{'Auto-publish ON' if settings['auto_publish_enabled'] else 'Auto-publish OFF'}."
            )

        elif action == 'queue_post':
            post_id_raw = request.form.get('post_id', '0')
            try:
                post_id = int(post_id_raw)
            except ValueError:
                post_id = 0

            if post_id <= 0:
                flash('Invalid post ID.')
            else:
                result = queue_post(
                    post_id=post_id,
                    created_by_user_id=current_user.id,
                    enqueue_source='admin_manual',
                )
                if not result.get('ok'):
                    flash('Unable to queue post (not found).')
                elif result.get('created'):
                    flash(f'Queued post #{post_id} for Facebook.')
                elif result.get('requeued'):
                    flash(f'Re-queued post #{post_id} for Facebook retry.')
                else:
                    flash(f'Post #{post_id} already in queue ({result.get("status")}).')

        elif action == 'queue_recent':
            limit_raw = request.form.get('limit', '10')
            try:
                limit = int(limit_raw)
            except ValueError:
                limit = 10
            stats = queue_recent_posts(
                limit=limit,
                created_by_user_id=current_user.id,
                enqueue_source='admin_bulk_recent',
            )
            flash(
                f"Queue recent complete. "
                f"Created: {stats['created']}, Re-queued: {stats['requeued']}, Skipped: {stats['skipped']}."
            )

        elif action == 'publish_queue_item':
            queue_id_raw = request.form.get('queue_id', '0')
            try:
                queue_id = int(queue_id_raw)
            except ValueError:
                queue_id = 0
            if queue_id <= 0:
                flash('Invalid queue item.')
            else:
                result = publish_queue_item(queue_id)
                if result.get('ok'):
                    flash(f"Published queue item #{queue_id} to Facebook ({result.get('facebook_post_id')}).")
                else:
                    flash(f"Queue item #{queue_id} failed: {result.get('error')}.")

        elif action == 'retry_queue_item':
            queue_id_raw = request.form.get('queue_id', '0')
            try:
                queue_id = int(queue_id_raw)
            except ValueError:
                queue_id = 0
            if queue_id <= 0:
                flash('Invalid queue item.')
            else:
                conn = get_db()
                updated = conn.execute(
                    """
                    UPDATE facebook_post_queue
                    SET status = 'queued',
                        last_error = NULL,
                        scheduled_for = datetime('now'),
                        updated_at = datetime('now')
                    WHERE id = ? AND status IN ('failed', 'skipped')
                    """,
                    (queue_id,),
                ).rowcount
                conn.commit()
                conn.close()
                if updated:
                    flash(f'Queue item #{queue_id} moved back to queued.')
                else:
                    flash(f'Queue item #{queue_id} is not retryable.')

        elif action == 'run_publisher':
            max_items_raw = request.form.get('max_items', '')
            max_items = None
            if max_items_raw:
                try:
                    max_items = int(max_items_raw)
                except ValueError:
                    max_items = None
            stats = run_facebook_queue(max_items=max_items, manual_trigger=True)
            if stats.get('ok'):
                if stats.get('skipped_reason'):
                    flash(f"Publisher skipped: {stats['skipped_reason']}.")
                else:
                    flash(
                        f"Publisher run complete. "
                        f"Processed: {stats['processed']}, Posted: {stats['posted']}, Failed: {stats['failed']}."
                    )
            else:
                flash(f"Publisher failed: {stats.get('error', 'unknown error')}.")

        else:
            flash('Unknown Facebook action.')

        return redirect(url_for('admin.admin_facebook'))

    conn = get_db()
    settings = load_facebook_settings(conn)
    token_preview = mask_token(settings.get('access_token', ''))

    latest_queue_by_post = {
        row['post_id']: row['status']
        for row in conn.execute(
            '''
            SELECT q.post_id, q.status
            FROM facebook_post_queue q
            JOIN (
                SELECT post_id, MAX(id) AS latest_id
                FROM facebook_post_queue
                GROUP BY post_id
            ) latest ON latest.latest_id = q.id
            '''
        ).fetchall()
    }

    recent_posts = []
    for row in conn.execute(
        """
        SELECT id, title, county, agency_name, incident_date, created_at
        FROM posts
        ORDER BY incident_date DESC, created_at DESC
        LIMIT 25
        """
    ).fetchall():
        item = dict(row)
        item['queue_status'] = latest_queue_by_post.get(row['id'])
        recent_posts.append(item)

    queue_rows = conn.execute(
        """
        SELECT
            q.id,
            q.post_id,
            q.status,
            q.scheduled_for,
            q.attempts,
            q.max_attempts,
            q.facebook_post_id,
            q.last_error,
            q.enqueue_source,
            q.created_at,
            q.posted_at,
            p.title,
            p.county,
            p.agency_name,
            p.incident_date
        FROM facebook_post_queue q
        LEFT JOIN posts p ON p.id = q.post_id
        ORDER BY
            CASE q.status
                WHEN 'processing' THEN 0
                WHEN 'queued' THEN 1
                WHEN 'failed' THEN 2
                WHEN 'posted' THEN 3
                ELSE 4
            END,
            datetime(COALESCE(q.scheduled_for, q.created_at)) ASC,
            q.id DESC
        LIMIT 120
        """
    ).fetchall()
    conn.close()

    return render_template(
        'admin_facebook.html',
        settings=settings,
        token_preview=token_preview,
        queue_rows=queue_rows,
        recent_posts=recent_posts,
    )


@admin_bp.route('/system/settings', methods=['GET', 'POST'])
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_settings():
    """Typed admin settings backed by app_settings."""
    import app as _app_module

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()
        conn = get_db()
        try:
            if action == 'change_password':
                new_password = (request.form.get('new_password') or '').strip()
                if len(new_password) < 12:
                    raise ValueError('Password must be at least 12 characters.')

                hashed_pw = _app_module.bcrypt.generate_password_hash(new_password).decode('utf-8')
                conn.execute(
                    'UPDATE users SET password = ? WHERE id = ?',
                    (hashed_pw, current_user.id),
                )
                _log_admin_action(
                    'security.password_changed',
                    target_type='user',
                    target_id=current_user.id,
                    metadata={'username': current_user.username},
                    conn=conn,
                )
                conn.commit()
                flash('Password updated successfully.', 'success')
            elif action == 'save_auth_settings':
                updates = {}
                for key in ('admin_login_max_attempts', 'admin_login_window_minutes', 'admin_login_lockout_minutes'):
                    value = _app_module._coerce_setting_value(key, request.form.get(key))
                    _save_app_setting(conn, key, value)
                    updates[key] = value
                _log_admin_action(
                    'settings.authentication_updated',
                    target_type='app_settings',
                    metadata=updates,
                    conn=conn,
                )
                conn.commit()
                flash('Authentication settings saved.', 'success')
            elif action == 'save_ingestion_settings':
                updates = {}
                for key in ('max_upload_mb', 'ingest_alert_repeat_hours'):
                    value = _app_module._coerce_setting_value(key, request.form.get(key))
                    _save_app_setting(conn, key, value)
                    updates[key] = value
                _log_admin_action(
                    'settings.ingestion_updated',
                    target_type='app_settings',
                    metadata=updates,
                    conn=conn,
                )
                conn.commit()
                _app_module._apply_runtime_app_settings(conn=conn)
                flash('Ingestion settings saved.', 'success')
            elif action == 'save_revenue_settings':
                updates = {}
                for key in (
                    'donations_enabled',
                    'winter_storm_banner_enabled',
                    'winter_storm_banner_headline',
                    'winter_storm_banner_body',
                ):
                    value = _app_module._coerce_setting_value(key, request.form.get(key))
                    _save_app_setting(conn, key, value)
                    updates[key] = value
                _log_admin_action(
                    'settings.revenue_updated',
                    target_type='app_settings',
                    metadata=updates,
                    conn=conn,
                )
                conn.commit()
                flash('Revenue settings saved.', 'success')
            else:
                raise ValueError('Unknown settings action.')
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), 'error')
        finally:
            conn.close()

        return redirect(url_for('admin.admin_settings'))

    conn = get_db()
    setting_values = _app_module._settings_form_values(conn)
    runtime_info = {
        'base_url': config.BASE_URL,
        'upload_dir': current_app.config['UPLOAD_FOLDER'],
        'max_upload_bytes': current_app.config.get('MAX_CONTENT_LENGTH'),
        'referrer_policy': config.REFERRER_POLICY,
        'api_cors_allow_origin': getattr(config, 'API_CORS_ALLOW_ORIGIN', '*'),
        'session_cookie_samesite': config.SESSION_COOKIE_SAMESITE,
        'content_security_policy': bool(config.CONTENT_SECURITY_POLICY),
        'donation_currency': _app_module._donation_currency().upper(),
    }
    conn.close()
    return render_template(
        'admin_settings.html',
        setting_values=setting_values,
        setting_specs=_app_module.APP_SETTING_SPECS,
        runtime_info=runtime_info,
    )


@admin_bp.route('/emails', methods=['GET', 'POST'])
@login_required
def admin_emails():
    """Legacy route redirected to the current digest email ops console."""
    return redirect(url_for('admin.admin_email_ops'), code=301)


@admin_bp.route('/emails/template/<template_type>')
@login_required
def get_email_template(template_type):
    return redirect(url_for('admin.admin_email_ops'), code=301)
