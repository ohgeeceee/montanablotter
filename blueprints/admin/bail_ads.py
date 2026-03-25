from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timedelta

from flask import Response, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, OPERATIONS_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action

# ---------------------------------------------------------------------------
# All bail-ad helpers (_bail_ad_package_lookup, _bail_agency_dedupe_key, etc.)
# and shared constants (_BAIL_OUTREACH_STATUSES) stay in app.py — they are
# also used by public /advertise/bail-bonds/* routes.  Access them via the
# lazy import pattern below.
# ---------------------------------------------------------------------------


@admin_bp.route('/bail-ads')
@login_required
def admin_bail_ads():
    import app as _app_module
    package_map = _app_module._bail_ad_package_lookup()
    q = (request.args.get('q') or '').strip()[:120]
    status_filter = (request.args.get('status') or 'all').strip().lower()
    if status_filter not in _app_module._BAIL_OUTREACH_STATUSES and status_filter != 'all':
        status_filter = 'all'
    stats = {
        'pending': 0,
        'in_review': 0,
        'approved': 0,
        'declined': 0,
        'total': 0,
    }
    order_stats = {
        'checkout_pending': 0,
        'active': 0,
        'active_pending_creative_review': 0,
        'payment_failed': 0,
        'canceled': 0,
        'total': 0,
    }
    inquiries = []
    orders = []
    creatives = []
    performance_30d = {
        'impressions': 0,
        'clicks': 0,
        'leads': 0,
        'ctr_pct': 0.0,
        'lead_rate_pct': 0.0,
    }
    county_performance_30d = []
    renewal_candidates = []
    upgrade_candidates = []
    consumer_pipeline_30d = {
        'calls': 0,
        'qualified_leads': 0,
        'booked_bonds': 0,
        'booked_from_qualified_pct': 0.0,
    }
    county_pipeline_30d = []
    consumer_leads = []
    advertiser_pipeline_30d = []
    agencies = []
    email_logs = []
    status_counts = {status: 0 for status in sorted(_app_module._BAIL_OUTREACH_STATUSES)}
    total_count = 0
    simulator_stats = {
        'page_views': 0,
        'logo_uploads': 0,
        'share_links': 0,
        'inquiry_syncs': 0,
        'checkout_clicks': 0,
    }
    schema_ready = True

    conn = get_db()
    try:
        _app_module._ensure_bail_ad_simulator_order_columns(conn)
        _app_module._ensure_bail_ad_simulator_event_schema(conn)
        stats_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
                COALESCE(SUM(CASE WHEN status = 'in_review' THEN 1 ELSE 0 END), 0) AS in_review,
                COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0) AS approved,
                COALESCE(SUM(CASE WHEN status = 'declined' THEN 1 ELSE 0 END), 0) AS declined,
                COUNT(*) AS total
            FROM bail_ad_inquiries
            '''
        ).fetchone()
        if stats_row:
            stats = dict(stats_row)

        inquiries = conn.execute(
            '''
            SELECT
                id, business_name, contact_name, email, phone, website_url, license_number,
                counties_served, package_interest, monthly_budget_cents, source, status,
                review_notes, reviewed_by, reviewed_at, created_at
            FROM bail_ad_inquiries
            ORDER BY datetime(created_at) DESC
            LIMIT 200
            '''
        ).fetchall()

        order_stats_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'checkout_pending' THEN 1 ELSE 0 END), 0) AS checkout_pending,
                COALESCE(SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END), 0) AS active,
                COALESCE(SUM(CASE WHEN status = 'active_pending_creative_review' THEN 1 ELSE 0 END), 0) AS active_pending_creative_review,
                COALESCE(SUM(CASE WHEN status = 'payment_failed' THEN 1 ELSE 0 END), 0) AS payment_failed,
                COALESCE(SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END), 0) AS canceled,
                COUNT(*) AS total
            FROM bail_ad_orders
            '''
        ).fetchone()
        if order_stats_row:
            order_stats = dict(order_stats_row)

        orders = conn.execute(
            '''
            SELECT
                id, business_name, contact_name, email, phone, website_url, license_number,
                package_id, billing_cycle, amount_cents, currency,
                status, county_targets, add_on_ids, notes, provider_session_id, provider_subscription_id,
                onboarding_token, paid_at, created_at
            FROM bail_ad_orders
            ORDER BY datetime(created_at) DESC
            LIMIT 120
            '''
        ).fetchall()

        creatives = conn.execute(
            '''
            SELECT
                bail_ad_creatives.id,
                bail_ad_creatives.order_id,
                bail_ad_creatives.headline,
                bail_ad_creatives.target_url,
                bail_ad_creatives.logo_path,
                bail_ad_creatives.status,
                bail_ad_creatives.review_notes,
                bail_ad_creatives.reviewed_by,
                bail_ad_creatives.reviewed_at,
                bail_ad_creatives.updated_at,
                bail_ad_orders.business_name
            FROM bail_ad_creatives
            JOIN bail_ad_orders ON bail_ad_orders.id = bail_ad_creatives.order_id
            ORDER BY datetime(bail_ad_creatives.updated_at) DESC
            LIMIT 120
            '''
        ).fetchall()

        perf_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
                COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
                COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads
            FROM bail_ad_events
            WHERE created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        if perf_row:
            performance_30d.update(dict(perf_row))
            impressions = float(performance_30d['impressions'] or 0)
            clicks = float(performance_30d['clicks'] or 0)
            leads = float(performance_30d['leads'] or 0)
            performance_30d['ctr_pct'] = (clicks / impressions * 100.0) if impressions else 0.0
            performance_30d['lead_rate_pct'] = (leads / clicks * 100.0) if clicks else 0.0

        county_performance_30d = conn.execute(
            '''
            SELECT
                COALESCE(NULLIF(county, ''), '(unassigned)') AS county,
                COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
                COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
                COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads
            FROM bail_ad_events
            WHERE created_at >= date('now', '-30 days')
            GROUP BY COALESCE(NULLIF(county, ''), '(unassigned)')
            ORDER BY clicks DESC, impressions DESC, county ASC
            LIMIT 20
            '''
        ).fetchall()

        _app_module._ensure_bail_consumer_lead_schema(conn)
        consumer_totals_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status IN ('qualified', 'booked') THEN 1 ELSE 0 END), 0) AS qualified_leads,
                COALESCE(SUM(CASE WHEN status = 'booked' THEN 1 ELSE 0 END), 0) AS booked_bonds
            FROM bail_consumer_leads
            WHERE created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        calls_row = conn.execute(
            '''
            SELECT COUNT(*) AS calls
            FROM bail_ad_events
            WHERE event_type IN ('call', 'lead')
              AND created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        consumer_pipeline_30d['calls'] = int((calls_row['calls'] if calls_row else 0) or 0)
        if consumer_totals_row:
            consumer_pipeline_30d['qualified_leads'] = int(consumer_totals_row['qualified_leads'] or 0)
            consumer_pipeline_30d['booked_bonds'] = int(consumer_totals_row['booked_bonds'] or 0)
        qualified_total = float(consumer_pipeline_30d['qualified_leads'] or 0)
        booked_total = float(consumer_pipeline_30d['booked_bonds'] or 0)
        consumer_pipeline_30d['booked_from_qualified_pct'] = (booked_total / qualified_total * 100.0) if qualified_total else 0.0

        calls_by_county = {
            (row['county'] or '(unassigned)'): int(row['calls'] or 0)
            for row in conn.execute(
                '''
                SELECT COALESCE(NULLIF(county, ''), '(unassigned)') AS county, COUNT(*) AS calls
                FROM bail_ad_events
                WHERE event_type IN ('call', 'lead')
                  AND created_at >= date('now', '-30 days')
                GROUP BY COALESCE(NULLIF(county, ''), '(unassigned)')
                '''
            ).fetchall()
        }
        leads_by_county = {
            (row['county'] or '(unassigned)'): dict(row)
            for row in conn.execute(
                '''
                SELECT
                    COALESCE(NULLIF(county, ''), '(unassigned)') AS county,
                    COALESCE(SUM(CASE WHEN status IN ('qualified', 'booked') THEN 1 ELSE 0 END), 0) AS qualified_leads,
                    COALESCE(SUM(CASE WHEN status = 'booked' THEN 1 ELSE 0 END), 0) AS booked_bonds
                FROM bail_consumer_leads
                WHERE created_at >= date('now', '-30 days')
                GROUP BY COALESCE(NULLIF(county, ''), '(unassigned)')
                '''
            ).fetchall()
        }
        county_keys = set(calls_by_county.keys()) | set(leads_by_county.keys())
        county_pipeline_30d = sorted(
            [
                {
                    'county': county_name,
                    'calls': int(calls_by_county.get(county_name, 0) or 0),
                    'qualified_leads': int((leads_by_county.get(county_name) or {}).get('qualified_leads') or 0),
                    'booked_bonds': int((leads_by_county.get(county_name) or {}).get('booked_bonds') or 0),
                }
                for county_name in county_keys
            ],
            key=lambda item: (item['booked_bonds'], item['qualified_leads'], item['calls'], item['county']),
            reverse=True,
        )[:24]

        consumer_leads = conn.execute(
            '''
            SELECT
                id,
                full_name,
                phone,
                email,
                county,
                jail_facility,
                callback_preference,
                source,
                status,
                routed_business_names,
                review_notes,
                reviewed_by,
                reviewed_at,
                created_at
            FROM bail_consumer_leads
            ORDER BY datetime(created_at) DESC
            LIMIT 120
            '''
        ).fetchall()

        advertiser_pipeline_30d = _app_module._bail_advertiser_attribution_30d(conn, limit=120)

        events_by_order = {
            row['order_id']: row
            for row in conn.execute(
                '''
                SELECT
                    order_id,
                    COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
                    COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
                    COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads
                FROM bail_ad_events
                WHERE order_id IS NOT NULL
                  AND created_at >= date('now', '-30 days')
                GROUP BY order_id
                '''
            ).fetchall()
        }

        now_utc = datetime.utcnow()
        package_map = _app_module._bail_ad_package_lookup()
        for order in orders:
            order_dict = dict(order)
            paid_at_raw = order_dict.get('paid_at') or order_dict.get('created_at')
            paid_at = _app_module._parse_sqlite_timestamp(paid_at_raw)
            if not paid_at:
                continue

            cycle = (order_dict.get('billing_cycle') or 'monthly').lower()
            renewal_days = 365 if cycle == 'annual' else 30
            next_renewal = paid_at + timedelta(days=renewal_days)
            days_to_renewal = int((next_renewal - now_utc).total_seconds() // 86400)

            if order_dict.get('status') in {'active', 'active_pending_creative_review'} and days_to_renewal <= 14:
                renewal_candidates.append({
                    'id': order_dict['id'],
                    'business_name': order_dict.get('business_name') or '',
                    'package_id': order_dict.get('package_id') or '',
                    'billing_cycle': cycle,
                    'days_to_renewal': days_to_renewal,
                    'next_renewal': next_renewal.strftime('%Y-%m-%d'),
                })

            metrics = events_by_order.get(order_dict['id']) or {'impressions': 0, 'clicks': 0, 'leads': 0}
            counties = _app_module._bail_ad_county_list(order_dict.get('county_targets') or '')
            package_id = (order_dict.get('package_id') or '').lower()
            click_count = int(metrics['clicks'] or 0)
            recommendation = ''
            if package_id in {'starter', 'silver_link', 'exclusive_county_sponsorship'} and click_count >= 15:
                recommendation = 'Upgrade to The Gold Bond Bundle for top banner + sidebar + 2 counties.'
            elif package_id in {'featured_bondsman_banner', 'emergency_call_sidebar'} and click_count >= 20:
                recommendation = 'Upgrade to The Gold Bond Bundle for multi-touch coverage across placements.'
            elif package_id in {'growth', 'gold_bond'} and click_count >= 25:
                recommendation = 'Migrate this account to The Gold Bond Bundle pricing framework.'
            elif package_id == 'gold_bond_bundle' and click_count >= 40:
                recommendation = 'Add one Exclusive County Sponsorship for deeper local saturation.'

            if recommendation:
                pkg = package_map.get(package_id) or {}
                upgrade_candidates.append({
                    'id': order_dict['id'],
                    'business_name': order_dict.get('business_name') or '',
                    'package_id': package_id,
                    'clicks': int(metrics['clicks'] or 0),
                    'impressions': int(metrics['impressions'] or 0),
                    'county_count': len(counties),
                    'county_slots': int(pkg.get('county_slots') or 0),
                    'recommendation': recommendation,
                })

        _app_module._ensure_bail_agency_outreach_schema(conn)
        _app_module._seed_bail_agency_outreach(conn)
        conn.commit()

        count_row = conn.execute(
            '''
            SELECT
                COUNT(*) AS total_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'new' THEN 1 ELSE 0 END), 0) AS new_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'queued' THEN 1 ELSE 0 END), 0) AS queued_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'contacted' THEN 1 ELSE 0 END), 0) AS contacted_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'replied' THEN 1 ELSE 0 END), 0) AS replied_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'meeting_scheduled' THEN 1 ELSE 0 END), 0) AS meeting_scheduled_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'closed_won' THEN 1 ELSE 0 END), 0) AS closed_won_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'closed_lost' THEN 1 ELSE 0 END), 0) AS closed_lost_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'do_not_contact' THEN 1 ELSE 0 END), 0) AS do_not_contact_count
            FROM bail_agency_outreach
            '''
        ).fetchone()
        if count_row:
            total_count = int(count_row['total_count'] or 0)
            for key in status_counts:
                status_counts[key] = int(count_row[f'{key}_count'] or 0)

        clauses = []
        params = []
        if status_filter != 'all':
            clauses.append('outreach_status = ?')
            params.append(status_filter)
        if q:
            like = f'%{q}%'
            clauses.append(
                '(agency_name LIKE ? OR contact_name LIKE ? OR email LIKE ? OR phone LIKE ? OR counties LIKE ? OR notes LIKE ?)'
            )
            params.extend([like, like, like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        agency_rows = conn.execute(
            f'''
            SELECT
                id, agency_name, contact_name, email, phone, counties, source, outreach_status,
                last_contacted_at, next_follow_up_at, owner,
                email_subject_template, email_body_template, call_script_template, notes,
                created_at, updated_at
            FROM bail_agency_outreach
            {where_sql}
            ORDER BY
                CASE WHEN next_follow_up_at IS NULL OR next_follow_up_at = '' THEN 1 ELSE 0 END ASC,
                date(next_follow_up_at) ASC,
                datetime(updated_at) DESC
            LIMIT 400
            ''',
            tuple(params),
        ).fetchall()
        for row in agency_rows:
            agency = dict(row)
            agency.update(_app_module._bail_agency_rendered_templates(agency))
            agencies.append(agency)

        email_logs = conn.execute(
            '''
            SELECT
                id,
                agency_id,
                agency_name,
                recipient_email,
                email_kind,
                subject,
                sent_by,
                send_status,
                error_message,
                created_at
            FROM bail_agency_email_logs
            ORDER BY datetime(created_at) DESC
            LIMIT 120
            '''
        ).fetchall()

        simulator_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END), 0) AS page_views,
                COALESCE(SUM(CASE WHEN event_type = 'logo_upload' THEN 1 ELSE 0 END), 0) AS logo_uploads,
                COALESCE(SUM(CASE WHEN event_type = 'share_link' THEN 1 ELSE 0 END), 0) AS share_links,
                COALESCE(SUM(CASE WHEN event_type = 'inquiry_sync' THEN 1 ELSE 0 END), 0) AS inquiry_syncs,
                COALESCE(SUM(CASE WHEN event_type = 'checkout_click' THEN 1 ELSE 0 END), 0) AS checkout_clicks
            FROM bail_ad_simulator_events
            WHERE created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        if simulator_row:
            simulator_stats.update(dict(simulator_row))
    except sqlite3.OperationalError:
        schema_ready = False
    finally:
        conn.close()

    return render_template(
        'admin_bail_ads.html',
        schema_ready=schema_ready,
        stats=stats,
        order_stats=order_stats,
        inquiries=inquiries,
        orders=orders,
        creatives=creatives,
        performance_30d=performance_30d,
        county_performance_30d=county_performance_30d,
        renewal_candidates=renewal_candidates,
        upgrade_candidates=upgrade_candidates,
        consumer_pipeline_30d=consumer_pipeline_30d,
        county_pipeline_30d=county_pipeline_30d,
        consumer_leads=consumer_leads,
        advertiser_pipeline_30d=advertiser_pipeline_30d,
        agencies=agencies,
        total_count=total_count,
        status_counts=status_counts,
        status_filter=status_filter,
        q=q,
        outreach_statuses=sorted(_app_module._BAIL_OUTREACH_STATUSES),
        default_test_email=_app_module._default_bail_test_email(),
        email_logs=email_logs,
        simulator_stats=simulator_stats,
        package_map=package_map,
    )


@admin_bp.route('/bail-ads/agencies')
@login_required
def admin_bail_agency_cms():
    import app as _app_module
    q = (request.args.get('q') or '').strip()[:120]
    status_filter = (request.args.get('status') or 'all').strip().lower()
    if status_filter not in _app_module._BAIL_OUTREACH_STATUSES and status_filter != 'all':
        status_filter = 'all'
    target = url_for('admin.admin_bail_ads', q=q, status=status_filter)
    return redirect(f'{target}#agency-cms')


@admin_bp.route('/bail-ads/simulator')
@login_required
def admin_bail_ads_simulator():
    import app as _app_module
    conn = get_db()
    sales_agencies = []
    recent_creatives = []
    simulator_stats = {
        'page_views': 0,
        'logo_uploads': 0,
        'share_links': 0,
        'inquiry_syncs': 0,
        'checkout_clicks': 0,
    }
    try:
        _app_module._ensure_bail_agency_outreach_schema(conn)
        _app_module._ensure_bail_ad_simulator_order_columns(conn)
        _app_module._ensure_bail_ad_simulator_event_schema(conn)
        conn.commit()

        simulator_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END), 0) AS page_views,
                COALESCE(SUM(CASE WHEN event_type = 'logo_upload' THEN 1 ELSE 0 END), 0) AS logo_uploads,
                COALESCE(SUM(CASE WHEN event_type = 'share_link' THEN 1 ELSE 0 END), 0) AS share_links,
                COALESCE(SUM(CASE WHEN event_type = 'inquiry_sync' THEN 1 ELSE 0 END), 0) AS inquiry_syncs,
                COALESCE(SUM(CASE WHEN event_type = 'checkout_click' THEN 1 ELSE 0 END), 0) AS checkout_clicks
            FROM bail_ad_simulator_events
            WHERE created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        if simulator_row:
            simulator_stats.update(dict(simulator_row))

        sales_agencies = conn.execute(
            '''
            SELECT
                a.id,
                a.agency_name,
                a.contact_name,
                a.email,
                a.phone,
                a.counties,
                a.outreach_status,
                COALESCE((
                    SELECT c.logo_path
                    FROM bail_ad_orders o
                    LEFT JOIN bail_ad_creatives c ON c.order_id = o.id
                    WHERE lower(o.business_name) = lower(a.agency_name)
                      AND COALESCE(c.logo_path, '') != ''
                    ORDER BY datetime(o.created_at) DESC
                    LIMIT 1
                ), (
                    SELECT o.simulator_logo_path
                    FROM bail_ad_orders o
                    WHERE lower(o.business_name) = lower(a.agency_name)
                      AND COALESCE(o.simulator_logo_path, '') != ''
                    ORDER BY datetime(o.created_at) DESC
                    LIMIT 1
                ), '') AS logo_path,
                COALESCE((
                    SELECT c.target_url
                    FROM bail_ad_orders o
                    LEFT JOIN bail_ad_creatives c ON c.order_id = o.id
                    WHERE lower(o.business_name) = lower(a.agency_name)
                      AND COALESCE(c.target_url, '') != ''
                    ORDER BY datetime(o.created_at) DESC
                    LIMIT 1
                ), (
                    SELECT o.simulator_target_url
                    FROM bail_ad_orders o
                    WHERE lower(o.business_name) = lower(a.agency_name)
                      AND COALESCE(o.simulator_target_url, '') != ''
                    ORDER BY datetime(o.created_at) DESC
                    LIMIT 1
                ), '') AS target_url
            FROM bail_agency_outreach a
            ORDER BY datetime(a.updated_at) DESC
            LIMIT 40
            '''
        ).fetchall()

        recent_creatives = conn.execute(
            '''
            SELECT
                o.business_name AS agency_name,
                COALESCE(c.logo_path, o.simulator_logo_path, '') AS logo_path,
                COALESCE(c.target_url, o.simulator_target_url, o.website_url, '') AS target_url,
                o.package_id,
                o.created_at
            FROM bail_ad_orders o
            LEFT JOIN bail_ad_creatives c ON c.order_id = o.id
            WHERE COALESCE(c.logo_path, o.simulator_logo_path, '') != ''
            ORDER BY datetime(o.created_at) DESC
            LIMIT 24
            '''
        ).fetchall()
    finally:
        conn.close()

    simulator_view = (request.args.get('sim_view') or '').strip().lower()
    if simulator_view not in {'banner', 'sidebar'}:
        simulator_view = 'banner'
    simulator_bootstrap = {
        'agencyName': (request.args.get('agency_name') or request.args.get('agency') or 'Your Agency').strip()[:80] or 'Your Agency',
        'initialImageUrl': _app_module._safe_bail_ad_simulator_image_url(request.args.get('logo_url') or request.args.get('logo') or ''),
        'initialView': simulator_view,
        'initialCounty': (request.args.get('sim_county') or 'Cascade County').strip()[:80] or 'Cascade County',
        'initialTargetUrl': (request.args.get('target_url') or '').strip()[:300],
        'publicPreviewBaseUrl': url_for('advertise_bail_bonds'),
        'checkoutBaseUrl': url_for('advertise_bail_bonds_checkout'),
        'uploadEndpoint': url_for('upload_bail_ad_simulator_asset'),
        'eventEndpoint': url_for('track_bail_ad_simulator_event'),
        'internalMode': True,
        'allowInquirySync': False,
    }

    return render_template(
        'admin_bail_ad_simulator.html',
        simulator_bootstrap=simulator_bootstrap,
        simulator_stats=simulator_stats,
        sales_agencies=sales_agencies,
        recent_creatives=recent_creatives,
    )


@admin_bp.route('/bail-ads/agencies/create', methods=['POST'])
@login_required
def admin_bail_agency_cms_create():
    import app as _app_module
    agency_name = (request.form.get('agency_name') or '').strip()[:160]
    contact_name = (request.form.get('contact_name') or '').strip()[:120]
    email = (request.form.get('email') or '').strip().lower()[:160]
    phone = (request.form.get('phone') or '').strip()[:40]
    counties = (request.form.get('counties') or '').strip()[:500]
    source = (request.form.get('source') or 'manual').strip()[:80]
    owner = (request.form.get('owner') or '').strip()[:120]

    if not agency_name:
        flash('Agency name is required.', 'warning')
        return redirect(f"{url_for('admin.admin_bail_ads')}#agency-cms")

    dedupe_key = _app_module._bail_agency_dedupe_key(agency_name, email, phone)
    if not dedupe_key:
        flash('Unable to create agency record.', 'error')
        return redirect(f"{url_for('admin.admin_bail_ads')}#agency-cms")

    conn = get_db()
    try:
        _app_module._ensure_bail_agency_outreach_schema(conn)
        conn.execute(
            '''
            INSERT INTO bail_agency_outreach (
                dedupe_key, agency_name, contact_name, email, phone, counties, source, owner, outreach_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT(dedupe_key) DO UPDATE SET
                agency_name = excluded.agency_name,
                contact_name = CASE WHEN excluded.contact_name != '' THEN excluded.contact_name ELSE bail_agency_outreach.contact_name END,
                email = CASE WHEN excluded.email != '' THEN excluded.email ELSE bail_agency_outreach.email END,
                phone = CASE WHEN excluded.phone != '' THEN excluded.phone ELSE bail_agency_outreach.phone END,
                counties = CASE WHEN excluded.counties != '' THEN excluded.counties ELSE bail_agency_outreach.counties END,
                source = CASE WHEN excluded.source != '' THEN excluded.source ELSE bail_agency_outreach.source END,
                owner = CASE WHEN excluded.owner != '' THEN excluded.owner ELSE bail_agency_outreach.owner END,
                updated_at = datetime('now')
            ''',
            (dedupe_key, agency_name, contact_name, email, phone, counties, source, owner),
        )
        conn.commit()
        flash(f'Agency record saved for {agency_name}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail agency CMS table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(f"{url_for('admin.admin_bail_ads')}#agency-cms")


@admin_bp.route('/bail-ads/agencies/<int:agency_id>/update', methods=['POST'])
@login_required
def admin_bail_agency_cms_update(agency_id):
    import app as _app_module
    action = (request.form.get('action') or 'save').strip().lower()
    agency_name = (request.form.get('agency_name') or '').strip()[:160]
    contact_name = (request.form.get('contact_name') or '').strip()[:120]
    email = (request.form.get('email') or '').strip().lower()[:160]
    phone = (request.form.get('phone') or '').strip()[:40]
    counties = (request.form.get('counties') or '').strip()[:500]
    source = (request.form.get('source') or '').strip()[:80]
    outreach_status = (request.form.get('outreach_status') or '').strip().lower()
    if outreach_status not in _app_module._BAIL_OUTREACH_STATUSES:
        outreach_status = 'new'
    last_contacted_at = (request.form.get('last_contacted_at') or '').strip()[:32]
    next_follow_up_at = (request.form.get('next_follow_up_at') or '').strip()[:32]
    owner = (request.form.get('owner') or '').strip()[:120]
    email_subject_template = (request.form.get('email_subject_template') or '').strip()[:500]
    email_body_template = (request.form.get('email_body_template') or '').strip()[:4000]
    call_script_template = (request.form.get('call_script_template') or '').strip()[:3000]
    notes = (request.form.get('notes') or '').strip()[:3000]
    test_email = (request.form.get('test_email') or '').strip().lower()[:160]
    actor = (getattr(current_user, 'username', '') or 'admin').strip()[:120]

    if action == 'contacted_now':
        last_contacted_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        if outreach_status == 'new':
            outreach_status = 'contacted'

    dedupe_key = _app_module._bail_agency_dedupe_key(agency_name, email, phone)
    if not agency_name or not dedupe_key:
        flash('Agency name is required for updates.', 'warning')
        return redirect(f"{url_for('admin.admin_bail_ads')}#agency-cms")

    conn = get_db()
    try:
        _app_module._ensure_bail_agency_outreach_schema(conn)
        result = conn.execute(
            '''
            UPDATE bail_agency_outreach
            SET dedupe_key = ?, agency_name = ?, contact_name = ?, email = ?, phone = ?, counties = ?, source = ?,
                outreach_status = ?, last_contacted_at = ?, next_follow_up_at = ?, owner = ?,
                email_subject_template = ?, email_body_template = ?, call_script_template = ?, notes = ?,
                updated_at = datetime('now')
            WHERE id = ?
            ''',
            (
                dedupe_key,
                agency_name,
                contact_name,
                email,
                phone,
                counties,
                source,
                outreach_status,
                last_contacted_at,
                next_follow_up_at,
                owner,
                email_subject_template,
                email_body_template,
                call_script_template,
                notes,
                agency_id,
            ),
        )
        conn.commit()
        if result.rowcount <= 0:
            flash('Agency record not found.', 'warning')
        elif action == 'send_email':
            if not email or '@' not in email:
                _app_module._log_bail_agency_email(
                    conn=conn,
                    agency_id=agency_id,
                    agency_name=agency_name,
                    recipient_email=email or '',
                    email_kind='live',
                    subject=email_subject_template,
                    body_preview=email_body_template,
                    sent_by=actor,
                    send_status='skipped',
                    error_message='invalid_recipient_email',
                )
                conn.commit()
                flash(f'{agency_name} saved, but no valid email address is set.', 'warning')
            else:
                agency_payload = {
                    'agency_name': agency_name,
                    'contact_name': contact_name,
                    'counties': counties,
                    'email_subject_template': email_subject_template,
                    'email_body_template': email_body_template,
                    'call_script_template': call_script_template,
                }
                rendered = _app_module._bail_agency_rendered_templates(agency_payload)
                sent = _app_module._send_bail_lead_notification_email(
                    [email],
                    rendered['subject_preview'],
                    rendered['email_preview'],
                )
                _app_module._log_bail_agency_email(
                    conn=conn,
                    agency_id=agency_id,
                    agency_name=agency_name,
                    recipient_email=email,
                    email_kind='live',
                    subject=rendered['subject_preview'],
                    body_preview=rendered['email_preview'],
                    sent_by=actor,
                    send_status='sent' if sent else 'failed',
                    error_message='' if sent else 'smtp_send_failed',
                )
                conn.commit()
                if sent:
                    sent_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    next_status = outreach_status
                    if next_status in {'new', 'queued'}:
                        next_status = 'contacted'
                    conn.execute(
                        '''
                        UPDATE bail_agency_outreach
                        SET outreach_status = ?, last_contacted_at = ?, updated_at = datetime('now')
                        WHERE id = ?
                        ''',
                        (next_status, sent_at, agency_id),
                    )
                    conn.commit()
                    flash(f'Email sent to {agency_name} at {email}.', 'success')
                else:
                    flash(f'{agency_name} saved, but email send failed. Check SMTP settings.', 'warning')
        elif action == 'send_test_email':
            target_email = test_email
            if not target_email or '@' not in target_email:
                target_email = _app_module._default_bail_test_email()
            if not target_email or '@' not in target_email:
                _app_module._log_bail_agency_email(
                    conn=conn,
                    agency_id=agency_id,
                    agency_name=agency_name,
                    recipient_email=test_email or '',
                    email_kind='test',
                    subject=email_subject_template,
                    body_preview=email_body_template,
                    sent_by=actor,
                    send_status='skipped',
                    error_message='invalid_test_recipient_email',
                )
                conn.commit()
                flash('Agency saved, but no valid test recipient email is configured.', 'warning')
            else:
                agency_payload = {
                    'agency_name': agency_name,
                    'contact_name': contact_name,
                    'counties': counties,
                    'email_subject_template': email_subject_template,
                    'email_body_template': email_body_template,
                    'call_script_template': call_script_template,
                }
                rendered = _app_module._bail_agency_rendered_templates(agency_payload)
                subject = f"[TEST] {rendered['subject_preview']}"
                body = (
                    f"Test send for agency outreach template.\n"
                    f"Agency: {agency_name}\n"
                    f"Timestamp (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"{rendered['email_preview']}"
                )
                sent = _app_module._send_bail_lead_notification_email([target_email], subject, body)
                _app_module._log_bail_agency_email(
                    conn=conn,
                    agency_id=agency_id,
                    agency_name=agency_name,
                    recipient_email=target_email,
                    email_kind='test',
                    subject=subject,
                    body_preview=body,
                    sent_by=actor,
                    send_status='sent' if sent else 'failed',
                    error_message='' if sent else 'smtp_send_failed',
                )
                conn.commit()
                if sent:
                    flash(f'Test email sent to {target_email} for {agency_name}.', 'success')
                else:
                    flash(f'{agency_name} saved, but test email send failed. Check SMTP settings.', 'warning')
        elif action == 'contacted_now':
            flash(f'{agency_name} marked as contacted.', 'success')
        else:
            flash(f'{agency_name} updated.', 'success')
    except sqlite3.IntegrityError:
        flash('Another agency already uses that dedupe key (name/email/phone combination).', 'error')
    except sqlite3.OperationalError:
        flash('Bail agency CMS table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(f"{url_for('admin.admin_bail_ads')}#agency-cms")


@admin_bp.route('/bail-ads/agencies/<int:agency_id>/delete', methods=['POST'])
@login_required
def admin_bail_agency_cms_delete(agency_id):
    import app as _app_module
    conn = get_db()
    try:
        _app_module._ensure_bail_agency_outreach_schema(conn)
        row = conn.execute(
            'SELECT agency_name FROM bail_agency_outreach WHERE id = ? LIMIT 1',
            (agency_id,),
        ).fetchone()
        if not row:
            flash('Agency record not found.', 'warning')
            return redirect(f"{url_for('admin.admin_bail_ads')}#agency-cms")
        agency_name = (row['agency_name'] or f'Agency #{agency_id}').strip()
        conn.execute('DELETE FROM bail_agency_outreach WHERE id = ?', (agency_id,))
        conn.commit()
        flash(f'{agency_name} deleted.', 'success')
    except sqlite3.OperationalError:
        flash('Bail agency CMS table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(f"{url_for('admin.admin_bail_ads')}#agency-cms")


@admin_bp.route('/bail-ads/attribution/export.csv')
@login_required
def admin_bail_ads_attribution_export():
    import app as _app_module
    conn = get_db()
    try:
        _app_module._ensure_bail_consumer_lead_schema(conn)
        rows = _app_module._bail_advertiser_attribution_30d(conn, limit=10000)
    except sqlite3.OperationalError:
        conn.close()
        return Response('Attribution tables are not available.\n', status=503, mimetype='text/plain')
    conn.close()

    package_map = _app_module._bail_ad_package_lookup()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([
        'order_id',
        'business_name',
        'package_id',
        'package_name',
        'order_status',
        'county_targets',
        'calls_30d',
        'texts_30d',
        'routed_leads_30d',
        'qualified_leads_30d',
        'booked_bonds_30d',
        'qualified_rate_pct',
        'booked_rate_pct',
    ])
    for row in rows:
        package = package_map.get(row.get('package_id') or '')
        package_name = (package.get('name') if package else '') or (row.get('package_id') or '').replace('_', ' ').title()
        writer.writerow([
            row.get('order_id') or '',
            row.get('business_name') or '',
            row.get('package_id') or '',
            package_name,
            row.get('status') or '',
            row.get('county_targets') or '',
            int(row.get('calls') or 0),
            int(row.get('texts') or 0),
            int(row.get('routed_leads') or 0),
            int(row.get('qualified_leads') or 0),
            int(row.get('booked_bonds') or 0),
            f"{float(row.get('qualified_rate_pct') or 0.0):.2f}",
            f"{float(row.get('booked_rate_pct') or 0.0):.2f}",
        ])

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename=bail_ads_attribution_30d_{timestamp}.csv'
    response.headers['Cache-Control'] = 'no-store'
    return response


@admin_bp.route('/bail-ads/<int:inquiry_id>/status', methods=['POST'])
@login_required
def admin_bail_ads_update_status(inquiry_id):
    next_status = (request.form.get('status') or '').strip().lower()
    review_notes = (request.form.get('review_notes') or '').strip()[:1200]
    if next_status not in {'pending', 'in_review', 'approved', 'declined', 'archived'}:
        flash('Invalid bail ad status.', 'error')
        return redirect(url_for('admin.admin_bail_ads'))

    reviewer = getattr(current_user, 'username', '') or 'admin'
    conn = get_db()
    try:
        result = conn.execute(
            '''
            UPDATE bail_ad_inquiries
            SET status = ?, review_notes = ?, reviewed_by = ?, reviewed_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ?
            ''',
            (next_status, review_notes, reviewer, inquiry_id),
        )
        conn.commit()
        if result.rowcount <= 0:
            flash('Inquiry not found.', 'error')
        else:
            flash(f'Inquiry #{inquiry_id} updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad inquiry table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_bail_ads'))


@admin_bp.route('/bail-ads/creatives/<int:creative_id>/status', methods=['POST'])
@login_required
def admin_bail_ads_creative_status(creative_id):
    next_status = (request.form.get('status') or '').strip().lower()
    review_notes = (request.form.get('review_notes') or '').strip()[:1200]
    if next_status not in {'pending', 'approved', 'rejected'}:
        flash('Invalid creative status.', 'error')
        return redirect(url_for('admin.admin_bail_ads'))

    reviewer = getattr(current_user, 'username', '') or 'admin'
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT order_id FROM bail_ad_creatives WHERE id = ? LIMIT 1',
            (creative_id,),
        ).fetchone()
        if not row:
            flash('Creative record not found.', 'error')
            conn.close()
            return redirect(url_for('admin.admin_bail_ads'))

        conn.execute(
            '''
            UPDATE bail_ad_creatives
            SET status = ?, review_notes = ?, reviewed_by = ?, reviewed_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ?
            ''',
            (next_status, review_notes, reviewer, creative_id),
        )
        if next_status == 'approved':
            conn.execute(
                '''
                UPDATE bail_ad_orders
                SET status = CASE
                        WHEN status IN ('active_pending_creative_review', 'checkout_pending') THEN 'active'
                        ELSE status
                    END,
                    updated_at = datetime('now')
                WHERE id = ?
                ''',
                (row['order_id'],),
            )
        conn.commit()
        flash(f'Creative #{creative_id} updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad creative table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_bail_ads'))


@admin_bp.route('/bail-ads/orders/<int:order_id>/status', methods=['POST'])
@login_required
def admin_bail_ads_order_status(order_id):
    business_name = (request.form.get('business_name') or '').strip()[:120]
    contact_name = (request.form.get('contact_name') or '').strip()[:120]
    email = (request.form.get('email') or '').strip().lower()[:160]
    phone = (request.form.get('phone') or '').strip()[:40]
    website_url = (request.form.get('website_url') or '').strip()[:300]
    county_targets = (request.form.get('county_targets') or '').strip()[:500]
    next_status = (request.form.get('status') or '').strip().lower()
    notes = (request.form.get('notes') or '').strip()[:1200]
    allowed_statuses = {'checkout_pending', 'active', 'active_pending_creative_review', 'payment_failed', 'canceled', 'paused'}
    if next_status not in allowed_statuses:
        flash('Invalid order status.', 'error')
        return redirect(url_for('admin.admin_bail_ads'))
    if not business_name:
        flash('Business name is required.', 'warning')
        return redirect(url_for('admin.admin_bail_ads'))
    if email and '@' not in email:
        flash('Order email must be valid or left blank.', 'warning')
        return redirect(url_for('admin.admin_bail_ads'))

    conn = get_db()
    try:
        result = conn.execute(
            '''
            UPDATE bail_ad_orders
            SET business_name = ?, contact_name = ?, email = ?, phone = ?, website_url = ?, county_targets = ?,
                status = ?, notes = ?, updated_at = datetime('now')
            WHERE id = ?
            ''',
            (business_name, contact_name, email, phone, website_url, county_targets, next_status, notes, order_id),
        )
        conn.commit()
        if result.rowcount <= 0:
            flash('Order not found.', 'error')
        else:
            flash(f'{business_name} updated.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad order table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_bail_ads'))


@admin_bp.route('/bail-ads/orders/<int:order_id>/delete', methods=['POST'])
@login_required
def admin_bail_ads_order_delete(order_id):
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT business_name FROM bail_ad_orders WHERE id = ? LIMIT 1',
            (order_id,),
        ).fetchone()
        if not row:
            flash('Advertiser record not found.', 'warning')
            return redirect(url_for('admin.admin_bail_ads'))
        business_name = (row['business_name'] or f'Order #{order_id}').strip()
        conn.execute('DELETE FROM bail_ad_orders WHERE id = ?', (order_id,))
        conn.commit()
        flash(f'{business_name} deleted.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad order table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_bail_ads'))


@admin_bp.route('/bail-ads/orders/bulk-status', methods=['POST'])
@login_required
def admin_bail_ads_bulk_order_status():
    next_status = (request.form.get('status') or '').strip().lower()
    notes = (request.form.get('notes') or '').strip()[:1200]
    allowed_statuses = {'checkout_pending', 'active', 'active_pending_creative_review', 'payment_failed', 'canceled', 'paused'}
    if next_status not in allowed_statuses:
        flash('Invalid order status.', 'error')
        return redirect(url_for('admin.admin_bail_ads'))

    order_ids = []
    for raw_id in request.form.getlist('order_ids'):
        try:
            order_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if order_id > 0:
            order_ids.append(order_id)
    order_ids = sorted(set(order_ids))
    if not order_ids:
        flash('Select at least one order first.', 'warning')
        return redirect(url_for('admin.admin_bail_ads'))

    placeholders = ','.join('?' for _ in order_ids)
    conn = get_db()
    try:
        if notes:
            result = conn.execute(
                f'''
                UPDATE bail_ad_orders
                SET status = ?, notes = ?, updated_at = datetime('now')
                WHERE id IN ({placeholders})
                ''',
                tuple([next_status, notes] + order_ids),
            )
        else:
            result = conn.execute(
                f'''
                UPDATE bail_ad_orders
                SET status = ?, updated_at = datetime('now')
                WHERE id IN ({placeholders})
                ''',
                tuple([next_status] + order_ids),
            )
        conn.commit()
        changed_count = int(result.rowcount or 0)
        if changed_count <= 0:
            flash('No matching orders were found.', 'warning')
        elif changed_count == 1:
            flash(f'1 order updated to {next_status}.', 'success')
        else:
            flash(f'{changed_count} orders updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad order table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_bail_ads'))


@admin_bp.route('/bail-ads/leads/<int:lead_id>/status', methods=['POST'])
@login_required
def admin_bail_consumer_lead_status(lead_id):
    import app as _app_module
    next_status = (request.form.get('status') or '').strip().lower()
    review_notes = (request.form.get('review_notes') or '').strip()[:1200]
    allowed_statuses = {'new', 'contacted', 'qualified', 'booked', 'unqualified', 'archived'}
    if next_status not in allowed_statuses:
        flash('Invalid lead status.', 'error')
        return redirect(url_for('admin.admin_bail_ads'))

    reviewer = getattr(current_user, 'username', '') or 'admin'
    conn = get_db()
    try:
        _app_module._ensure_bail_consumer_lead_schema(conn)
        result = conn.execute(
            '''
            UPDATE bail_consumer_leads
            SET status = ?, review_notes = ?, reviewed_by = ?, reviewed_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ?
            ''',
            (next_status, review_notes, reviewer, lead_id),
        )
        conn.commit()
        if result.rowcount <= 0:
            flash('Lead not found.', 'warning')
        else:
            flash(f'Lead #{lead_id} updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail consumer lead tables are not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_bail_ads'))


# ---------------------------------------------------------------------------
# /admin/content/seo  (grouped here as it uses bail-ad search-console helpers)
# ---------------------------------------------------------------------------

from utils.auth_constants import CONTENT_REVIEW_ROLES  # noqa: E402
from werkzeug.utils import secure_filename  # noqa: E402


@admin_bp.route('/content/seo', methods=['GET', 'POST'])
@login_required
@require_role(*CONTENT_REVIEW_ROLES)
def admin_seo_console():
    import app as _app_module
    if request.method == 'POST':
        upload = request.files.get('search_console_csv')
        if not upload or not (upload.filename or '').strip():
            flash('Choose a Search Console CSV to import.', 'error')
            return redirect(url_for('admin.admin_seo_console'))

        conn = get_db()
        try:
            source_kind, rows = _app_module._parse_search_console_csv(upload)
            source_filename = secure_filename(upload.filename or 'search-console.csv') or 'search-console.csv'
            _app_module._store_search_console_import(conn, source_filename, source_kind, rows)
            _log_admin_action(
                'seo.search_console_imported',
                target_type='search_console_import',
                metadata={'source_filename': source_filename, 'source_kind': source_kind, 'row_count': len(rows)},
                conn=conn,
            )
            conn.commit()
            flash(f'Imported {len(rows)} Search Console rows from {source_filename}.', 'success')
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), 'error')
        finally:
            conn.close()
        return redirect(url_for('admin.admin_seo_console'))

    conn = get_db()
    search_console = _app_module._search_console_workflow_context(conn)
    conn.close()
    return render_template('admin_seo_console.html', search_console=search_console)
