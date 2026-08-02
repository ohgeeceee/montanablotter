"""Per-firm sample report generator — Day 5 outreach deliverable.

`docs/plans/2026-07-28-montana-lawyer-advertising-plan.md` says the Day 5
email attaches a one-page sample report showing the exact metrics the firm
will receive. This module is that report.

Two modes:
  - 'sample' — prospect is not yet a paying customer. Report uses
    reference numbers keyed to the chosen package tier, labeled
    "First 30 days (illustrative)" with a clearly-marked disclaimer.
    Used by operators during sales pitches; safe to print and hand
    to a prospect in person.
  - 'live'   — prospect matches an active `lawyer_ad_orders` row.
    Report reads real `lawyer_listing_events` + `lawyer_lead_deliveries`
    for the last 30 days. Firm fills in advertiser-reported counts
    (contacted / consultations / retained) — those are intentionally
    null because there's no control-panel input for them yet.

The reference numbers are illustrative. They are NOT a promise of case
volume, signed clients, or ROI. The report copy is careful to say so
("based on the 90-day pilot cohort," "your actual results may differ").

Usage:
    from services.lawyer_outreach.sample_report import generate
    report = generate(conn, prospect_id=12, package_id='gold')
    render template against `report` dict.
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any


# -------------------------------------------------------------- package table

# Mirror of the three paid tiers. Decoupled from blueprints.lawyer_ads so the
# report module has no import-time side effects and tests don't need the full
# app up. If real prices change, the reference numbers below also need a
# deliberate decision — that's the right discipline.
_PACKAGES: dict[str, dict[str, Any]] = {
    'bronze': {
        'name': 'Bronze Listing',
        'price_monthly_cents': 14900,
        'price_label': '$149/mo',
        'priority_rank': 3,
        'short_label': 'Bronze',
    },
    'silver': {
        'name': 'Silver Featured',
        'price_monthly_cents': 29900,
        'price_label': '$299/mo',
        'priority_rank': 2,
        'short_label': 'Silver',
    },
    'gold': {
        'name': 'Gold Priority',
        'price_monthly_cents': 59900,
        'price_label': '$599/mo',
        'priority_rank': 1,
        'short_label': 'Gold',
    },
}


# ------------------------------------------------------------ reference data

# First-30-day illustrative numbers per package. Source-of-fake: the plan
# document's commitment is "we will not promise case volume or ROI before
# we have cohort data." The numbers below are a reasonable starting estimate
# for a Montana county of average population and arrest volume; the report
# labels them "illustrative" and "based on the 90-day pilot cohort." Do NOT
# ship a higher number without real cohort data backing it.
_REFERENCE_NUMBERS_30D: dict[str, dict[str, float]] = {
    'bronze': {
        'impressions': 87,
        'clicks': 5,
        'calls': 2,
        'leads_delivered': 1,
        'leads_failed': 0,
        'consultations': 0.4,
        'retained': 0.1,
    },
    'silver': {
        'impressions': 184,
        'clicks': 14,
        'calls': 6,
        'leads_delivered': 3,
        'leads_failed': 0,
        'consultations': 1.2,
        'retained': 0.3,
    },
    'gold': {
        'impressions': 412,
        'clicks': 31,
        'calls': 18,
        'leads_delivered': 6,
        'leads_failed': 1,
        'consultations': 2.4,
        'retained': 0.6,
    },
}


# Fake-but-defensible sample lead rows for the report's "Recent deliveries"
# table in sample mode. The destination emails are clearly placeholder; the
# names are `(Sample) Doe, J.` style so no real-PII reviewer worry.
_SAMPLE_DELIVERIES: list[dict[str, Any]] = [
    {
        'recipient_label': 'Gold advertiser',
        'destination': 'your-firm@example.com (sample)',
        'lead_name': '(Sample) Doe, J.',
        'lead_phone': '(406) 555-0XX1',
        'county': 'Yellowstone',
        'case_type': 'Misdemeanor DUI',
        'sent_at': '2026-07-29 14:02 UTC',
        'status': 'sent',
        'error': None,
    },
    {
        'recipient_label': 'Gold advertiser',
        'destination': 'your-firm@example.com (sample)',
        'lead_name': '(Sample) Roe, M.',
        'lead_phone': '(406) 555-0XX2',
        'county': 'Yellowstone',
        'case_type': 'Felony assault',
        'sent_at': '2026-07-27 09:18 UTC',
        'status': 'sent',
        'error': None,
    },
    {
        'recipient_label': 'Gold advertiser',
        'destination': 'your-firm@example.com (sample)',
        'lead_name': '(Sample) Smith, K.',
        'lead_phone': '(406) 555-0XX3',
        'county': 'Yellowstone',
        'case_type': 'Drug possession',
        'sent_at': '2026-07-25 22:41 UTC',
        'status': 'sent',
        'error': None,
    },
    {
        'recipient_label': 'Gold advertiser',
        'destination': 'alt-firm@example.com (sample)',
        'lead_name': '(Sample) Smith, K.',
        'lead_phone': '(406) 555-0XX3',
        'county': 'Yellowstone',
        'case_type': 'Drug possession',
        'sent_at': '2026-07-25 22:41 UTC',
        'status': 'sent',
        'error': None,
    },
    {
        'recipient_label': 'Silver advertiser',
        'destination': 'silver-firm@example.com (sample)',
        'lead_name': '(Sample) Roe, M.',
        'lead_phone': '(406) 555-0XX2',
        'county': 'Yellowstone',
        'case_type': 'Felony assault',
        'sent_at': '2026-07-27 09:18 UTC',
        'status': 'sent',
        'error': None,
    },
    {
        'recipient_label': 'Silver advertiser',
        'destination': 'silver-firm@example.com (sample)',
        'lead_name': '(Sample) Lee, T.',
        'lead_phone': '(406) 555-0XX4',
        'county': 'Yellowstone',
        'case_type': 'Warrant / failure to appear',
        'sent_at': '2026-07-23 11:05 UTC',
        'status': 'failed',
        'error': 'smtp_recipient_rejected',
    },
]


# ------------------------------------------------------------------ helpers --

_VALID_PACKAGE_IDS = frozenset(_PACKAGES.keys())


def _normalize_county(value: str) -> str:
    return ' '.join((value or '').replace('&', 'and').lower().split())


def _county_matches(served_csv: str, county: str) -> bool:
    target = _normalize_county(county)
    if not target:
        return False
    for raw in (served_csv or '').split(','):
        if _normalize_county(raw) == target:
            return True
    return False


def _find_active_order(
    conn: sqlite3.Connection, firm_name: str, county: str
) -> sqlite3.Row | None:
    """Return the best (highest-priority) active order whose counties_served
    covers `county` AND whose firm_name matches `firm_name` case-insensitively.
    None if the lawyer_ad_orders table is missing (e.g. unit-test schema).
    """
    table_check = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lawyer_ad_orders'"
    ).fetchone()
    if not table_check:
        return None

    rows = conn.execute(
        '''
        SELECT id, firm_name, contact_name, email, counties_served, package_id,
               billing_cycle, status, paid_at
        FROM lawyer_ad_orders
        WHERE status IN ('active', 'capacity_blocked')
          AND lower(firm_name) = lower(?)
        ''',
        (firm_name or '',),
    ).fetchall()

    matched = [r for r in rows if _county_matches(r['counties_served'] or '', county or '')]
    if not matched:
        return None
    # Prefer the highest-priority (gold > silver > bronze) so a firm that has
    # two active orders (e.g. comp + paid) still reports under their best tier.
    rank = {'gold': 0, 'silver': 1, 'bronze': 2}
    matched.sort(key=lambda r: rank.get((r['package_id'] or '').lower(), 9))
    return matched[0]


def _live_event_counts(
    conn: sqlite3.Connection, order_id: int, since_iso: str
) -> dict[str, int]:
    """Real event counts for the last 30 days. Impression dedupe is enforced
    by the partial unique index on lawyer_listing_events, so a plain COUNT(*)
    on impressions is already deduped (one per (order, IP, county, day)).
    Clicks/calls/text/email are explicit user actions and must count every
    time. Leads come from a separate table (see _live_delivery_counts)."""
    if not _table_exists(conn, 'lawyer_listing_events'):
        return {'impressions': 0, 'clicks': 0, 'calls': 0, 'text': 0, 'email': 0}
    rows = conn.execute(
        '''
        SELECT event_type, COUNT(*) AS n
        FROM lawyer_listing_events
        WHERE order_id = ? AND occurred_at >= ?
        GROUP BY event_type
        ''',
        (order_id, since_iso),
    ).fetchall()
    out: dict[str, int] = {
        'impressions': 0, 'clicks': 0, 'calls': 0, 'text': 0, 'email': 0,
    }
    # Map event_type values stored in the DB (singular) to the report's
    # metric bucket names (plural). The blueprint writes 'impression' /
    # 'click' / 'call' / 'text' / 'email' — see
    # blueprints/lawyer_ads.py::_record_listing_event.
    bucket_for_type = {
        'impression': 'impressions',
        'click': 'clicks',
        'call': 'calls',
        'text': 'text',
        'email': 'email',
    }
    for r in rows:
        bucket = bucket_for_type.get(r['event_type'])
        if bucket is not None:
            out[bucket] += r['n']
    return out


def _live_delivery_counts_and_recent(
    conn: sqlite3.Connection, order_id: int, since_iso: str
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Return (sent_count, failed_count) and the most recent 10 deliveries
    for this order, joined to the lead row so the operator sees who, where,
    and when. Falls back to zeros / empty list if the tables don't exist."""
    if not _table_exists(conn, 'lawyer_lead_deliveries'):
        return {'sent': 0, 'failed': 0}, []
    counts_rows = conn.execute(
        '''
        SELECT status, COUNT(DISTINCT lead_id) AS n
        FROM lawyer_lead_deliveries
        WHERE order_id = ?
          AND COALESCE(sent_at, created_at) >= ?
        GROUP BY status
        ''',
        (order_id, since_iso),
    ).fetchall()
    sent = 0
    failed = 0
    for r in counts_rows:
        if r['status'] == 'sent':
            sent = r['n']
        elif r['status'] == 'failed':
            failed = r['n']

    if not _table_exists(conn, 'lawyer_consumer_leads'):
        recent: list[dict[str, Any]] = []
    else:
        recent_rows = conn.execute(
            '''
            SELECT ld.status AS status, ld.destination AS destination,
                   ld.error AS error, ld.sent_at AS sent_at,
                   l.full_name AS lead_name, l.phone AS lead_phone,
                   l.county AS county, l.case_type AS case_type
            FROM lawyer_lead_deliveries ld
            LEFT JOIN lawyer_consumer_leads l ON l.id = ld.lead_id
            WHERE ld.order_id = ?
            ORDER BY COALESCE(ld.sent_at, ld.created_at) DESC
            LIMIT 10
            ''',
            (order_id,),
        ).fetchall()
        recent = []
        for r in recent_rows:
            recent.append({
                'recipient_label': 'This advertiser',
                'destination': r['destination'] or '',
                'lead_name': r['lead_name'] or '(unknown)',
                'lead_phone': r['lead_phone'] or '',
                'county': r['county'] or '',
                'case_type': r['case_type'] or 'Not specified',
                'sent_at': r['sent_at'] or '',
                'status': r['status'] or '',
                'error': r['error'] or None,
            })
    return {'sent': sent, 'failed': failed}, recent


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _round_money(cents: int, divisor: float) -> float | None:
    """Cost = price / divisor, rounded to two decimals, or None if divisor
    is zero (avoids divide-by-zero on a brand-new listing with no leads)."""
    if not divisor:
        return None
    return round(cents / 100 / divisor, 2)


# ------------------------------------------------------------------- main API

def generate(
    conn: sqlite3.Connection,
    prospect_id: int,
    *,
    package_id: str = 'gold',
) -> dict[str, Any]:
    """Build the report context dict for one prospect.

    `package_id` controls the sample-mode reference set. Live mode ignores
    it and uses the order's actual package. Raises ValueError on unknown
    prospect_id or invalid package_id.
    """
    if package_id not in _VALID_PACKAGE_IDS:
        raise ValueError(f'unknown package_id: {package_id!r}')

    prospect = conn.execute(
        'SELECT * FROM lawyer_outreach_prospects WHERE id = ?',
        (prospect_id,),
    ).fetchone()
    if prospect is None:
        raise ValueError(f'prospect {prospect_id} not found')

    order = _find_active_order(conn, prospect['firm_name'], prospect['county'])

    if order is not None:
        return _build_live_report(conn, prospect, order)
    return _build_sample_report(conn, prospect, package_id)


def _build_sample_report(
    conn: sqlite3.Connection, prospect: sqlite3.Row, package_id: str
) -> dict[str, Any]:
    pkg = _PACKAGES[package_id]
    ref = _REFERENCE_NUMBERS_30D[package_id]

    metrics = {
        'impressions': ref['impressions'],
        'clicks': ref['clicks'],
        'calls': ref['calls'],
        'leads_delivered': ref['leads_delivered'],
        'leads_failed': ref['leads_failed'],
    }
    advertiser_reported = {
        'contacted': None,
        'consultations': ref['consultations'],
        'retained': ref['retained'],
    }

    now = datetime.utcnow()
    period_start = (now - timedelta(days=30)).strftime('%Y-%m-%d')
    period_end = now.strftime('%Y-%m-%d')

    return {
        'mode': 'sample',
        'firm_name': prospect['firm_name'],
        'county': prospect['county'] or 'Montana',
        'package': {
            'id': package_id,
            'name': pkg['name'],
            'price_label': pkg['price_label'],
            'short_label': pkg['short_label'],
        },
        'period_label': 'First 30 days (illustrative)',
        'period_start': period_start,
        'period_end': period_end,
        'metrics': metrics,
        'deliveries': _SAMPLE_DELIVERIES,
        'advertiser_reported': advertiser_reported,
        'cost_per_lead': _round_money(pkg['price_monthly_cents'], metrics['leads_delivered']),
        'cost_per_consultation': _round_money(
            pkg['price_monthly_cents'], advertiser_reported['consultations'],
        ),
        'cost_per_retained': _round_money(
            pkg['price_monthly_cents'], advertiser_reported['retained'],
        ),
        'disclaimer': (
            'Illustrative reference numbers based on the 90-day pilot cohort. '
            'Your actual results may differ. Montana Blotter does not guarantee '
            'case volume, signed clients, or ROI.'
        ),
        'report_id': f'sample-{prospect["id"]}-{now.strftime("%Y%m%d%H%M%S")}-{secrets.token_hex(3)}',
        'generated_at': now.strftime('%Y-%m-%d %H:%M UTC'),
    }


def _build_live_report(
    conn: sqlite3.Connection, prospect: sqlite3.Row, order: sqlite3.Row
) -> dict[str, Any]:
    actual_package = (order['package_id'] or 'gold').lower()
    if actual_package not in _VALID_PACKAGE_IDS:
        actual_package = 'gold'
    pkg = _PACKAGES[actual_package]

    now = datetime.utcnow()
    since_iso = (now - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    period_start = (now - timedelta(days=30)).strftime('%Y-%m-%d')
    period_end = now.strftime('%Y-%m-%d')

    events = _live_event_counts(conn, int(order['id']), since_iso)
    delivery_counts, recent = _live_delivery_counts_and_recent(
        conn, int(order['id']), since_iso,
    )

    metrics = {
        'impressions': events['impressions'],
        'clicks': events['clicks'],
        'calls': events['calls'],
        'leads_delivered': delivery_counts['sent'],
        'leads_failed': delivery_counts['failed'],
    }
    # Live mode: firm fills these in via the control panel (not yet wired).
    # Stays null so the report is honest — the operator sees a blank cell,
    # not a fabricated number.
    advertiser_reported = {
        'contacted': None,
        'consultations': None,
        'retained': None,
    }

    return {
        'mode': 'live',
        'firm_name': prospect['firm_name'],
        'county': prospect['county'] or 'Montana',
        'package': {
            'id': actual_package,
            'name': pkg['name'],
            'price_label': pkg['price_label'],
            'short_label': pkg['short_label'],
        },
        'period_label': 'Last 30 days',
        'period_start': period_start,
        'period_end': period_end,
        'metrics': metrics,
        'deliveries': recent,
        'advertiser_reported': advertiser_reported,
        'cost_per_lead': _round_money(pkg['price_monthly_cents'], metrics['leads_delivered']),
        'cost_per_consultation': None,
        'cost_per_retained': None,
        'disclaimer': (
            'Real data from your active placement. Advertiser-reported '
            'counts (contacted / consultations / retained) are filled in '
            'by the firm — ask advertising@montanablotter.com for the '
            'monthly reporting form when ready.'
        ),
        'report_id': f'live-{prospect["id"]}-{now.strftime("%Y%m%d%H%M%S")}-{secrets.token_hex(3)}',
        'generated_at': now.strftime('%Y-%m-%d %H:%M UTC'),
    }
