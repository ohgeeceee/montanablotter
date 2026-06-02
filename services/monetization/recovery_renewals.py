"""Recovery center ad renewal reminder scaffolding.

The ``recovery_ad_orders`` table tracks ``activated_at`` and a
``billing_cycle`` of ``monthly`` or ``annual`` but does not store an explicit
``renews_on`` column — Stripe is the source of truth. For advertiser-facing
reminders ("hey, your listing is up for renewal in 7 days") and admin
dashboards ("which subs are expiring this week?"), we need a deterministic
function that computes the projected next renewal from the activation date
plus the billing interval.

This module is scaffolding for future renewal-notification flows:

* :func:`project_next_renewal` — given an order's activation timestamp and
  billing cycle, return the ISO date (``YYYY-MM-DD``) when the next renewal
  is expected to land. Adds the interval, then snaps forward day-by-day
  until the result is strictly in the future (so a "30 days from now"
  expectation never returns yesterday).
* :func:`days_until_renewal` — wrapper around the above returning a signed
  integer (negative = already past, 0 = today, positive = upcoming).
* :func:`find_upcoming_renewals` — query helper that returns active
  recovery ad orders whose projected renewal falls within ``within_days``.

The functions are pure / deterministic so they can be unit-tested without
touching the database. Callers that need DB access pass an explicit
``sqlite3.Connection``.

No production data is read or written by this module.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional


_VALID_CYCLES = ('monthly', 'annual')


def _parse_activated_at(raw: Optional[str]) -> Optional[date]:
    """Parse an ``activated_at`` string in either ``YYYY-MM-DD HH:MM:SS`` or
    ``YYYY-MM-DD`` form. Returns ``None`` if unparseable / blank.
    """
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    for fmt, length in (('%Y-%m-%d %H:%M:%S', 19), ('%Y-%m-%d', 10)):
        try:
            return datetime.strptime(raw[:length], fmt).date()
        except ValueError:
            continue
    return None


def _interval_days(billing_cycle: str) -> int:
    """Return the interval in days for a billing cycle label."""
    cycle = (billing_cycle or '').strip().lower()
    if cycle == 'annual':
        return 365
    # default to monthly for unknown / empty values
    return 30


def project_next_renewal(
    activated_at: Optional[str],
    billing_cycle: str,
    *,
    today: Optional[date] = None,
) -> Optional[date]:
    """Project the next renewal date for a recovery ad order.

    The math is: take ``activated_at`` and step forward by the billing
    interval (30 days for ``monthly``, 365 for ``annual``) until the result
    is on or after ``today``. Returns ``None`` if ``activated_at`` is
    unparseable.

    Args:
        activated_at: Stored activation timestamp string.
        billing_cycle: ``monthly`` or ``annual`` (anything else → monthly).
        today: Optional override for "now" (useful in tests).

    Returns:
        ISO ``date`` for the projected renewal, or ``None`` if input is bad.
    """
    base = _parse_activated_at(activated_at)
    if base is None:
        return None
    today = today or date.today()
    interval = _interval_days(billing_cycle)
    candidate = base
    # Walk forward in interval steps until we're at or after today.
    # Cap at ~6 years (annual × 6) to defend against pathological inputs.
    for _ in range(365 * 6):
        if candidate >= today:
            return candidate
        candidate = candidate + timedelta(days=interval)
    return candidate


def days_until_renewal(
    activated_at: Optional[str],
    billing_cycle: str,
    *,
    today: Optional[date] = None,
) -> Optional[int]:
    """Return the signed number of days from ``today`` to the projected
    renewal. Negative = past, 0 = today, positive = upcoming. ``None`` if
    the activation date is unparseable.
    """
    next_renewal = project_next_renewal(activated_at, billing_cycle, today=today)
    if next_renewal is None:
        return None
    today = today or date.today()
    return (next_renewal - today).days


def find_upcoming_renewals(
    conn: sqlite3.Connection,
    *,
    within_days: int = 7,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Return active recovery ad orders whose projected renewal falls
    within ``within_days`` of today.

    The query is in Python rather than SQL because the renewal date is a
    derived value (no stored column to filter on). For typical order
    volumes (tens to a few hundred active subs) this is fast enough.

    Args:
        conn: SQLite connection.
        within_days: How many days ahead to look. Negative values are
            treated as 0.
        today: Optional override for "now" (test hook).

    Returns:
        List of dicts, sorted by ``days_until_renewal`` ascending:

        ``[{order_id, center_name, email, package_id, billing_cycle,
            activated_at, next_renewal, days_until_renewal}, ...]``
    """
    within_days = max(0, int(within_days))
    today = today or date.today()
    rows = conn.execute(
        '''
        SELECT id, center_name, email, package_id, billing_cycle,
               activated_at, status
        FROM recovery_ad_orders
        WHERE status = 'active'
          AND activated_at IS NOT NULL
        ORDER BY id DESC
        '''
    ).fetchall()

    results: List[Dict[str, Any]] = []
    for r in rows:
        next_renewal = project_next_renewal(
            r['activated_at'], r['billing_cycle'], today=today,
        )
        if next_renewal is None:
            continue
        days = (next_renewal - today).days
        if 0 <= days <= within_days:
            results.append({
                'order_id': int(r['id']),
                'center_name': r['center_name'] or '',
                'email': r['email'] or '',
                'package_id': r['package_id'] or '',
                'billing_cycle': r['billing_cycle'] or '',
                'activated_at': r['activated_at'] or '',
                'next_renewal': next_renewal.isoformat(),
                'days_until_renewal': int(days),
            })
    results.sort(key=lambda x: x['days_until_renewal'])
    return results
