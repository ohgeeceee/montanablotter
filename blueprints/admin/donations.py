from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, OPERATIONS_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action


# ---------------------------------------------------------------------------
# Helpers that stay in app.py (also called from /webhooks/stripe):
#   _donation_launch_snapshot, _apply_stripe_event, _apply_stripe_bail_ad_event
# ---------------------------------------------------------------------------


@admin_bp.route('/donations')
@login_required
def admin_donations():
    import app as _app_module
    launch_snapshot = _app_module._donation_launch_snapshot()
    conn = get_db()
    schema_ready = launch_snapshot['schema_ready']
    totals_all_time = {'gross_cents': 0, 'success_count': 0, 'avg_cents': 0}
    totals_mtd = {'gross_cents': 0, 'success_count': 0, 'avg_cents': 0}
    totals_ytd = {'gross_cents': 0, 'success_count': 0, 'avg_cents': 0}
    recurring_stats = {'active_subscriptions': 0, 'monthly_success_count': 0}
    funnel_30d = {
        'donate_view': 0,
        'checkout_start': 0,
        'checkout_success': 0,
        'checkout_cancel': 0,
        'start_rate_pct': 0.0,
        'completion_rate_pct': 0.0,
    }
    webhook_7d = {
        'total': 0,
        'processed': 0,
        'unprocessed': 0,
        'error_count': 0,
    }
    recent_donations = []
    recent_events = []
    recent_webhook_errors = []
    top_sources_30d = []

    try:
        total_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0) AS gross_cents,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS success_count,
                COALESCE(AVG(CASE WHEN status = 'succeeded' THEN amount_cents END), 0) AS avg_cents
            FROM donations
            '''
        ).fetchone()
        if total_row:
            totals_all_time = dict(total_row)

        mtd_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0) AS gross_cents,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS success_count,
                COALESCE(AVG(CASE WHEN status = 'succeeded' THEN amount_cents END), 0) AS avg_cents
            FROM donations
            WHERE created_at >= date('now', 'start of month')
            '''
        ).fetchone()
        if mtd_row:
            totals_mtd = dict(mtd_row)

        ytd_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0) AS gross_cents,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS success_count,
                COALESCE(AVG(CASE WHEN status = 'succeeded' THEN amount_cents END), 0) AS avg_cents
            FROM donations
            WHERE created_at >= date('now', 'start of year')
            '''
        ).fetchone()
        if ytd_row:
            totals_ytd = dict(ytd_row)

        recurring_row = conn.execute(
            '''
            SELECT
                COALESCE(COUNT(DISTINCT provider_subscription_id), 0) AS active_subscriptions,
                COALESCE(SUM(CASE WHEN status = 'succeeded' AND mode = 'monthly' THEN 1 ELSE 0 END), 0) AS monthly_success_count
            FROM donations
            WHERE provider_subscription_id IS NOT NULL AND provider_subscription_id != ''
            '''
        ).fetchone()
        if recurring_row:
            recurring_stats = dict(recurring_row)

        funnel_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN event_type = 'donate_view' THEN 1 ELSE 0 END), 0) AS donate_view,
                COALESCE(SUM(CASE WHEN event_type = 'checkout_start' THEN 1 ELSE 0 END), 0) AS checkout_start,
                COALESCE(SUM(CASE WHEN event_type = 'checkout_success' THEN 1 ELSE 0 END), 0) AS checkout_success,
                COALESCE(SUM(CASE WHEN event_type = 'checkout_cancel' THEN 1 ELSE 0 END), 0) AS checkout_cancel
            FROM donation_events
            WHERE created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        if funnel_row:
            funnel_30d.update(dict(funnel_row))
            donate_views = float(funnel_30d['donate_view'] or 0)
            starts = float(funnel_30d['checkout_start'] or 0)
            successes = float(funnel_30d['checkout_success'] or 0)
            funnel_30d['start_rate_pct'] = (starts / donate_views * 100.0) if donate_views else 0.0
            funnel_30d['completion_rate_pct'] = (successes / starts * 100.0) if starts else 0.0

        webhook_row = conn.execute(
            '''
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END), 0) AS processed,
                COALESCE(SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END), 0) AS unprocessed,
                COALESCE(SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END), 0) AS error_count
            FROM payment_webhook_events
            WHERE created_at >= date('now', '-7 days')
            '''
        ).fetchone()
        if webhook_row:
            webhook_7d = dict(webhook_row)

        recent_donations = conn.execute(
            '''
            SELECT provider, mode, status, amount_cents, currency, donor_name, source, created_at
            FROM donations
            ORDER BY datetime(created_at) DESC
            LIMIT 30
            '''
        ).fetchall()

        recent_events = conn.execute(
            '''
            SELECT event_type, source, page_path, amount_cents, created_at
            FROM donation_events
            ORDER BY datetime(created_at) DESC
            LIMIT 30
            '''
        ).fetchall()

        recent_webhook_errors = conn.execute(
            '''
            SELECT event_type, error, created_at
            FROM payment_webhook_events
            WHERE error IS NOT NULL AND error != ''
            ORDER BY datetime(created_at) DESC
            LIMIT 20
            '''
        ).fetchall()

        top_sources_30d = conn.execute(
            '''
            SELECT
                COALESCE(NULLIF(source, ''), '(direct)') AS source,
                COUNT(*) AS donation_count,
                COALESCE(SUM(amount_cents), 0) AS gross_cents
            FROM donations
            WHERE status = 'succeeded'
              AND created_at >= date('now', '-30 days')
            GROUP BY COALESCE(NULLIF(source, ''), '(direct)')
            ORDER BY gross_cents DESC, donation_count DESC, source ASC
            LIMIT 10
            '''
        ).fetchall()
    except sqlite3.OperationalError:
        schema_ready = False
    finally:
        conn.close()

    launch_snapshot['schema_ready'] = schema_ready
    launch_snapshot['launch_ready'] = bool(
        launch_snapshot['schema_ready']
        and launch_snapshot['donations_enabled']
        and launch_snapshot['stripe_checkout_ready']
        and launch_snapshot['stripe_webhook_ready']
        and int(launch_snapshot['stale_webhook_events_10m'] or 0) == 0
    )

    donations_enabled = launch_snapshot['donations_enabled']
    stripe_checkout_ready = launch_snapshot['stripe_checkout_ready']
    stripe_webhook_ready = launch_snapshot['stripe_webhook_ready']
    return render_template(
        'admin_donations.html',
        donations_enabled=donations_enabled,
        stripe_checkout_ready=stripe_checkout_ready,
        stripe_webhook_ready=stripe_webhook_ready,
        launch_snapshot=launch_snapshot,
        schema_ready=schema_ready,
        totals_all_time=totals_all_time,
        totals_mtd=totals_mtd,
        totals_ytd=totals_ytd,
        recurring_stats=recurring_stats,
        funnel_30d=funnel_30d,
        webhook_7d=webhook_7d,
        recent_donations=recent_donations,
        recent_events=recent_events,
        recent_webhook_errors=recent_webhook_errors,
        top_sources_30d=top_sources_30d,
    )


@admin_bp.route('/donations/preflight')
@login_required
def admin_donations_preflight():
    import app as _app_module
    from flask import jsonify
    return jsonify(_app_module._donation_launch_snapshot())


@admin_bp.route('/donations/reconcile', methods=['POST'])
@login_required
def admin_donations_reconcile():
    import app as _app_module
    try:
        requested_limit = int(request.form.get('limit', 100))
    except (TypeError, ValueError):
        requested_limit = 100
    limit = max(1, min(500, requested_limit))

    conn = get_db()
    succeeded = 0
    failed = 0
    try:
        rows = conn.execute(
            '''
            SELECT event_id, payload_json
            FROM payment_webhook_events
            WHERE provider = 'stripe' AND processed = 0
            ORDER BY datetime(created_at) ASC
            LIMIT ?
            ''',
            (limit,),
        ).fetchall()

        for row in rows:
            event_id = row['event_id']
            payload_text = row['payload_json'] or ''
            try:
                event = json.loads(payload_text)
                if not isinstance(event, dict):
                    raise ValueError('Webhook payload is not a JSON object')

                _app_module._apply_stripe_bail_ad_event(conn, event)
                _app_module._apply_stripe_event(
                    conn,
                    event,
                    event_source='/admin/donations/reconcile',
                    event_ip_hash='',
                    event_referrer='',
                )
                conn.execute(
                    '''
                    UPDATE payment_webhook_events
                    SET processed = 1, processed_at = datetime('now'), error = NULL
                    WHERE event_id = ?
                    ''',
                    (event_id,),
                )
                conn.commit()
                succeeded += 1
            except Exception as exc:
                conn.rollback()
                conn.execute(
                    '''
                    UPDATE payment_webhook_events
                    SET processed = 0, processed_at = datetime('now'), error = ?
                    WHERE event_id = ?
                    ''',
                    (str(exc)[:500], event_id),
                )
                conn.commit()
                failed += 1
    except sqlite3.OperationalError:
        conn.close()
        flash('Donation webhook tables are not available. Run migration first.', 'error')
        return redirect(url_for('admin.admin_donations'))

    conn.close()
    flash(f'Reconciliation complete. Processed {succeeded} event(s), {failed} failed.', 'success' if failed == 0 else 'warning')
    return redirect(url_for('admin.admin_donations'))


@admin_bp.route('/donations/export.csv')
@login_required
def admin_donations_export():
    conn = get_db()
    try:
        rows = conn.execute(
            '''
            SELECT
                id, created_at, updated_at, provider, mode, status, amount_cents, currency,
                donor_name, source, provider_session_id, provider_payment_intent_id,
                provider_subscription_id, email_hash
            FROM donations
            ORDER BY datetime(created_at) DESC
            '''
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return Response('Donation tables are not available.\n', status=503, mimetype='text/plain')
    conn.close()

    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([
        'id',
        'created_at',
        'updated_at',
        'provider',
        'mode',
        'status',
        'amount_cents',
        'amount_usd',
        'currency',
        'donor_name',
        'source',
        'provider_session_id',
        'provider_payment_intent_id',
        'provider_subscription_id',
        'email_hash',
    ])
    for row in rows:
        amount_cents = int(row['amount_cents'] or 0)
        writer.writerow([
            row['id'],
            row['created_at'],
            row['updated_at'],
            row['provider'],
            row['mode'],
            row['status'],
            amount_cents,
            f'{amount_cents / 100:.2f}',
            (row['currency'] or '').upper(),
            row['donor_name'] or '',
            row['source'] or '',
            row['provider_session_id'] or '',
            row['provider_payment_intent_id'] or '',
            row['provider_subscription_id'] or '',
            row['email_hash'] or '',
        ])

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename=donations_export_{timestamp}.csv'
    response.headers['Cache-Control'] = 'no-store'
    return response
