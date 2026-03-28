"""
Recovery Center Advertising — helpers, Stripe event handler, and public routes.
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
from init_db import ensure_recovery_ad_schema

recovery_ads_bp = Blueprint('recovery_ads', __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOGO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'recovery_logos'
)
PHOTO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'recovery_photos'
)
_ALLOWED_IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB


# ---------------------------------------------------------------------------
# Package definitions
# ---------------------------------------------------------------------------

_PACKAGES = [
    {
        'id': 'bronze',
        'name': 'Bronze Listing',
        'price_monthly_cents': 9900,
        'price_annual_cents': 100900,
        'price_label': '$99/mo',
        'price_label_annual': '$1,009/yr',
        'logo': False,
        'photo': False,
        'featured': False,
        'description_limit': 0,
        'highlight': False,
        'features': [
            'Center name',
            'Phone number',
            'Website link',
        ],
        'short_description': 'Basic directory listing with contact information.',
    },
    {
        'id': 'silver',
        'name': 'Silver Listing',
        'price_monthly_cents': 19900,
        'price_annual_cents': 203000,
        'price_label': '$199/mo',
        'price_label_annual': '$2,030/yr',
        'logo': True,
        'photo': False,
        'featured': False,
        'description_limit': 200,
        'highlight': False,
        'features': [
            'Everything in Bronze',
            'Logo upload',
            'Tagline',
            '200-character description',
            'Services list',
        ],
        'short_description': 'Enhanced listing with branding and services.',
    },
    {
        'id': 'gold',
        'name': 'Gold Featured Listing',
        'price_monthly_cents': 39900,
        'price_annual_cents': 407000,
        'price_label': '$399/mo',
        'price_label_annual': '$4,070/yr',
        'logo': True,
        'photo': True,
        'featured': True,
        'description_limit': 500,
        'highlight': True,
        'features': [
            'Everything in Silver',
            'Featured top-of-page placement',
            'Hero photo upload',
            '500-character description',
            'Monthly impression & click stats',
        ],
        'short_description': 'Premium featured placement at the top of the directory.',
    },
]


def _recovery_ad_package_lookup() -> dict:
    return {pkg['id']: pkg for pkg in _PACKAGES}


def _recovery_ad_price_cents(package_id: str, billing_cycle: str) -> int:
    pkg = _recovery_ad_package_lookup().get(package_id)
    if not pkg:
        return 0
    if billing_cycle == 'annual':
        return pkg['price_annual_cents']
    return pkg['price_monthly_cents']


def _recovery_ad_checkout_ready() -> bool:
    try:
        import stripe as _stripe
    except Exception:
        return False
    secret = (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip()
    pub = (getattr(config, 'STRIPE_PUBLISHABLE_KEY', '') or '').strip()
    return bool(_stripe and secret and pub)


# ---------------------------------------------------------------------------
# Stripe event handler (called from blueprints/payments.py webhook)
# ---------------------------------------------------------------------------

def apply_stripe_recovery_ad_event(conn: sqlite3.Connection, event: dict) -> None:
    """Process a Stripe webhook event for recovery ad subscriptions."""
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}

    if (metadata.get('flow') or '').strip() != 'recovery_ad':
        return

    handled = {
        'checkout.session.completed',
        'checkout.session.async_payment_succeeded',
        'checkout.session.expired',
        'checkout.session.async_payment_failed',
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
                UPDATE recovery_ad_orders
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
        'checkout.session.expired': 'expired',
        'checkout.session.async_payment_failed': 'payment_failed',
    }
    mapped_status = status_map[event_type]

    center_name = (metadata.get('center_name') or '').strip()[:120]
    contact_name = (metadata.get('contact_name') or '').strip()[:120]
    email = (metadata.get('email') or '').strip().lower()[:160]
    phone = (metadata.get('phone') or '').strip()[:40]
    website = (metadata.get('website') or '').strip()[:300]
    package_id = (metadata.get('package_id') or '').strip()[:32]
    billing_cycle = (metadata.get('billing_cycle') or 'monthly').strip().lower()
    if billing_cycle not in ('monthly', 'annual'):
        billing_cycle = 'monthly'
    token = (metadata.get('token') or '').strip()[:64] or secrets.token_urlsafe(24)
    stripe_customer_id = (data_object.get('customer') or '').strip()[:120]
    stripe_subscription_id = (data_object.get('subscription') or '').strip()[:120]

    activated_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if mapped_status == 'active' else None

    conn.execute(
        '''
        INSERT INTO recovery_ad_orders (
            center_name, contact_name, email, phone, website,
            package_id, billing_cycle,
            stripe_customer_id, stripe_subscription_id, stripe_session_id,
            status, token, activated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stripe_session_id) DO UPDATE SET
            status = excluded.status,
            stripe_subscription_id = COALESCE(excluded.stripe_subscription_id, stripe_subscription_id),
            stripe_customer_id = COALESCE(excluded.stripe_customer_id, stripe_customer_id),
            activated_at = COALESCE(recovery_ad_orders.activated_at, excluded.activated_at)
        ''',
        (
            center_name, contact_name, email, phone, website,
            package_id, billing_cycle,
            stripe_customer_id, stripe_subscription_id, session_id,
            mapped_status, token, activated_at,
        ),
    )

    if mapped_status == 'active':
        order_row = conn.execute(
            'SELECT id FROM recovery_ad_orders WHERE stripe_session_id = ?',
            (session_id,),
        ).fetchone()
        if order_row:
            conn.execute(
                '''
                INSERT OR IGNORE INTO recovery_ad_listings (order_id)
                VALUES (?)
                ''',
                (order_row['id'],),
            )

    conn.commit()


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@recovery_ads_bp.route('/recovery-centers')
def recovery_centers_directory():
    conn = get_db()
    ensure_recovery_ad_schema(conn)

    rows = conn.execute(
        '''
        SELECT o.id, o.center_name, o.phone, o.website, o.package_id,
               l.tagline, l.description, l.services, l.city, l.county,
               l.logo_path, l.photo_path, l.impressions, l.clicks
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
        WHERE o.status = 'active'
        ORDER BY
            CASE o.package_id WHEN 'gold' THEN 0 WHEN 'silver' THEN 1 ELSE 2 END,
            o.activated_at ASC
        '''
    ).fetchall()
    listings = [dict(r) for r in rows]

    # Track impressions
    ids = [r['id'] for r in listings]
    for oid in ids:
        conn.execute(
            'UPDATE recovery_ad_listings SET impressions = impressions + 1 WHERE order_id = ?',
            (oid,),
        )
    if ids:
        conn.commit()
    conn.close()

    def _parse_services(raw):
        if not raw:
            return []
        try:
            return _json.loads(raw) or []
        except Exception:
            return []

    for r in listings:
        r['services_list'] = _parse_services(r.get('services'))

    gold = [r for r in listings if r['package_id'] == 'gold']
    silver = [r for r in listings if r['package_id'] == 'silver']
    bronze = [r for r in listings if r['package_id'] == 'bronze']

    return render_template(
        'recovery_centers_directory.html',
        gold_listings=gold,
        silver_listings=silver,
        bronze_listings=bronze,
        page_title='Montana Recovery Centers Directory',
        meta_description='Find addiction treatment and recovery centers in Montana. Listings for Great Falls, Billings, Missoula, and all 56 counties.',
        active_nav='recovery_centers',
        current_year=datetime.now().year,
    )


@recovery_ads_bp.route('/recovery-centers/click/<int:order_id>')
def recovery_center_click(order_id):
    conn = get_db()
    row = conn.execute(
        'SELECT website FROM recovery_ad_orders WHERE id = ? AND status = ?',
        (order_id, 'active'),
    ).fetchone()
    if row:
        conn.execute(
            'UPDATE recovery_ad_listings SET clicks = clicks + 1 WHERE order_id = ?',
            (order_id,),
        )
        conn.commit()
        conn.close()
        website = (row['website'] or '').strip()
        if website and (website.startswith('http://') or website.startswith('https://')):
            return redirect(website)
    conn.close()
    return redirect(url_for('.recovery_centers_directory'))


@recovery_ads_bp.route('/advertise/recovery')
def advertise_recovery():
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )
    return render_template(
        'advertise_recovery.html',
        packages=_PACKAGES,
        support_email=support_email,
        checkout_ready=_recovery_ad_checkout_ready(),
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@recovery_ads_bp.route('/advertise/recovery/checkout', methods=['GET', 'POST'])
def advertise_recovery_checkout():
    try:
        import stripe as _stripe
    except Exception:
        _stripe = None

    package_lookup = _recovery_ad_package_lookup()
    errors = []

    prefill_package = (request.values.get('package') or '').strip().lower()
    if prefill_package not in package_lookup:
        prefill_package = ''

    form_data = {
        'center_name': (request.values.get('center_name') or '').strip()[:120],
        'contact_name': (request.values.get('contact_name') or '').strip()[:120],
        'email': (request.values.get('email') or '').strip().lower()[:160],
        'phone': (request.values.get('phone') or '').strip()[:40],
        'website': (request.values.get('website') or '').strip()[:300],
        'package_id': prefill_package,
        'billing_cycle': 'monthly',
    }

    if not _recovery_ad_checkout_ready():
        return render_template(
            'advertise_recovery_checkout.html',
            packages=_PACKAGES,
            package_lookup=package_lookup,
            form_data=form_data,
            form_errors=['Secure checkout is not configured. Please contact support.'],
            checkout_ready=False,
            active_nav='advertise',
            current_year=datetime.now().year,
        ), 503

    if request.method == 'POST':
        form_data = {
            'center_name': (request.form.get('center_name') or '').strip()[:120],
            'contact_name': (request.form.get('contact_name') or '').strip()[:120],
            'email': (request.form.get('email') or '').strip().lower()[:160],
            'phone': (request.form.get('phone') or '').strip()[:40],
            'website': (request.form.get('website') or '').strip()[:300],
            'package_id': (request.form.get('package_id') or '').strip().lower()[:32],
            'billing_cycle': (request.form.get('billing_cycle') or 'monthly').strip().lower(),
        }

        if not form_data['center_name']:
            errors.append('Center name is required.')
        if not form_data['contact_name']:
            errors.append('Contact name is required.')
        if not form_data['email'] or '@' not in form_data['email']:
            errors.append('A valid email is required.')
        if not form_data['phone']:
            errors.append('Phone number is required.')
        if form_data['package_id'] not in package_lookup:
            errors.append('Please select a valid package.')
        if form_data['billing_cycle'] not in ('monthly', 'annual'):
            errors.append('Billing cycle is invalid.')
        if request.form.get('terms_ack') != 'yes':
            errors.append('You must accept the advertising terms to continue.')

        if not errors:
            token = secrets.token_urlsafe(24)
            amount_cents = _recovery_ad_price_cents(form_data['package_id'], form_data['billing_cycle'])
            pkg = package_lookup[form_data['package_id']]
            interval = 'year' if form_data['billing_cycle'] == 'annual' else 'month'
            base_url = (getattr(config, 'BASE_URL', '') or '').rstrip('/')

            _stripe.api_key = (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip()

            try:
                checkout_session = _stripe.checkout.Session.create(
                    mode='subscription',
                    line_items=[{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {'name': f"Montana Blotter Recovery Ad — {pkg['name']}"},
                            'unit_amount': amount_cents,
                            'recurring': {'interval': interval},
                        },
                        'quantity': 1,
                    }],
                    success_url=f'{base_url}/advertise/recovery/checkout/success?session_id={{CHECKOUT_SESSION_ID}}',
                    cancel_url=f'{base_url}/advertise/recovery/checkout/cancel',
                    customer_email=form_data['email'],
                    allow_promotion_codes=False,
                    billing_address_collection='auto',
                    metadata={
                        'flow': 'recovery_ad',
                        'package_id': form_data['package_id'],
                        'billing_cycle': form_data['billing_cycle'],
                        'center_name': form_data['center_name'],
                        'contact_name': form_data['contact_name'],
                        'email': form_data['email'],
                        'phone': form_data['phone'],
                        'website': form_data['website'],
                        'token': token,
                    },
                )
            except Exception:
                errors.append('Unable to start secure checkout. Please try again.')
                checkout_session = None

            if checkout_session:
                return redirect(checkout_session.url, code=303)

    return render_template(
        'advertise_recovery_checkout.html',
        packages=_PACKAGES,
        package_lookup=package_lookup,
        form_data=form_data,
        form_errors=errors,
        checkout_ready=True,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@recovery_ads_bp.route('/advertise/recovery/checkout/success')
def advertise_recovery_checkout_success():
    session_id = (request.args.get('session_id') or '').strip()
    order = None
    if session_id:
        conn = get_db()
        row = conn.execute(
            '''
            SELECT id, center_name, package_id, billing_cycle, status, token, created_at
            FROM recovery_ad_orders
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
                    url_for('.advertise_recovery_control_panel',
                            token=order['token'],
                            welcome='1')
                )
    return render_template(
        'advertise_recovery_checkout_success.html',
        order=order,
        session_id=session_id,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@recovery_ads_bp.route('/advertise/recovery/checkout/cancel')
def advertise_recovery_checkout_cancel():
    return render_template(
        'advertise_recovery_checkout_cancel.html',
        packages=_PACKAGES,
        active_nav='advertise',
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
    """Save an uploaded image file. Returns relative URL path or empty string."""
    if not upload or not upload.filename:
        return ''
    ext = _safe_ext(secure_filename(upload.filename))
    if ext not in _ALLOWED_IMAGE_EXTS:
        return ''
    data = upload.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        return ''
    os.makedirs(dest_dir, exist_ok=True)
    stored = f'{prefix}_{secrets.token_hex(8)}.{ext}'
    path = os.path.join(dest_dir, stored)
    with open(path, 'wb') as f:
        f.write(data)
    rel_dir = os.path.basename(dest_dir)
    return f'/static/{rel_dir}/{stored}'


# ---------------------------------------------------------------------------
# Advertiser control panel
# ---------------------------------------------------------------------------

@recovery_ads_bp.route('/recovery-control-panel/<token>')
def advertise_recovery_control_panel(token):
    safe_token = (token or '').strip()[:128]
    welcome = request.args.get('welcome') == '1'
    conn = get_db()
    ensure_recovery_ad_schema(conn)

    order_row = conn.execute(
        '''
        SELECT o.id, o.center_name, o.contact_name, o.email, o.phone,
               o.website, o.package_id, o.billing_cycle, o.status,
               o.stripe_subscription_id, o.activated_at,
               l.tagline, l.description, l.services, l.city, l.county,
               l.logo_path, l.photo_path, l.impressions, l.clicks
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
        WHERE o.token = ?
        ''',
        (safe_token,),
    ).fetchone()
    conn.close()

    if not order_row:
        return render_template('404.html'), 404

    order = dict(order_row)
    pkg = _recovery_ad_package_lookup().get(order['package_id']) or {}
    services_list = []
    if order.get('services'):
        try:
            services_list = _json.loads(order['services']) or []
        except Exception:
            pass

    return render_template(
        'advertise_recovery_control_panel.html',
        order=order,
        package=pkg,
        services_list=services_list,
        token=safe_token,
        welcome=welcome,
        page_title=f"{order['center_name']} — Recovery Center Control Panel",
        meta_description='Manage your recovery center directory listing on Montana Blotter.',
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@recovery_ads_bp.route('/recovery-control-panel/<token>/update', methods=['POST'])
def advertise_recovery_control_panel_update(token):
    safe_token = (token or '').strip()[:128]
    conn = get_db()
    order_row = conn.execute(
        'SELECT id, package_id FROM recovery_ad_orders WHERE token = ?',
        (safe_token,),
    ).fetchone()
    if not order_row:
        conn.close()
        abort(404)

    order_id = order_row['id']
    pkg = _recovery_ad_package_lookup().get(order_row['package_id']) or {}
    desc_limit = pkg.get('description_limit') or 0

    tagline = (request.form.get('tagline') or '').strip()[:120]
    description = (request.form.get('description') or '').strip()
    if desc_limit:
        description = description[:desc_limit]
    city = (request.form.get('city') or '').strip()[:80]
    county = (request.form.get('county') or '').strip()[:80]
    website = (request.form.get('website') or '').strip()[:300]
    raw_services = [s.strip() for s in (request.form.get('services') or '').split(',') if s.strip()]
    services_json = _json.dumps(raw_services[:20])

    logo_path = ''
    photo_path = ''
    if pkg.get('logo'):
        logo_upload = request.files.get('logo')
        if logo_upload and logo_upload.filename:
            logo_path = _save_upload(logo_upload, LOGO_UPLOAD_DIR, f'logo_{order_id}')
    if pkg.get('photo'):
        photo_upload = request.files.get('photo')
        if photo_upload and photo_upload.filename:
            photo_path = _save_upload(photo_upload, PHOTO_UPLOAD_DIR, f'photo_{order_id}')

    update_fields = "tagline=?, description=?, services=?, city=?, county=?, updated_at=datetime('now')"
    params = [tagline, description, services_json, city, county]
    if logo_path:
        update_fields += ', logo_path=?'
        params.append(logo_path)
    if photo_path:
        update_fields += ', photo_path=?'
        params.append(photo_path)
    params.append(order_id)

    conn.execute(f'UPDATE recovery_ad_listings SET {update_fields} WHERE order_id=?', params)
    if website:
        conn.execute('UPDATE recovery_ad_orders SET website=? WHERE id=?', (website, order_id))
    conn.commit()
    conn.close()

    return redirect(url_for('.advertise_recovery_control_panel', token=safe_token))
