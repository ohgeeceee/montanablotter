"""Advertiser client portal (token-in-URL, same model as bail onboarding).

Public route but useless without a 256-bit token; no enumeration — every
failure renders the same 404. Sections (each gated by per-sponsor feature
toggles on client_portals.features):

  1. listing_editor  Profile & Directory Listing Editor
                     (logo upload, bio, phone/direct line, direct link)
  2. metrics         Ad performance: impressions / clicks / calls / leads
                     + county-level exposure (live event tables)
  3. lead_receipt    Inquiries generated from the client's ad placement
  4. billing         Stripe customer-portal redirect + self-hosted
                     subscription/invoice table (read-only)

Editing here NEVER touches the provider order / billing rows; client
profile overrides live on client_portals and the directory renderer joins
them in. Outbound email is never sent from this module.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from contextlib import closing

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from werkzeug.utils import secure_filename

from db import get_db
from services.crm import crm_service

log = logging.getLogger(__name__)
client_portal_bp = Blueprint('client_portal', __name__, url_prefix='/portal')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'crm_logos')
_ALLOWED_IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB, same cap as the ad desks


def _save_logo(upload, prefix: str) -> str:
    """Same bounded image-save contract as blueprints/lawyer_ads._save_upload."""
    if not upload or not upload.filename:
        return ''
    ext = upload.filename.rsplit('.', 1)[-1].lower() if '.' in upload.filename else ''
    if ext not in _ALLOWED_IMAGE_EXTS:
        return ''
    data = upload.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        return ''
    os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
    stored = f'{prefix}_{secrets.token_hex(8)}.{ext}'
    with open(os.path.join(LOGO_UPLOAD_DIR, stored), 'wb') as handle:
        handle.write(data)
    return f'/static/crm_logos/{stored}'


def _resolve(conn, token_or_slug):
    ctx = crm_service.portal_context(conn, token_or_slug)
    if ctx is None:
        abort(404)
    return ctx


@client_portal_bp.route('/<token_or_slug>')
def view(token_or_slug):
    with closing(get_db()) as conn:
        ctx = _resolve(conn, token_or_slug)
        lead_id = ctx['lead_id']
        enabled = lambda f: crm_service.feature_enabled(ctx, f)  # noqa: E731
        metrics = (crm_service.portal_metrics(conn, ctx, days=30)
                   if enabled('metrics') else None)
        leads = (crm_service.lead_receipt(conn, ctx)
                 if enabled('lead_receipt') else [])
        subs = crm_service.billing_rollup(conn, lead_id) if enabled('billing') else []
        invoices = crm_service.invoice_rollup(conn, lead_id) if enabled('billing') else []
        thread = crm_service.email_thread(conn, lead_id, limit=20)
    return render_template('client_portal.html', portal=ctx,
                           token_or_slug=token_or_slug,
                           enabled=enabled,
                           tier_label=crm_service.PORTAL_TIERS[ctx['tier']],
                           metrics=metrics, leads=leads,
                           subscriptions=subs, invoices=invoices, thread=thread)


@client_portal_bp.route('/<token_or_slug>/listing', methods=['POST'])
def save_listing(token_or_slug):
    """Profile & Directory Listing Editor save (feature: listing_editor)."""
    with closing(get_db()) as conn:
        ctx = _resolve(conn, token_or_slug)
        if not crm_service.feature_enabled(ctx, 'listing_editor'):
            abort(403)
        try:
            logo_url = None
            upload = request.files.get('logo')
            if upload and upload.filename:
                saved = _save_logo(upload, f'crm_logo_{ctx["id"]}')
                if not saved:
                    flash('Logo must be a JPG/PNG/WebP under 2 MB — upload skipped.')
                else:
                    logo_url = saved
            crm_service.update_listing(
                conn, ctx['id'],
                bio=request.form.get('bio', ''),
                display_phone=request.form.get('display_phone', ''),
                direct_line=request.form.get('direct_line', ''),
                direct_link=request.form.get('direct_link', ''),
                logo_url=logo_url)
            flash('Listing updated.')
        except ValueError as exc:
            flash(f'Could not save: {exc}')
    return redirect(url_for('client_portal.view', token_or_slug=token_or_slug))


@client_portal_bp.route('/<token_or_slug>/request', methods=['POST'])
def request_change(token_or_slug):
    """Client submits a creative/billing change request -> logged as an
    inbound email-log row for the revenue team. Sends nothing outbound."""
    with closing(get_db()) as conn:
        ctx = _resolve(conn, token_or_slug)
        body = (request.form.get('message') or '').strip()
        if not (2 < len(body) <= 4000):
            flash('Please write a little more (max 4000 characters).')
        else:
            crm_service.log_email(
                conn, ctx['lead_id'], 'inbound',
                subject=f'Portal request ({ctx["tier"]})', body=body,
                from_address=ctx['lead_email'] or '', to_address='revenue@montanablotter.com',
                provider='client_portal')
            flash('Request received — the team will follow up by email.')
    return redirect(url_for('client_portal.view', token_or_slug=token_or_slug))


@client_portal_bp.route('/<token_or_slug>/billing-portal', methods=['POST'])
def stripe_billing_portal(token_or_slug):
    """Redirect to Stripe's hosted customer billing portal (card change,
    cancel, invoices). Mirrors blueprints/bail_bond_ads.billing_portal:
    server-side session create, customer id taken from OUR order row —
    never from form input."""
    with closing(get_db()) as conn:
        ctx = _resolve(conn, token_or_slug)
        if not crm_service.feature_enabled(ctx, 'billing'):
            abort(403)
        customer_id = crm_service.stripe_link(conn, ctx)
    if not customer_id:
        flash('No Stripe customer is linked to your account yet — '
              'email revenue@montanablotter.com to manage billing.')
        return redirect(url_for('client_portal.view', token_or_slug=token_or_slug))
    try:
        import stripe
        stripe.api_key = current_app.config.get('STRIPE_SECRET_KEY') or os.environ.get('STRIPE_SECRET_KEY', '')
        domain = request.url_root.rstrip('/')
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f'{domain}/portal/{token_or_slug}?saved=billing')
        return redirect(portal.url)
    except Exception:
        log.exception('crm portal: stripe billing portal session failed')
        flash('Stripe portal unavailable right now — your billing table below still shows the latest sync.')
        return redirect(url_for('client_portal.view', token_or_slug=token_or_slug))
