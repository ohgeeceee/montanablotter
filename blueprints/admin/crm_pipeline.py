"""Admin CRM routes — one tabbed dashboard + actions.

Tab 1  Prospecting & Lead Feed   filter by stage/county; one-click outreach
Tab 2  Client Roster & Portals   portal links + per-sponsor feature toggles
Tab 3  Invoicing & Billing       MRR rollup, invoice statuses, manual invoice

Attaches to the existing admin_bp (same auth/audit pattern as
advertising_center.py). Applied to the live tree only with human approval;
then add `crm_pipeline` to the side-effect imports in
blueprints/admin/__init__.py.

Outreach = DRAFT + logged, never sent from these routes (Red-tier rule:
outbound email requires the human in the loop). Manual Stripe invoices are
the one external write and are role-gated + audit-logged.
"""
from __future__ import annotations

import logging
from contextlib import closing

from flask import (Response, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from db import get_db
from blueprints.admin import admin_bp, require_role, _log_admin_action
from services.crm import crm_service
from services.crm.prospecting.engine import run_ingest
from services.crm.prospecting.providers import VERTICALS

log = logging.getLogger(__name__)
READ_ROLES = ('super_admin', 'ops', 'revenue', 'read_only')
WRITE_ROLES = ('super_admin', 'ops', 'revenue')

CRM_TABS = ('leads', 'clients', 'billing')


@admin_bp.route('/revenue/crm')
@require_role(*READ_ROLES)
def crm_dashboard():
    """Tabbed CRM hub. ?tab=leads|clients|billing"""
    tab = request.args.get('tab', 'leads')
    if tab not in CRM_TABS:
        tab = 'leads'
    ctx = {'tab': tab}

    if tab == 'leads':
        stage = (request.args.get('stage') or '').strip()
        county = (request.args.get('county') or '').strip()
        where, params = ['1=1'], []
        if stage:
            where.append('stage = ?')
            params.append(stage)
        if county:
            where.append("(',' || counties || ',') LIKE ?")
            params.append(f'%,{county},%')
        with closing(get_db()) as conn:
            rows = conn.execute(
                f'''SELECT id, business_name, category, target_vertical,
                           counties, stage, email, phone, license_number,
                           updated_at
                    FROM advertising_prospects
                    WHERE {' AND '.join(where)}
                    ORDER BY updated_at DESC LIMIT 200''', params).fetchall()
            stages = [r['stage'] for r in conn.execute(
                'SELECT DISTINCT stage FROM advertising_prospects '
                'ORDER BY stage').fetchall()]
        ctx.update(leads=rows, stages=stages, stage=stage, county=county)

    elif tab == 'clients':
        search = (request.args.get('q') or '').strip()
        status = (request.args.get('status') or '').strip()
        with closing(get_db()) as conn:
            portals = crm_service.admin_clients(conn, search=search, status=status)
        ctx.update(portals=portals, search=search, status_filter=status,
                   features=crm_service.PORTAL_FEATURES,
                   tiers=crm_service.PORTAL_TIERS)

    else:  # billing
        with closing(get_db()) as conn:
            subs = crm_service.billing_rollup(conn)
            invs = crm_service.invoice_rollup(conn)
            mrr = crm_service.mrr_summary(conn)
        ctx.update(subscriptions=subs, invoices=invs, mrr=mrr)

    return render_template('admin_crm_dashboard.html', **ctx)


# ---------------------------------------------------------------------------
# Tab 1 actions
# ---------------------------------------------------------------------------

@admin_bp.route('/revenue/crm/leads/<int:lead_id>/outreach', methods=['POST'])
@require_role(*WRITE_ROLES)
def crm_outreach_draft(lead_id):
    """One-click cold outreach: generate a personalised draft from the
    prospect record, show it as downloadable text, and log it against the
    lead. NOTHING is emailed here — the operator sends from their own
    mailbox after editing (Red-line: no automated outbound)."""
    with closing(get_db()) as conn:
        p = conn.execute(
            'SELECT * FROM advertising_prospects WHERE id = ?',
            (lead_id,)).fetchone()
        if p is None:
            abort(404)
        to = p['email'] or ''
        subject = f"Getting {p['business_name']} in front of {p['counties'] or 'Montana'} readers"
        first_line = {
            'bail': "We sell exclusive county sponsorship slots on Montana Blotter's jail & court coverage pages — families search there at the exact moment they need a bondsman.",
            'lawyer': "Montana Blotter publishes county police blotters and court filings that drive high-intent local traffic — the same readers who need counsel after an incident.",
            'pi': "Our county-level crime and court coverage attracts readers researching cases — natural demand for licensed investigators.",
            'process': "Attorneys and litigants already read our court docket coverage; a serving-company listing puts you a click below it.",
        }.get(p['target_vertical'] or '', "Our county public-records pages attract exactly the local audience you serve.")
        body = (
            f"To: {to}\nSubject: {subject}\n\n"
            f"Hi {p['contact_name'] or 'there'},\n\n"
            f"I run Montana Blotter (montanablotter.com), Montana's open public-records"
            f" site covering all 56 counties. {first_line}\n\n"
            + (f"We already have your {p['target_vertical']} listing on file"
               f" ({p['counties']})." if p['counties'] else '')
            + "\n\nSponsorship tiers start at a county banner. Worth a 10-minute call this week?\n\n"
            "— Jon, Montana Blotter\nrevenue@montanablotter.com\n")
        draft_body = '=== DRAFT (not sent) ===\n' + body
        eid = crm_service.log_email(
            conn, lead_id, 'outbound', subject=subject, body=draft_body,
            to_address=to, provider='manual', send_status='logged')
        _log_admin_action('crm_outreach_draft', 'crm_email_log', eid,
                          {'lead_id': lead_id})
    draft = f"=== DRAFT — not sent (logged as email-log #{eid}) ===\n{body}"
    return Response(draft, mimetype='text/plain', headers={
        'Content-Disposition': f'attachment; filename="outreach_{lead_id}.txt"'})


# ---------------------------------------------------------------------------
# Find Leads (unchanged ingest flow, moved under the dashboard nav)
# ---------------------------------------------------------------------------

@admin_bp.route('/revenue/crm/find-leads', methods=['GET', 'POST'])
@require_role(*WRITE_ROLES)
def crm_find_leads():
    """`Find Leads`: paste a registry export (CSV/JSON/HTML table text)
    or upload a file, preview the dedupe result, then commit."""
    error = ''
    preview = None
    if request.method == 'POST':
        vertical = request.form.get('vertical', 'bail')
        if vertical not in VERTICALS:
            error = 'Unknown vertical.'
        else:
            dry = request.form.get('commit') != '1'
            text = (request.form.get('paste') or '').strip()
            upload = request.files.get('file')
            import os
            import tempfile
            path = None
            tmp = None
            try:
                if upload and upload.filename:
                    suffix = os.path.splitext(upload.filename)[1].lower() or '.csv'
                    fd, path = tempfile.mkstemp(suffix=suffix)
                    tmp = path
                    with os.fdopen(fd, 'wb') as fh:
                        upload.save(fh)
                elif text:
                    suffix = '.html' if '<table' in text.lower() else '.csv'
                    fd, path = tempfile.mkstemp(suffix=suffix)
                    tmp = path
                    with os.fdopen(fd, 'w') as fh:
                        fh.write(text)
                else:
                    error = 'Paste export rows or attach a file.'
            except OSError as exc:
                abort(500, description=str(exc))
            if not error:
                from services.crm.prospecting.providers import directory_import
                try:
                    records = list(directory_import.fetch_file(path, vertical))
                except Exception as exc:
                    records = []
                    error = f'Could not parse file: {exc}'
                finally:
                    if tmp:
                        os.unlink(tmp)
            if not error:
                with closing(get_db()) as conn:
                    if dry:
                        conn.execute('SAVEPOINT dryrun')
                        counts = run_ingest(conn, records, dry_run=False)
                        conn.execute('ROLLBACK TO dryrun')
                        conn.execute('RELEASE dryrun')
                        preview = {'counts': counts, 'n': len(records), 'committed': False}
                    else:
                        counts = run_ingest(conn, records, dry_run=False)
                        _log_admin_action('crm_find_leads_commit',
                                          'crm_prospect_batch', 0,
                                          {'vertical': vertical, **counts})
                        preview = {'counts': counts, 'n': len(records), 'committed': True}
                        flash(f"Committed {counts['inserted']} new leads, "
                              f"{counts['updated']} enriched.")
    return render_template('admin_crm_find_leads.html',
                           verticals=VERTICALS, error=error, preview=preview)


# ---------------------------------------------------------------------------
# Tab 2 actions
# ---------------------------------------------------------------------------

@admin_bp.route('/revenue/crm/clients/<int:portal_id>/features', methods=['POST'])
@require_role(*WRITE_ROLES)
def crm_set_features(portal_id):
    """Per-sponsor portal feature toggles."""
    wanted = request.form.getlist('features')
    with closing(get_db()) as conn:
        if not conn.execute('SELECT 1 FROM client_portals WHERE id = ?',
                            (portal_id,)).fetchone():
            abort(404)
        try:
            crm_service.set_features(conn, portal_id, wanted)
        except ValueError as exc:
            abort(400, description=str(exc))
        _log_admin_action('crm_set_features', 'client_portal', portal_id,
                          {'features': wanted})
    flash('Portal features updated.')
    return redirect(url_for('admin.crm_dashboard', tab='clients'))


@admin_bp.route('/revenue/crm/leads/<int:lead_id>/portal', methods=['POST'])
@require_role(*WRITE_ROLES)
def crm_open_portal(lead_id):
    tier = request.form.get('tier', 'banner')
    slug = request.form.get('slug', '')
    with closing(get_db()) as conn:
        try:
            from services.crm.portal_tokens import new_portal_token
            res = crm_service.open_portal(conn, lead_id, tier, slug,
                                          token_factory=new_portal_token)
        except ValueError as exc:
            abort(400, description=str(exc))
        _log_admin_action('crm_portal_open', 'client_portal', res['portal_id'],
                          {'lead_id': lead_id, 'tier': tier, 'created': res['created']})
        url = url_for('client_portal.view', token_or_slug=res['access_token'],
                      _external=True)
        flash(('Portal created. Link: ' if res['created'] else 'Existing portal. Link: ') + url)
    return redirect(url_for('admin.crm_dashboard', tab='clients'))


# ---------------------------------------------------------------------------
# Tab 3 actions
# ---------------------------------------------------------------------------

@admin_bp.route('/revenue/crm/leads/<int:lead_id>/email-log', methods=['POST'])
@require_role(*WRITE_ROLES)
def crm_log_email(lead_id):
    f = request.form
    with closing(get_db()) as conn:
        if not conn.execute('SELECT 1 FROM advertising_prospects WHERE id = ?',
                            (lead_id,)).fetchone():
            abort(404)
        eid = crm_service.log_email(
            conn, lead_id, f.get('direction', 'outbound'),
            f.get('subject', ''), f.get('body', ''),
            from_address=f.get('from_address', ''), to_address=f.get('to_address', ''),
            tracking_id=f.get('tracking_id', ''), provider='manual')
        _log_admin_action('crm_email_log', 'crm_email_log', eid, {'lead_id': lead_id})
    return redirect(url_for('admin.advertising_prospect', prospect_id=lead_id))


@admin_bp.route('/revenue/crm/invoices/create', methods=['POST'])
@require_role('super_admin', 'revenue')
def crm_manual_invoice():
    """Manual one-off invoice against a Stripe customer linked to an order.
    External write -> role-gated, audited; Stripe remains source of truth."""
    order_table = request.form.get('order_table', '')
    order_id = request.form.get('order_id', '')
    amount_cents = int(request.form.get('amount_cents') or 0)
    days_due = int(request.form.get('days_due') or 15)
    if order_table not in ('lawyer_ad_orders', 'bail_ad_orders') or not order_id.isdigit() \
            or amount_cents < 100:
        abort(400, description='pick an order and an amount >= $1.00')
    name_col = 'firm_name' if order_table == 'lawyer_ad_orders' else 'business_name'
    with closing(get_db()) as conn:
        row = conn.execute(
            f'SELECT provider_customer_id AS c, {name_col} AS n '
            f'FROM {order_table} WHERE id = ?', (int(order_id),)).fetchone()
    if row is None or not (row['c'] or ''):
        abort(400, description='that order has no Stripe customer id')
    try:
        import stripe
        stripe.api_key = (current_app.config.get('STRIPE_SECRET_KEY')
                          or current_app.config.get('STRIPE_SECRET_KEY_LAWYER')
                          or '')
        inv = stripe.Invoice.create(
            customer=row['c'],
            days_until_due=days_due,
            auto_advance=True,
            invoice_items=[{'amount': amount_cents, 'currency': 'usd',
                            'description': f'Montana Blotter ad — {row["n"] or order_table} #{order_id}'}])
        _log_admin_action('crm_manual_invoice', order_table, int(order_id),
                          {'stripe_invoice': inv.id, 'amount_cents': amount_cents})
        flash(f'Stripe invoice {inv.id} created.')
    except Exception as exc:
        log.exception('crm manual invoice failed')
        flash(f'Stripe invoice failed: {exc}')
    return redirect(url_for('admin.crm_dashboard', tab='billing'))
