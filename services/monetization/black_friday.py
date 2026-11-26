from datetime import datetime, timezone
import logging

import config
from db import get_db

log = logging.getLogger(__name__)

CAMPAIGN_SLUG = 'black-friday-2026'
PROMOTION_CODE = 'BLACKFRIDAY50'
DEFAULT_START_AT = '2026-11-27T07:00:00+00:00'
DEFAULT_END_AT = '2026-12-01T07:00:00+00:00'

PLAN_OFFERS = {
    'plus': {
        'name': 'Blotter Plus',
        'product_id': 'prod_UcOWCDGyNU1Anp',
        'regular_cents': 7900,
        'sale_cents': 3950,
        'regular_price': '$79',
        'sale_price': '$39.50',
        'savings': '$39.50',
    },
    'pro': {
        'name': 'Blotter Pro',
        'product_id': 'prod_V1Cs2JkMO1Alya',
        'regular_cents': 26000,
        'sale_cents': 13000,
        'regular_price': '$260',
        'sale_price': '$130',
        'savings': '$130',
    },
}


def _utcnow():
    return datetime.now(timezone.utc)


def _campaign_time(value, fallback):
    raw = (value or fallback).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        log.warning('Invalid Black Friday campaign datetime; using the default window')
        parsed = datetime.fromisoformat(fallback)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def campaign_window():
    start_at = _campaign_time(
        getattr(config, 'BLACK_FRIDAY_START_AT', ''),
        DEFAULT_START_AT,
    )
    end_at = _campaign_time(
        getattr(config, 'BLACK_FRIDAY_END_AT', ''),
        DEFAULT_END_AT,
    )
    return start_at, end_at


def campaign_is_live(now=None):
    promotion_code_id = (getattr(config, 'BLACK_FRIDAY_PROMOTION_CODE_ID', '') or '').strip()
    if not promotion_code_id:
        return False
    start_at, end_at = campaign_window()
    current_time = now or _utcnow()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return start_at <= current_time.astimezone(timezone.utc) < end_at


def campaign_context(now=None):
    return {
        'slug': CAMPAIGN_SLUG,
        'code': PROMOTION_CODE,
        'is_live': campaign_is_live(now=now),
        'date_label': 'November 27–30',
        'end_label': 'Monday, November 30 at 11:59 p.m. MT',
        'plans': PLAN_OFFERS,
    }


def is_new_customer(public_user_id):
    try:
        user_id = int(public_user_id)
    except (TypeError, ValueError):
        return False

    conn = get_db()
    try:
        row = conn.execute(
            '''
            SELECT subscriber_plan, stripe_subscription_id, subscription_status,
                   subscription_activated_at, subscription_canceled_at
            FROM public_users
            WHERE id = ?
            ''',
            (user_id,),
        ).fetchone()
    except Exception:
        log.exception('Black Friday eligibility check failed for user %s', user_id)
        return False
    finally:
        conn.close()

    if not row:
        return False
    plan = (row['subscriber_plan'] or '').strip().lower()
    return plan in {'', 'free', 'member'} and not any((
        (row['stripe_subscription_id'] or '').strip(),
        (row['subscription_status'] or '').strip(),
        row['subscription_activated_at'],
        row['subscription_canceled_at'],
    ))


def promotion_code_for_checkout(plan, interval, campaign, public_user_id, now=None):
    if (
        campaign == CAMPAIGN_SLUG
        and interval == 'annual'
        and plan in PLAN_OFFERS
        and campaign_is_live(now=now)
        and is_new_customer(public_user_id)
    ):
        return (getattr(config, 'BLACK_FRIDAY_PROMOTION_CODE_ID', '') or '').strip()
    return ''
