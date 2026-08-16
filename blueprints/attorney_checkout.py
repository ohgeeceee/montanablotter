"""
Attorney sponsorship self-serve checkout — Stripe subscription flow.

Mirrors recovery_ads.py patterns: package defs, Stripe checkout session,
webhook handler (called from payments.py), control panel, token-based
post-checkout access.

Silver ($99/mo) and Gold ($199/mo) match the tiers already defined in
blueprints/attorney_ads.py. Free Bronze remains the opt-in directory listing
handled by attorney_ads claim form + admin approval.
"""
from __future__ import annotations

import json as _json
import os
import secrets
import sqlite3
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

import config
from db import get_db

attorney_checkout_bp = Blueprint('attorney_checkout', __name__)

# ---------------------------------------------------------------------------
# Package definitions — must match _TIERS in blueprints/attorney_ads.py
# ---------------------------------------------------------------------------

_ATTORNEY_PACKAGES = [
    {
        'id': 'silver',
        'name': 'Silver Featured',
        'price_monthly_cents': 9900,
        'price_annual_cents': 99000,
        'price_label': '$99/mo',
        'price_label_annual': '$990/yr (save $198)',
        'logo': True,
        'photo': False,
        'featured': False,
        'description_limit': 500,
        'features': [
            'Pinned above free listings in your county',
            'Logo on your profile card',
            '500-character blurb',
            'Larger card with "Featured" badge',
            'Mobile tap-to-call',
            'Monthly impression report',
        ],
    },
    {
        'id': 'gold',
        'name': 'Gold Priority',
        'price_monthly_cents': 19900,
        'price_annual_cents': 199000,
        'price_label': '$199/mo',
        'price_label_annual': '$1,990/yr (save $398)',
        'logo': True,
        'photo': True,
        'featured': True,
        'description_limit': 500,
        'features': [
            'Everything in Silver',
            'Top-of-county placement (above Silver)',
            'Photo on your profile card',
            'Custom callout (1-line tagline)',
            '"Priority Placement" badge',
            'Featured on warrant detail pages in your county',
            'Priority placement on /arrests in your county',
        ],
    },
]

def _attorney_package_lookup() -> dict:
    return {p['id']: p for p in _ATTORNEY_PACKAGES}

def _attorney_price_cents(package_id: str, billing_cycle: str) -> int:
    pkg = _attorney_package_lookup().get(package_id)
    if not pkg:
        return 0
    if billing_cycle == 'annual':
        return pkg['price_annual_cents']
    return pkg['price_monthly_cents']

def _attorney_checkout_ready() -> bool:
    try:
        import stripe as _stripe  # noqa
    except Exception:
        return False
    secret = (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip()
    pub = (getattr(config, 'STRIPE_PUBLISHABLE_KEY', '') or '').strip()
    return bool(secret and pub)

def _checkout_redirect_url(session) -> str:
    if isinstance(session, dict):
        return (session.get('url') or '').strip()
    return (getattr(session, 'url', '') or '').strip()

# ---------------------------------------------------------------------------
# Stripe webhook handler — called from blueprints/payments.py
# ---------------------------------------------------------------------------

def apply_stripe_attorney_event(conn: sqlite3.Connection, event: dict) -> None:
    """Process a Stripe webhook event for attorney sponsorship subscriptions."""
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}

    if (metadata.get('flow') or '').strip() != 'attorney_sponsorship':
        return

    handled = {
        'checkout.session.completed',
        'checkout.session.async_payment_succeeded',
        'checkout.session.async_payment_failed',
        'checkout.session.expired',
        'customer.subscription.deleted',
    }
    if event_type not in handled:
        return

    # subscription.deleted carries subscription object, not session
    if event_type == 'customer.subscription.deleted':
        sub_id = (data_object.get('id') or '').strip()
        if sub_id:
            conn.execute(
                '''
                UPDATE attorney_checkout_orders
                SET status = 'cancelled', cancelled_at = datetime('now')
                WHERE stripe_subscription_id = ? AND status = 'active'
                ''',
                (sub_id,),
            )
            conn.commit()
        return

    session_id = (data_object.get('id') or '').strip()
    if not session_id:
        return

    status_map = {
        'checkout.session.completed': 'active',
        'checkout.session.async_payment_succeeded': 'active',
        'checkout.session.async_payment_failed': 'payment_failed',
        'checkout.session.expired': 'expired',
    }
    mapped_status = status_map[event_type]

    firm_name = (metadata.get('firm_name') or '').strip()[:120]
    contact_name = (metadata.get('contact_name') or '').strip()[:120]
    email = (metadata.get('email') or '').strip().lower()[:160]
    phone = (metadata.get('phone') or '').strip()[:40]
    website = (metadata.get('website') or '').strip()[:300]
    package_id = (metadata.get('package_id') or '').strip().lower()[:32]
    counties_served = (metadata.get('counties_served') or '').strip()[:400]
    practice_areas = (metadata.get('practice_areas') or '').strip()[:400]
    blurb = (metadata.get('blurb') or '').strip()[:1000]
    mt_bar_number = (metadata.get('mt_bar_number') or '').strip()[:40]
    billing_cycle = (metadata.get('billing_cycle') or 'monthly').strip().lower()
    if billing_cycle not in ('monthly', 'annual'):
        billing_cycle = 'monthly'
    token = (metadata.get('token') or '').strip()[:64] or secrets.token_urlsafe(24)
    stripe_customer_id = (data_object.get('customer') or '').strip()[:120]
    stripe_subscription_id = (data_object.get('subscription') or '').strip()[:120]

    activated_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if mapped_status == 'active' else None

    conn.execute(
        '''
        INSERT INTO attorney_checkout_orders (
            firm_name, contact_name, email, phone, website,
            package_id, billing_cycle,
            stripe_customer_id, stripe_subscription_id, stripe_session_id,
            status, token, activated_at,
            counties_served, practice_areas, blurb, mt_bar_number
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_session_id) DO UPDATE SET
            status = excluded.status,
            stripe_subscription_id = COALESCE(excluded.stripe_subscription_id, stripe_subscription_id),
            stripe_customer_id = COALESCE(excluded.stripe_customer_id, stripe_customer_id),
            activated_at = COALESCE(attorney_checkout_orders.activated_at, excluded.activated_at)
        ''',
        (
            firm_name, contact_name, email, phone, website,
            package_id, billing_cycle,
            stripe_customer_id, stripe_subscription_id, session_id,
            mapped_status, token, activated_at,
            counties_served, practice_areas, blurb, mt_bar_number,
        ),
    )

    if mapped_status == 'active':
        order_row = conn.execute(
            'SELECT id FROM attorney_checkout_orders WHERE stripe_session_id = ?',
            (session_id,),
        ).fetchone()
        if order_row:
            conn.execute(
                '''
                INSERT OR IGNORE INTO attorney_checkout_listings (order_id)
                VALUES (?)
                ''',
                (order_row['id'],),
            )

    conn.commit()

# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@attorney_checkout_bp.route('/advertise/attorney-sponsorship/checkout')
def advertise_attorney_checkout():
    """Checkout form for attorney sponsorship tiers."""
    package_lookup = _attorney_package_lookup()
    errors = []

    prefill_package = (request.values.get('package') or '').strip().lower()
    if prefill_package not in package_lookup:
        prefill_package = ''

    form_data = {
        'firm_name': (request.values.get('firm_name') or '').strip()[:120],
        'contact_name': (request.values.get('contact_name') or '').strip()[:120],
        'email': (request.values.get('email') or '').strip().lower()[:160],
        'phone': (request.values.get('phone') or '').strip()[:40],
        'website': (request.values.get('website') or '').strip()[:300],
        'counties_served': (request.values.get('counties_served') or '').strip()[:400],
        'practice_areas': (request.values.get('practice_areas') or '').strip()[:400],
        'blurb': (request.values.get('blurb') or '').strip()[:1000],
        'mt_bar_number': (request.values.get('mt_bar_number') or '').strip()[:40],
        'package_id': prefill_package,
        'billing_cycle': 'monthly',
    }

    if not _attorney_checkout_ready():
        return render_template(
            'advertise_attorney_checkout.html',
            packages=_ATTORNEY_PACKAGES,
            package_lookup=package_lookup,
            form_data=form_data,
            form_errors=['Secure checkout is not configured. Please contact support.'],
            checkout_ready=False,
            current_year=datetime.now().year,
        ), 503

    if request.method == 'POST':
        form_data = {
            'firm_name': (request.form.get('firm_name') or '').strip()[:120],
            'contact_name': (request.form.get('contact_name') or '').strip()[:120],
            'email': (request.form.get('email') or '').strip().lower()[:160],
            'phone': (request.form.get('phone') or '').strip()[:40],
            'website': (request.form.get('website') or '').strip()[:300],
            'counties_served': (request.form.get('counties_served') or '').strip()[:400],
            'practice_areas': (request.form.get('practice_areas') or '').strip()[:400],
            'blurb': (request.form.get('blurb') or '').strip()[:1000],
            'mt_bar_number': (request.form.get('mt_bar_number') or '').strip()[:40],
            'package_id': (request.form.get('package_id') or '').strip().lower()[:32],
            'billing_cycle': (request.form.get('billing_cycle') or 'monthly').strip().lower(),
        }

        if not form_data['firm_name']:
            errors.append('Firm name is required.')
        if not form_data['contact_name']:
            errors.append('Contact name is required.')
        if not form_data['email'] or '@' not in form_data['email']:
            errors.append('A valid email is required.')
        if not form_data['counties_served']:
            errors.append('At least one county is required.')
        if form_data['package_id'] not in package_lookup:
            errors.append('Please select a valid package.')
        if form_data['billing_cycle'] not in ('monthly', 'annual'):
            errors.append('Billing cycle is invalid.')
        if request.form.get('terms_ack') != 'yes':
            errors.append('You must accept the advertising terms to continue.')

        if not errors:
            token = secrets.token_urlsafe(24)
            amount_cents = _attorney_price_cents(form_data['package_id'], form_data['billing_cycle'])
            pkg = package_lookup[form_data['package_id']]
            interval = 'year' if form_data['billing_cycle'] == 'annual' else 'month'
            base_url = (getattr(config, 'BASE_URL', '') or '').rstrip('/')

            import stripe
            stripe.api_key = (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip()

            try:
                checkout_session = stripe.checkout.Session.create(
                    mode='subscription',
                    line_items=[{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {'name': f"Montana Blotter Attorney Sponsorship — {pkg['name']}"},
                            'unit_amount': amount_cents,
                            'recurring': {'interval': interval},
                        },
                        'quantity': 1,
                    }],
                    success_url=f'{base_url}/advertise/attorney-sponsorship/checkout/success?session_id={{CHECKOUT_SESSION_ID}}',
                    cancel_url=f'{base_url}/advertise/attorney-sponsorship/checkout/cancel',
                    customer_email=form_data['email'],
                    allow_promotion_codes=False,
                    billing_address_collection='auto',
                    metadata={
                        'flow': 'attorney_sponsorship',
                        'package_id': form_data['package_id'],
                        'billing_cycle': form_data['billing_cycle'],
                        'firm_name': form_data['firm_name'],
                        'contact_name': form_data['contact_name'],
                        'email': form_data['email'],
                        'phone': form_data['phone'],
                        'website': form_data['website'],
                        'counties_served': form_data['counties_served'],
                        'practice_areas': form_data['practice_areas'],
                        'blurb': form_data['blurb'],
                        'mt_bar_number': form_data['mt_bar_number'],
                        'token': token,
                    },
                )
            except Exception:
                errors.append('Unable to start secure checkout. Please try again.')
                checkout_session = None

            if checkout_session:
                checkout_url = _checkout_redirect_url(checkout_session)
                if checkout_url:
                    return redirect(checkout_url, code=303)
                errors.append('Unable to start secure checkout. Please try again.')

    return render_template(
        'advertise_attorney_checkout.html',
        packages=_ATTORNEY_PACKAGES,
        package_lookup=package_lookup,
        form_data=form_data,
        form_errors=errors,
        checkout_ready=True,
        current_year=datetime.now().year,
    )


@attorney_checkout_bp.route('/advertise/attorney-sponsorship/checkout/success')
def advertise_attorney_checkout_success():
    session_id = (request.args.get('session_id') or '').strip()
    order = None
    if session_id:
        conn = get_db()
        row = conn.execute(
            '''
            SELECT id, firm_name, package_id, billing_cycle, status, token, created_at
            FROM attorney_checkout_orders
            WHERE stripe_session_id = ?
            ORDER BY id DESC LIMIT 1
            ''',
            (session_id,),
        ).fetchone()
        conn.close()
        if row:
            order = dict(row)
            if order.get('token'):
                return redirect(
                    url_for('.attorney_control_panel', token=order['token'],
                            welcome='1')
                )
    return render_template(
        'advertise_attorney_checkout_success.html',
        order=order,
        session_id=session_id,
        current_year=datetime.now().year,
    )


@attorney_checkout_bp.route('/advertise/attorney-sponsorship/checkout/cancel')
def advertise_attorney_checkout_cancel():
    return render_template(
        'advertise_attorney_checkout_cancel.html',
        packages=_ATTORNEY_PACKAGES,
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def _safe_ext(filename: str) -> str:
    if '.' not in filename:
        return ''
    return filename.rsplit('.', 1)[-1].lower()

def _save_upload(upload, dest_dir: str, prefix: str) -> str:
    if not upload or not upload.filename:
        return ''
    ext = _safe_ext(secure_filename(upload.filename))
    if ext not in {'jpg', 'jpeg', 'png', 'webp', 'svg'}:
        return ''
    data = upload.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        return ''
    os.makedirs(dest_dir, exist_ok=True)
    stored = f'{prefix}_{secrets.token_hex(8)}.{ext}'
    path = os.path.join(dest_dir, stored)
    with open(path, 'wb') as f:
        f.write(data)
    rel_dir = os.path.basename(dest_dir)
    return f'/static/{rel_dir}/{stored}'

_LOGO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'attorney_logos'
)
_PHOTO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'attorney_photos'
)
os.makedirs(_LOGO_DIR, exist_ok=True)
os.makedirs(_PHOTO_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Advertiser control panel
# ---------------------------------------------------------------------------

@attorney_checkout_bp.route('/attorney-control-panel/<token>')
def attorney_control_panel(token):
    safe_token = (token or '').strip()[:128]
    welcome = request.args.get('welcome') == '1'
    conn = get_db()

    from init_db import ensure_attorney_checkout_schema
    ensure_attorney_checkout_schema(conn)

    order_row = conn.execute(
        '''
        SELECT o.id, o.firm_name, o.contact_name, o.email, o.phone,
               o.website, o.package_id, o.billing_cycle, o.status,
               o.stripe_subscription_id, o.activated_at,
               l.logo_path, l.photo_path, l.blurb, l.impressions, l.clicks
        FROM attorney_checkout_orders o
        LEFT JOIN attorney_checkout_listings l ON l.order_id = o.id
        WHERE o.token = ?
        ''',
        (safe_token,),
    ).fetchone()
    conn.close()

    if not order_row:
        return render_template('404.html'), 404

    order = dict(order_row)
    pkg = _attorney_package_lookup().get(order['package_id']) or {}

    return render_template(
        'advertise_attorney_control_panel.html',
        order=order,
        package=pkg,
        token=safe_token,
        welcome=welcome,
        page_title=f"{order['firm_name']} — Attorney Sponsorship Control Panel",
        meta_description='Manage your Montana Blotter attorney sponsorship listing.',
        current_year=datetime.now().year,
    )


@attorney_checkout_bp.route('/attorney-control-panel/<token>/update', methods=['POST'])
def attorney_control_panel_update(token):
    safe_token = (token or '').strip()[:128]
    conn = get_db()

    from init_db import ensure_attorney_checkout_schema
    ensure_attorney_checkout_schema(conn)

    order_row = conn.execute(
        'SELECT id, package_id FROM attorney_checkout_orders WHERE token = ?',
        (safe_token,),
    ).fetchone()
    if not order_row:
        conn.close()
        return render_template('404.html'), 404

    order_id = order_row['id']

    logo = request.files.get('logo')
    photo = request.files.get('photo')
    blurb = (request.form.get('blurb') or '').strip()[:1000]
    tagline = (request.form.get('tagline') or '').strip()[:120]

    logo_path = _save_upload(logo, _LOGO_DIR, 'attorney_logo') if logo else ''
    photo_path = _save_upload(photo, _PHOTO_DIR, 'attorney_photo') if photo else ''

    if logo_path or photo_path or blurb or tagline:
        conn.execute(
            '''
            UPDATE attorney_checkout_listings
            SET logo_path = COALESCE(?, logo_path),
                photo_path = COALESCE(?, photo_path),
                blurb = COALESCE(?, blurb),
                tagline = COALESCE(?, tagline),
                updated_at = datetime('now')
            WHERE order_id = ?
            ''',
            (logo_path or None, photo_path or None, blurb or None, tagline or None, order_id),
        )
        conn.commit()

    conn.close()
    return redirect(url_for('.attorney_control_panel', token=safe_token))


@attorney_checkout_bp.route('/attorney-control-panel/<token>/cancel', methods=['POST'])
def attorney_control_panel_cancel(token):
    """Request cancellation of an active attorney sponsorship."""
    safe_token = (token or '').strip()[:128]
    conn = get_db()

    from init_db import ensure_attorney_checkout_schema
    ensure_attorney_checkout_schema(conn)

    order_row = conn.execute(
        'SELECT id, stripe_subscription_id, status FROM attorney_checkout_orders WHERE token = ?',
        (safe_token,),
    ).fetchone()
    if not order_row:
        conn.close()
        return render_template('404.html'), 404

    if order_row['status'] != 'active':
        conn.close()
        return render_template(
            'advertise_attorney_control_panel.html',
            order=dict(order_row),
            package=_attorney_package_lookup().get(order_row['package_id'] or ''),
            token=safe_token,
            cancel_error='This listing is not currently active.',
            current_year=datetime.now().year,
        )

    # If we have a subscription ID, cancel at Stripe too
    sub_id = (order_row['stripe_subscription_id'] or '').strip()
    if sub_id:
        try:
            import stripe
            stripe.api_key = (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip()
            stripe.Subscription.cancel(sub_id)
        except Exception:
            pass  # will be reconciled on next webhook

    conn.execute(
        'UPDATE attorney_checkout_orders SET status = ? WHERE id = ?',
        ('cancellation_requested', order_id),
    )
    conn.commit()
    conn.close()

    return redirect(url_for('.attorney_control_panel', token=safe_token,
                            cancellation='1'))
