"""
Bail Bond Advertising — public routes, checkout, control panel, and helpers.

Mirrors `blueprints/recovery_ads.py` for consistency.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

import stripe
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

import config
from db import get_db
from services.crm import crm_service
from init_db import ensure_advertise_sales_lead_schema

from blueprints.payments import _checkout_redirect_url  # noqa: E402  shared Stripe-session helper

log = logging.getLogger(__name__)

bail_bond_ads_bp = Blueprint('bail_bond_ads', __name__)


def _app():
    import app as _app_module
    return _app_module

# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

def _slugify_key(value):
    import re as _re
    return _re.sub(r'[^a-z0-9]+', '-', (value or '').strip().lower()).strip('-')

# ---------------------------------------------------------------------------
# Upload constants
# ---------------------------------------------------------------------------

LOGO_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), 'static', 'bail_ads'
)
os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Helper methods (moved from app.py)
# ---------------------------------------------------------------------------

def _bail_ad_packages():
    return [
        {
            'id': 'exclusive_county_sponsorship',
            'name': 'The Horizon Exclusive',
            'type': 'County Sponsorship',
            'price_monthly_cents': 15000,
            'price_annual_cents': 180000,
            'county_slots': 1,
            'badge': 'County Sponsorship',
            'price_label_monthly': '$150 - $350',
            'short_description': 'Exclusive county feed sponsorship with hyper-local branding.',
            'full_description': 'Reserve a single county feed for one agency only and own the local arrest audience in that market.',
            'pricing_model': 'county_tiered',
            'features': [
                'Exclusive county feed sponsorship',
                'Single agency per county',
                'Hyper-local branding',
            ],
            'cta': 'Select County',
            'highlight': False,
        },
        {
            'id': 'emergency_call_sidebar',
            'name': 'The Summit Sidebar',
            'type': 'Emergency Call Sidebar',
            'price_monthly_cents': 30000,
            'price_annual_cents': 360000,
            'county_slots': 0,
            'badge': 'Emergency Call Sidebar',
            'short_description': 'Sticky 300x600 visibility built for emergency response traffic.',
            'full_description': 'Stay visible through long scroll sessions with a persistent sidebar unit optimized for immediate mobile action.',
            'features': [
                '300x600 sticky sidebar unit',
                'Persistent visibility on scroll',
                'Mobile-first tap-to-call',
            ],
            'cta': 'Claim Sidebar',
            'highlight': False,
        },
        {
            'id': 'featured_bondsman_banner',
            'name': 'The Big Sky Header',
            'type': 'Top Banner Placement',
            'price_monthly_cents': 45000,
            'price_annual_cents': 540000,
            'county_slots': 0,
            'badge': 'Top Banner Placement',
            'short_description': 'Premium statewide header built for first-view visibility.',
            'full_description': 'Own the first thing readers see with a 970x250 placement spanning MontanaBlotter arrest coverage.',
            'features': [
                '970x250 premium header',
                'Top-of-feed statewide visibility',
                'First-view real estate',
            ],
            'cta': 'Secure Header',
            'highlight': False,
        },
        {
            'id': 'gold_bond_bundle',
            'name': 'The Gold Bond Bundle',
            'type': 'Market Dominance',
            'price_monthly_cents': 65000,
            'price_annual_cents': 780000,
            'county_slots': 2,
            'badge': 'Market Dominance',
            'short_description': 'Header, sidebar, and county exclusivity bundled into one featured package.',
            'full_description': 'Take over the highest-intent surfaces across MontanaBlotter with bundled pricing and multi-touch coverage.',
            'features': [
                'Includes Header + Sidebar',
                'Plus 2 Exclusive Counties',
                '15% bundled discount applied',
            ],
            'cta': 'Dominate Market',
            'highlight': True,
        },
        {
            'id': 'silver_link',
            'name': 'The Silver Link',
            'price_monthly_cents': 35000,
            'price_annual_cents': 350000,
            'county_slots': 1,
            'badge': 'Sidebar + one county feed',
            'legacy': True,
            'active': False,
            'features': [
                'Emergency Call sticky sidebar ad placement',
                'Sponsored link placement in one county feed',
                'Mobile-first tap-to-call call-to-action',
            ],
        },
        {
            'id': 'gold_bond',
            'name': 'The Gold Bond',
            'price_monthly_cents': 65000,
            'price_annual_cents': 650000,
            'county_slots': 2,
            'badge': 'Top banner + sidebar + 2 counties',
            'legacy': True,
            'active': False,
            'features': [
                'Featured Bondsman top banner placement',
                'Emergency Call sticky sidebar placement',
                'Sponsored coverage in two county feeds',
            ],
        },
        {
            'id': 'state_power',
            'name': 'The State Power',
            'price_monthly_cents': 150000,
            'price_annual_cents': 1500000,
            'county_slots': len(_app().COUNTY_DATA),
            'all_counties': True,
            'badge': 'Statewide takeover package',
            'legacy': True,
            'active': False,
            'features': [
                'Top banner placement on all pages',
                'Emergency Call sticky sidebar placement',
                'County coverage across all Montana counties',
            ],
        },
    ]


def _bail_ad_public_packages():
    return [pkg for pkg in _bail_ad_packages() if pkg.get('active', True)]


def _format_bail_ad_currency(cents):
    return f"${int(cents or 0) / 100:,.0f}"


def _safe_bail_ad_simulator_image_url(raw_value):
    value = (raw_value or '').strip()[:1000]
    if not value:
        return ''
    parsed = urlparse(value)
    if parsed.scheme in {'http', 'https'} and parsed.netloc:
        return value
    if not parsed.scheme and value.startswith('/'):
        return value
    return ''


def _bail_ad_pricing_cards(package_options):
    cards = []
    for pkg in package_options:
        cards.append(
            {
                'id': pkg['id'],
                'name': pkg.get('name') or '',
                'type': pkg.get('type') or pkg.get('badge') or '',
                'price': pkg.get('price_label_monthly') or _format_bail_ad_currency(pkg.get('price_monthly_cents')),
                'annual': f"{_format_bail_ad_currency(pkg.get('price_annual_cents'))}/yr",
                'features': list(pkg.get('features') or []),
                'cta': pkg.get('cta') or 'Select Package',
                'highlight': bool(pkg.get('highlight')),
                'checkoutUrl': url_for('bail_bond_ads.advertise_bail_bonds_checkout', package=pkg['id'], source='package_card'),
            }
        )
    return cards


def _bail_ad_package_id_for_simulator_view(view_name=''):
    return 'emergency_call_sidebar' if (view_name or '').strip().lower() == 'sidebar' else 'featured_bondsman_banner'


def _bail_ad_simulator_view_for_package(package_id=''):
    return 'sidebar' if _normalize_bail_ad_package_id(package_id) == 'emergency_call_sidebar' else 'banner'


def _bail_ad_simulator_preview(order):
    return {
        'logo_path': _safe_bail_ad_simulator_image_url((order or {}).get('simulator_logo_path') or ''),
        'target_url': ((order or {}).get('simulator_target_url') or '').strip()[:300],
        'share_url': ((order or {}).get('simulator_share_url') or '').strip()[:500],
        'view': ((order or {}).get('simulator_view') or '').strip().lower()[:24],
    }


def _bail_ad_package_aliases():
    return {
        'starter': 'exclusive_county_sponsorship',
        'growth': 'gold_bond_bundle',
        'dominance': 'gold_bond_bundle',
        'featured': 'featured_bondsman_banner',
        'sidebar': 'emergency_call_sidebar',
        'county': 'exclusive_county_sponsorship',
        'gold_bundle': 'gold_bond_bundle',
    }


def _normalize_bail_ad_package_id(raw_value):
    token = (raw_value or '').strip().lower()
    if not token:
        return ''
    return _bail_ad_package_aliases().get(token, token)


def _bail_ad_package_lookup():
    lookup = {pkg['id']: pkg for pkg in _bail_ad_packages()}
    for legacy_id, package_id in _bail_ad_package_aliases().items():
        package = lookup.get(package_id)
        if package:
            lookup[legacy_id] = package
    return lookup


def _bail_ad_addons():
    return [
        {
            'id': 'in_feed_integration',
            'name': 'In-Feed Integration',
            'description': 'Sponsored in-feed placement every 5th or 10th arrest entry.',
            'price_monthly_cents': 20000,
            'price_annual_cents': 200000,
        },
    ]


def _bail_ad_addon_lookup():
    return {addon['id']: addon for addon in _bail_ad_addons()}


def _parse_addon_ids(raw_values):
    lookup = _bail_ad_addon_lookup()
    out = []
    seen = set()
    for raw in raw_values or []:
        token = (raw or '').strip().lower()
        if token in lookup and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _parse_budget_cents(raw_value):
    token = (raw_value or '').strip().replace('$', '').replace(',', '')
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    cents = int(round(value * 100))
    if cents <= 0:
        return None
    return min(cents, 100000000)


def _bail_ad_checkout_ready():
    return _app()._stripe_ready_for_checkout()


def _bail_ad_allowed_asset(filename):
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in {'png', 'jpg', 'jpeg', 'webp', 'gif'}


def _ensure_bail_ad_simulator_order_columns(conn):
    columns = {
        (row['name'] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute("PRAGMA table_info('bail_ad_orders')").fetchall()
    }
    additions = [
        ('simulator_logo_path', "ALTER TABLE bail_ad_orders ADD COLUMN simulator_logo_path TEXT NOT NULL DEFAULT ''"),
        ('simulator_target_url', "ALTER TABLE bail_ad_orders ADD COLUMN simulator_target_url TEXT NOT NULL DEFAULT ''"),
        ('simulator_share_url', "ALTER TABLE bail_ad_orders ADD COLUMN simulator_share_url TEXT NOT NULL DEFAULT ''"),
        ('simulator_view', "ALTER TABLE bail_ad_orders ADD COLUMN simulator_view TEXT NOT NULL DEFAULT ''"),
    ]
    for column_name, sql in additions:
        if column_name not in columns:
            conn.execute(sql)


def _ensure_bail_ad_simulator_event_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_ad_simulator_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            sim_view TEXT NOT NULL DEFAULT '',
            county TEXT NOT NULL DEFAULT '',
            agency_name TEXT NOT NULL DEFAULT '',
            asset_path TEXT NOT NULL DEFAULT '',
            share_url TEXT NOT NULL DEFAULT '',
            internal_mode INTEGER NOT NULL DEFAULT 0,
            ip_hash TEXT NOT NULL DEFAULT '',
            referrer TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_simulator_events_created ON bail_ad_simulator_events(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_simulator_events_type ON bail_ad_simulator_events(event_type)')


def _record_bail_ad_simulator_event(
    conn,
    event_type,
    source='',
    sim_view='',
    county='',
    agency_name='',
    asset_path='',
    share_url='',
    internal_mode=False,
):
    if not event_type:
        return
    _ensure_bail_ad_simulator_event_schema(conn)
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    conn.execute(
        '''
        INSERT INTO bail_ad_simulator_events (
            event_type, source, sim_view, county, agency_name, asset_path, share_url, internal_mode, ip_hash, referrer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            (event_type or '').strip()[:40],
            (source or '').strip()[:80],
            (sim_view or '').strip()[:24],
            (county or '').strip()[:80],
            (agency_name or '').strip()[:120],
            (asset_path or '').strip()[:500],
            (share_url or '').strip()[:500],
            1 if internal_mode else 0,
            ip_hash,
            referrer,
        ),
    )


def _parse_county_targets(raw_value):
    raw = (raw_value or '').replace('\n', ',').replace(';', ',')
    county_lookup = {county['name'].lower(): county['name'] for county in _app().COUNTY_DATA.values()}
    slug_lookup = {slug.lower(): county['name'] for slug, county in _app().COUNTY_DATA.items()}
    parsed = []
    seen = set()
    for token in raw.split(','):
        value = token.strip()
        if not value:
            continue
        key = value.lower()
        normalized = county_lookup.get(key) or slug_lookup.get(key) or value[:64]
        slug_key = normalized.lower()
        if slug_key in seen:
            continue
        seen.add(slug_key)
        parsed.append(normalized)
        if len(parsed) >= max(12, len(_app().COUNTY_DATA)):
            break
    return parsed


def _all_bail_counties():
    counties = []
    seen = set()
    for county in getattr(config, 'MONTANA_COUNTIES', []) or []:
        name = (county or '').strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        counties.append(name)
    for county in _app().COUNTY_DATA.values():
        name = (county.get('name') or '').strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        counties.append(name)
    return sorted(counties)


def _normalize_bail_county(raw_value):
    value = (raw_value or '').strip()
    if not value:
        return ''
    lower_map = {county.lower(): county for county in _all_bail_counties()}
    slug_map = {_app()._slugify_key(county): county for county in _all_bail_counties()}
    token = value.lower()
    if token in lower_map:
        return lower_map[token]
    if token in slug_map:
        return slug_map[token]
    slug = _slugify_key(value)
    if slug in slug_map:
        return slug_map[slug]
    return value[:80]


def _format_phone_for_tel(raw_phone):
    token = ''.join(ch for ch in (raw_phone or '') if ch.isdigit())
    if len(token) == 10:
        token = f'1{token}'
    if len(token) < 11:
        return ''
    return f'+{token}'


def _bail_help_contact(default_phone=''):
    phone = (getattr(config, 'BAIL_HELP_PHONE', '') or '').strip()
    sms_number = (getattr(config, 'BAIL_HELP_SMS', '') or '').strip()
    chat_url = (getattr(config, 'BAIL_HELP_CHAT_URL', '') or '').strip()

    if not phone:
        phone = (default_phone or '').strip()
    if not sms_number:
        sms_number = phone

    tel_href = _format_phone_for_tel(phone)
    sms_href = _format_phone_for_tel(sms_number)
    return {
        'phone': phone,
        'phone_display': phone or 'Call',
        'tel_href': f'tel:{tel_href}' if tel_href else '',
        'sms_href': f'sms:{sms_href}' if sms_href else '',
        'chat_url': chat_url,
    }


def _bail_ad_contract_context(onboarding_token: str | None = None):
    configured_url = (getattr(config, 'LETSBAIL_AD_CONTRACT_URL', '') or '').strip()
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or (getattr(config, 'EMAIL_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )
    safe_token = (onboarding_token or '').strip()[:128]
    contract_url = configured_url
    if not contract_url and safe_token:
        contract_url = url_for('bail_bond_ads.advertise_bail_private_contract', token=safe_token)
    return {
        'title': "Montana Blotter Contract",
        'partner_name': "Montana Blotter",
        'url': contract_url,
        'external': bool(configured_url),
        'updated_label': 'Updated March 19, 2026',
        'summary': 'Review placement terms, recurring billing, creative standards, county inventory rules, and cancellation timing before launch.',
        'support_email': support_email,
        'highlights': [
            'All creative is subject to Montana Blotter compliance and quality review before launch.',
            'Subscriptions renew automatically on the selected billing cycle until canceled.',
            'County exclusives and bundled placements remain subject to inventory availability.',
        ],
    }


def _ensure_bail_consumer_lead_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_consumer_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            county TEXT NOT NULL,
            jail_facility TEXT,
            callback_preference TEXT,
            notes TEXT,
            source TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            routed_order_ids TEXT,
            routed_business_names TEXT,
            routed_emails TEXT,
            routed_phones TEXT,
            ip_hash TEXT,
            referrer TEXT,
            review_notes TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_created ON bail_consumer_leads(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_status ON bail_consumer_leads(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_county ON bail_consumer_leads(county)')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_consumer_lead_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            event_type TEXT NOT NULL,
            county TEXT,
            source TEXT,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES bail_consumer_leads(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_created ON bail_consumer_lead_events(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_type ON bail_consumer_lead_events(event_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_county ON bail_consumer_lead_events(county)')


def _record_bail_consumer_event(conn, event_type, county='', source='', lead_id=None):
    safe_event = (event_type or '').strip().lower()[:40]
    if not safe_event:
        return
    safe_county = _normalize_bail_county(county)[:80]
    safe_source = (source or '').strip()[:80]
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    conn.execute(
        '''
        INSERT INTO bail_consumer_lead_events (lead_id, event_type, county, source, ip_hash, referrer)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            int(lead_id) if lead_id else None,
            safe_event,
            safe_county,
            safe_source,
            ip_hash,
            referrer,
        ),
    )


def _bail_lead_notify_recipients():
    recipients = []
    configured = getattr(config, 'BAIL_LEAD_NOTIFY_EMAILS', ()) or ()
    if isinstance(configured, str):
        configured = [part.strip() for part in configured.split(',') if part.strip()]
    for entry in configured:
        email = (entry or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)
    if not recipients:
        fallback = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
        if fallback and '@' in fallback:
            recipients.append(fallback)
    return recipients


def _send_bail_lead_notification_email(to_emails, subject, body):
    recipients = []
    for value in to_emails or []:
        email = (value or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)
    if not recipients:
        return False

    smtp_user = (getattr(config, 'SMTP_USER', '') or '').strip()
    smtp_password = (getattr(config, 'SMTP_PASSWORD', '') or '').strip()
    smtp_server = (getattr(config, 'SMTP_SERVER', '') or '').strip()
    smtp_port = int(getattr(config, 'SMTP_PORT', 587) or 587)
    if not (smtp_user and smtp_password and smtp_server):
        return False

    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = ', '.join(recipients)
    try:
        smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(smtp_user, recipients, msg.as_string())
        smtp.quit()
        return True
    except Exception:
        return False


def _post_bail_lead_webhook(payload):
    webhook_url = (getattr(config, 'BAIL_LEAD_WEBHOOK_URL', '') or '').strip()
    if not webhook_url:
        return False
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=8):
            return True
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def _active_bail_ad_listings(conn):
    _ensure_bail_ad_simulator_order_columns(conn)
    rows = conn.execute(
        '''
        SELECT
            bail_ad_orders.id,
            bail_ad_orders.business_name,
            bail_ad_orders.phone,
            bail_ad_orders.email,
            bail_ad_orders.website_url,
            bail_ad_orders.county_targets,
            bail_ad_orders.package_id,
            bail_ad_orders.status,
            bail_ad_orders.simulator_logo_path,
            bail_ad_orders.simulator_target_url,
            bail_ad_creatives.headline,
            bail_ad_creatives.body_copy,
            bail_ad_creatives.cta_text,
            bail_ad_creatives.target_url,
            bail_ad_creatives.logo_path
        FROM bail_ad_orders
        LEFT JOIN bail_ad_creatives ON bail_ad_creatives.order_id = bail_ad_orders.id
        WHERE bail_ad_orders.status = 'active'
          AND (bail_ad_creatives.status = 'approved' OR bail_ad_creatives.status IS NULL)
        ORDER BY datetime(bail_ad_orders.paid_at) DESC, datetime(bail_ad_orders.created_at) DESC
        '''
    ).fetchall()

    listings = []
    for row in rows:
        county_list = _bail_ad_county_list(row['county_targets'])
        phone_value = (row['phone'] or '').strip()
        phone_token = _format_phone_for_tel(phone_value)
        listings.append({
            'id': row['id'],
            'business_name': row['business_name'],
            'phone': phone_value,
            'phone_href': f"tel:{phone_token}" if phone_token else '',
            'sms_href': f"sms:{phone_token}" if phone_token else '',
            'email': row['email'],
            'website_url': row['website_url'],
            'counties': county_list,
            'package_id': row['package_id'],
            'status': row['status'],
            'headline': row['headline'] or f"{row['business_name']} Bail Bonds",
            'body_copy': row['body_copy'] or 'Licensed local bail bond support available.',
            'cta_text': row['cta_text'] or 'Contact Now',
            'target_url': row['target_url'] or row['simulator_target_url'] or row['website_url'] or '',
            'logo_path': row['logo_path'] or row['simulator_logo_path'] or '',
        })
    crm_service.apply_directory_overrides(conn, listings, 'bail')
    return listings


def _bail_ad_package_supports_banner(package_id=''):
    return _normalize_bail_ad_package_id(package_id) in {
        'featured_bondsman_banner',
        'gold_bond_bundle',
        'state_power',
        'gold_bond',
    }


def _bail_ad_package_supports_sidebar(package_id=''):
    return _normalize_bail_ad_package_id(package_id) in {
        'emergency_call_sidebar',
        'gold_bond_bundle',
        'silver_link',
        'state_power',
        'gold_bond',
    }


def _bail_ad_package_supports_county(package_id=''):
    return _normalize_bail_ad_package_id(package_id) in {
        'exclusive_county_sponsorship',
        'gold_bond_bundle',
        'silver_link',
        'gold_bond',
        'state_power',
    }


def _bail_ad_clone_for_surface(listing, surface, county=''):
    if not listing:
        return None
    item = dict(listing)
    normalized_county = _normalize_bail_county(county)
    item['surface'] = surface
    item['tracking_county'] = normalized_county or (item.get('counties') or [''])[0]
    if surface == 'banner':
        item['surface_label'] = 'Featured Bondsman Banner'
        item['surface_note'] = 'Sponsored statewide placement'
        item['cta_text'] = item.get('cta_text') or 'Visit Sponsor'
    elif surface == 'county':
        item['surface_label'] = f"{normalized_county or 'County'} Sponsor"
        item['surface_note'] = 'Exclusive local sponsor'
        item['cta_text'] = item.get('cta_text') or 'Call Local Sponsor'
    else:
        item['surface_label'] = 'Emergency Call Sidebar'
        item['surface_note'] = 'Persistent sponsored placement'
        item['cta_text'] = item.get('cta_text') or 'Call Now'
    return item


def _bail_ad_public_placements(conn, county=''):
    listings = _active_bail_ad_listings(conn)
    normalized_county = _normalize_bail_county(county)

    def _pick(predicate):
        for listing in listings:
            if predicate(listing):
                return listing
        return None

    county_sponsor = None
    if normalized_county:
        county_sponsor = _pick(
            lambda item: (
                _bail_ad_package_supports_county(item.get('package_id'))
                and normalized_county in (item.get('counties') or [])
            )
        )

    banner = _pick(lambda item: _bail_ad_package_supports_banner(item.get('package_id')))

    # Keep the public experience lighter: surface only one paid placement per
    # page instead of stacking a top banner and a sticky sidebar together.
    if county_sponsor:
        return {
            'banner': None,
            'sidebar': _bail_ad_clone_for_surface(county_sponsor, 'county', county=normalized_county),
            'county_sponsor': _bail_ad_clone_for_surface(county_sponsor, 'county', county=normalized_county),
        }
    if banner:
        return {
            'banner': _bail_ad_clone_for_surface(banner, 'banner', county=normalized_county),
            'sidebar': None,
            'county_sponsor': None,
        }

    sidebar = _pick(lambda item: _bail_ad_package_supports_sidebar(item.get('package_id')))

    return {
        'banner': None,
        'sidebar': _bail_ad_clone_for_surface(sidebar, 'sidebar', county=normalized_county),
        'county_sponsor': None,
    }


def _bail_county_sections(listings, selected_county=''):
    selected = _normalize_bail_county(selected_county)
    by_county = {}
    for listing in listings:
        counties = listing.get('counties') or ['Statewide']
        for county in counties:
            normalized_county = _normalize_bail_county(county) or county
            if selected and normalized_county != selected:
                continue
            by_county.setdefault(normalized_county, []).append(listing)
    return [{'county': county, 'listings': values} for county, values in sorted(by_county.items())]


def _bail_lead_routing_targets(listings, county):
    normalized_county = _normalize_bail_county(county)
    targets = []
    for listing in listings:
        target_counties = listing.get('counties') or []
        if target_counties and normalized_county and normalized_county not in target_counties:
            continue
        targets.append(listing)
    return targets[:3]


def _bail_advertiser_attribution_30d(conn, limit=120):
    calls_by_order = {
        int(row['order_id']): int(row['calls'] or 0)
        for row in conn.execute(
            '''
            SELECT order_id, COUNT(*) AS calls
            FROM bail_ad_events
            WHERE order_id IS NOT NULL
              AND event_type IN ('call', 'lead')
              AND created_at >= date('now', '-30 days')
            GROUP BY order_id
            '''
        ).fetchall()
        if row['order_id'] is not None
    }
    texts_by_order = {
        int(row['order_id']): int(row['texts'] or 0)
        for row in conn.execute(
            '''
            SELECT order_id, COUNT(*) AS texts
            FROM bail_ad_events
            WHERE order_id IS NOT NULL
              AND event_type = 'text'
              AND created_at >= date('now', '-30 days')
            GROUP BY order_id
            '''
        ).fetchall()
        if row['order_id'] is not None
    }

    routed_by_order = {}
    for row in conn.execute(
        '''
        SELECT routed_order_ids, status
        FROM bail_consumer_leads
        WHERE created_at >= date('now', '-30 days')
        '''
    ).fetchall():
        order_ids = []
        for token in (row['routed_order_ids'] or '').split(','):
            clean = token.strip()
            if not clean:
                continue
            try:
                value = int(clean)
            except ValueError:
                continue
            if value > 0:
                order_ids.append(value)
        for order_id in sorted(set(order_ids)):
            stats_bucket = routed_by_order.setdefault(order_id, {'routed': 0, 'qualified': 0, 'booked': 0})
            stats_bucket['routed'] += 1
            if (row['status'] or '').strip().lower() in {'qualified', 'booked'}:
                stats_bucket['qualified'] += 1
            if (row['status'] or '').strip().lower() == 'booked':
                stats_bucket['booked'] += 1

    pipeline_order_ids = set(calls_by_order.keys()) | set(texts_by_order.keys()) | set(routed_by_order.keys())
    order_lookup = {}
    if pipeline_order_ids:
        placeholders = ','.join('?' for _ in sorted(pipeline_order_ids))
        for row in conn.execute(
            f'''
            SELECT id, business_name, package_id, status, county_targets
            FROM bail_ad_orders
            WHERE id IN ({placeholders})
            ''',
            tuple(sorted(pipeline_order_ids)),
        ).fetchall():
            order_lookup[int(row['id'])] = dict(row)

    out = []
    for order_id in sorted(pipeline_order_ids):
        order_info = order_lookup.get(order_id) or {}
        routed = int((routed_by_order.get(order_id) or {}).get('routed') or 0)
        qualified = int((routed_by_order.get(order_id) or {}).get('qualified') or 0)
        booked = int((routed_by_order.get(order_id) or {}).get('booked') or 0)
        calls = int(calls_by_order.get(order_id, 0) or 0)
        texts = int(texts_by_order.get(order_id, 0) or 0)
        out.append({
            'order_id': order_id,
            'business_name': order_info.get('business_name') or f'Order #{order_id}',
            'package_id': order_info.get('package_id') or '',
            'status': order_info.get('status') or '',
            'county_targets': order_info.get('county_targets') or '',
            'calls': calls,
            'texts': texts,
            'routed_leads': routed,
            'qualified_leads': qualified,
            'booked_bonds': booked,
            'qualified_rate_pct': (qualified / routed * 100.0) if routed else 0.0,
            'booked_rate_pct': (booked / qualified * 100.0) if qualified else 0.0,
        })
    out.sort(
        key=lambda item: (
            item['booked_bonds'],
            item['qualified_leads'],
            item['routed_leads'],
            item['calls'],
            item['texts'],
        ),
        reverse=True,
    )
    return out[:max(1, int(limit or 120))]


def _humanize_bail_ad_status(status):
    mapping = {
        'checkout_pending': 'Checkout Pending',
        'active': 'Active',
        'active_pending_creative_review': 'Active Pending Creative Review',
        'payment_failed': 'Payment Failed',
        'canceled': 'Canceled',
        'paused': 'Paused',
        'pending': 'Pending',
        'approved': 'Approved',
        'rejected': 'Rejected',
    }
    normalized = (status or '').strip().lower()
    return mapping.get(normalized, normalized.replace('_', ' ').title() or 'Unknown')


def _format_bail_ad_datetime(value, include_time=False, fallback='Pending'):
    parsed = _parse_sqlite_timestamp(value)
    if not parsed:
        return fallback
    fmt = '%b %d, %Y %I:%M %p UTC' if include_time else '%b %d, %Y'
    return parsed.strftime(fmt).replace(' 0', ' ')


def _bail_ad_control_panel_context(conn, token, session_id=''):
    _ensure_bail_ad_simulator_order_columns(conn)
    safe_token = (token or '').strip()[:128]
    if not safe_token:
        return None

    order_row = conn.execute(
        '''
        SELECT
            id,
            business_name,
            contact_name,
            email,
            phone,
            website_url,
            license_number,
            county_targets,
            package_id,
            billing_cycle,
            amount_cents,
            currency,
            status,
            add_on_ids,
            onboarding_token,
            notes,
            simulator_logo_path,
            simulator_target_url,
            simulator_share_url,
            simulator_view,
            paid_at,
            created_at,
            updated_at,
            provider_session_id,
            provider_subscription_id
        FROM bail_ad_orders
        WHERE onboarding_token = ?
        LIMIT 1
        ''',
        (safe_token,),
    ).fetchone()
    if not order_row:
        return None

    package_lookup = _bail_ad_package_lookup()
    addon_lookup = _bail_ad_addon_lookup()
    order = dict(order_row)
    package = package_lookup.get(order.get('package_id') or '') or {}
    order['package_name'] = (package.get('name') if package else '') or (order.get('package_id') or '').replace('_', ' ').title()
    order['status_label'] = _humanize_bail_ad_status(order.get('status'))
    order['billing_label'] = ((order.get('billing_cycle') or 'monthly').replace('_', ' ')).title()
    order['amount_display'] = f"${(int(order.get('amount_cents') or 0) / 100):,.2f}"
    order['currency_display'] = (order.get('currency') or 'usd').upper()
    order['county_list'] = _bail_ad_county_list(order.get('county_targets') or '')
    order['county_count'] = len(order['county_list'])
    order['package_badge'] = package.get('badge') or 'Advertiser Account'
    order['package_features'] = package.get('features') or []
    order['county_slots'] = int(package.get('county_slots') or 0)
    order['add_on_labels'] = [
        addon_lookup[addon_id]['name']
        for addon_id in _parse_addon_ids((order.get('add_on_ids') or '').split(','))
        if addon_id in addon_lookup
    ]
    order['created_label'] = _format_bail_ad_datetime(order.get('created_at'), include_time=True)
    order['updated_label'] = _format_bail_ad_datetime(order.get('updated_at'), include_time=True)
    order['paid_label'] = _format_bail_ad_datetime(
        order.get('paid_at') or order.get('created_at'),
        include_time=True,
        fallback='Pending',
    )

    paid_at = _parse_sqlite_timestamp(order.get('paid_at') or order.get('created_at'))
    cycle = (order.get('billing_cycle') or 'monthly').strip().lower()
    renewal_days = 365 if cycle == 'annual' else 30
    next_renewal_dt = paid_at + timedelta(days=renewal_days) if paid_at else None
    days_to_renewal = None
    if next_renewal_dt:
        days_to_renewal = int((next_renewal_dt - datetime.utcnow()).total_seconds() // 86400)
    order['next_renewal'] = next_renewal_dt.strftime('%b %d, %Y') if next_renewal_dt else 'Pending'
    order['days_to_renewal'] = max(days_to_renewal, 0) if days_to_renewal is not None else None

    creative_row = conn.execute(
        '''
        SELECT
            id,
            headline,
            body_copy,
            cta_text,
            target_url,
            logo_path,
            status,
            review_notes,
            created_at,
            updated_at
        FROM bail_ad_creatives
        WHERE order_id = ?
        LIMIT 1
        ''',
        (order['id'],),
    ).fetchone()
    creative = dict(creative_row) if creative_row else None
    if creative:
        creative['status_label'] = _humanize_bail_ad_status(creative.get('status'))
        creative['updated_label'] = _format_bail_ad_datetime(creative.get('updated_at'), include_time=True)
        creative['created_label'] = _format_bail_ad_datetime(creative.get('created_at'), include_time=True)
    simulator_preview = _bail_ad_simulator_preview(order)

    slot_rows = conn.execute(
        '''
        SELECT county, slot_type, status, starts_at, ends_at, updated_at
        FROM bail_ad_slots
        WHERE order_id = ?
        ORDER BY county ASC, slot_type ASC
        ''',
        (order['id'],),
    ).fetchall()
    slots = []
    for row in slot_rows:
        slot = dict(row)
        slot['status_label'] = _humanize_bail_ad_status(slot.get('status'))
        slot['starts_label'] = _format_bail_ad_datetime(slot.get('starts_at'), fallback='Pending')
        slot['ends_label'] = _format_bail_ad_datetime(slot.get('ends_at'), fallback='Open')
        slots.append(slot)

    perf_row = conn.execute(
        '''
        SELECT
            COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
            COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
            COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads,
            COALESCE(SUM(CASE WHEN event_type = 'call' THEN 1 ELSE 0 END), 0) AS calls,
            COALESCE(SUM(CASE WHEN event_type = 'text' THEN 1 ELSE 0 END), 0) AS texts
        FROM bail_ad_events
        WHERE order_id = ?
          AND created_at >= date('now', '-30 days')
        ''',
        (order['id'],),
    ).fetchone()
    performance_30d = dict(perf_row) if perf_row else {
        'impressions': 0,
        'clicks': 0,
        'leads': 0,
        'calls': 0,
        'texts': 0,
    }
    impressions = float(performance_30d.get('impressions') or 0)
    clicks = float(performance_30d.get('clicks') or 0)
    leads = float(performance_30d.get('leads') or 0)
    calls = float(performance_30d.get('calls') or 0)
    texts = float(performance_30d.get('texts') or 0)
    performance_30d['ctr_pct'] = (clicks / impressions * 100.0) if impressions else 0.0
    performance_30d['lead_rate_pct'] = (leads / clicks * 100.0) if clicks else 0.0
    performance_30d['contact_actions'] = int(calls + texts + leads)

    county_rows = conn.execute(
        '''
        SELECT
            COALESCE(NULLIF(county, ''), 'Statewide') AS county,
            COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
            COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
            COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads,
            COALESCE(SUM(CASE WHEN event_type = 'call' THEN 1 ELSE 0 END), 0) AS calls,
            COALESCE(SUM(CASE WHEN event_type = 'text' THEN 1 ELSE 0 END), 0) AS texts
        FROM bail_ad_events
        WHERE order_id = ?
          AND created_at >= date('now', '-30 days')
        GROUP BY COALESCE(NULLIF(county, ''), 'Statewide')
        ORDER BY clicks DESC, impressions DESC, county ASC
        ''',
        (order['id'],),
    ).fetchall()
    county_performance_30d = []
    for row in county_rows:
        county_metrics = dict(row)
        county_impressions = float(county_metrics.get('impressions') or 0)
        county_clicks = float(county_metrics.get('clicks') or 0)
        county_metrics['ctr_pct'] = (county_clicks / county_impressions * 100.0) if county_impressions else 0.0
        county_performance_30d.append(county_metrics)

    attribution = {
        'calls': 0,
        'texts': 0,
        'routed_leads': 0,
        'qualified_leads': 0,
        'booked_bonds': 0,
        'qualified_rate_pct': 0.0,
        'booked_rate_pct': 0.0,
    }
    for item in _bail_advertiser_attribution_30d(conn, limit=10000):
        if int(item.get('order_id') or 0) == int(order['id']):
            attribution.update(item)
            break

    benchmark_row = conn.execute(
        '''
        SELECT
            COUNT(DISTINCT order_id) AS advertiser_count,
            COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
            COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
            COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads,
            COALESCE(SUM(CASE WHEN event_type = 'call' THEN 1 ELSE 0 END), 0) AS calls,
            COALESCE(SUM(CASE WHEN event_type = 'text' THEN 1 ELSE 0 END), 0) AS texts
        FROM bail_ad_events
        WHERE order_id IS NOT NULL
          AND created_at >= date('now', '-30 days')
        '''
    ).fetchone()
    benchmarks = dict(benchmark_row) if benchmark_row else {
        'advertiser_count': 0,
        'impressions': 0,
        'clicks': 0,
        'leads': 0,
        'calls': 0,
        'texts': 0,
    }
    advertiser_count = max(1, int(benchmarks.get('advertiser_count') or 0))
    benchmarks['avg_clicks'] = float(benchmarks.get('clicks') or 0) / advertiser_count
    benchmarks['avg_impressions'] = float(benchmarks.get('impressions') or 0) / advertiser_count
    benchmarks['avg_contact_actions'] = (
        float(benchmarks.get('calls') or 0)
        + float(benchmarks.get('texts') or 0)
        + float(benchmarks.get('leads') or 0)
    ) / advertiser_count
    benchmarks['click_index_pct'] = (
        (float(performance_30d.get('clicks') or 0) / benchmarks['avg_clicks']) * 100.0
        if benchmarks['avg_clicks'] else 0.0
    )
    benchmarks['contact_index_pct'] = (
        (float(performance_30d.get('contact_actions') or 0) / benchmarks['avg_contact_actions']) * 100.0
        if benchmarks['avg_contact_actions'] else 0.0
    )

    top_county = county_performance_30d[0] if county_performance_30d else None
    top_county_share = (
        (float(top_county.get('clicks') or 0) / clicks * 100.0)
        if top_county and clicks else 0.0
    )

    booking_signal_score = min(
        100,
        int(
            float(performance_30d.get('clicks') or 0) * 2
            + float(attribution.get('calls') or 0) * 8
            + float(attribution.get('texts') or 0) * 6
            + float(attribution.get('qualified_leads') or 0) * 18
            + float(attribution.get('booked_bonds') or 0) * 28
        ),
    )
    if booking_signal_score >= 80:
        signal_label = 'Booked'
    elif booking_signal_score >= 55:
        signal_label = 'High Intent'
    elif booking_signal_score >= 30:
        signal_label = 'Active'
    elif booking_signal_score >= 10:
        signal_label = 'Warming'
    else:
        signal_label = 'Cold Start'

    launch_checklist = [
        {
            'title': 'Payment Confirmed',
            'detail': f"{order['amount_display']} {order['currency_display']} recorded on {order['paid_label']}.",
            'complete': bool(order.get('provider_session_id') or session_id),
        },
        {
            'title': 'Creative Submitted',
            'detail': (
                f"Latest submission updated {creative['updated_label']}."
                if creative else
                'Headline, CTA, landing page, and logo still need to be submitted.'
            ),
            'complete': bool(creative),
        },
        {
            'title': 'Compliance Review',
            'detail': (
                'Approved and ready for live placement.'
                if creative and (creative.get('status') or '').lower() == 'approved' else
                'Waiting on review or revisions before full rollout.'
            ),
            'complete': bool(creative and (creative.get('status') or '').lower() == 'approved'),
        },
        {
            'title': 'Tracking Live',
            'detail': (
                f"{int(performance_30d.get('impressions') or 0)} tracked impressions in the last 30 days."
                if performance_30d.get('impressions') else
                'No live delivery recorded yet.'
            ),
            'complete': bool(performance_30d.get('impressions')),
        },
    ]

    priority_actions = []
    if not creative:
        priority_actions.append({
            'title': 'Submit creative assets',
            'detail': 'The account is paid, but ad copy and destination details still need to be loaded before review can finish.',
            'href': url_for('bail_bond_ads.advertise_bail_onboarding', token=safe_token),
            'label': 'Open Onboarding',
        })
    elif (creative.get('status') or '').lower() == 'pending':
        priority_actions.append({
            'title': 'Review queue is in progress',
            'detail': 'Your latest creative is pending moderation. Keep the control panel link handy for notes or revisions.',
            'href': url_for('bail_bond_ads.advertise_bail_onboarding', token=safe_token),
            'label': 'Review Submission',
        })
    elif (creative.get('status') or '').lower() == 'rejected':
        priority_actions.append({
            'title': 'Revise creative now',
            'detail': 'The ad is blocked on compliance notes. Update the headline, copy, or destination to get back into rotation.',
            'href': url_for('bail_bond_ads.advertise_bail_onboarding', token=safe_token),
            'label': 'Fix Creative',
        })

    if performance_30d.get('clicks') and not attribution.get('routed_leads'):
        priority_actions.append({
            'title': 'Tighten your landing path',
            'detail': 'Traffic is clicking but not routing into tracked leads. A direct call page or prefilled SMS path should convert harder.',
            'href': url_for('bail_bond_ads.advertise_bail_onboarding', token=safe_token),
            'label': 'Update CTA',
        })

    if days_to_renewal is not None and days_to_renewal <= 14:
        priority_actions.append({
            'title': 'Renewal window is close',
            'detail': f"Next billing cycle lands in {max(days_to_renewal, 0)} day{'s' if days_to_renewal != 1 else ''}. Review ROI now before the next charge.",
            'href': url_for('bail_bond_ads.advertise_bail_control_panel', token=safe_token),
            'label': 'Check ROI',
        })

    if not priority_actions:
        priority_actions.append({
            'title': 'Account is in a holding pattern',
            'detail': 'No urgent blockers are showing. Use the county radar and booking score below to decide whether to expand coverage.',
            'href': url_for('bail_bond_ads.advertise_bail_control_panel', token=safe_token),
            'label': 'Review Signals',
        })

    if top_county:
        county_radar_body = (
            f"{top_county['county']} is driving {int(round(top_county_share))}% of your 30-day clicks."
            if clicks else
            f"{top_county['county']} is the first county showing live ad delivery."
        )
        county_radar_value = top_county.get('county') or 'Statewide'
    else:
        county_radar_body = 'No county-level delivery is recorded yet. Once impressions start, this radar will isolate the hottest local pocket.'
        county_radar_value = 'Standby'

    if order['county_slots'] > 0 and order['county_list']:
        exclusivity_value = f"{len(order['county_list'])} county lane{'s' if len(order['county_list']) != 1 else ''}"
        exclusivity_body = f"Current county footprint: {', '.join(order['county_list'])}. This package is built to defend local share of voice, not just generate generic clicks."
        exclusivity_title = 'Exclusivity Watch'
    else:
        exclusivity_value = f"{int(performance_30d.get('contact_actions') or 0)} response actions"
        exclusivity_body = 'This placement leans on immediate tap-to-call behavior. Calls, texts, and form leads are grouped here to show buyer urgency, not just page traffic.'
        exclusivity_title = 'Response Pressure'

    if creative and (creative.get('status') or '').lower() == 'approved':
        creative_value = 'Approved'
        creative_body = 'Your ad creative has cleared review and is eligible for full placement rotation.'
    elif creative and (creative.get('status') or '').lower() == 'pending':
        creative_value = 'In Review'
        creative_body = 'Creative is submitted and waiting on moderation. Keep the CTA and landing path stable until review finishes.'
    elif creative and (creative.get('status') or '').lower() == 'rejected':
        creative_value = 'Needs Revision'
        creative_body = 'The current creative is blocked on review notes. Update it before traffic scaling makes sense.'
    else:
        creative_value = 'Not Started'
        creative_body = 'No creative package is attached yet. Payment is complete, but launch is still blocked on onboarding.'

    signature_features = [
        {
            'title': 'County Saturation Radar',
            'value': county_radar_value,
            'body': county_radar_body,
        },
        {
            'title': 'Booking Signal Score',
            'value': f'{booking_signal_score}/100',
            'body': f"{signal_label} demand signal based on clicks, calls, texts, qualified leads, and booked bonds in the last 30 days.",
        },
        {
            'title': exclusivity_title,
            'value': exclusivity_value,
            'body': exclusivity_body,
        },
        {
            'title': 'Creative Approval Pulse',
            'value': creative_value,
            'body': creative_body,
        },
    ]

    return {
        'order': order,
        'package': package,
        'creative': creative,
        'slots': slots,
        'performance_30d': performance_30d,
        'county_performance_30d': county_performance_30d,
        'attribution': attribution,
        'benchmarks': benchmarks,
        'signature_features': signature_features,
        'priority_actions': priority_actions[:3],
        'launch_checklist': launch_checklist,
        'booking_signal_score': booking_signal_score,
        'signal_label': signal_label,
        'simulator_preview': simulator_preview,
    }


_BAIL_OUTREACH_STATUSES = {
    'new',
    'queued',
    'contacted',
    'replied',
    'meeting_scheduled',
    'closed_won',
    'closed_lost',
    'do_not_contact',
}


def _crm_phone_token(raw_phone):
    digits = ''.join(ch for ch in (raw_phone or '') if ch.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def _bail_agency_dedupe_key(agency_name, email, phone):
    agency_token = _slugify_key(agency_name)[:80]
    email_token = (email or '').strip().lower()[:160]
    phone_token = _crm_phone_token(phone)
    if not agency_token:
        return ''
    return f'{agency_token}|{email_token}|{phone_token}'


def _ensure_bail_agency_outreach_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_agency_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL UNIQUE,
            agency_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            counties TEXT,
            source TEXT,
            outreach_status TEXT NOT NULL DEFAULT 'new',
            last_contacted_at TEXT,
            next_follow_up_at TEXT,
            owner TEXT,
            email_subject_template TEXT,
            email_body_template TEXT,
            call_script_template TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_status ON bail_agency_outreach(outreach_status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_followup ON bail_agency_outreach(next_follow_up_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_name ON bail_agency_outreach(agency_name)')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_agency_email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER,
            agency_name TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            email_kind TEXT NOT NULL,
            subject TEXT,
            body_preview TEXT,
            sent_by TEXT,
            send_status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES bail_agency_outreach(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_created ON bail_agency_email_logs(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_agency ON bail_agency_email_logs(agency_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_status ON bail_agency_email_logs(send_status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_kind ON bail_agency_email_logs(email_kind)')


def _log_bail_agency_email(
    conn,
    agency_id,
    agency_name,
    recipient_email,
    email_kind,
    subject,
    body_preview,
    sent_by,
    send_status,
    error_message='',
):
    conn.execute(
        '''
        INSERT INTO bail_agency_email_logs (
            agency_id, agency_name, recipient_email, email_kind, subject, body_preview, sent_by, send_status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            int(agency_id) if agency_id else None,
            (agency_name or '').strip()[:160],
            (recipient_email or '').strip().lower()[:160],
            (email_kind or '').strip().lower()[:32],
            (subject or '').strip()[:500],
            (body_preview or '').strip()[:1200],
            (sent_by or '').strip()[:120],
            (send_status or '').strip().lower()[:32],
            (error_message or '').strip()[:500],
        ),
    )


def _seed_bail_agency_outreach(conn):
    rows = conn.execute(
        '''
        SELECT business_name AS agency_name, contact_name, email, phone, counties_served AS counties, source
        FROM bail_ad_inquiries
        WHERE business_name IS NOT NULL AND business_name != ''
        UNION ALL
        SELECT business_name AS agency_name, contact_name, email, phone, county_targets AS counties, source
        FROM bail_ad_orders
        WHERE business_name IS NOT NULL AND business_name != ''
        '''
    ).fetchall()

    for row in rows:
        agency_name = (row['agency_name'] or '').strip()[:160]
        if not agency_name:
            continue
        contact_name = (row['contact_name'] or '').strip()[:120]
        email = (row['email'] or '').strip().lower()[:160]
        phone = (row['phone'] or '').strip()[:40]
        counties = (row['counties'] or '').strip()[:500]
        source = (row['source'] or '').strip()[:80]
        dedupe_key = _bail_agency_dedupe_key(agency_name, email, phone)
        if not dedupe_key:
            continue

        conn.execute(
            '''
            INSERT INTO bail_agency_outreach (
                dedupe_key, agency_name, contact_name, email, phone, counties, source, outreach_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT(dedupe_key) DO UPDATE SET
                contact_name = CASE
                    WHEN (bail_agency_outreach.contact_name IS NULL OR bail_agency_outreach.contact_name = '')
                         AND excluded.contact_name != '' THEN excluded.contact_name
                    ELSE bail_agency_outreach.contact_name
                END,
                email = CASE
                    WHEN (bail_agency_outreach.email IS NULL OR bail_agency_outreach.email = '')
                         AND excluded.email != '' THEN excluded.email
                    ELSE bail_agency_outreach.email
                END,
                phone = CASE
                    WHEN (bail_agency_outreach.phone IS NULL OR bail_agency_outreach.phone = '')
                         AND excluded.phone != '' THEN excluded.phone
                    ELSE bail_agency_outreach.phone
                END,
                counties = CASE
                    WHEN (bail_agency_outreach.counties IS NULL OR bail_agency_outreach.counties = '')
                         AND excluded.counties != '' THEN excluded.counties
                    ELSE bail_agency_outreach.counties
                END,
                source = CASE
                    WHEN (bail_agency_outreach.source IS NULL OR bail_agency_outreach.source = '')
                         AND excluded.source != '' THEN excluded.source
                    ELSE bail_agency_outreach.source
                END,
                updated_at = datetime('now')
            ''',
            (dedupe_key, agency_name, contact_name, email, phone, counties, source),
        )


def _bail_agency_default_templates(agency):
    agency_name = (agency.get('agency_name') or '').strip() or 'Your Agency'
    counties = (agency.get('counties') or '').strip() or 'your target counties'
    subject = f'Quick lead growth plan for {agency_name}'
    body = (
        f"Hi {{contact_name_or_team}},\n\n"
        f"I run growth partnerships for Montana Blotter. We already have high-intent county traffic around {counties}, "
        f"and I wanted to share a simple 30-day plan for {agency_name}.\n\n"
        f"Plan focus:\n"
        f"- More qualified inbound calls from your target counties\n"
        f"- Better speed-to-lead using call/text routing\n"
        f"- Clear weekly reporting on qualified leads and booked bonds\n\n"
        f"If useful, I can send a 10-minute breakdown specific to your coverage area.\n\n"
        f"Thanks,\n"
        f"{{sender_name}}"
    )
    script = (
        f"Hi {{contact_name_or_team}}, this is {{sender_name}} from Montana Blotter.\n"
        f"We help bail bond agencies increase qualified county-level calls.\n"
        f"Quick question: are you currently looking to improve lead quality, volume, or both?\n\n"
        f"If both, I can share a 30-day plan for {agency_name} in {counties}.\n"
        f"It takes 10 minutes to review."
    )
    return {
        'subject': subject,
        'email_body': body,
        'call_script': script,
    }


def _render_bail_template(template_text, context):
    rendered = template_text or ''
    for key, value in context.items():
        rendered = rendered.replace('{{' + key + '}}', value or '')
    return rendered


def _bail_agency_rendered_templates(agency):
    defaults = _bail_agency_default_templates(agency)
    subject_template = (agency.get('email_subject_template') or '').strip() or defaults['subject']
    email_template = (agency.get('email_body_template') or '').strip() or defaults['email_body']
    script_template = (agency.get('call_script_template') or '').strip() or defaults['call_script']
    context = {
        'agency_name': (agency.get('agency_name') or '').strip(),
        'contact_name': (agency.get('contact_name') or '').strip(),
        'contact_name_or_team': (agency.get('contact_name') or '').strip() or 'team',
        'counties': (agency.get('counties') or '').strip() or 'your target counties',
        'sender_name': 'Montana Blotter Team',
        'today_iso': datetime.utcnow().strftime('%Y-%m-%d'),
    }
    return {
        'subject_template': subject_template,
        'email_template': email_template,
        'script_template': script_template,
        'subject_preview': _render_bail_template(subject_template, context),
        'email_preview': _render_bail_template(email_template, context),
        'script_preview': _render_bail_template(script_template, context),
    }


def _default_bail_test_email():
    username_value = (getattr(current_user, 'username', '') or '').strip().lower()
    if username_value and '@' in username_value:
        return username_value
    notify_recipients = _bail_lead_notify_recipients()
    if notify_recipients:
        return notify_recipients[0]
    smtp_user = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
    if smtp_user and '@' in smtp_user:
        return smtp_user
    return ''


def _exclusive_county_tier_monthly_cents(county_name=''):
    county_key = (county_name or '').strip().lower()
    premium_counties = {'yellowstone', 'missoula', 'gallatin'}
    metro_counties = {'cascade', 'flathead', 'lewis and clark', 'lewis & clark'}
    if county_key in premium_counties:
        return 35000
    if county_key in metro_counties:
        return 25000
    return 15000


def _bail_ad_price_cents(package_id, billing_cycle, county_targets=None):
    package = _bail_ad_package_lookup().get(_normalize_bail_ad_package_id(package_id))
    if not package:
        return None

    monthly_cents = int(package.get('price_monthly_cents') or 0)
    if package.get('pricing_model') == 'county_tiered':
        primary_county = ''
        if isinstance(county_targets, (list, tuple)) and county_targets:
            primary_county = county_targets[0]
        elif isinstance(county_targets, str):
            parsed = _parse_county_targets(county_targets)
            primary_county = parsed[0] if parsed else ''
        monthly_cents = _exclusive_county_tier_monthly_cents(primary_county)

    if billing_cycle == 'annual':
        annual_cents = int(package.get('price_annual_cents') or 0)
        if package.get('pricing_model') == 'county_tiered':
            annual_cents = monthly_cents * 12
        return annual_cents or monthly_cents * 12
    return monthly_cents


def _bail_ad_addon_total_cents(addon_ids, billing_cycle):
    lookup = _bail_ad_addon_lookup()
    total = 0
    for addon_id in addon_ids or []:
        addon = lookup.get(addon_id)
        if not addon:
            continue
        if billing_cycle == 'annual':
            total += int(addon.get('price_annual_cents') or 0) or int(addon['price_monthly_cents']) * 10
        else:
            total += int(addon['price_monthly_cents'])
    return total


def _bail_ad_county_list(value):
    raw = (value or '').replace('\n', ',').replace(';', ',')
    out = []
    seen = set()
    for part in raw.split(','):
        token = part.strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token[:64])
    return out


def _upsert_bail_ad_slot_assignments(conn, order_id, county_targets, slot_count):
    if not order_id or slot_count <= 0:
        return 0
    targets = _parse_county_targets(county_targets)
    if not targets:
        return 0

    created = 0
    for county_name in targets[:slot_count]:
        existing = conn.execute(
            'SELECT id FROM bail_ad_slots WHERE order_id = ? AND county = ? LIMIT 1',
            (order_id, county_name),
        ).fetchone()
        if existing:
            conn.execute(
                '''
                UPDATE bail_ad_slots
                SET status = 'active', starts_at = COALESCE(starts_at, datetime('now')), updated_at = datetime('now')
                WHERE id = ?
                ''',
                (existing['id'],),
            )
            continue
        conn.execute(
            '''
            INSERT INTO bail_ad_slots (order_id, county, slot_type, status, starts_at)
            VALUES (?, ?, 'county_feature', 'active', datetime('now'))
            ''',
            (order_id, county_name),
        )
        created += 1
    return created


def _apply_stripe_bail_ad_event(conn, event):
    _ensure_bail_ad_simulator_order_columns(conn)
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}
    if (metadata.get('flow') or '').strip() != 'bail_ad':
        return

    if event_type not in {'checkout.session.completed', 'checkout.session.async_payment_succeeded', 'checkout.session.expired', 'checkout.session.async_payment_failed'}:
        return

    session_id = (data_object.get('id') or '').strip()
    if not session_id:
        return

    package_id = _normalize_bail_ad_package_id(metadata.get('package_id'))
    billing_cycle = (metadata.get('billing_cycle') or 'monthly').strip().lower()
    if billing_cycle not in {'monthly', 'annual'}:
        billing_cycle = 'monthly'
    package = _bail_ad_package_lookup().get(package_id)
    if not package:
        return

    mapped_status = {
        'checkout.session.completed': 'active',
        'checkout.session.async_payment_succeeded': 'active',
        'checkout.session.expired': 'canceled',
        'checkout.session.async_payment_failed': 'payment_failed',
    }[event_type]

    raw_county_targets = metadata.get('county_targets') or ''
    if package.get('all_counties') and (raw_county_targets or '').strip().lower() in {'all', 'all_counties', 'statewide'}:
        county_target_values = sorted({county['name'] for county in _app().COUNTY_DATA.values()})
    else:
        county_target_values = _parse_county_targets(raw_county_targets)
    amount_cents = int(data_object.get('amount_total') or 0)
    if amount_cents <= 0:
        amount_cents = _bail_ad_price_cents(package_id, billing_cycle, county_target_values) or 0
    currency = (data_object.get('currency') or 'usd').lower()
    business_name = (metadata.get('business_name') or '').strip()[:120]
    contact_name = (metadata.get('contact_name') or '').strip()[:120]
    email = (metadata.get('email') or '').strip().lower()[:160]
    phone = (metadata.get('phone') or '').strip()[:40]
    website_url = (metadata.get('website_url') or '').strip()[:300]
    license_number = (metadata.get('license_number') or '').strip()[:80]
    county_targets = ', '.join(county_target_values)
    source = (metadata.get('source') or 'bail_ad_checkout').strip()[:80]
    add_on_ids = ','.join(_parse_addon_ids((metadata.get('add_on_ids') or '').split(',')))
    onboarding_token = (metadata.get('onboarding_token') or '').strip()[:64]
    simulator_logo_path = _safe_bail_ad_simulator_image_url(metadata.get('simulator_logo_path') or '')
    simulator_target_url = (metadata.get('simulator_target_url') or '').strip()[:300]
    simulator_share_url = (metadata.get('simulator_share_url') or '').strip()[:500]
    simulator_view = (metadata.get('simulator_view') or '').strip().lower()[:24]
    if simulator_view not in {'banner', 'sidebar'}:
        simulator_view = ''
    provider_subscription_id = data_object.get('subscription')
    provider_customer_id = data_object.get('customer')

    existing = conn.execute(
        '''
        SELECT id, onboarding_token
        FROM bail_ad_orders
        WHERE provider_session_id = ?
        LIMIT 1
        ''',
        (session_id,),
    ).fetchone()
    if existing and not onboarding_token:
        onboarding_token = existing['onboarding_token'] or ''
    if not onboarding_token:
        onboarding_token = secrets.token_urlsafe(24)

    conn.execute(
        '''
        INSERT INTO bail_ad_orders (
            business_name, contact_name, email, phone, website_url, license_number,
            county_targets, package_id, billing_cycle, amount_cents, currency, source,
            add_on_ids, status, provider, provider_session_id, provider_subscription_id, provider_customer_id,
            onboarding_token, paid_at, simulator_logo_path, simulator_target_url, simulator_share_url, simulator_view
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stripe', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider_session_id) DO UPDATE SET
            business_name = excluded.business_name,
            contact_name = excluded.contact_name,
            email = excluded.email,
            phone = excluded.phone,
            website_url = excluded.website_url,
            license_number = excluded.license_number,
            county_targets = excluded.county_targets,
            package_id = excluded.package_id,
            billing_cycle = excluded.billing_cycle,
            amount_cents = CASE WHEN excluded.amount_cents > 0 THEN excluded.amount_cents ELSE bail_ad_orders.amount_cents END,
            currency = excluded.currency,
            source = excluded.source,
            add_on_ids = excluded.add_on_ids,
            status = excluded.status,
            provider_subscription_id = COALESCE(excluded.provider_subscription_id, bail_ad_orders.provider_subscription_id),
            provider_customer_id = COALESCE(excluded.provider_customer_id, bail_ad_orders.provider_customer_id),
            onboarding_token = CASE WHEN excluded.onboarding_token != '' THEN excluded.onboarding_token ELSE bail_ad_orders.onboarding_token END,
            paid_at = CASE WHEN excluded.paid_at IS NOT NULL THEN excluded.paid_at ELSE bail_ad_orders.paid_at END,
            simulator_logo_path = CASE WHEN excluded.simulator_logo_path != '' THEN excluded.simulator_logo_path ELSE bail_ad_orders.simulator_logo_path END,
            simulator_target_url = CASE WHEN excluded.simulator_target_url != '' THEN excluded.simulator_target_url ELSE bail_ad_orders.simulator_target_url END,
            simulator_share_url = CASE WHEN excluded.simulator_share_url != '' THEN excluded.simulator_share_url ELSE bail_ad_orders.simulator_share_url END,
            simulator_view = CASE WHEN excluded.simulator_view != '' THEN excluded.simulator_view ELSE bail_ad_orders.simulator_view END,
            updated_at = datetime('now')
        ''',
        (
            business_name,
            contact_name,
            email,
            phone,
            website_url,
            license_number,
            county_targets,
            package_id,
            billing_cycle,
            amount_cents,
            currency,
            source,
            add_on_ids,
            mapped_status,
            session_id,
            provider_subscription_id,
            provider_customer_id,
            onboarding_token,
            datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if mapped_status == 'active' else None,
            simulator_logo_path,
            simulator_target_url,
            simulator_share_url,
            simulator_view,
        ),
    )

    order_row = conn.execute(
        'SELECT id FROM bail_ad_orders WHERE provider_session_id = ? LIMIT 1',
        (session_id,),
    ).fetchone()
    if mapped_status == 'active' and order_row:
        _upsert_bail_ad_slot_assignments(
            conn,
            order_row['id'],
            county_targets,
            int(package.get('county_slots') or 0),
        )




# ---------------------------------------------------------------------------
# Routes (moved from blueprints/payments.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Advertise / bail-bonds routes
# ---------------------------------------------------------------------------

@bail_bond_ads_bp.route('/advertise')
@bail_bond_ads_bp.route('/advertise/')
def advertise_redirect():
    return redirect(url_for('.advertise_bail_bonds'))


@bail_bond_ads_bp.route('/advertise/bail-bonds/contact', methods=['POST'])
def advertise_bail_bonds_contact():
    """Sales-call lead intake from the /advertise/bail-bonds landing page.

    Persists to `advertise_sales_leads` (product='bail') so the team can
    follow up. Mirrors the lawyer-side route in blueprints/lawyer_ads.py.
    """
    form = request.form
    name = (form.get('name') or '').strip()[:200]
    agency = (form.get('agency') or form.get('firm') or '').strip()[:200]
    email = (form.get('email') or '').strip().lower()[:200]
    phone = (form.get('phone') or '').strip()[:40]
    message = (form.get('message') or '').strip()[:1500]
    source = (form.get('source') or request.args.get('source') or 'advertise_bail_landing').strip()[:80]

    errors = []
    if not name:
        errors.append('Name is required.')
    if not email or '@' not in email:
        errors.append('A valid email is required.')
    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('bail_bond_ads.advertise_bond_ads') + '?contact=error#schedule-a-call')

    from app import _client_ip
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:32]
    user_agent = (request.headers.get('User-Agent') or '')[:500]

    conn = get_db()
    try:
        ensure_advertise_sales_lead_schema(conn)
        conn.execute(
            '''
            INSERT INTO advertise_sales_leads (
                product, name, firm_or_agency, email, phone,
                county, package_interest, message, source,
                ip_hash, user_agent, status
            ) VALUES (?, ?, ?, ?, ?, '', '', ?, ?, ?, ?, 'new')
            ''',
            ('bail', name, agency, email, phone, message, source, ip_hash, user_agent),
        )
        conn.commit()
    finally:
        conn.close()

    flash('Thanks. We will be in touch within one business day.', 'success')
    return redirect(url_for('bail_bond_ads.advertise_bond_ads') + '?contact=submitted#schedule-a-call')


@bail_bond_ads_bp.route('/advertise/bail-bonds', methods=['GET', 'POST'])
@bail_bond_ads_bp.route('/advertise/bail-bonds/', methods=['GET', 'POST'])
def advertise_bail_bonds():
    package_options = _bail_ad_public_packages()
    package_ids = {pkg['id'] for pkg in package_options}
    pricing_cards = _bail_ad_pricing_cards(package_options)
    help_contact = _bail_help_contact()
    contract_info = _bail_ad_contract_context()

    # Live storefront stats: pull active placements and county coverage
    # from the database so the landing page reflects real inventory, not
    # copy.
    import sqlite3 as _sqlite3
    coverage_stats = {
        'active_orders': 0,
        'counties_with_coverage': 0,
        'header_slots_filled': 0,
        'sidebar_slots_filled': 0,
        'county_slots_filled': 0,
        'bundle_slots_filled': 0,
        'header_cap': 1,
        'sidebar_cap': 1,
        'bundle_cap': 2,
    }
    try:
        stats_conn = get_db()
        try:
            rows = stats_conn.execute(
                """
                SELECT package_id, county_targets
                FROM bail_ad_orders
                WHERE status = 'active'
                """
            ).fetchall()
            counties_covered = set()
            for r in rows:
                pid = (r['package_id'] or '').strip()
                if pid == 'featured_bondsman_banner':
                    coverage_stats['header_slots_filled'] += 1
                elif pid == 'emergency_call_sidebar':
                    coverage_stats['sidebar_slots_filled'] += 1
                elif pid == 'gold_bond_bundle':
                    coverage_stats['bundle_slots_filled'] += 1
                elif pid == 'exclusive_county_sponsorship':
                    coverage_stats['county_slots_filled'] += 1
                coverage_stats['active_orders'] += 1
                for c in (r['county_targets'] or '').split(','):
                    token = c.strip()
                    if token:
                        counties_covered.add(token.lower())
            coverage_stats['counties_with_coverage'] = len(counties_covered)
        finally:
            stats_conn.close()
    except _sqlite3.OperationalError:
        pass

    form_data = {
        'business_name': '',
        'contact_name': '',
        'email': '',
        'phone': '',
        'website_url': '',
        'license_number': '',
        'counties_served': '',
        'package_interest': '',
        'monthly_budget': '',
        'message': '',
    }
    errors = []
    submitted = request.args.get('submitted') == '1'

    if request.method == 'POST':
        form_data = {
            'business_name': (request.form.get('business_name') or '').strip()[:120],
            'contact_name': (request.form.get('contact_name') or '').strip()[:120],
            'email': (request.form.get('email') or '').strip().lower()[:160],
            'phone': (request.form.get('phone') or '').strip()[:40],
            'website_url': (request.form.get('website_url') or '').strip()[:300],
            'license_number': (request.form.get('license_number') or '').strip()[:80],
            'counties_served': (request.form.get('counties_served') or '').strip()[:500],
            'package_interest': _normalize_bail_ad_package_id((request.form.get('package_interest') or '').strip()[:32]),
            'monthly_budget': (request.form.get('monthly_budget') or '').strip()[:32],
            'message': (request.form.get('message') or '').strip()[:1200],
        }

        if not form_data['business_name']:
            errors.append('Business name is required.')
        if not form_data['contact_name']:
            errors.append('Contact name is required.')
        if not form_data['email'] or '@' not in form_data['email']:
            errors.append('A valid email is required.')
        if not form_data['phone']:
            errors.append('Phone number is required.')
        if not form_data['license_number']:
            errors.append('State license number is required.')
        if not form_data['counties_served']:
            errors.append('Please list at least one county served.')
        if form_data['package_interest'] and form_data['package_interest'] not in package_ids:
            errors.append('Selected package is invalid.')
        if request.form.get('policy_ack') != 'yes':
            errors.append('You must confirm the advertising policy.')
        if request.form.get('contract_ack') != 'yes':
            errors.append("You must review the Montana Blotter Contract.")

        budget_cents = _parse_budget_cents(form_data['monthly_budget'])
        source = (request.form.get('source') or request.args.get('source') or 'bail_ad_page').strip()[:80]
        if not errors:
            ip_hash = hashlib.sha256((_app()._client_ip() or '').encode()).hexdigest()[:16]
            referrer = (request.referrer or '')[:500]
            conn = get_db()
            conn.execute(
                '''
                INSERT INTO bail_ad_inquiries (
                    business_name, contact_name, email, phone, website_url,
                    license_number, counties_served, package_interest,
                    monthly_budget_cents, message, source, status, ip_hash, referrer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    form_data['business_name'],
                    form_data['contact_name'],
                    form_data['email'],
                    form_data['phone'],
                    form_data['website_url'],
                    form_data['license_number'],
                    form_data['counties_served'],
                    form_data['package_interest'],
                    budget_cents,
                    form_data['message'],
                    source,
                    'pending',
                    ip_hash,
                    referrer,
                ),
            )
            conn.commit()
            conn.close()
            return redirect(url_for('.advertise_bail_bonds', submitted='1'))

    simulator_view = (request.args.get('sim_view') or '').strip().lower()
    if not simulator_view:
        simulator_view = 'sidebar' if form_data.get('package_interest') == 'emergency_call_sidebar' else 'banner'
    if simulator_view not in {'banner', 'sidebar'}:
        simulator_view = 'banner'
    simulator_bootstrap = {
        'agencyName': (request.args.get('agency_name') or request.args.get('agency') or form_data.get('business_name') or 'Your Agency').strip()[:80] or 'Your Agency',
        'initialImageUrl': _safe_bail_ad_simulator_image_url(request.args.get('logo_url') or request.args.get('logo') or ''),
        'initialView': simulator_view,
        'initialCounty': (request.args.get('sim_county') or 'Cascade County').strip()[:80] or 'Cascade County',
        'initialTargetUrl': (request.args.get('target_url') or request.args.get('website_url') or form_data.get('website_url') or '').strip()[:300],
        'publicPreviewBaseUrl': url_for('.advertise_bail_bonds'),
        'checkoutBaseUrl': url_for('.advertise_bail_bonds_checkout'),
        'uploadEndpoint': url_for('api.upload_bail_ad_simulator_asset'),
        'eventEndpoint': url_for('api.track_bail_ad_simulator_event'),
        'internalMode': False,
        'allowInquirySync': True,
    }

    return render_template(
        'advertise_bail_bonds.html',
        package_options=package_options,
        pricing_cards=pricing_cards,
        simulator_bootstrap=simulator_bootstrap,
        help_contact=help_contact,
        contract_info=contract_info,
        form_data=form_data,
        form_errors=errors,
        submitted=submitted,
        coverage_stats=coverage_stats,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/advertise/bail-bonds/checkout', methods=['GET', 'POST'])
@bail_bond_ads_bp.route('/advertise/bail-bonds/checkout/', methods=['GET', 'POST'])
def advertise_bail_bonds_checkout():
    conn = get_db()
    _ensure_bail_ad_simulator_order_columns(conn)
    conn.commit()
    conn.close()
    package_map = _bail_ad_package_lookup()
    package_options = _bail_ad_public_packages()
    package_ids = {pkg['id'] for pkg in package_options}
    addon_options = _bail_ad_addons()
    addon_lookup = _bail_ad_addon_lookup()
    contract_info = _bail_ad_contract_context()
    if not _bail_ad_checkout_ready():
        return render_template(
            'advertise_bail_checkout.html',
            package_options=package_options,
            addon_options=addon_options,
            addon_lookup=addon_lookup,
            contract_info=contract_info,
            form_data={},
            form_errors=['Secure checkout is not configured yet. Please contact support.'],
            checkout_ready=False,
            current_year=datetime.now().year,
            active_nav='advertise',
        ), 503

    simulator_view_prefill = (request.values.get('simulator_view') or request.values.get('sim_view') or '').strip().lower()
    prefill_package = _normalize_bail_ad_package_id(request.values.get('package'))
    if not prefill_package and simulator_view_prefill in {'banner', 'sidebar'}:
        prefill_package = _bail_ad_package_id_for_simulator_view(simulator_view_prefill)
    if prefill_package not in package_ids:
        prefill_package = ''

    form_data = {
        'business_name': (request.values.get('business_name') or request.values.get('agency_name') or '').strip()[:120],
        'contact_name': (request.values.get('contact_name') or '').strip()[:120],
        'email': (request.values.get('email') or '').strip().lower()[:160],
        'phone': (request.values.get('phone') or '').strip()[:40],
        'website_url': (request.values.get('website_url') or request.values.get('target_url') or '').strip()[:300],
        'license_number': (request.values.get('license_number') or '').strip()[:80],
        'county_targets': (request.values.get('county_targets') or '').strip()[:500],
        'package_id': prefill_package,
        'billing_cycle': 'monthly',
        'source': (request.args.get('source') or 'bail_ad_checkout').strip()[:80],
        'add_on_ids': [],
        'simulator_logo_path': _safe_bail_ad_simulator_image_url(request.values.get('simulator_logo_path') or request.values.get('logo_url') or request.values.get('logo') or ''),
        'simulator_target_url': (request.values.get('simulator_target_url') or request.values.get('target_url') or request.values.get('website_url') or '').strip()[:300],
        'simulator_share_url': (request.values.get('simulator_share_url') or '').strip()[:500],
        'simulator_view': simulator_view_prefill if simulator_view_prefill in {'banner', 'sidebar'} else '',
    }
    errors = []

    if request.method == 'POST':
        form_data = {
            'business_name': (request.form.get('business_name') or '').strip()[:120],
            'contact_name': (request.form.get('contact_name') or '').strip()[:120],
            'email': (request.form.get('email') or '').strip().lower()[:160],
            'phone': (request.form.get('phone') or '').strip()[:40],
            'website_url': (request.form.get('website_url') or '').strip()[:300],
            'license_number': (request.form.get('license_number') or '').strip()[:80],
            'county_targets': (request.form.get('county_targets') or '').strip()[:500],
            'package_id': _normalize_bail_ad_package_id((request.form.get('package_id') or '').strip()[:32]),
            'billing_cycle': (request.form.get('billing_cycle') or 'monthly').strip().lower()[:16],
            'source': (request.form.get('source') or 'bail_ad_checkout').strip()[:80],
            'add_on_ids': _parse_addon_ids(request.form.getlist('add_on_ids')),
            'simulator_logo_path': _safe_bail_ad_simulator_image_url(request.form.get('simulator_logo_path') or ''),
            'simulator_target_url': (request.form.get('simulator_target_url') or '').strip()[:300],
            'simulator_share_url': (request.form.get('simulator_share_url') or '').strip()[:500],
            'simulator_view': (request.form.get('simulator_view') or '').strip().lower()[:24],
        }
        if form_data['simulator_view'] not in {'banner', 'sidebar'}:
            form_data['simulator_view'] = ''
        if not form_data['business_name']:
            errors.append('Business name is required.')
        if not form_data['contact_name']:
            errors.append('Contact name is required.')
        if '@' not in form_data['email']:
            errors.append('Valid contact email is required.')
        if not form_data['phone']:
            errors.append('Phone number is required.')
        if not form_data['license_number']:
            errors.append('License number is required.')
        if form_data['package_id'] not in package_ids:
            errors.append('Please select a valid package.')
        if form_data['billing_cycle'] not in {'monthly', 'annual'}:
            errors.append('Billing cycle is invalid.')

        selected_package = package_map.get(form_data['package_id']) if form_data['package_id'] in package_ids else None
        parsed_counties = _parse_county_targets(form_data['county_targets'])
        if selected_package:
            package_id = selected_package.get('id')
            slot_count = int(selected_package.get('county_slots') or 0)
            if package_id == 'exclusive_county_sponsorship' and len(parsed_counties) != 1:
                errors.append('Exclusive County Sponsorship requires exactly one county target.')
            elif package_id == 'gold_bond_bundle':
                if len(parsed_counties) < 2:
                    errors.append('The Gold Bond Bundle requires exactly two county targets.')
                elif len(parsed_counties) > 2:
                    errors.append('The Gold Bond Bundle includes two county targets. Please select two.')
            elif slot_count > 0 and len(parsed_counties) < slot_count:
                errors.append(f"Please provide at least {slot_count} county target{'s' if slot_count != 1 else ''}.")

        if request.form.get('policy_ack') != 'yes':
            errors.append('Advertising policy acknowledgement is required.')
        if request.form.get('contract_ack') != 'yes':
            errors.append("You must review and accept the Montana Blotter Contract.")
        if request.form.get('terms_ack') != 'yes':
            errors.append('You must accept billing terms to continue.')

        if not errors:
            package = package_map[form_data['package_id']]
            base_amount_cents = _bail_ad_price_cents(
                form_data['package_id'],
                form_data['billing_cycle'],
                parsed_counties,
            ) or 0
            addon_amount_cents = _bail_ad_addon_total_cents(form_data['add_on_ids'], form_data['billing_cycle'])
            amount_cents = base_amount_cents + addon_amount_cents
            if amount_cents <= 0:
                errors.append('Unable to price selected package.')
            else:
                stripe_keys = _app()._stripe_keys()
                stripe.api_key = stripe_keys['secret_key']
                onboarding_token = secrets.token_urlsafe(24)
                interval = 'year' if form_data['billing_cycle'] == 'annual' else 'month'
                metadata_county_targets = (
                    'all_counties'
                    if package.get('all_counties')
                    else ','.join(parsed_counties)
                )
                checkout_payload = {
                    'mode': 'subscription',
                    'line_items': [{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {
                                'name': f"Montana Blotter Bail Ad - {package['name']}",
                            },
                            'unit_amount': amount_cents,
                            'recurring': {'interval': interval},
                        },
                        'quantity': 1,
                    }],
                    'success_url': f'{_app().BASE_URL}/advertise/bail-bonds/checkout/success?session_id={{CHECKOUT_SESSION_ID}}',
                    'cancel_url': f'{_app().BASE_URL}/advertise/bail-bonds/checkout/cancel',
                    'customer_email': form_data['email'],
                    'allow_promotion_codes': False,
                    'billing_address_collection': 'auto',
                    'metadata': {
                        'flow': 'bail_ad',
                        'package_id': form_data['package_id'],
                        'billing_cycle': form_data['billing_cycle'],
                        'business_name': form_data['business_name'],
                        'contact_name': form_data['contact_name'],
                        'email': form_data['email'],
                        'phone': form_data['phone'],
                        'website_url': form_data['website_url'],
                        'license_number': form_data['license_number'],
                        'county_targets': metadata_county_targets,
                        'source': form_data['source'],
                        'add_on_ids': ','.join(form_data['add_on_ids']),
                        'contract_url': contract_info['url'],
                        'contract_version': contract_info['updated_label'],
                        'onboarding_token': onboarding_token,
                        'simulator_logo_path': form_data['simulator_logo_path'],
                        'simulator_target_url': form_data['simulator_target_url'],
                        'simulator_share_url': form_data['simulator_share_url'],
                        'simulator_view': form_data['simulator_view'],
                    },
                }
                try:
                    checkout_session = stripe.checkout.Session.create(**checkout_payload)
                except Exception:
                    errors.append('Unable to start secure checkout right now. Please try again.')
                    checkout_session = None

                if checkout_session:
                    conn = get_db()
                    conn.execute(
                        '''
                        INSERT INTO bail_ad_orders (
                            business_name, contact_name, email, phone, website_url, license_number,
                            county_targets, package_id, billing_cycle, amount_cents, currency, source,
                            add_on_ids, notes, status, provider, provider_session_id, provider_subscription_id, provider_customer_id,
                            onboarding_token, simulator_logo_path, simulator_target_url, simulator_share_url, simulator_view
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'usd', ?, ?, ?, 'checkout_pending', 'stripe', ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(provider_session_id) DO UPDATE SET
                            business_name = excluded.business_name,
                            contact_name = excluded.contact_name,
                            email = excluded.email,
                            phone = excluded.phone,
                            website_url = excluded.website_url,
                            license_number = excluded.license_number,
                            county_targets = excluded.county_targets,
                            package_id = excluded.package_id,
                            billing_cycle = excluded.billing_cycle,
                            amount_cents = excluded.amount_cents,
                            source = excluded.source,
                            add_on_ids = excluded.add_on_ids,
                            notes = excluded.notes,
                            onboarding_token = excluded.onboarding_token,
                            simulator_logo_path = excluded.simulator_logo_path,
                            simulator_target_url = excluded.simulator_target_url,
                            simulator_share_url = excluded.simulator_share_url,
                            simulator_view = excluded.simulator_view,
                            updated_at = datetime('now')
                        ''',
                        (
                            form_data['business_name'],
                            form_data['contact_name'],
                            form_data['email'],
                            form_data['phone'],
                            form_data['website_url'],
                            form_data['license_number'],
                            ', '.join(parsed_counties),
                            form_data['package_id'],
                            form_data['billing_cycle'],
                            amount_cents,
                            form_data['source'],
                            ','.join(form_data['add_on_ids']),
                            'Imported from ad simulator' if form_data['simulator_logo_path'] else '',
                            checkout_session.get('id'),
                            checkout_session.get('subscription'),
                            checkout_session.get('customer'),
                            onboarding_token,
                            form_data['simulator_logo_path'],
                            form_data['simulator_target_url'],
                            form_data['simulator_share_url'],
                            form_data['simulator_view'],
                        ),
                    )
                    conn.commit()
                    conn.close()
                    return redirect(checkout_session.get('url'))

    selected_package = package_map.get(form_data.get('package_id') or '')
    return render_template(
        'advertise_bail_checkout.html',
        package_options=package_options,
        addon_options=addon_options,
        addon_lookup=addon_lookup,
        contract_info=contract_info,
        selected_package=selected_package,
        form_data=form_data,
        form_errors=errors,
        checkout_ready=True,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/advertise/bail-bonds/checkout/success')
@bail_bond_ads_bp.route('/advertise/bail-bonds/checkout/success/')
def advertise_bail_checkout_success():
    session_id = (request.args.get('session_id') or '').strip()
    order = None
    package_map = _bail_ad_package_lookup()
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or (getattr(config, 'EMAIL_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )
    if session_id:
        conn = get_db()
        row = conn.execute(
            '''
            SELECT
                id,
                business_name,
                package_id,
                billing_cycle,
                amount_cents,
                currency,
                status,
                onboarding_token,
                county_targets,
                add_on_ids,
                paid_at,
                created_at
            FROM bail_ad_orders
            WHERE provider_session_id = ?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (session_id,),
        ).fetchone()
        conn.close()
        if row:
            order = dict(row)
            package = package_map.get(order.get('package_id') or '')
            order['package_name'] = (package.get('name') if package else '') or (order.get('package_id') or '').replace('_', ' ').title()
            if order.get('onboarding_token'):
                return redirect(
                    url_for(
                        '.advertise_bail_control_panel',
                        token=order['onboarding_token'],
                        welcome='1',
                        session_id=session_id,
                    )
                )
    return render_template(
        'advertise_bail_checkout_success.html',
        order=order,
        session_id=session_id,
        support_email=support_email,
        contract_info=_bail_ad_contract_context(),
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/advertise/bail-bonds/control-panel/<token>')
@bail_bond_ads_bp.route('/advertise/bail-bonds/control-panel/<token>/')
def advertise_bail_control_panel(token):
    safe_token = (token or '').strip()[:128]
    session_id = (request.args.get('session_id') or '').strip()[:255]
    welcome = request.args.get('welcome') == '1'
    conn = get_db()
    context = _bail_ad_control_panel_context(conn, safe_token, session_id=session_id)
    conn.close()
    if not context:
        return render_template('404.html'), 404

    order = context['order']
    return render_template(
        'advertise_bail_control_panel.html',
        order=order,
        package=context['package'],
        creative=context['creative'],
        simulator_preview=context['simulator_preview'],
        slots=context['slots'],
        performance_30d=context['performance_30d'],
        county_performance_30d=context['county_performance_30d'],
        attribution=context['attribution'],
        benchmarks=context['benchmarks'],
        signature_features=context['signature_features'],
        priority_actions=context['priority_actions'],
        launch_checklist=context['launch_checklist'],
        booking_signal_score=context['booking_signal_score'],
        signal_label=context['signal_label'],
        welcome=welcome,
        session_id=session_id,
        contract_info=_bail_ad_contract_context(order['onboarding_token']),
        page_title=f"{order['business_name']} Control Panel",
        meta_description='Private control panel for bail bonds advertisers after payment.',
        canonical_url='',
        og_title=f"{order['business_name']} Control Panel",
        og_description='Track creative approval, county coverage, performance, and renewal timing in one place.',
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/advertise/bail-bonds/checkout/cancel')
@bail_bond_ads_bp.route('/advertise/bail-bonds/checkout/cancel/')
def advertise_bail_checkout_cancel():
    return render_template(
        'advertise_bail_checkout_cancel.html',
        contract_info=_bail_ad_contract_context(),
        active_nav='advertise',
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Subscription checkout routes
# ---------------------------------------------------------------------------

@bail_bond_ads_bp.route('/checkout/subscription', methods=['POST'])
def checkout_subscription():
    keys = _app()._stripe_keys()
    stripe.api_key = keys['secret_key']

    plan = (request.form.get('plan') or '').strip().lower()
    interval = (request.form.get('interval') or 'monthly').strip().lower()

    if plan not in {'insider', 'professional'}:
        return render_template('pricing.html', checkout_error='Invalid plan selected.'), 400
    if interval not in {'monthly', 'yearly'}:
        return render_template('pricing.html', checkout_error='Invalid billing interval.'), 400

    # Require public user session so we can tie the subscription to an account
    public_user_id = session.get('public_user_id')
    if not public_user_id:
        return redirect(url_for('auth.register', next=request.full_path, message='Please create a free account before subscribing.'))

    conn = get_db()
    user_row = conn.execute(
        'SELECT id, email, subscriber_plan FROM public_users WHERE id = ? AND is_active = 1',
        (int(public_user_id),),
    ).fetchone()
    conn.close()
    if not user_row:
        return redirect(url_for('auth.register', next=request.full_path, message='Please create a free account before subscribing.'))

    pricing = {
        'insider': {'monthly': 799, 'yearly': 6999, 'name': 'Insider'},
        'professional': {'monthly': 1499, 'yearly': 12999, 'name': 'Professional'},
    }
    plan_info = pricing[plan]
    amount_cents = plan_info[interval]
    interval_spec = 'year' if interval == 'yearly' else 'month'

    try:
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"Montana Blotter {plan_info['name']}",
                        'description': f"{plan_info['name']} subscription — {interval} billing",
                    },
                    'unit_amount': amount_cents,
                    'recurring': {'interval': interval_spec},
                },
                'quantity': 1,
            }],
            success_url=f"{_app().BASE_URL}/checkout/subscription/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{_app().BASE_URL}/checkout/subscription/cancel",
            customer_email=user_row['email'] or None,
            metadata={
                'flow': 'subscription',
                'plan': plan,
                'interval': interval,
                'public_user_id': str(user_row['id']),
            },
        )
    except Exception as exc:
        return render_template('pricing.html', checkout_error=f'Unable to start checkout: {exc}'), 503

    checkout_url = _checkout_redirect_url(checkout_session)
    if not checkout_url:
        return render_template('pricing.html', checkout_error='Unable to start checkout right now.'), 503
    return redirect(checkout_url)


@bail_bond_ads_bp.route('/checkout/subscription/success')
def checkout_subscription_success():
    session_id = (request.args.get('session_id') or '').strip()
    return render_template(
        'checkout_subscription_success.html',
        session_id=session_id,
        active_nav='pricing',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/checkout/subscription/cancel')
def checkout_subscription_cancel():
    return render_template(
        'checkout_subscription_cancel.html',
        active_nav='pricing',
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Subscription checkout — Plus and Pro tiers
# ---------------------------------------------------------------------------

@bail_bond_ads_bp.route('/checkout/<any(plus, pro):plan>', methods=['GET'])
def checkout_tier(plan):
    """Create a Stripe Checkout Session for the given plan."""
    from urllib.parse import urlencode
    public_user_id = session.get('public_user_id')
    log.info('subscription checkout: public_user_id=%s plan=%s', public_user_id, plan)
    if not public_user_id:
        flash('Please log in or create an account to subscribe.', 'info')
        return redirect('/login?next=/subscribe')
    email = None
    try:
        conn = get_db()
        row = conn.execute('SELECT email FROM public_users WHERE id = ?', (int(public_user_id),)).fetchone()
        conn.close()
        if row:
            email = row['email'] or None
    except Exception:
        log.exception('subscription checkout: DB error fetching user %s', public_user_id)

    interval = (request.args.get('interval') or 'monthly').strip().lower()
    if interval not in {'monthly', 'annual'}:
        flash('Please select a valid billing interval.', 'error')
        return redirect('/subscribe')

    keys = _app()._stripe_keys()
    base_url = (getattr(config, 'BASE_URL', '') or '').strip() or request.host_url.rstrip('/')
    stripe.api_key = keys['secret_key']
    price_ids = _plan_price_ids(plan)
    price_id = price_ids.get(interval)

    if not price_id:
        # Fallback to Payment Link
        params = {'client_reference_id': str(public_user_id)}
        if email:
            params['prefilled_email'] = email
        payment_link = PLAN_PAYMENT_LINKS.get((plan, interval), '')
        if not payment_link:
            flash('Subscription plan is not available right now.', 'error')
            return redirect('/subscribe')
        dest = f"{payment_link}?{urlencode(params)}"
        log.info('subscription checkout: falling back to payment link %s %s/%s', public_user_id, plan, interval)
        return redirect(dest)

    try:
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            client_reference_id=str(public_user_id),
            customer_email=email or None,
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=f"{base_url}/subscribe/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/pricing?canceled=1",
            subscription_data={
                'metadata': {
                    'flow': 'subscription',
                    'plan': plan,
                    'interval': interval,
                    'public_user_id': str(public_user_id),
                },
            },
            metadata={
                'flow': 'subscription',
                'plan': plan,
                'interval': interval,
                'public_user_id': str(public_user_id),
            },
        )
        checkout_url = _checkout_redirect_url(checkout_session)
        if checkout_url:
            log.info('subscription checkout: created Stripe Checkout Session for user %s plan=%s/%s', public_user_id, plan, interval)
            return redirect(checkout_url)
        raise RuntimeError('Stripe checkout session did not return a URL')
    except Exception:
        # Fallback to Payment Link
        params = {'client_reference_id': str(public_user_id)}
        if email:
            params['prefilled_email'] = email
        payment_link = PLAN_PAYMENT_LINKS.get((plan, interval), '')
        if not payment_link:
            flash('Subscription could not be processed. Please try again.', 'error')
            return redirect('/subscribe')
        dest = f"{payment_link}?{urlencode(params)}"
        log.exception('subscription checkout: error, falling back to payment link %s %s/%s', public_user_id, plan, interval)
        return redirect(dest)


def _warrant_access_price_ids() -> dict[str, str]:
    """Return the active Stripe price IDs for warrant access."""
    return {
        'weekly': (getattr(config, 'WARRANT_WEEKLY_PRICE_ID', '') or '').strip(),
        'monthly': (getattr(config, 'WARRANT_MONTHLY_PRICE_ID', '') or '').strip(),
        'annual': (getattr(config, 'WARRANT_ANNUAL_PRICE_ID', '') or '').strip(),
    }


@bail_bond_ads_bp.route('/checkout/warrant-access', methods=['GET', 'POST'])
def checkout_warrant_access():
    """Create a Stripe Checkout Session for the paid wanted/warrant add-on."""
    public_user_id = session.get('public_user_id')
    if not public_user_id:
        flash('Please log in or create an account to subscribe.', 'info')
        return redirect('/login?next=/wanted/subscribe')

    email = None
    try:
        conn = get_db()
        row = conn.execute(
            'SELECT email FROM public_users WHERE id = ? AND is_active = 1',
            (int(public_user_id),),
        ).fetchone()
        conn.close()
        if row:
            email = row['email'] or None
    except Exception:
        log.exception('warrant-access checkout: DB error fetching user %s', public_user_id)

    plan = (request.args.get('plan') or 'monthly').strip().lower()
    if plan not in {'weekly', 'monthly', 'annual'}:
        flash('Please select a valid warrant access plan.', 'error')
        return redirect('/wanted/subscribe')

    price_id = _warrant_access_price_ids().get(plan, '')
    if not price_id:
        flash('Warrant access checkout is not available right now.', 'error')
        return redirect('/wanted/subscribe')

    keys = _app()._stripe_keys()
    base_url = (getattr(config, 'BASE_URL', '') or '').strip() or request.host_url.rstrip('/')
    stripe.api_key = keys['secret_key']
    try:
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            client_reference_id=str(public_user_id),
            customer_email=email or None,
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=f"{base_url}/checkout/warrant-access/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/wanted/subscribe?canceled=1",
            subscription_data={
                'metadata': {
                    'flow': 'warrant_access',
                    'public_user_id': str(public_user_id),
                    'plan': 'warrant_access',
                    'interval': plan,
                },
            },
            metadata={
                'flow': 'warrant_access',
                'public_user_id': str(public_user_id),
                'plan': 'warrant_access',
                'interval': plan,
            },
        )
        checkout_url = _checkout_redirect_url(checkout_session)
        if checkout_url:
            log.info(
                'warrant-access checkout: created Stripe Checkout Session for user %s plan=%s',
                public_user_id,
                plan,
            )
            return redirect(checkout_url)
        raise RuntimeError('Stripe checkout session did not return a URL')
    except Exception:
        log.exception('warrant-access checkout: Stripe session creation failed for user %s plan=%s', public_user_id, plan)
        flash('Unable to start warrant access checkout. Please try again.', 'error')
        return redirect('/wanted/subscribe')


@bail_bond_ads_bp.route('/checkout/warrant-access/success')
def checkout_warrant_access_success():
    return render_template(
        'checkout_warrant_success.html',
        active_nav='wanted',
        page_title='Warrant Access Activated',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/subscribe/success')
def subscribe_success():
    return render_template(
        'checkout_subscription_success.html',
        active_nav='subscribe',
        page_title='Subscription Activated',
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Paid name-removal / privacy suppression (one-time fee; amount set in Stripe)
# ---------------------------------------------------------------------------

@bail_bond_ads_bp.route('/remove-my-name', methods=['GET', 'POST'])
def remove_my_name():
    """Public request form for paid name suppression.

    A one-time payment (amount shown via NAME_SUPPRESS_AMOUNT_LABEL, charged
    through the Stripe Price in NAME_SUPPRESS_PRICE_ID) covers a verified privacy
    review. On approval the person's name is REDACTED (not deleted) across public
    records. Requires human review before suppression is applied — payment alone
    does not suppress.
    """
    # Pre-fill from query string (e.g. a "Request removal" CTA on a person page).
    prefill_name = (request.args.get('name') or '').strip()
    prefill_county = (request.args.get('county') or '').strip()
    errors = []
    submitted_name = prefill_name
    submitted_county = prefill_county
    if request.method == 'POST':
        person_name = (request.form.get('person_name') or '').strip()
        dob = (request.form.get('dob') or '').strip()
        county = (request.form.get('county') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        submitted_name = person_name
        submitted_county = county
        if len(person_name) < 2:
            errors.append('Please enter the full name to suppress.')
        if '@' not in email or '.' not in email:
            errors.append('Please enter a valid contact email.')
        if not errors:
            public_user_id = session.get('public_user_id')
            conn = get_db()
            try:
                cur = conn.execute(
                    '''INSERT INTO name_suppression_requests
                       (public_user_id, email, person_name, dob, county,
                        status, ip_address, created_at)
                       VALUES (?, ?, ?, ?, ?, 'pending', ?, datetime('now'))''',
                    (public_user_id, email, person_name, dob or None,
                     county or None, request.remote_addr),
                )
                request_id = cur.lastrowid
                conn.commit()
            finally:
                conn.close()
            # Stripe checkout for the one-time suppression product.
            keys = _stripe_keys()
            if not keys['secret_key'] or not config.NAME_SUPPRESS_PRICE_ID:
                flash('Name removal checkout is unavailable right now. Please try again later.', 'error')
                return redirect('/remove-my-name')
            stripe.api_key = keys['secret_key']
            try:
                checkout_session = stripe.checkout.Session.create(
                    mode='payment',
                    client_reference_id=str(request_id),
                    customer_email=email or None,
                    line_items=[{'price': config.NAME_SUPPRESS_PRICE_ID, 'quantity': 1}],
                    success_url=f"{_base_url()}/checkout/name-removal/success?session_id={{CHECKOUT_SESSION_ID}}",
                    cancel_url=f"{_base_url()}/remove-my-name?canceled=1",
                    metadata={
                        'flow': 'name_removal',
                        'request_id': str(request_id),
                        'person_name': person_name,
                    },
                )
                conn = get_db()
                try:
                    conn.execute(
                        'UPDATE name_suppression_requests SET stripe_session_id = ? WHERE id = ?',
                        (checkout_session.id, request_id),
                    )
                    conn.commit()
                finally:
                    conn.close()
                checkout_url = _checkout_redirect_url(checkout_session)
                if checkout_url:
                    return redirect(checkout_url)
                raise RuntimeError('Stripe checkout session did not return a URL')
            except Exception:
                log.exception('name-removal checkout: Stripe session creation failed for request %s', request_id)
                flash('Unable to start name-removal checkout. Please try again.', 'error')
                return redirect('/remove-my-name')

    return render_template(
        'remove_my_name.html',
        errors=errors,
        amount_label=config.NAME_SUPPRESS_AMOUNT_LABEL,
        prefill_name=submitted_name,
        prefill_county=submitted_county,
        active_nav='remove-name',
        page_title='Remove My Name — Montana Blotter',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/checkout/name-removal/success')
def checkout_name_removal_success():
    return render_template(
        'checkout_name_removal_success.html',
        active_nav='remove-name',
        page_title='Name Removal Request Received',
        current_year=datetime.now().year,
    )


def _stripe_keys():
    """Return dict with secret_key/public_key from config (mirrors app config)."""
    return {
        'secret_key': getattr(config, 'STRIPE_SECRET_KEY', '') or '',
        'public_key': getattr(config, 'STRIPE_PUBLISHABLE_KEY', '') or '',
    }


def _base_url() -> str:
    return (getattr(config, 'BASE_URL', '') or '').strip() or request.host_url.rstrip('/')


@bail_bond_ads_bp.route('/pro')
def pro_landing():
    """Dedicated Pro landing page for attorneys, journalists, and investigators."""
    return render_template(
        'pro_landing.html',
        active_nav='pro',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/pricing')
def pricing_page():
    from services.monetization.paywall import user_has_warrant_access
    public_user_id = session.get('public_user_id')
    is_logged_in = bool(public_user_id)
    already_subscribed = is_logged_in and user_has_warrant_access()
    next_url = request.args.get('next', '/subscribe')
    return render_template(
        'pricing.html',
        active_nav='subscribe',
        page_title='Montana Blotter Subscription Plans',
        meta_description='Choose the right plan for Montana public records access — from Free to Pro.',
        canonical_url=f'{_app().BASE_URL}/pricing',
        current_year=datetime.now().year,
        is_logged_in=is_logged_in,
        already_subscribed=already_subscribed,
        next_url=next_url,
    )


@bail_bond_ads_bp.route('/billing')
def billing_portal():
    """Self-service card updates and invoice history via the Stripe customer portal.

    Without this a subscriber whose card is declined has no way to fix it, and
    the subscription decays from past_due to canceled with reason payment_failed.
    """
    public_user_id = session.get('public_user_id')
    if not public_user_id:
        return redirect('/login?next=/billing')

    subscription_id = ''
    try:
        conn = get_db()
        try:
            row = conn.execute(
                'SELECT stripe_subscription_id FROM public_users WHERE id = ?',
                (int(public_user_id),),
            ).fetchone()
            subscription_id = ((row['stripe_subscription_id'] or '') if row else '').strip()
        finally:
            conn.close()
    except Exception:
        log.exception('billing portal: DB error fetching user %s', public_user_id)

    if not subscription_id:
        flash('No subscription found on this account yet.', 'error')
        return redirect('/pricing')

    keys = _app()._stripe_keys()
    base_url = (getattr(config, 'BASE_URL', '') or '').strip() or request.host_url.rstrip('/')
    stripe.api_key = keys['secret_key']
    try:
        subscription = stripe.Subscription.retrieve(subscription_id)
        customer_id = subscription.get('customer') if hasattr(subscription, 'get') else None
        if isinstance(customer_id, dict):
            customer_id = customer_id.get('id')
        if not customer_id:
            raise RuntimeError(f'subscription {subscription_id} has no customer')
        portal = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f'{base_url}/pricing',
        )
        portal_url = portal.get('url') if hasattr(portal, 'get') else None
        if not portal_url:
            raise RuntimeError('Stripe billing portal session did not return a URL')
        log.info('billing portal: opened for user %s', public_user_id)
        return redirect(portal_url)
    except Exception as exc:
        log.exception('billing portal: failed for user %s', public_user_id)
        flash('We could not open the billing portal. Reply to any of our emails and we will fix it by hand.', 'error')
        return redirect('/pricing')


@bail_bond_ads_bp.route('/wanted/subscribe')
def wanted_subscribe():
    """Paid landing page for the active-warrant database."""
    from services.monetization.paywall import user_has_warrant_access
    from services.persons.warrants_public import warrant_paywall_preview_context

    public_user_id = session.get('public_user_id')
    is_logged_in = bool(public_user_id)
    already_subscribed = is_logged_in and user_has_warrant_access()
    next_url = request.args.get('next') or '/wanted'

    conn = get_db()
    try:
        preview_context = warrant_paywall_preview_context(conn, limit=3)
    finally:
        conn.close()

    return render_template(
        'wanted_subscribe.html',
        active_nav='wanted',
        page_title='Montana Active Warrants Subscription',
        meta_description='Subscribe for paid access to the Montana active warrant database, with wanted posters, mugshots when available, county filters, and daily updates.',
        canonical_url=f'{_app().BASE_URL}/wanted/subscribe',
        current_year=datetime.now().year,
        is_logged_in=is_logged_in,
        already_subscribed=already_subscribed,
        next_url=next_url,
        **preview_context,
    )


@bail_bond_ads_bp.route('/advertise/bail-bonds/control-panel/<token>/contract')
def advertise_bail_private_contract(token):
    safe_token = (token or '').strip()[:128]
    conn = get_db()
    try:
        order = conn.execute(
            '''
            SELECT business_name, onboarding_token
            FROM bail_ad_orders
            WHERE onboarding_token = ?
            LIMIT 1
            ''',
            (safe_token,),
        ).fetchone()
    finally:
        conn.close()
    if not order:
        return render_template('404.html'), 404

    contract_info = _bail_ad_contract_context(order['onboarding_token'])
    return render_template(
        'advertise_bail_contract.html',
        contract_info=contract_info,
        page_title=contract_info['title'],
        meta_description="Review the Montana Blotter Contract for advertising placements, billing, creative review, and cancellation terms.",
        canonical_url='',
        og_title=contract_info['title'],
        og_description=contract_info['summary'],
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@bail_bond_ads_bp.route('/advertise/bail-bonds/onboarding/<token>', methods=['GET', 'POST'])
@bail_bond_ads_bp.route('/advertise/bail-bonds/onboarding/<token>/', methods=['GET', 'POST'])
def advertise_bail_onboarding(token):
    safe_token = (token or '').strip()[:128]
    conn = get_db()
    _ensure_bail_ad_simulator_order_columns(conn)
    row = conn.execute(
        '''
        SELECT
            id,
            business_name,
            package_id,
            billing_cycle,
            status,
            county_targets,
            onboarding_token,
            simulator_logo_path,
            simulator_target_url,
            simulator_share_url,
            simulator_view
        FROM bail_ad_orders
        WHERE onboarding_token = ?
        LIMIT 1
        ''',
        (safe_token,),
    ).fetchone()
    if not row:
        conn.close()
        return render_template('404.html'), 404

    order = dict(row)
    package = _bail_ad_package_lookup().get(order.get('package_id') or '')
    order['package_name'] = (package.get('name') if package else '') or (order.get('package_id') or '').replace('_', ' ').title()
    simulator_preview = _bail_ad_simulator_preview(order)
    creative_row = conn.execute(
        '''
        SELECT id, headline, body_copy, cta_text, target_url, logo_path, status, review_notes, created_at, updated_at
        FROM bail_ad_creatives
        WHERE order_id = ?
        LIMIT 1
        ''',
        (order['id'],),
    ).fetchone()
    creative = dict(creative_row) if creative_row else None

    form_data = {
        'headline': (creative.get('headline') if creative else '') or '',
        'body_copy': (creative.get('body_copy') if creative else '') or '',
        'cta_text': (creative.get('cta_text') if creative else '') or '',
        'target_url': (creative.get('target_url') if creative else '') or simulator_preview.get('target_url') or '',
    }
    errors = []
    submitted = request.args.get('submitted') == '1'

    if request.method == 'POST':
        form_data = {
            'headline': (request.form.get('headline') or '').strip()[:120],
            'body_copy': (request.form.get('body_copy') or '').strip()[:800],
            'cta_text': (request.form.get('cta_text') or '').strip()[:50],
            'target_url': (request.form.get('target_url') or '').strip()[:300],
        }
        if not form_data['headline']:
            errors.append('Headline is required.')
        if not form_data['body_copy']:
            errors.append('Body copy is required.')
        if not form_data['target_url']:
            errors.append('Target URL is required.')

        logo_file = request.files.get('logo_file')
        logo_path = (creative.get('logo_path') if creative else '') or ''
        if logo_file and logo_file.filename:
            if not _bail_ad_allowed_asset(logo_file.filename):
                errors.append('Logo file must be PNG, JPG, JPEG, WEBP, or GIF.')
            else:
                file_name = secure_filename(logo_file.filename)
                token_prefix = safe_token[:12]
                storage_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{token_prefix}_{file_name}"
                ads_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'bail_ads')
                os.makedirs(ads_dir, exist_ok=True)
                abs_path = os.path.join(ads_dir, storage_name)
                logo_file.save(abs_path)
                logo_path = f"/uploads/bail_ads/{storage_name}"

        if not errors:
            conn.execute(
                '''
                INSERT INTO bail_ad_creatives (
                    order_id, headline, body_copy, cta_text, target_url, logo_path, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(order_id) DO UPDATE SET
                    headline = excluded.headline,
                    body_copy = excluded.body_copy,
                    cta_text = excluded.cta_text,
                    target_url = excluded.target_url,
                    logo_path = CASE WHEN excluded.logo_path != '' THEN excluded.logo_path ELSE bail_ad_creatives.logo_path END,
                    status = 'pending',
                    review_notes = NULL,
                    reviewed_by = NULL,
                    reviewed_at = NULL,
                    updated_at = datetime('now')
                ''',
                (
                    order['id'],
                    form_data['headline'],
                    form_data['body_copy'],
                    form_data['cta_text'],
                    form_data['target_url'],
                    logo_path,
                ),
            )
            conn.execute(
                '''
                UPDATE bail_ad_orders
                SET status = CASE
                        WHEN status = 'active' THEN 'active_pending_creative_review'
                        ELSE status
                    END,
                    updated_at = datetime('now')
                WHERE id = ?
                ''',
                (order['id'],),
            )
            conn.commit()
            conn.close()
            return redirect(url_for('.advertise_bail_onboarding', token=safe_token, submitted='1'))

    conn.commit()
    conn.close()
    return render_template(
        'advertise_bail_onboarding.html',
        order=order,
        creative=creative,
        simulator_preview=simulator_preview,
        contract_info=_bail_ad_contract_context(),
        form_data=form_data,
        form_errors=errors,
        submitted=submitted,
        active_nav='advertise',
        current_year=datetime.now().year,
    )