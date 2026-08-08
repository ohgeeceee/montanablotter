"""
Sponsored Listings — paid bail bond / attorney placement on county jail booking pages.

Business model:
- Bail bond agencies pay $99/mo or $999/yr per county for a sponsored position
  on the sidebar of booking detail pages and the jail bookings page for their county.
- Criminal defense attorneys pay $79/mo or $799/yr for the same placement.
- Ads appear in a "Sponsored" sidebar section, filtered by county.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import stripe
from flask import (
    Blueprint,
    abort,
    redirect,
    render_template,
    request,
    session,
)

import config
from db import get_db

sponsored_bp = Blueprint('sponsored', __name__, url_prefix='/sponsored')

# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

_PRICING = {
    'bail_bond': {
        'label': 'Bail Bond Agency',
        'monthly_cents': 9900,
        'annual_cents': 99900,
        'monthly_label': '$99/mo',
        'annual_label': '$999/yr',
    },
    'attorney': {
        'label': 'Criminal Defense Attorney',
        'monthly_cents': 7900,
        'annual_cents': 79900,
        'monthly_label': '$79/mo',
        'annual_label': '$799/yr',
    },
}


def _app():
    from app import app
    return app


def _stripe_keys():
    m = _app()
    return {
        'secret_key': (
            getattr(config, 'STRIPE_SECRET_KEY', '')
            or m.config.get('STRIPE_SECRET_KEY', '')
            or ''
        ).strip(),
        'publishable_key': (
            getattr(config, 'STRIPE_PUBLISHABLE_KEY', '')
            or m.config.get('STRIPE_PUBLISHABLE_KEY', '')
            or ''
        ).strip(),
    }


# ---------------------------------------------------------------------------
# Public helpers (used by template rendering functions in other blueprints)
# ---------------------------------------------------------------------------

def get_sponsored_for_county(conn, county_slug: str) -> list[dict]:
    """Return all active sponsored listings for a given county."""
    rows = conn.execute('''
        SELECT id, business_name, business_type, phone, website, ad_text, logo_path
        FROM sponsored_listings
        WHERE county_slug = ?
          AND is_active = 1
          AND status = 'active'
          AND (expires_at IS NULL OR expires_at > datetime('now'))
        ORDER BY sort_order ASC, created_at DESC
    ''', (county_slug,)).fetchall()
    return [dict(r) for r in rows]


def record_impression(conn, listing_id: int) -> None:
    """Increment daily impression counter for a listing."""
    conn.execute('''
        INSERT INTO sponsored_listing_stats (listing_id, impression_date, impressions)
        VALUES (?, date('now'), 1)
        ON CONFLICT(listing_id, impression_date)
        DO UPDATE SET impressions = impressions + 1
    ''', (listing_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Admin: manage listings
# ---------------------------------------------------------------------------

@sponsored_bp.route('/admin/listings')
def admin_listings():
    # Simple admin check: require session admin_auth key
    if not session.get('admin_auth'):
        return redirect('/admin/login?next=/sponsored/admin/listings')

    conn = get_db()
    rows = conn.execute('''
        SELECT s.*, COALESCE(ssl.impressions, 0) AS total_impressions
        FROM sponsored_listings s
        LEFT JOIN (
            SELECT listing_id, SUM(impressions) AS impressions
            FROM sponsored_listing_stats
            GROUP BY listing_id
        ) ssl ON s.id = ssl.listing_id
        ORDER BY s.created_at DESC
    ''').fetchall()
    conn.close()
    return render_template(
        'admin_sponsored_listings.html',
        listings=[dict(r) for r in rows],
        pricing=_PRICING,
        current_year=datetime.now().year,
    )


@sponsored_bp.route('/admin/listings/<int:listing_id>/toggle', methods=['POST'])
def toggle_listing(listing_id):
    if not session.get('admin_auth'):
        abort(401)

    conn = get_db()
    listing = conn.execute(
        'SELECT id, is_active, status FROM sponsored_listings WHERE id = ?',
        (listing_id,),
    ).fetchone()
    if not listing:
        conn.close()
        abort(404)

    new_active = 0 if listing['is_active'] else 1
    new_status = 'active' if new_active else 'paused'
    conn.execute(
        'UPDATE sponsored_listings SET is_active = ?, status = ? WHERE id = ?',
        (new_active, new_status, listing_id),
    )
    conn.commit()
    conn.close()
    return redirect('/sponsored/admin/listings')


# ---------------------------------------------------------------------------
# Stripe event handler (called from webhook)
# ---------------------------------------------------------------------------

def apply_stripe_sponsored_event(conn, event: dict) -> None:
    """Process Stripe webhook events for sponsored listings."""
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}

    if (metadata.get('flow') or '').strip() != 'sponsored_listing':
        return

    session_id = (data_object.get('id') or '').strip()
    business_type = (metadata.get('business_type') or '').strip()
    county_slug = (metadata.get('county_slug') or '').strip()
    token = (metadata.get('token') or '').strip()

    if event_type == 'checkout.session.completed':
        customer_email = (data_object.get('customer_details') or {}).get('email', '')
        sub_id = (data_object.get('subscription') or '').strip()

        conn.execute('''
            UPDATE sponsored_listings
            SET email = COALESCE(NULLIF(email, '-'), ?),
                stripe_customer_id = ?,
                stripe_subscription_id = ?,
                status = 'active',
                activated_at = datetime('now')
            WHERE stripe_session_id = ? AND status = 'pending'
        ''', (customer_email or '', data_object.get('customer', ''), sub_id, session_id))
        conn.commit()

    elif event_type == 'customer.subscription.deleted':
        sub_id = (data_object.get('id') or '').strip()
        if sub_id:
            conn.execute('''
                UPDATE sponsored_listings
                SET is_active = 0, status = 'cancelled'
                WHERE stripe_subscription_id = ? AND status = 'active'
            ''', (sub_id,))
            conn.commit()

    elif event_type in ('checkout.session.expired', 'checkout.session.async_payment_failed'):
        conn.execute(
            'UPDATE sponsored_listings SET status = ? WHERE id = ? AND status = ?',
            ('expired', session_id, 'pending'),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Public checkout page
# ---------------------------------------------------------------------------

@sponsored_bp.route('/checkout', methods=['GET'])
def checkout_page():
    business_type = request.args.get('type', '').strip().lower()
    county_slug = request.args.get('county', '').strip().lower()
    billing = request.args.get('interval', 'monthly').strip().lower()

    error = None
    if business_type and business_type not in _PRICING:
        business_type = ''
        error = 'Invalid business type.'

    if billing not in ('monthly', 'annual'):
        billing = 'monthly'

    return render_template(
        'sponsored_checkout.html',
        error=error,
        pricing=_PRICING,
        selected_type=business_type,
        county_slug=county_slug,
        billing=billing,
        current_year=datetime.now().year,
    )


@sponsored_bp.route('/checkout/create', methods=['POST'])
def create_checkout():
    business_type = request.form.get('type', '').strip().lower()
    county_slug = request.form.get('county', '').strip().lower()
    billing = request.form.get('interval', 'monthly').strip().lower()
    business_name = request.form.get('business_name', '').strip()
    email = request.form.get('email', '').strip()
    phone = request.form.get('phone', '').strip()
    website = request.form.get('website', '').strip()

    if business_type not in _PRICING:
        return render_template(
            'sponsored_checkout.html',
            error='Select a valid business type.',
            pricing=_PRICING,
            selected_type=business_type,
            county_slug=county_slug,
            billing=billing,
        )

    if not county_slug or not business_name or not email:
        return render_template(
            'sponsored_checkout.html',
            error='Business name, email, and county are required.',
            pricing=_PRICING,
            selected_type=business_type,
            county_slug=county_slug,
            billing=billing,
        )

    if billing not in ('monthly', 'annual'):
        billing = 'monthly'

    plan = _PRICING[business_type]
    price_cents = plan[f'{billing}_cents']

    stripe.api_key = _stripe_keys()['secret_key']
    token = secrets.token_hex(16)

    try:
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f"{plan['label']} — {county_slug.title()} County",
                        'description': (
                            f"Sponsored listing on Montana Blotter's {county_slug.title()} County "
                            f"jail booking pages. Includes business name, phone, website, and ad text."
                        ),
                    },
                    'unit_amount': price_cents,
                    'recurring': {
                        'interval': 'month' if billing == 'monthly' else 'year',
                    },
                },
                'quantity': 1,
            }],
            metadata={
                'flow': 'sponsored_listing',
                'business_type': business_type,
                'county_slug': county_slug,
                'token': token,
            },
            success_url=f"{_app().config.get('BASE_URL', 'https://montanablotter.com')}/sponsored/success?session_id={{CHECKOUT_SESSION_ID}}&token={token}",
            cancel_url=f"{_app().config.get('BASE_URL', 'https://montanablotter.com')}/sponsored/checkout?type={business_type}&county={county_slug}",
        )

        conn = get_db()
        conn.execute('''
            INSERT INTO sponsored_listings (business_name, business_type, email, phone, website,
                                            county_slug, stripe_session_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        ''', (business_name, business_type, email or '-', phone or '', website or '',
              county_slug, checkout_session['id']))
        conn.commit()
        conn.close()

        return redirect(checkout_session['url'], 303)

    except Exception as e:
        return render_template(
            'sponsored_checkout.html',
            error=f"Checkout error: {e}",
            pricing=_PRICING,
            selected_type=business_type,
            county_slug=county_slug,
            billing=billing,
        )


@sponsored_bp.route('/success')
def success():
    session_id = request.args.get('session_id', '').strip()
    token = request.args.get('token', '').strip()

    conn = get_db()
    listing = conn.execute(
        'SELECT id, business_name, county_slug, status FROM sponsored_listings WHERE stripe_session_id = ?',
        (session_id,),
    ).fetchone()
    county_slug = listing['county_slug'] if listing else ''
    conn.close()

    if listing and listing['status'] == 'active':
        return render_template(
            'sponsored_success.html',
            title='Your Listing is Live!',
            message=f"Your sponsored listing for {county_slug.title()} County is now active and visible on our jail booking pages.",
        )

    return render_template(
        'sponsored_success.html',
        title='Payment Received',
        message=(
            "Your payment was received. Your sponsored listing will appear on "
            f"{county_slug.title()} County's jail booking pages shortly after review."
        ),
    )