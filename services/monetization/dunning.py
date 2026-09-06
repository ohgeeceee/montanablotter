"""Failed-payment recovery for reader subscriptions.

Stripe retries a failed invoice automatically, but without a customer-facing
recovery path the subscriber never learns their card was declined and cannot
update it. Montana Blotter had no billing portal and did not subscribe to
``invoice.payment_failed``, so ``past_due`` subscriptions silently decayed into
``canceled`` with reason ``payment_failed``.

This module owns the notification side. Access-state transitions live in
``app._apply_subscription_stripe_event``; the self-service card-update page is
``/billing`` in ``blueprints.payments``.
"""
from __future__ import annotations

import json
import logging
import smtplib
import sqlite3
from html import escape
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config

logger = logging.getLogger(__name__)

BILLING_PATH = '/billing'

PLAN_LABELS = {
    'plus': 'Plus',
    'pro': 'Pro',
    'warrant_access': 'Warrant Access',
    'free': 'Free',
}


def base_url() -> str:
    return (getattr(config, 'BASE_URL', '') or 'https://montanablotter.com').rstrip('/')


def billing_url() -> str:
    return f'{base_url()}{BILLING_PATH}'


def plan_label(plan: str) -> str:
    return PLAN_LABELS.get((plan or '').strip().lower(), 'your subscription')


def _smtp_send(to_addr: str, subject: str, html_body: str) -> bool:
    """Mirrors services.alerts.dispatcher._send_email."""
    smtp_user = getattr(config, 'SMTP_USER', None) or getattr(config, 'EMAIL_USER', '')
    smtp_password = getattr(config, 'SMTP_PASSWORD', None) or getattr(config, 'EMAIL_PASSWORD', '')
    server = getattr(config, 'SMTP_SERVER', '')
    port = int(getattr(config, 'SMTP_PORT', 587) or 587)
    if not server or not smtp_user:
        logger.warning('dunning: SMTP not configured, skipping send to %s', to_addr)
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f'Montana Blotter <{smtp_user}>'
    msg['To'] = to_addr
    msg.attach(MIMEText(html_body, 'html'))
    try:
        with smtplib.SMTP(server, port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.sendmail(smtp_user, to_addr, msg.as_string())
        return True
    except Exception as exc:
        logger.warning('dunning: email to %s failed: %s', to_addr, exc)
        return False


def _amount_str(invoice: dict) -> str:
    cents = invoice.get('amount_due')
    if cents is None:
        return 'the past-due balance'
    currency = (invoice.get('currency') or 'usd').upper()
    symbol = '$' if currency == 'USD' else f'{currency} '
    return f'{symbol}{cents / 100:.2f}'


def payment_failed_html(display_name: str, plan: str, amount: str, url: str) -> str:
    greeting = escape(display_name or 'there')
    plan, amount, url = escape(plan), escape(amount), escape(url, quote=True)
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Georgia,serif;max-width:560px;margin:0 auto;padding:24px;color:#1E1B18;background:#fff;">
  <div style="border-bottom:3px solid #1E1B18;padding-bottom:12px;margin-bottom:20px;">
    <p style="font-family:'Courier New',monospace;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:0.15em;color:#888;margin:0;">Montana Blotter &middot; Billing</p>
    <h1 style="font-size:22px;font-weight:bold;margin:8px 0 0;">We couldn't process your last payment</h1>
  </div>
  <p style="font-size:15px;line-height:1.6;">Hi {greeting},</p>
  <p style="font-size:15px;line-height:1.6;">
    The payment of <strong>{amount}</strong> for your <strong>{plan}</strong> subscription
    was declined by your card issuer. Your access is still switched on for now and we
    will automatically retry the charge, but it will lapse if the retry fails too.
  </p>
  <p style="font-size:15px;line-height:1.6;">
    This is usually an expired card or a bank declining an unfamiliar charge. Updating
    your payment method takes about thirty seconds and keeps your access uninterrupted.
  </p>
  <div style="margin:24px 0;">
    <a href="{url}" style="display:inline-block;background:#1E1B18;color:#fff;padding:12px 24px;text-decoration:none;font-weight:bold;font-size:14px;border-radius:6px;">
      Update payment method &rarr;
    </a>
  </div>
  <p style="font-size:14px;line-height:1.6;color:#555;">
    If the link doesn't work, sign in at <a href="{base_url()}/login">{base_url()}/login</a>
    and open <a href="{url}">Billing</a> from your account.
  </p>
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
  <p style="font-size:13px;line-height:1.6;color:#555;">
    Reply to this email if something looks wrong &mdash; a person reads it, and we would
    much rather fix a billing problem than lose you over one.
  </p>
  <p style="font-size:11px;color:#aaa;font-family:'Courier New',monospace;">
    You're receiving this because you have an active Montana Blotter subscription.
  </p>
</body>
</html>
""".strip()


def access_ended_html(display_name: str, plan: str, url: str) -> str:
    greeting = escape(display_name or 'there')
    plan, url = escape(plan), escape(url, quote=True)
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:Georgia,serif;max-width:560px;margin:0 auto;padding:24px;color:#1E1B18;background:#fff;">
  <div style="border-bottom:3px solid #1E1B18;padding-bottom:12px;margin-bottom:20px;">
    <p style="font-family:'Courier New',monospace;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:0.15em;color:#888;margin:0;">Montana Blotter &middot; Billing</p>
    <h1 style="font-size:22px;font-weight:bold;margin:8px 0 0;">Your {plan} access has lapsed</h1>
  </div>
  <p style="font-size:15px;line-height:1.6;">Hi {greeting},</p>
  <p style="font-size:15px;line-height:1.6;">
    Your <strong>{plan}</strong> subscription has now ended. Nothing was lost &mdash;
    your saved searches and alert settings are still here.
  </p>
  <div style="margin:24px 0;">
    <a href="{url}" style="display:inline-block;background:#1E1B18;color:#fff;padding:12px 24px;text-decoration:none;font-weight:bold;font-size:14px;border-radius:6px;">
      Restart access &rarr;
    </a>
  </div>
  <p style="font-size:11px;color:#aaa;font-family:'Courier New',monospace;">
    You're receiving this because you had an active Montana Blotter subscription.
  </p>
</body>
</html>
""".strip()


def _subscriber_for(conn: sqlite3.Connection, subscription_id: str):
    try:
        return conn.execute(
            '''SELECT id, email, display_name, subscriber_plan
               FROM public_users WHERE stripe_subscription_id = ? LIMIT 1''',
            (subscription_id,),
        ).fetchone()
    except sqlite3.Error:
        logger.exception('dunning: lookup failed for %s', subscription_id)
        return None


def _record_event(conn: sqlite3.Connection, user_id: int, event_id: str,
                  event_type: str, payload: dict) -> None:
    try:
        conn.execute(
            '''INSERT INTO subscription_events (user_id, stripe_event_id, event_type, payload_json)
               VALUES (?, ?, ?, ?)''',
            (int(user_id), event_id, event_type, json.dumps(payload)[:20000]),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception('dunning: could not record %s for user %s', event_type, user_id)


def notify_payment_failed(conn: sqlite3.Connection, subscription_id: str,
                          invoice: dict, event_id: str = '') -> dict:
    """Email a card-update link after a declined subscription invoice.

    Only the first declined attempt emails; Stripe's later retries must not
    turn into a drip of duplicate notices.
    """
    result = {'sent': False, 'skipped': '', 'email': ''}
    if not subscription_id:
        result['skipped'] = 'no_subscription_id'
        return result

    attempt = invoice.get('attempt_count')
    if isinstance(attempt, int) and attempt > 1:
        result['skipped'] = f'retry_attempt_{attempt}'
        return result

    row = _subscriber_for(conn, subscription_id)
    if row is None:
        result['skipped'] = 'subscriber_not_found'
        return result

    email = (row['email'] or '').strip()
    if not email:
        result['skipped'] = 'no_email'
        return result

    label = plan_label(row['subscriber_plan'])
    body = payment_failed_html(
        (row['display_name'] or '').strip(),
        label,
        _amount_str(invoice),
        billing_url(),
    )
    ok = _smtp_send(email, f'Action needed: your Montana Blotter {label} payment was declined', body)
    _record_event(conn, row['id'], event_id, 'dunning_payment_failed',
                  {'subscription_id': subscription_id, 'sent': ok,
                   'invoice': invoice.get('id'), 'attempt_count': attempt})
    result.update(sent=ok, email=email, skipped='' if ok else 'smtp_failed')
    logger.info('dunning: payment_failed notice to user %s sent=%s', row['id'], ok)
    return result


def notify_access_ended(conn: sqlite3.Connection, subscription_id: str,
                        plan: str = '', event_id: str = '') -> dict:
    """Email a reactivation link once a subscription is actually cancelled."""
    result = {'sent': False, 'skipped': '', 'email': ''}
    if not subscription_id:
        result['skipped'] = 'no_subscription_id'
        return result

    row = _subscriber_for(conn, subscription_id)
    if row is None:
        result['skipped'] = 'subscriber_not_found'
        return result

    email = (row['email'] or '').strip()
    if not email:
        result['skipped'] = 'no_email'
        return result

    label = plan_label(plan or row['subscriber_plan'])
    body = access_ended_html((row['display_name'] or '').strip(), label, f'{base_url()}/pricing')
    ok = _smtp_send(email, f'Your Montana Blotter {label} access has lapsed', body)
    _record_event(conn, row['id'], event_id, 'dunning_access_ended',
                  {'subscription_id': subscription_id, 'sent': ok})
    result.update(sent=ok, email=email, skipped='' if ok else 'smtp_failed')
    logger.info('dunning: access_ended notice to user %s sent=%s', row['id'], ok)
    return result
