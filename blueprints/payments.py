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

# Warrant Access subscription — created 2026-05-31
_WARRANT_MONTHLY_PRICE_ID = 'price_1Td9jmGL8T8btZcu5OXZzr9g'
_WARRANT_TRIAL_FEE_PRICE_ID = 'price_1Td9jmGL8T8btZcumpWLuzLf'
_WARRANT_PAYMENT_LINK = 'https://buy.stripe.com/14A4gzajyeoAcDU4qh8EM03'


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


def _warrant_access_price_ids():
    """Return the active Stripe price IDs for warrant access."""
    return {
        'weekly': (getattr(config, 'WARRANT_WEEKLY_PRICE_ID', '') or '').strip(),
        'monthly': (getattr(config, 'WARRANT_MONTHLY_PRICE_ID', '') or _WARRANT_MONTHLY_PRICE_ID or '').strip(),
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


# ---------------------------------------------------------------------------
# Advertise / bail-bonds routes
# ---------------------------------------------------------------------------

@payments_bp.route('/advertise')
@payments_bp.route('/advertise/')
def advertise_redirect():
    return redirect(url_for('.advertise_bail_bonds'))


@payments_bp.route('/advertise/bail-bonds', methods=['GET', 'POST'])
@payments_bp.route('/advertise/bail-bonds/', methods=['GET', 'POST'])
def advertise_bail_bonds():
    m = _app()
    package_options = m._bail_ad_public_packages()
    package_ids = {pkg['id'] for pkg in package_options}
    pricing_cards = m._bail_ad_pricing_cards(package_options)
    help_contact = m._bail_help_contact()
    contract_info = m._bail_ad_contract_context()

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
            'package_interest': m._normalize_bail_ad_package_id((request.form.get('package_interest') or '').strip()[:32]),
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

        budget_cents = m._parse_budget_cents(form_data['monthly_budget'])
        source = (request.form.get('source') or request.args.get('source') or 'bail_ad_page').strip()[:80]
        if not errors:
            ip_hash = hashlib.sha256((m._client_ip() or '').encode()).hexdigest()[:16]
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
        'initialImageUrl': m._safe_bail_ad_simulator_image_url(request.args.get('logo_url') or request.args.get('logo') or ''),
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
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@payments_bp.route('/advertise/bail-bonds/checkout', methods=['GET', 'POST'])
@payments_bp.route('/advertise/bail-bonds/checkout/', methods=['GET', 'POST'])
def advertise_bail_bonds_checkout():
    m = _app()
    conn = get_db()
    m._ensure_bail_ad_simulator_order_columns(conn)
    conn.commit()
    conn.close()
    package_map = m._bail_ad_package_lookup()
    package_options = m._bail_ad_public_packages()
    package_ids = {pkg['id'] for pkg in package_options}
    addon_options = m._bail_ad_addons()
    addon_lookup = m._bail_ad_addon_lookup()
    contract_info = m._bail_ad_contract_context()
    if not m._bail_ad_checkout_ready():
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
    prefill_package = m._normalize_bail_ad_package_id(request.values.get('package'))
    if not prefill_package and simulator_view_prefill in {'banner', 'sidebar'}:
        prefill_package = m._bail_ad_package_id_for_simulator_view(simulator_view_prefill)
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
        'simulator_logo_path': m._safe_bail_ad_simulator_image_url(request.values.get('simulator_logo_path') or request.values.get('logo_url') or request.values.get('logo') or ''),
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
            'package_id': m._normalize_bail_ad_package_id((request.form.get('package_id') or '').strip()[:32]),
            'billing_cycle': (request.form.get('billing_cycle') or 'monthly').strip().lower()[:16],
            'source': (request.form.get('source') or 'bail_ad_checkout').strip()[:80],
            'add_on_ids': m._parse_addon_ids(request.form.getlist('add_on_ids')),
            'simulator_logo_path': m._safe_bail_ad_simulator_image_url(request.form.get('simulator_logo_path') or ''),
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
        parsed_counties = m._parse_county_targets(form_data['county_targets'])
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
            base_amount_cents = m._bail_ad_price_cents(
                form_data['package_id'],
                form_data['billing_cycle'],
                parsed_counties,
            ) or 0
            addon_amount_cents = m._bail_ad_addon_total_cents(form_data['add_on_ids'], form_data['billing_cycle'])
            amount_cents = base_amount_cents + addon_amount_cents
            if amount_cents <= 0:
                errors.append('Unable to price selected package.')
            else:
                stripe_keys = m._stripe_keys()
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
                    'success_url': f'{m.BASE_URL}/advertise/bail-bonds/checkout/success?session_id={{CHECKOUT_SESSION_ID}}',
                    'cancel_url': f'{m.BASE_URL}/advertise/bail-bonds/checkout/cancel',
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


@payments_bp.route('/advertise/bail-bonds/checkout/success')
@payments_bp.route('/advertise/bail-bonds/checkout/success/')
def advertise_bail_checkout_success():
    m = _app()
    session_id = (request.args.get('session_id') or '').strip()
    order = None
    package_map = m._bail_ad_package_lookup()
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
        contract_info=m._bail_ad_contract_context(),
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@payments_bp.route('/advertise/bail-bonds/control-panel/<token>')
@payments_bp.route('/advertise/bail-bonds/control-panel/<token>/')
def advertise_bail_control_panel(token):
    m = _app()
    safe_token = (token or '').strip()[:128]
    session_id = (request.args.get('session_id') or '').strip()[:255]
    welcome = request.args.get('welcome') == '1'
    conn = get_db()
    context = m._bail_ad_control_panel_context(conn, safe_token, session_id=session_id)
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
        contract_info=m._bail_ad_contract_context(order['onboarding_token']),
        page_title=f"{order['business_name']} Control Panel",
        meta_description='Private control panel for bail bonds advertisers after payment.',
        canonical_url='',
        og_title=f"{order['business_name']} Control Panel",
        og_description='Track creative approval, county coverage, performance, and renewal timing in one place.',
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@payments_bp.route('/advertise/bail-bonds/checkout/cancel')
@payments_bp.route('/advertise/bail-bonds/checkout/cancel/')
def advertise_bail_checkout_cancel():
    m = _app()
    return render_template(
        'advertise_bail_checkout_cancel.html',
        contract_info=m._bail_ad_contract_context(),
        active_nav='advertise',
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Subscription checkout routes
# ---------------------------------------------------------------------------

@payments_bp.route('/checkout/subscription', methods=['POST'])
def checkout_subscription():
    m = _app()
    keys = m._stripe_keys()
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
            success_url=f"{m.BASE_URL}/checkout/subscription/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{m.BASE_URL}/checkout/subscription/cancel",
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


@payments_bp.route('/checkout/subscription/success')
def checkout_subscription_success():
    session_id = (request.args.get('session_id') or '').strip()
    return render_template(
        'checkout_subscription_success.html',
        session_id=session_id,
        active_nav='pricing',
        current_year=datetime.now().year,
    )


@payments_bp.route('/checkout/subscription/cancel')
def checkout_subscription_cancel():
    return render_template(
        'checkout_subscription_cancel.html',
        active_nav='pricing',
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Warrant Access checkout — $1/week or $8/month recurring subscription
# ---------------------------------------------------------------------------

@payments_bp.route('/checkout/warrant-access', methods=['GET', 'POST'])
def checkout_warrant_access():
    from urllib.parse import urlencode
    m = _app()
    public_user_id = session.get('public_user_id')
    log.info('warrant-access checkout: public_user_id=%s', public_user_id)
    if not public_user_id:
        log.warning('warrant-access checkout: no session — redirecting to login')
        flash('Please log in or create an account to subscribe.', 'info')
        return redirect('/login?next=/wanted/subscribe')
    email = None
    try:
        conn = get_db()
        row = conn.execute('SELECT email FROM public_users WHERE id = ?', (int(public_user_id),)).fetchone()
        conn.close()
        if row:
            email = row['email'] or None
    except Exception:
        log.exception('warrant-access checkout: DB error fetching user %s', public_user_id)

    plan = (request.args.get('plan') or 'monthly').strip().lower()
    if plan not in {'weekly', 'monthly'}:
        flash('Please select a valid warrant access plan.', 'error')
        return redirect('/wanted/subscribe')

    keys = m._stripe_keys()
    base_url = (getattr(config, 'BASE_URL', '') or '').strip() or request.host_url.rstrip('/')
    stripe.api_key = keys['secret_key']
    price_ids = _warrant_access_price_ids()

    try:
        price_id = price_ids.get(plan)
        if not price_id:
            raise RuntimeError(f'Warrant access {plan} Stripe price ID is not configured')
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            client_reference_id=str(public_user_id),
            customer_email=email or None,
            line_items=[
                {
                    'price': price_id,
                    'quantity': 1,
                },
            ],
            success_url=f"{base_url}/checkout/warrant-access/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/wanted/subscribe?canceled=1",
            subscription_data={
                'metadata': {
                    'flow': 'warrant_access',
                    'public_user_id': str(public_user_id),
                    'plan': plan,
                },
            },
            metadata={
                'flow': 'warrant_access',
                'public_user_id': str(public_user_id),
                'plan': plan,
            },
        )
        checkout_url = _checkout_redirect_url(checkout_session)
        if checkout_url:
            log.info('warrant-access checkout: created Stripe Checkout Session for user %s plan=%s', public_user_id, plan)
            return redirect(checkout_url)
        raise RuntimeError('Stripe checkout session did not return a URL')
    except Exception:
        # Keep the existing Payment Link path as a fallback if Checkout Session
        # creation is unavailable in this environment.
        params = {'client_reference_id': str(public_user_id)}
        if email:
            params['prefilled_email'] = email
        dest = f"{_WARRANT_PAYMENT_LINK}?{urlencode(params)}"
        log.exception('warrant-access checkout: falling back to payment link for user %s', public_user_id)
        return redirect(dest)


@payments_bp.route('/checkout/warrant-access/success')
def checkout_warrant_access_success():
    return render_template(
        'checkout_warrant_success.html',
        active_nav='wanted',
        page_title='Warrant Access Activated',
        current_year=datetime.now().year,
    )


@payments_bp.route('/wanted/subscribe')
def wanted_subscribe():
    from services.monetization.paywall import (
        get_ad_unlock_remaining_seconds,
        user_has_warrant_access,
    )
    public_user_id = session.get('public_user_id')
    is_logged_in = bool(public_user_id)
    already_subscribed = is_logged_in and user_has_warrant_access()
    ad_unlock_remaining_seconds = (
        get_ad_unlock_remaining_seconds(int(public_user_id)) if public_user_id else 0
    )
    next_url = request.args.get('next', '/wanted/subscribe')
    return render_template(
        'wanted_subscribe.html',
        active_nav='wanted',
        page_title='Subscribe — Montana Active Warrants',
        meta_description='Get full access to Montana active warrant records for $1 trial week, then $7/month.',
        canonical_url=f'{_app().BASE_URL}/wanted/subscribe',
        current_year=datetime.now().year,
        is_logged_in=is_logged_in,
        already_subscribed=already_subscribed,
        next_url=next_url,
        ad_unlock_active=ad_unlock_remaining_seconds > 0,
        ad_unlock_remaining_seconds=ad_unlock_remaining_seconds,
    )


@payments_bp.route('/advertise/bail-bonds/control-panel/<token>/contract')
def advertise_bail_private_contract(token):
    m = _app()
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

    contract_info = m._bail_ad_contract_context(order['onboarding_token'])
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


@payments_bp.route('/advertise/bail-bonds/onboarding/<token>', methods=['GET', 'POST'])
@payments_bp.route('/advertise/bail-bonds/onboarding/<token>/', methods=['GET', 'POST'])
def advertise_bail_onboarding(token):
    m = _app()
    safe_token = (token or '').strip()[:128]
    conn = get_db()
    m._ensure_bail_ad_simulator_order_columns(conn)
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
    package = m._bail_ad_package_lookup().get(order.get('package_id') or '')
    order['package_name'] = (package.get('name') if package else '') or (order.get('package_id') or '').replace('_', ' ').title()
    simulator_preview = m._bail_ad_simulator_preview(order)
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
            if not m._bail_ad_allowed_asset(logo_file.filename):
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
        contract_info=m._bail_ad_contract_context(),
        form_data=form_data,
        form_errors=errors,
        submitted=submitted,
        active_nav='advertise',
        current_year=datetime.now().year,
    )
