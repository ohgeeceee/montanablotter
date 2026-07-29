"""
Lawyer Advertising — paid-placement directory + lead-gen marketplace.

A separate /lawyers directory parallel to /attorneys (which stays free opt-in).
Attorneys buy placements in Bronze / Silver / Gold tiers; we route consumer
inquiries from the public intake form to active advertisers by county.

Mirrors `blueprints/recovery_ads.py` for consistency.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import sqlite3
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.utils import secure_filename

import config
from db import get_db
from init_db import ensure_lawyer_ad_schema

lawyer_ads_bp = Blueprint('lawyer_ads', __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOGO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'lawyer_logos'
)
PHOTO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'lawyer_photos'
)
_ALLOWED_IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB

os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
os.makedirs(PHOTO_UPLOAD_DIR, exist_ok=True)


_MONTANA_COUNTIES = [
    'Beaverhead', 'Big Horn', 'Blaine', 'Broadwater', 'Carbon', 'Carter',
    'Cascade', 'Chouteau', 'Custer', 'Daniels', 'Dawson', 'Deer Lodge',
    'Fallon', 'Fergus', 'Flathead', 'Gallatin', 'Garfield', 'Glacier',
    'Golden Valley', 'Granite', 'Hill', 'Jefferson', 'Judith Basin', 'Lake',
    'Lewis and Clark', 'Liberty', 'Lincoln', 'Madison', 'McCone', 'Meagher',
    'Mineral', 'Missoula', 'Musselshell', 'Park', 'Petroleum', 'Phillips',
    'Pondera', 'Powder River', 'Powell', 'Prairie', 'Ravalli', 'Richland',
    'Roosevelt', 'Rosebud', 'Sanders', 'Sheridan', 'Silver Bow', 'Stillwater',
    'Sweet Grass', 'Teton', 'Toole', 'Treasure', 'Valley', 'Wheatland',
    'Wibaux', 'Yellowstone',
]

# ---------------------------------------------------------------------------
# Package definitions — Bronze / Silver / Gold
# ---------------------------------------------------------------------------

_PACKAGES = [
    {
        'id': 'bronze',
        'name': 'Bronze Listing',
        'price_monthly_cents': 14900,
        'price_annual_cents': 152000,
        'price_label': '$149/mo',
        'price_label_annual': '$1,520/yr',
        'logo': False,
        'photo': False,
        'featured': False,
        'description_limit': 200,
        'priority_rank': 3,
        'features': [
            'Firm name + counties served',
            'Phone + website',
            'Tap-to-call and tap-to-email',
            'Basic intake lead routing',
        ],
        'short_description': 'Standard paid listing with tap-to-call and lead capture.',
    },
    {
        'id': 'silver',
        'name': 'Silver Featured',
        'price_monthly_cents': 29900,
        'price_annual_cents': 305000,
        'price_label': '$299/mo',
        'price_label_annual': '$3,050/yr',
        'logo': True,
        'photo': False,
        'featured': False,
        'description_limit': 500,
        'priority_rank': 2,
        'features': [
            'Everything in Bronze',
            'Logo on your profile card',
            '500-character blurb',
            'Pinned above Bronze listings',
            'Monthly impression + click report',
        ],
        'short_description': 'Logo + larger card. Pinned above Bronze in your counties.',
    },
    {
        'id': 'gold',
        'name': 'Gold Priority',
        'price_monthly_cents': 59900,
        'price_annual_cents': 611000,
        'price_label': '$599/mo',
        'price_label_annual': '$6,110/yr',
        'logo': True,
        'photo': True,
        'featured': True,
        'description_limit': 1000,
        'priority_rank': 1,
        'features': [
            'Everything in Silver',
            'Top of every county section you serve',
            'Photo on your profile card',
            'Custom callout / tagline',
            'Priority lead routing',
            'Immediate email lead alerts',
            'Monthly placement + conversion report',
        ],
        'short_description': 'Top placement + priority lead routing. The firm Montana Blotter surfaces first.',
    },
]

_VALID_PACKAGE_IDS = {p['id'] for p in _PACKAGES}


def _normalize_county(value: str) -> str:
    return ' '.join((value or '').replace('&', 'and').lower().split())


def _county_matches(counties_served: str, county: str) -> bool:
    target = _normalize_county(county)
    if not target:
        return False
    return any(_normalize_county(candidate) == target for candidate in _parse_counties(counties_served))


# Per-county inventory caps: 1 Gold / 2 Silver / 2 Bronze. Operator comp
# orders (provider='manual') and the /admin manual-entry flow are excluded.
_LAWYER_COUNTY_CAPS = {'gold': 1, 'silver': 2, 'bronze': 2}


def _county_active_capacity(conn: sqlite3.Connection, county: str) -> dict:
    """Return current active count per package for a single county."""
    counts = {'gold': 0, 'silver': 0, 'bronze': 0}
    target = _normalize_county(county)
    if not target:
        return counts
    rows = conn.execute(
        """
        SELECT o.package_id AS package_id, o.counties_served AS counties_served
        FROM lawyer_ad_orders o
        JOIN lawyer_ad_listings l ON l.order_id = o.id
        WHERE o.status = 'active' AND l.is_active = 1
        """
    ).fetchall()
    for row in rows:
        counties = _parse_counties(row['counties_served'])
        if not any(_normalize_county(c) == target for c in counties):
            continue
        pkg = (row['package_id'] or '').lower()
        if pkg in counts:
            counts[pkg] += 1
    return counts


def _county_capacity_blocked(conn: sqlite3.Connection, package_id: str, counties_served: str) -> bool:
    """Return True if any county this listing serves has already hit its package cap."""
    cap = _LAWYER_COUNTY_CAPS.get(package_id)
    if not cap:
        return False
    target_county = None
    for county in _parse_counties(counties_served):
        active = _county_active_capacity(conn, county).get(package_id, 0)
        if active >= cap:
            return True
    return False


_PRACTICE_AREAS = [
    'Criminal Defense',
    'DUI / Traffic',
    'Family Law',
    'Personal Injury',
    'Civil Litigation',
    'Estate Planning',
    'Real Estate',
    'Immigration',
    'Bankruptcy',
    'Employment',
]


def _package_lookup() -> dict:
    return {p['id']: p for p in _PACKAGES}


def _price_cents(package_id: str, billing_cycle: str) -> int:
    pkg = _package_lookup().get(package_id)
    if not pkg:
        return 0
    if billing_cycle == 'annual':
        return pkg['price_annual_cents']
    return pkg['price_monthly_cents']


def _checkout_ready() -> bool:
    try:
        import stripe as _stripe
    except Exception:
        return False
    secret = (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip()
    pub = (getattr(config, 'STRIPE_PUBLISHABLE_KEY', '') or '').strip()
    return bool(_stripe and secret and pub)


def _checkout_redirect_url(checkout_session) -> str:
    if isinstance(checkout_session, dict):
        return (checkout_session.get('url') or '').strip()
    return (getattr(checkout_session, 'url', '') or '').strip()


def _hash_ip(ip: str) -> str:
    if not ip:
        return ''
    return hashlib.sha256(ip.encode('utf-8')).hexdigest()[:32]


def _smtp_settings() -> tuple[str, str, str, int]:
    user = (getattr(config, 'SMTP_USER', '') or getattr(config, 'EMAIL_USER', '') or '').strip()
    password = (getattr(config, 'SMTP_PASSWORD', '') or getattr(config, 'EMAIL_PASSWORD', '') or '').strip()
    server = (getattr(config, 'SMTP_SERVER', '') or '').strip()
    port = int(getattr(config, 'SMTP_PORT', 587) or 587)
    return user, password, server, port


def _send_lawyer_lead_email(order: dict, lead: dict) -> tuple[bool, str]:
    """Deliver one opted-in consumer lead to one active advertiser."""
    user, password, server, port = _smtp_settings()
    destination = (order.get('email') or '').strip().lower()
    if not (user and password and server and destination and '@' in destination):
        return False, 'smtp_not_configured'

    firm_name = escape(str(order.get('firm_name') or 'your firm'))
    full_name = escape(str(lead.get('full_name') or ''))
    phone = escape(str(lead.get('phone') or ''))
    email = escape(str(lead.get('email') or ''))
    county = escape(str(lead.get('county') or ''))
    case_type = escape(str(lead.get('case_type') or 'Not specified'))
    notes = escape(str(lead.get('notes') or 'Not provided'))
    base_url = (getattr(config, 'BASE_URL', '') or 'https://montanablotter.com').rstrip('/')

    msg = MIMEMultipart('alternative')
    msg['Subject'] = f'New Montana Blotter lead for {firm_name}'
    msg['From'] = f'Montana Blotter <{user}>'
    msg['To'] = destination
    msg['Reply-To'] = 'advertising@montanablotter.com'
    plain = (
        f'New consumer inquiry routed to {order.get("firm_name") or "your firm"}.\n\n'
        f'Name: {lead.get("full_name") or ""}\n'
        f'Phone: {lead.get("phone") or ""}\n'
        f'Email: {lead.get("email") or "Not provided"}\n'
        f'County: {lead.get("county") or ""}\n'
        f'Case type: {lead.get("case_type") or "Not specified"}\n'
        f'Notes: {lead.get("notes") or "Not provided"}\n\n'
        f'Please contact this person directly. Montana Blotter does not provide legal advice.\n'
        f'Advertising support: {base_url}/advertise/lawyers\n'
    )
    html = f'''<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#1e1b18;line-height:1.5;">
  <h2 style="font-family:Georgia,serif;">New consumer inquiry</h2>
  <p>This inquiry was routed to <strong>{firm_name}</strong> from Montana Blotter.</p>
  <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
    <tr><td><strong>Name</strong></td><td>{full_name}</td></tr>
    <tr><td><strong>Phone</strong></td><td><a href="tel:{phone}">{phone}</a></td></tr>
    <tr><td><strong>Email</strong></td><td>{email or 'Not provided'}</td></tr>
    <tr><td><strong>County</strong></td><td>{county}</td></tr>
    <tr><td><strong>Case type</strong></td><td>{case_type}</td></tr>
    <tr><td><strong>Notes</strong></td><td>{notes}</td></tr>
  </table>
  <p><strong>Follow up directly and promptly.</strong> Montana Blotter does not provide legal advice or endorse a specific attorney.</p>
  <p style="font-size:12px;color:#666;">Advertising support: <a href="{escape(base_url)}/advertise/lawyers">{escape(base_url)}/advertise/lawyers</a></p>
</body></html>'''
    msg.attach(MIMEText(plain, 'plain'))
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP(server, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, [destination], msg.as_string())
        return True, ''
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'Lawyer lead email failed order_id=%s destination=%s: %s',
            order.get('id'), destination, exc,
        )
        return False, str(exc)[:300]


def _record_lead_delivery(lead_id: int, order_id: int, destination: str, sent: bool, error: str = '') -> None:
    conn = get_db()
    conn.execute(
        '''
        INSERT INTO lawyer_lead_deliveries
            (lead_id, order_id, channel, destination, status, error, sent_at)
        VALUES (?, ?, 'email', ?, ?, ?, CASE WHEN ? = 1 THEN datetime('now') ELSE NULL END)
        ON CONFLICT(lead_id, order_id, channel, destination) DO UPDATE SET
            status = excluded.status,
            error = excluded.error,
            sent_at = excluded.sent_at
        ''',
        (lead_id, order_id, destination, 'sent' if sent else 'failed', error or None, 1 if sent else 0),
    )
    conn.commit()
    conn.close()


def _deliver_lawyer_lead(lead_id: int, matching_orders: list, lead: dict) -> None:
    for row in matching_orders:
        order = dict(row)
        destination = (order.get('email') or '').strip().lower()
        sent, error = _send_lawyer_lead_email(order, lead)
        _record_lead_delivery(lead_id, int(order['id']), destination, sent, error)


def _slug_county(name: str) -> str:
    """Canonical county-name URL slug used by both template links and the route."""
    if not name:
        return ''
    s = name.lower().strip()
    s = s.replace('&', '-and-')
    s = s.replace(' ', '-')
    while '--' in s:
        s = s.replace('--', '-')
    return s.strip('-')


def _parse_counties(raw: str) -> list:
    if not raw:
        return []
    return [c.strip() for c in raw.split(',') if c.strip()]


# ---------------------------------------------------------------------------
# Stripe event handler — called from blueprints/payments.py webhook
# ---------------------------------------------------------------------------

def apply_stripe_lawyer_ad_event(conn: sqlite3.Connection, event: dict) -> None:
    """Process a Stripe webhook event for lawyer ad subscriptions."""
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}

    if (metadata.get('flow') or '').strip() != 'lawyer_ad':
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

    if event_type == 'customer.subscription.deleted':
        sub_id = (data_object.get('id') or '').strip()
        if sub_id:
            conn.execute(
                '''
                UPDATE lawyer_ad_orders
                SET status = 'cancelled', cancelled_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE provider_subscription_id = ? AND status = 'active'
                ''',
                (sub_id,),
            )
            conn.execute(
                '''
                UPDATE lawyer_ad_listings
                SET is_active = 0, updated_at = datetime('now')
                WHERE order_id IN (
                    SELECT id FROM lawyer_ad_orders WHERE provider_subscription_id = ?
                )
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

    firm_name = (metadata.get('firm_name') or '').strip()[:200]
    contact_name = (metadata.get('contact_name') or '').strip()[:160]
    email = (metadata.get('email') or '').strip().lower()[:160]
    phone = (metadata.get('phone') or '').strip()[:40]
    website = (metadata.get('website') or '').strip()[:300]
    bar_number = (metadata.get('bar_number') or '').strip()[:40]
    counties_served = (metadata.get('counties_served') or '').strip()[:600]
    practice_areas = (metadata.get('practice_areas') or '').strip()[:600]
    package_id = (metadata.get('package_id') or '').strip()[:32]
    billing_cycle = (metadata.get('billing_cycle') or 'monthly').strip().lower()
    if billing_cycle not in ('monthly', 'annual'):
        billing_cycle = 'monthly'
    token = (metadata.get('token') or '').strip()[:64] or secrets.token_urlsafe(24)
    provider_customer_id = (data_object.get('customer') or '').strip()[:120]
    provider_subscription_id = (data_object.get('subscription') or '').strip()[:120]

    activated_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if mapped_status == 'active' else None

    conn.execute(
        '''
        INSERT INTO lawyer_ad_orders (
            firm_name, contact_name, email, phone, website,
            bar_number, counties_served, practice_areas,
            package_id, billing_cycle,
            provider_customer_id, provider_subscription_id, provider_session_id,
            status, onboarding_token, paid_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(provider_session_id) DO UPDATE SET
            status = excluded.status,
            provider_subscription_id = COALESCE(excluded.provider_subscription_id, lawyer_ad_orders.provider_subscription_id),
            provider_customer_id = COALESCE(excluded.provider_customer_id, lawyer_ad_orders.provider_customer_id),
            paid_at = COALESCE(lawyer_ad_orders.paid_at, excluded.paid_at),
            updated_at = datetime('now')
        ''',
        (
            firm_name, contact_name, email, phone, website,
            bar_number, counties_served, practice_areas,
            package_id, billing_cycle,
            provider_customer_id, provider_subscription_id, session_id,
            mapped_status, token, activated_at,
        ),
    )

    if mapped_status == 'active':
        # Per-county inventory cap. If any county this listing serves is at
        # the package cap, leave the order in a `capacity_blocked` terminal
        # status. The admin CMS can move it forward manually once the cap is
        # raised or another listing is cancelled.
        if _county_capacity_blocked(conn, package_id, counties_served):
            conn.execute(
                '''
                UPDATE lawyer_ad_orders
                SET status = 'capacity_blocked',
                    notes = COALESCE(notes, '') || ' [blocked: county cap reached]',
                    updated_at = datetime('now')
                WHERE provider_session_id = ?
                ''',
                (session_id,),
            )
            conn.commit()
            return
        order_row = conn.execute(
            'SELECT id FROM lawyer_ad_orders WHERE provider_session_id = ?',
            (session_id,),
        ).fetchone()
        if order_row:
            oid = order_row['id']
            conn.execute(
                '''
                INSERT INTO lawyer_ad_listings (order_id, firm_name, counties_served, practice_areas, is_active)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(order_id) DO UPDATE SET
                    firm_name = excluded.firm_name,
                    counties_served = excluded.counties_served,
                    practice_areas = excluded.practice_areas,
                    is_active = 1,
                    updated_at = datetime('now')
                ''',
                (oid, firm_name, counties_served, practice_areas),
            )

    conn.commit()


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@lawyer_ads_bp.route('/lawyers')
def lawyers_directory():
    """Public-facing paid lawyer directory (separate from /attorneys free opt-in)."""
    conn = get_db()
    ensure_lawyer_ad_schema(conn)

    rows = conn.execute(
        '''
        SELECT
            l.id AS listing_id,
            l.firm_name,
            l.tagline,
            l.description,
            l.practice_areas,
            l.counties_served,
            l.logo_path,
            l.photo_path,
            l.headline,
            l.body_copy,
            l.cta_text,
            l.target_url,
            l.impressions,
            l.clicks,
            o.id AS order_id,
            o.package_id,
            o.website,
            o.phone,
            o.email,
            o.paid_at
        FROM lawyer_ad_listings l
        JOIN lawyer_ad_orders o ON o.id = l.order_id
        WHERE o.status = 'active' AND l.is_active = 1
        ORDER BY
            CASE o.package_id WHEN 'gold' THEN 0 WHEN 'silver' THEN 1 ELSE 2 END,
            datetime(o.paid_at) ASC,
            l.firm_name ASC
        '''
    ).fetchall()

    listings = [dict(r) for r in rows]

    # Group by county for county sections
    by_county = {}
    for lst in listings:
        for county in _parse_counties(lst.get('counties_served')):
            by_county.setdefault(county, []).append(lst)

    # The browser event sink records rendered-card impressions. Avoid a second
    # server-side increment here so each page view counts once.

    help_contact = {
        'tel_href': '',
        'sms_href': '',
        'chat_url': '',
    }

    selected_county = (request.args.get('county') or '').strip()

    conn.close()

    counties_with_coverage = {county: len(items) for county, items in by_county.items()}
    available_counties = sorted(set(_MONTANA_COUNTIES) | set(counties_with_coverage))

    return render_template(
        'lawyers.html',
        listings=listings,
        by_county=by_county,
        selected_county=selected_county,
        available_counties=available_counties,
        counties_with_coverage=counties_with_coverage,
        help_contact=help_contact,
        page_title='Montana Lawyers Directory — Find a Defense Attorney Near You',
        meta_description=(
            'Paid listings from licensed Montana attorneys. Tap-to-call, '
            'tap-to-text, and free intake form routing. Defense, DUI, family, '
            'and personal injury attorneys by county.'
        ),
        canonical_url='https://montanablotter.com/lawyers',
        active_nav='lawyers',
        current_year=datetime.now().year,
    )


@lawyer_ads_bp.route('/lawyers/<county_slug>')
def lawyers_directory_county(county_slug):
    """County-scoped view of the lawyers directory."""
    needle = county_slug.replace('-', ' ').lower()

    conn = get_db()
    ensure_lawyer_ad_schema(conn)
    rows = conn.execute(
        '''
        SELECT
            l.id AS listing_id,
            l.firm_name, l.tagline, l.description, l.practice_areas,
            l.counties_served, l.logo_path, l.photo_path,
            l.headline, l.body_copy, l.cta_text, l.target_url,
            l.impressions, l.clicks,
            o.id AS order_id, o.package_id, o.website, o.phone, o.email, o.paid_at
        FROM lawyer_ad_listings l
        JOIN lawyer_ad_orders o ON o.id = l.order_id
        WHERE o.status = 'active' AND l.is_active = 1
        ORDER BY
            CASE o.package_id WHEN 'gold' THEN 0 WHEN 'silver' THEN 1 ELSE 2 END,
            l.firm_name ASC
        '''
    ).fetchall()
    conn.close()

    listings = [dict(r) for r in rows]
    target_county = None
    for lst in listings:
        for c in _parse_counties(lst.get('counties_served')):
            slug_norm = _slug_county(c).replace('-', ' ')
            if slug_norm == needle or c.lower() == needle:
                target_county = c
                break
        if target_county:
            break

    if not target_county:
        target_county = county_slug.replace('-', ' ').replace(' and ', ' & ').title()

    filtered_listings = [
        lst for lst in listings
        if _county_matches(lst.get('counties_served', ''), target_county)
    ]

    counties_with_coverage: dict[str, int] = {county: 0 for county in _MONTANA_COUNTIES}
    conn = get_db()
    ensure_lawyer_ad_schema(conn)
    live_county_counts = conn.execute(
        'SELECT o.counties_served AS counties_served FROM lawyer_ad_orders o'
    ).fetchall()
    conn.close()
    for row in live_county_counts:
        for c in _parse_counties(row['counties_served']):
            counties_with_coverage[c] = counties_with_coverage.get(c, 0) + 1
    available_counties = sorted(set(_MONTANA_COUNTIES) | set(counties_with_coverage))

    return render_template(
        'lawyers.html',
        listings=filtered_listings,
        by_county={target_county: filtered_listings} if filtered_listings else {},
        selected_county=target_county,
        available_counties=available_counties,
        counties_with_coverage=counties_with_coverage,
        page_title=f'{target_county} County Lawyers — Montana Blotter',
        meta_description=(
            f'Licensed Montana attorneys serving {target_county} County. '
            'Tap-to-call and free intake form routing.'
        ),
        canonical_url=f'https://montanablotter.com/lawyers/{_slug_county(target_county)}',
        active_nav='lawyers',
        current_year=datetime.now().year,
    )


@lawyer_ads_bp.route('/lawyers/intake', methods=['POST'])
def lawyers_intake():
    """Public lead intake — captures the consumer inquiry and routes it to active advertisers."""
    form = request.form
    full_name = (form.get('full_name') or '').strip()[:200]
    phone = (form.get('phone') or '').strip()[:40]
    email = (form.get('email') or '').strip().lower()[:160]
    county = (form.get('county') or '').strip()[:120]
    case_type = (form.get('case_type') or '').strip()[:80]
    notes = (form.get('notes') or '').strip()[:1000]
    source = (form.get('source') or 'lawyers_directory').strip()[:80]
    return_path = (form.get('return_path') or '/lawyers').strip()[:200]
    consent_ack = (form.get('consent_ack') or '').strip().lower()
    fax_number = (form.get('fax_number') or '').strip()[:80]

    errors = []
    if not full_name:
        errors.append('missing_name')
    if not phone:
        errors.append('missing_phone')
    if not county:
        errors.append('missing_county')
    if fax_number:
        errors.append('spam_detected')
    if consent_ack != 'yes':
        errors.append('missing_consent')

    if return_path and not (return_path.startswith('/lawyers') or return_path.startswith('/attorneys')):
        return_path = '/lawyers'

    if errors:
        return redirect(f'{return_path}?error=missing_required#intake')

    conn = get_db()
    ensure_lawyer_ad_schema(conn)

    ip_hash = _hash_ip(request.remote_addr or '')
    user_agent = (request.headers.get('User-Agent') or '')[:300]

    # Find active orders covering this county
    matching_orders = conn.execute(
        '''
        SELECT o.id, o.firm_name, o.email, o.phone, o.package_id
        FROM lawyer_ad_orders o
        WHERE o.status = 'active'
        ORDER BY
            CASE o.package_id WHEN 'gold' THEN 0 WHEN 'silver' THEN 1 ELSE 2 END,
            datetime(o.paid_at) ASC
        '''
    ).fetchall()
    matching_orders = [
        row for row in matching_orders
        if _county_matches(row['counties_served'], county)
    ]

    routed_ids = [str(r['id']) for r in matching_orders]
    routed_csv = ','.join(routed_ids)

    cur = conn.execute(
        '''
        INSERT INTO lawyer_consumer_leads (
            full_name, phone, email, county, case_type, notes,
            source, ip_hash, user_agent, routed_order_ids,
            consent_at, consent_ip_hash, consent_text_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, 'lawyer-lead-v1')
        ''',
        (
            full_name, phone, email, county, case_type, notes,
            source, ip_hash, user_agent, routed_csv, ip_hash,
        ),
    )
    lead_id = cur.lastrowid

    # Increment leads counter on each matched listing
    if routed_ids:
        placeholders = ','.join('?' * len(routed_ids))
        conn.execute(
            f'UPDATE lawyer_ad_listings SET leads = leads + 1 WHERE order_id IN ({placeholders})',
            routed_ids,
        )

    conn.execute(
        '''
        INSERT INTO lawyer_consumer_lead_events
            (lead_id, event_type, county, source, order_id)
        VALUES (?, 'form_submit', ?, ?, NULL)
        ''',
        (lead_id, county, source),
    )
    conn.commit()
    conn.close()

    if not matching_orders:
        flash(
            'Your inquiry was received. No paid attorney currently covers that county, '
            'so no contact information was shared.',
            'warning',
        )
    else:
        _deliver_lawyer_lead(
            lead_id,
            matching_orders,
            {
                'full_name': full_name,
                'phone': phone,
                'email': email,
                'county': county,
                'case_type': case_type,
                'notes': notes,
            },
        )
        flash(
            f'Your request was sent to {len(routed_ids)} active '
            f'{"attorney" if len(routed_ids) == 1 else "attorneys"} '
            f'covering {county} County.',
            'success',
        )
    return redirect(f'{return_path}?submitted=1#intake')


@lawyer_ads_bp.route('/api/lawyer-ads/event', methods=['POST'])
def api_lawyer_ads_event():
    """Impression/click/call/text event sink — write-only, never blocks page render."""
    payload = request.get_json(silent=True) or {}
    event_type = (payload.get('event_type') or '').strip()[:32]
    order_id = payload.get('order_id')
    county = (payload.get('county') or '').strip()[:120]
    source = (payload.get('source') or '').strip()[:80]

    if not event_type:
        return ('', 204)

    valid_events = {'impression', 'click', 'call', 'text', 'email', 'lead'}
    if event_type not in valid_events:
        return ('', 204)

    # Light abuse mitigation. The endpoint is intentionally open so the public
    # /lawyers page can fire sendBeacon events without auth, but we still:
    #   * cap payload sizes,
    #   * require a real active order_id before counting metrics,
    #   * reject the request if it is not same-origin (the public page always is).
    if (
        len(county) > 120
        or len(source) > 80
        or not isinstance(order_id, int)
        or order_id <= 0
    ):
        return ('', 204)

    forwarded_origin = (request.headers.get('Origin') or '').strip()
    host = (request.headers.get('Host') or '').strip()
    if forwarded_origin and host and forwarded_origin not in (
        f'http://{host}', f'https://{host}',
    ):
        # Cross-origin POST: do not count it. The page can still fire benign pings
        # via the same template, which won't trip this because sendBeacon is
        # same-origin by default.
        return ('', 204)

    try:
        conn = get_db()
        # Validate that the order exists and is currently active before we count it.
        order_row = conn.execute(
            "SELECT 1 FROM lawyer_ad_orders WHERE id = ? AND status = 'active'",
            (order_id,),
        ).fetchone()
        if not order_row:
            conn.close()
            return ('', 204)

        listing_row = conn.execute(
            'SELECT id FROM lawyer_ad_listings WHERE order_id = ? LIMIT 1',
            (order_id,),
        ).fetchone()
        listing_id = listing_row['id'] if listing_row else None

        metric_columns = {
            'impression': 'impressions',
            'click': 'clicks',
            'call': 'calls',
            'lead': 'leads',
        }
        metric_column = metric_columns.get(event_type)

        ip_hash = _hash_ip(request.remote_addr or '') or 'anon'
        user_agent = (request.headers.get('User-Agent') or '')[:300]

        # Insert the raw event row first so analytics can see every request.
        # Use the dedupe index to drive the metric increment: ON CONFLICT means
        # we already counted this impression today from this IP+county, so
        # we don't bump the listing metric.
        cur = conn.execute(
            '''
            INSERT INTO lawyer_listing_events
                (order_id, listing_id, event_type, ip_hash, user_agent_hash, county, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (order_id, listing_id, event_type, ip_hash,
             hashlib.sha256(user_agent.encode('utf-8')).hexdigest()[:32] if user_agent else '',
             county, source),
        )
        is_new_event = cur.lastrowid > 0 and cur.rowcount == 1

        if metric_column:
            should_increment = True
            # Deduped: at most one impression per (order, IP, county, day).
            # Clicks / calls / leads are explicit user actions and stay counted
            # every time so advertisers can see repeat-engagement patterns.
            if event_type == 'impression':
                prior = conn.execute(
                    '''
                    SELECT id FROM lawyer_listing_events
                    WHERE order_id = ? AND event_type = 'impression'
                      AND ip_hash = ? AND county = ? AND date(occurred_at) = date('now')
                    LIMIT 1
                    ''',
                    (order_id, ip_hash, county),
                ).fetchone()
                # If `prior` is this very row we just inserted, that's fine;
                # otherwise we already have one for today, so don't increment.
                if prior and prior['id'] != cur.lastrowid:
                    should_increment = False
            if should_increment:
                conn.execute(
                    f'UPDATE lawyer_ad_listings SET {metric_column} = {metric_column} + 1 WHERE order_id = ?',
                    (order_id,),
                )

        conn.execute(
            '''
            INSERT INTO lawyer_consumer_lead_events
                (event_type, county, source, order_id)
            VALUES (?, ?, ?, ?)
            ''',
            (event_type, county, source, order_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return ('', 204)


# ---------------------------------------------------------------------------
# Advertiser landing + checkout
# ---------------------------------------------------------------------------

@lawyer_ads_bp.route('/advertise/lawyers')
def advertise_lawyers():
    """Landing page for lawyers advertising."""
    return render_template(
        'advertise_lawyers.html',
        packages=_PACKAGES,
        package_lookup=_package_lookup(),
        practice_areas=_PRACTICE_AREAS,
        checkout_ready=_checkout_ready(),
        page_title='Advertise Your Law Firm on Montana Blotter',
        meta_description=(
            'Reach Montana families at the moment they search for a lawyer. '
            'Tap-to-call listings, lead capture intake, county targeting, '
            'and Gold-tier priority lead routing. Bronze $149/mo, Silver '
            '$299/mo, Gold $599/mo.'
        ),
        active_nav='advertise',
        current_year=datetime.now().year,
    )


def _create_stripe_session(stripe_module, *, form_data: dict, package: dict, amount_cents: int, base_url: str):
    """Build the Stripe Checkout session. Wrapped for test monkey-patching."""
    interval = 'year' if form_data['billing_cycle'] == 'annual' else 'month'
    return stripe_module.checkout.Session.create(
        mode='subscription',
        line_items=[{
            'price_data': {
                'currency': 'usd',
                'product_data': {'name': f"Montana Blotter Lawyer Ad — {package['name']}"},
                'unit_amount': amount_cents,
                'recurring': {'interval': interval},
            },
            'quantity': 1,
        }],
        success_url=f'{base_url}/advertise/lawyers/checkout/success?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=f'{base_url}/advertise/lawyers/checkout/cancel',
        customer_email=form_data['email'],
        allow_promotion_codes=False,
        billing_address_collection='auto',
        metadata={
            'flow': 'lawyer_ad',
            'package_id': form_data['package_id'],
            'billing_cycle': form_data['billing_cycle'],
            'firm_name': form_data['firm_name'],
            'contact_name': form_data['contact_name'],
            'email': form_data['email'],
            'phone': form_data['phone'],
            'website': form_data['website'],
            'bar_number': form_data['bar_number'],
            'counties_served': form_data['counties_served'],
            'practice_areas': form_data['practice_areas'],
            'token': form_data.get('_onboarding_token', ''),
        },
    )


@lawyer_ads_bp.route('/advertise/lawyers/checkout', methods=['GET', 'POST'])
def advertise_lawyers_checkout():
    package_lookup = _package_lookup()
    errors = []

    prefill_package = (request.values.get('package') or '').strip().lower()
    if prefill_package not in package_lookup:
        prefill_package = ''

    form_data = {
        'firm_name': (request.values.get('firm_name') or '').strip()[:200],
        'contact_name': (request.values.get('contact_name') or '').strip()[:160],
        'email': (request.values.get('email') or '').strip().lower()[:160],
        'phone': (request.values.get('phone') or '').strip()[:40],
        'website': (request.values.get('website') or '').strip()[:300],
        'bar_number': (request.values.get('bar_number') or '').strip()[:40],
        'counties_served': (request.values.get('counties_served') or '').strip()[:600],
        'practice_areas': (request.values.get('practice_areas') or '').strip()[:600],
        'package_id': prefill_package,
        'billing_cycle': 'monthly',
    }

    if not _checkout_ready():
        return render_template(
            'advertise_lawyers_checkout.html',
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
            'firm_name': (request.form.get('firm_name') or '').strip()[:200],
            'contact_name': (request.form.get('contact_name') or '').strip()[:160],
            'email': (request.form.get('email') or '').strip().lower()[:160],
            'phone': (request.form.get('phone') or '').strip()[:40],
            'website': (request.form.get('website') or '').strip()[:300],
            'bar_number': (request.form.get('bar_number') or '').strip()[:40],
            'counties_served': (request.form.get('counties_served') or '').strip()[:600],
            'practice_areas': (request.form.get('practice_areas') or '').strip()[:600],
            'package_id': (request.form.get('package_id') or '').strip().lower()[:32],
            'billing_cycle': (request.form.get('billing_cycle') or 'monthly').strip().lower(),
        }

        if not form_data['firm_name']:
            errors.append('Firm name is required.')
        if not form_data['contact_name']:
            errors.append('Contact name is required.')
        if not form_data['email'] or '@' not in form_data['email']:
            errors.append('A valid email is required.')
        if not form_data['phone']:
            errors.append('Phone number is required.')
        if not form_data['bar_number']:
            errors.append('Montana State Bar number is required.')
        if not form_data['counties_served']:
            errors.append('At least one county is required.')
        if form_data['package_id'] not in package_lookup:
            errors.append('Please select a valid package.')
        if form_data['billing_cycle'] not in ('monthly', 'annual'):
            errors.append('Billing cycle is invalid.')
        if request.form.get('terms_ack') != 'yes':
            errors.append('You must accept the advertising terms to continue.')

        # Normalize website
        if form_data['website'] and not form_data['website'].startswith(('http://', 'https://')):
            form_data['website'] = 'https://' + form_data['website']

        if not errors:
            try:
                import stripe as _stripe
            except Exception:
                _stripe = None

            if not _stripe:
                errors.append('Stripe is unavailable. Please contact support.')
            else:
                token = secrets.token_urlsafe(24)
                amount_cents = _price_cents(form_data['package_id'], form_data['billing_cycle'])
                pkg = package_lookup[form_data['package_id']]
                base_url = (getattr(config, 'BASE_URL', '') or '').rstrip('/')

                _stripe.api_key = (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip()

                try:
                    checkout_session = _create_stripe_session(
                        _stripe,
                        form_data={**form_data, '_onboarding_token': token},
                        package=pkg,
                        amount_cents=amount_cents,
                        base_url=base_url,
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
        'advertise_lawyers_checkout.html',
        packages=_PACKAGES,
        package_lookup=package_lookup,
        form_data=form_data,
        form_errors=errors,
        checkout_ready=True,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@lawyer_ads_bp.route('/advertise/lawyers/checkout/success')
def advertise_lawyers_checkout_success():
    session_id = (request.args.get('session_id') or '').strip()
    order = None
    if session_id:
        conn = get_db()
        row = conn.execute(
            '''
            SELECT id, firm_name, package_id, billing_cycle, status, onboarding_token, created_at
            FROM lawyer_ad_orders
            WHERE provider_session_id = ?
            ORDER BY id DESC LIMIT 1
            ''',
            (session_id,),
        ).fetchone()
        conn.close()
        if row:
            order = dict(row)
    return render_template(
        'advertise_lawyers_checkout_success.html',
        order=order,
        session_id=session_id,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@lawyer_ads_bp.route('/advertise/lawyers/checkout/cancel')
def advertise_lawyers_checkout_cancel():
    return render_template(
        'advertise_lawyers_checkout_cancel.html',
        active_nav='advertise',
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Advertiser control panel
# ---------------------------------------------------------------------------

def _safe_ext(filename: str) -> str:
    if '.' not in filename:
        return ''
    return filename.rsplit('.', 1)[-1].lower()


def _save_upload(upload, dest_dir: str, prefix: str) -> str:
    """Save a bounded advertiser image and return its public URL."""
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
    with open(os.path.join(dest_dir, stored), 'wb') as handle:
        handle.write(data)
    return f'/static/{os.path.basename(dest_dir)}/{stored}'


def _control_panel_order(token: str):
    conn = get_db()
    ensure_lawyer_ad_schema(conn)
    row = conn.execute(
        '''
        SELECT o.id, o.firm_name, o.contact_name, o.email, o.phone, o.website,
               o.bar_number, o.counties_served, o.practice_areas, o.package_id,
               o.billing_cycle, o.status, o.onboarding_token,
               l.tagline, l.description, l.logo_path, l.photo_path,
               l.cta_text, l.target_url, l.impressions, l.clicks, l.calls, l.leads
        FROM lawyer_ad_orders o
        LEFT JOIN lawyer_ad_listings l ON l.order_id = o.id
        WHERE o.onboarding_token = ?
        ''',
        (token,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


@lawyer_ads_bp.route('/lawyer-control-panel/<token>')
def lawyer_control_panel(token):
    safe_token = (token or '').strip()[:128]
    order = _control_panel_order(safe_token)
    if not order:
        abort(404)
    package = _package_lookup().get(order['package_id']) or {}
    return render_template(
        'advertise_lawyers_control_panel.html',
        order=order,
        package=package,
        token=safe_token,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@lawyer_ads_bp.route('/lawyer-control-panel/<token>/update', methods=['POST'])
def lawyer_control_panel_update(token):
    safe_token = (token or '').strip()[:128]
    order = _control_panel_order(safe_token)
    if not order:
        abort(404)

    submitted_token = (request.form.get('form_token') or '').strip()
    if not hmac.compare_digest(safe_token, submitted_token):
        abort(400)

    package = _package_lookup().get(order['package_id']) or {}
    description_limit = int(package.get('description_limit') or 0)
    description = (request.form.get('description') or '').strip()
    if description_limit:
        description = description[:description_limit]
    tagline = (request.form.get('tagline') or '').strip()[:120] if package.get('featured') else ''
    phone = (request.form.get('phone') or '').strip()[:40]
    website = (request.form.get('website') or '').strip()[:300]
    if website and not website.startswith(('http://', 'https://')):
        website = 'https://' + website
    target_url = (request.form.get('target_url') or '').strip()[:500]
    if target_url and not target_url.startswith(('http://', 'https://')):
        target_url = 'https://' + target_url
    cta_text = (request.form.get('cta_text') or '').strip()[:80]

    logo_path = ''
    photo_path = ''
    if package.get('logo'):
        logo_path = _save_upload(request.files.get('logo'), LOGO_UPLOAD_DIR, f'logo_{order["id"]}')
    if package.get('photo'):
        photo_path = _save_upload(request.files.get('photo'), PHOTO_UPLOAD_DIR, f'photo_{order["id"]}')

    conn = get_db()
    fields = [
        'tagline = ?', 'description = ?', 'cta_text = ?', 'target_url = ?',
        "updated_at = datetime('now')",
    ]
    params = [tagline, description, cta_text, target_url]
    if logo_path:
        fields.append('logo_path = ?')
        params.append(logo_path)
    if photo_path:
        fields.append('photo_path = ?')
        params.append(photo_path)
    params.append(order['id'])
    conn.execute(f'UPDATE lawyer_ad_listings SET {", ".join(fields)} WHERE order_id = ?', params)
    conn.execute(
        "UPDATE lawyer_ad_orders SET phone = ?, website = ?, updated_at = datetime('now') WHERE id = ?",
        (phone, website, order['id']),
    )
    conn.commit()
    conn.close()
    return redirect(url_for('.lawyer_control_panel', token=safe_token, saved=1))