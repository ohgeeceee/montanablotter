"""
Recovery Center Advertising — helpers and Stripe event handler.
Public routes are added in Task 4.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime

import config

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
