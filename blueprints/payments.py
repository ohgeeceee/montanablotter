from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
from datetime import datetime

import stripe
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

log = logging.getLogger(__name__)

import config
from db import get_db
from init_db import ensure_advertise_sales_lead_schema

# Subscription tier pricing — created 2026-08-05
_PLUS_MONTHLY_PRICE_ID = 'price_1U1ASIGL8T8btZcuzB9kv1Vx'
_PLUS_ANNUAL_PRICE_ID = 'price_1U1ASIGL8T8btZcuVdB0VRSx'
_PRO_MONTHLY_PRICE_ID = 'price_1U1ASIGL8T8btZcubZ3yUd2B'
_PRO_ANNUAL_PRICE_ID = 'price_1U1ASIGL8T8btZcu6xdYIHLR'

_PLUS_PAYMENT_LINKS = {
    'monthly': 'https://buy.stripe.com/eVq6oH3Va1BOfQ6aOF8EM0e',
    'annual': 'https://buy.stripe.com/6oU9ATezO94g8nE4qh8EM0f',
}

_PRO_PAYMENT_LINKS = {
    'monthly': 'https://buy.stripe.com/fZufZh1N2bco9rI9KB8EM0g',
    'annual': 'https://buy.stripe.com/3cI00jdvK0xK9rI9KB8EM0h',
}

PLAN_PRICE_IDS = {
    ('plus', 'monthly'): _PLUS_MONTHLY_PRICE_ID,
    ('plus', 'annual'): _PLUS_ANNUAL_PRICE_ID,
    ('pro', 'monthly'): _PRO_MONTHLY_PRICE_ID,
    ('pro', 'annual'): _PRO_ANNUAL_PRICE_ID,
}

PLAN_PAYMENT_LINKS = {
    ('plus', 'monthly'): _PLUS_PAYMENT_LINKS['monthly'],
    ('plus', 'annual'): _PLUS_PAYMENT_LINKS['annual'],
    ('pro', 'monthly'): _PRO_PAYMENT_LINKS['monthly'],
    ('pro', 'annual'): _PRO_PAYMENT_LINKS['annual'],
}

VALID_PLANS = {'plus', 'pro'}
VALID_INTERVALS = {'monthly', 'annual'}


payments_bp = Blueprint('payments', __name__)


def register_payments_blueprint(app):
    """Register the payments blueprint onto the Flask app."""
    app.register_blueprint(payments_bp)


# ---------------------------------------------------------------------------
# Supporter tier ($1/mo) — checkout + success/cancel routes
# ---------------------------------------------------------------------------

@payments_bp.route('/supporter/checkout', methods=['POST'])
def supporter_checkout():
    """Create a Stripe Checkout session for the $1/month supporter plan."""
    from flask import jsonify, request
    import config

    payload = request.get_json(silent=True) or {}
    email = (payload.get('email') or '').strip().lower()
    if not email or '@' not in email:
        return jsonify({'error': 'Valid email is required.'}), 400

    price_id = config.STRIPE_SUPPORTER_PRICE_ID
    if not price_id:
        return jsonify({'error': 'Supporter plan not available.'}), 503

    stripe.api_key = config.STRIPE_SECRET_KEY
    base_url = 'https://montanablotter.com'

    session = stripe.checkout.Session.create(
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        customer_email=email,
        success_url=f'{base_url}/supporter/success?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=f'{base_url}/supporter/cancel',
        metadata={'tier': 'supporter', 'email': email},
        subscription_data={
            'metadata': {'tier': 'supporter', 'email': email},
        },
    )
    return jsonify({'checkout_url': session.url})


@payments_bp.route('/supporter/success')
def supporter_success():
    """Stripe checkout success redirect for supporter tier."""
    from flask import render_template
    return render_template('checkout_subscription_success.html',
                           plan='supporter',
                           plan_label='Supporter ($1/month)')


@payments_bp.route('/supporter/cancel')
def supporter_cancel():
    """Stripe checkout cancel redirect for supporter tier."""
    from flask import render_template
    return render_template('checkout_subscription_cancel.html',
                           plan='supporter')


def _app():
    import app as _app_module
    return _app_module


def _plan_price_ids(plan: str) -> dict[str, str]:
    """Return the active Stripe price IDs for a given subscription plan."""
    return {
        'monthly': (getattr(config, 'PLAN_MONTHLY_PRICE_IDS', {}).get(plan, '') or
                     PLAN_PRICE_IDS.get((plan, 'monthly'), '')),
        'annual': (getattr(config, 'PLAN_ANNUAL_PRICE_IDS', {}).get(plan, '') or
                    PLAN_PRICE_IDS.get((plan, 'annual'), '')),
    }


def build_donation_checkout_payload(payload):
    m = _app()

    mode = (payload.get('mode') or 'one_time').strip().lower()
    if mode not in {'one_time', 'monthly'}:
        return {'error': 'Invalid donation mode', 'status': 400}

    try:
        amount_cents = int(payload.get('amount_cents'))
    except (TypeError, ValueError):
        return {'error': 'Invalid donation amount', 'status': 400}

    min_cents = m._donation_min_cents()
    max_cents = m._donation_max_cents()
    if amount_cents < min_cents or amount_cents > max_cents:
        return {'error': 'Donation amount out of allowed range', 'status': 400}

    public_user = m._get_public_user()
    source = (payload.get('source') or 'donate_page').strip()[:80]
    donor_name = (payload.get('name') or '').strip()[:120]
    email = (payload.get('email') or '').strip().lower()
    if not donor_name and public_user:
        donor_name = (public_user.display_name or '').strip()[:120]
    if not email and public_user:
        email = (public_user.email or '').strip().lower()
    if email and '@' not in email:
        email = ''

    return {
        'mode': mode,
        'amount_cents': amount_cents,
        'source': source,
        'donor_name': donor_name,
        'email': email,
        'currency': m._donation_currency(),
        'public_user_id': str(public_user.id) if public_user else '',
        'feature_gate': 'bondsman_command_center' if m._is_bondsman_subscription_source(source) else '',
    }


def create_donation_checkout_session(parsed_payload):
    m = _app()
    stripe_keys = m._stripe_keys()
    stripe.api_key = stripe_keys['secret_key']

    line_item = {
        'price_data': {
            'currency': parsed_payload['currency'],
            'product_data': {'name': 'Montana Blotter Donation'},
            'unit_amount': parsed_payload['amount_cents'],
        },
        'quantity': 1,
    }
    if parsed_payload['mode'] == 'monthly':
        line_item['price_data']['recurring'] = {'interval': 'month'}

    checkout_params = {
        'mode': 'subscription' if parsed_payload['mode'] == 'monthly' else 'payment',
        'line_items': [line_item],
        'success_url': f'{m.BASE_URL}/donate/success?session_id={{CHECKOUT_SESSION_ID}}',
        'cancel_url': f'{m.BASE_URL}/donate/cancel',
        'billing_address_collection': 'auto',
        'allow_promotion_codes': True,
        'metadata': {
            'source': parsed_payload['source'],
            'mode': parsed_payload['mode'],
            'amount_cents': str(parsed_payload['amount_cents']),
            'donor_name': parsed_payload['donor_name'],
            'public_user_id': parsed_payload['public_user_id'],
            'feature_gate': parsed_payload['feature_gate'],
        },
    }
    if parsed_payload['email']:
        checkout_params['customer_email'] = parsed_payload['email']

    return stripe.checkout.Session.create(**checkout_params)


def persist_donation_checkout(parsed_payload, checkout_session):
    m = _app()
    checkout_session_id = _checkout_value(checkout_session, 'id')
    checkout_payment_intent = _checkout_value(checkout_session, 'payment_intent')
    checkout_subscription = _checkout_value(checkout_session, 'subscription')

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO donations (
                provider, mode, status, amount_cents, currency, email_hash, donor_name,
                source, provider_session_id, provider_payment_intent_id, provider_subscription_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_session_id) DO UPDATE SET
                mode = excluded.mode,
                status = excluded.status,
                amount_cents = excluded.amount_cents,
                currency = excluded.currency,
                email_hash = excluded.email_hash,
                donor_name = excluded.donor_name,
                source = excluded.source,
                provider_payment_intent_id = excluded.provider_payment_intent_id,
                provider_subscription_id = excluded.provider_subscription_id,
                updated_at = datetime('now')
            ''',
            (
                'stripe',
                parsed_payload['mode'],
                'pending',
                parsed_payload['amount_cents'],
                parsed_payload['currency'],
                m._donation_email_hash(parsed_payload['email']),
                parsed_payload['donor_name'],
                parsed_payload['source'],
                checkout_session_id,
                checkout_payment_intent,
                checkout_subscription,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    m._record_donation_event(
        'checkout_start',
        source=parsed_payload['source'],
        page_path='/donate',
        amount_cents=parsed_payload['amount_cents'],
    )


def _checkout_value(checkout_session, key, default=''):
    if isinstance(checkout_session, dict):
        return checkout_session.get(key, default)
    return getattr(checkout_session, key, default)


def _checkout_redirect_url(checkout_session):
    return (_checkout_value(checkout_session, 'url', '') or '').strip()


# ---------------------------------------------------------------------------
# Donate routes
# ---------------------------------------------------------------------------

@payments_bp.route('/donate')
def donate():
    m = _app()
    keys = m._stripe_keys()
    source = (request.args.get('source') or '').strip()[:80]
    source = source or 'donate_page'
    donation_campaign = m._donation_campaign_context(request.args.get('campaign'))
    return render_template(
        'donate.html',
        donations_enabled=m._donations_enabled(),
        stripe_ready=m._stripe_ready_for_checkout(),
        stripe_publishable_key=keys['publishable_key'],
        suggested_amounts_cents=m._allowed_donation_amounts(),
        donation_min_cents=m._donation_min_cents(),
        donation_max_cents=m._donation_max_cents(),
        donation_currency=m._donation_currency(),
        donate_source=source,
        donation_campaign=donation_campaign,
        active_nav='donate',
        current_year=datetime.now().year,
    )


@payments_bp.route('/donate/checkout', methods=['POST'])
def donate_checkout():
    m = _app()
    if not m._donations_enabled():
        flash('Donations are currently unavailable.', 'error')
        return redirect(url_for('.donate', source='donate_page'))
    if not m._stripe_ready_for_checkout():
        flash('Payment provider is not configured yet.', 'error')
        return redirect(url_for('.donate', source='donate_page'))

    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict() if request.form else {}

    parsed = build_donation_checkout_payload(payload)
    if 'error' in parsed:
        flash(parsed['error'], 'error')
        return redirect(url_for('.donate', source=payload.get('source') or 'donate_page'))

    try:
        checkout_session = create_donation_checkout_session(parsed)
    except Exception:
        flash('Unable to start secure checkout right now. Please try again.', 'error')
        return redirect(url_for('.donate', source=parsed['source']))

    checkout_url = _checkout_value(checkout_session, 'url', '')
    if not checkout_url:
        flash('Unable to start secure checkout right now. Please try again.', 'error')
        return redirect(url_for('.donate', source=parsed['source']))

    persist_donation_checkout(parsed, checkout_session)
    return redirect(checkout_url)


@payments_bp.route('/donate/success')
def donate_success():
    session_id = (request.args.get('session_id') or '').strip()
    donation = None
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
                mode,
                status,
                amount_cents,
                currency,
                source,
                provider_payment_intent_id,
                provider_subscription_id,
                created_at
            FROM donations
            WHERE provider = 'stripe' AND provider_session_id = ?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (session_id,),
        ).fetchone()
        conn.close()
        donation = dict(row) if row else None

    return render_template(
        'donate_success.html',
        donation=donation,
        session_id=session_id,
        support_email=support_email,
        active_nav='donate',
        current_year=datetime.now().year,
    )


@payments_bp.route('/donate/cancel')
def donate_cancel():
    return render_template(
        'donate_cancel.html',
        active_nav='donate',
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------

@payments_bp.route('/webhooks/stripe', methods=['POST'])
def stripe_webhook():
    m = _app()
    if not m._stripe_ready_for_webhooks():
        return ('', 503)

    payload = request.get_data(cache=False)
    signature = request.headers.get('Stripe-Signature', '')
    keys = m._stripe_keys()
    stripe.api_key = keys['secret_key']

    # Try primary secret first, then warrant-specific secret
    warrant_secret = (getattr(config, 'STRIPE_WARRANT_WEBHOOK_SECRET', '') or '').strip()
    event = None
    for secret in filter(None, [keys['webhook_secret'], warrant_secret]):
        try:
            event = stripe.Webhook.construct_event(payload, signature, secret)
            break
        except Exception:
            continue
    if event is None:
        return ('', 400)

    event_id = (event.get('id') or '').strip()
    event_type = (event.get('type') or '').strip()
    if not event_id or not event_type:
        return ('', 400)

    payload_text = payload.decode('utf-8', errors='replace')
    conn = get_db()
    try:
        conn.execute(
            '''
            INSERT INTO payment_webhook_events (provider, event_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            ''',
            ('stripe', event_id, event_type, payload_text),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return ('', 200)

    webhook_ip_hash = hashlib.sha256((m._client_ip() or '').encode()).hexdigest()[:16]
    webhook_referrer = (request.referrer or '')[:500]
    try:
        m._apply_stripe_bail_ad_event(conn, event)
        from blueprints.recovery_ads import apply_stripe_recovery_ad_event
        apply_stripe_recovery_ad_event(conn, event)
        from blueprints.bail_bond_ads import apply_stripe_bail_ad_event
        apply_stripe_bail_ad_event(conn, event)
        m._apply_stripe_event(
            conn,
            event,
            event_source='/webhooks/stripe',
            event_ip_hash=webhook_ip_hash,
            event_referrer=webhook_referrer,
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
    except Exception as exc:
        conn.execute(
            '''
            UPDATE payment_webhook_events
            SET processed = 0, error = ?, processed_at = datetime('now')
            WHERE event_id = ?
            ''',
            (str(exc)[:500], event_id),
        )
        conn.commit()
        conn.close()
        return ('', 500)

    conn.close()
    return ('', 200)


