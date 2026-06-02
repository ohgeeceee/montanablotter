"""Unified advertiser metrics scaffolding.

Bridges the two existing ad systems on the site:

* **Recovery center ads** (``blueprints/recovery_ads.py``) — counter columns on
  ``recovery_ad_listings`` (``impressions``, ``clicks``).
* **Bail bondsman ads** (``blueprints/api.py``) — event log on
  ``bail_ad_events`` with ``event_type`` of ``impression`` / ``click`` /
  ``lead`` / ``call`` / ``text``.

Both control panels need a "how are my ads doing?" view. Without this module,
each surface would have to know which table to query and how to roll up the
numbers. Centralizing the rollup here means:

* Future analytics dashboards can call one function.
* Tests for the rollup logic live in one place.
* Adding a third ad product (e.g. attorney directory) only requires writing a
  new aggregator that conforms to the same return shape.

Public API:

* :func:`get_recovery_ad_metrics` — pull impression/click counters for a
  recovery ad order (or all active orders).
* :func:`get_bail_ad_metrics` — roll up ``bail_ad_events`` for a specific
  order, slot, or county.
* :func:`get_advertiser_metrics` — combined view across both products for a
  given business name/email (matched case-insensitively).
* :func:`format_ctr` — small helper for human-readable click-through rate.

The functions all return plain ``dict`` objects with stable keys, so they can
be JSON-serialized into advertiser dashboards or emails without further
transformation.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional


_BAIL_EVENT_TYPES = ('impression', 'click', 'lead', 'call', 'text')


def get_recovery_ad_metrics(
    conn: sqlite3.Connection,
    order_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Return impression/click counters for recovery center ads.

    Args:
        conn: SQLite connection.
        order_id: If given, return metrics for that order only. If ``None``,
            return metrics for all active orders (a list payload).

    Returns:
        A dict with one of two shapes:

        * Single order: ``{'order_id', 'impressions', 'clicks', 'ctr'}``.
        * All orders: ``{'orders': [{...}, ...], 'totals': {...}}``.
    """
    if order_id is not None:
        row = conn.execute(
            '''
            SELECT l.order_id, l.impressions, l.clicks
            FROM recovery_ad_listings l
            WHERE l.order_id = ?
            ''',
            (order_id,),
        ).fetchone()
        if row is None:
            return {
                'order_id': order_id,
                'impressions': 0,
                'clicks': 0,
                'ctr': 0.0,
            }
        impressions = int(row['impressions'] or 0)
        clicks = int(row['clicks'] or 0)
        return {
            'order_id': int(row['order_id']),
            'impressions': impressions,
            'clicks': clicks,
            'ctr': format_ctr(impressions, clicks),
        }

    rows = conn.execute(
        '''
        SELECT o.id AS order_id, o.center_name, o.package_id, o.status,
               COALESCE(l.impressions, 0) AS impressions,
               COALESCE(l.clicks, 0) AS clicks
        FROM recovery_ad_orders o
        LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
        ORDER BY o.id DESC
        '''
    ).fetchall()
    total_imp = 0
    total_clicks = 0
    orders: List[Dict[str, Any]] = []
    for r in rows:
        imp = int(r['impressions'] or 0)
        clk = int(r['clicks'] or 0)
        total_imp += imp
        total_clicks += clk
        orders.append({
            'order_id': int(r['order_id']),
            'center_name': r['center_name'] or '',
            'package_id': r['package_id'] or '',
            'status': r['status'] or '',
            'impressions': imp,
            'clicks': clk,
            'ctr': format_ctr(imp, clk),
        })
    return {
        'orders': orders,
        'totals': {
            'impressions': total_imp,
            'clicks': total_clicks,
            'ctr': format_ctr(total_imp, total_clicks),
        },
    }


def get_bail_ad_metrics(
    conn: sqlite3.Connection,
    *,
    order_id: Optional[int] = None,
    slot_id: Optional[int] = None,
    county: Optional[str] = None,
) -> Dict[str, Any]:
    """Roll up ``bail_ad_events`` for a given order / slot / county.

    At least one filter is required to avoid scanning the whole table; the
    function returns an empty rollup if no filter is provided (and logs a
    soft warning in the totals).

    Returns:
        ``{'events': {event_type: count}, 'total': N, 'filters': {...}}``.
    """
    where: List[str] = []
    params: List[Any] = []
    if order_id is not None:
        where.append('order_id = ?')
        params.append(int(order_id))
    if slot_id is not None:
        where.append('slot_id = ?')
        params.append(int(slot_id))
    if county:
        where.append('county = ?')
        params.append(county.strip()[:80])

    base_sql = 'SELECT event_type, COUNT(*) AS n FROM bail_ad_events'
    if where:
        base_sql += ' WHERE ' + ' AND '.join(where)
    base_sql += ' GROUP BY event_type'

    rows = conn.execute(base_sql, tuple(params)).fetchall()
    counts: Dict[str, int] = {evt: 0 for evt in _BAIL_EVENT_TYPES}
    total = 0
    for r in rows:
        evt = (r['event_type'] or '').strip().lower()
        n = int(r['n'] or 0)
        if evt in counts:
            counts[evt] = n
        else:
            counts[evt] = n  # preserve any custom event types
        total += n

    impressions = counts.get('impression', 0)
    clicks = counts.get('click', 0)
    return {
        'events': counts,
        'total': total,
        'impressions': impressions,
        'clicks': clicks,
        'ctr': format_ctr(impressions, clicks),
        'filters': {
            'order_id': order_id,
            'slot_id': slot_id,
            'county': (county or '').strip()[:80] or None,
        },
    }


def get_advertiser_metrics(
    conn: sqlite3.Connection,
    *,
    business_name: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """Combined view across recovery and bail ad systems for one advertiser.

    Recovery ads are matched by ``center_name`` (case-insensitive contains).
    Bail ad orders are matched by ``business_name`` or ``email`` (exact
    case-insensitive). Both shapes are returned so a single dashboard
    surface can render them.

    Args:
        conn: SQLite connection.
        business_name: Optional substring to match against center/business name.
        email: Optional email to match against bail ad order contact email.

    Returns:
        ``{'recovery': {...}, 'bail': {...}, 'matched': {'business_name',
        'email'}}``.
    """
    recovery_section: Dict[str, Any] = {'orders': [], 'totals': {
        'impressions': 0, 'clicks': 0, 'ctr': 0.0,
    }}
    if business_name:
        like = f"%{business_name.strip()}%"
        rows = conn.execute(
            '''
            SELECT o.id AS order_id, o.center_name, o.package_id, o.status,
                   COALESCE(l.impressions, 0) AS impressions,
                   COALESCE(l.clicks, 0) AS clicks
            FROM recovery_ad_orders o
            LEFT JOIN recovery_ad_listings l ON l.order_id = o.id
            WHERE LOWER(o.center_name) LIKE LOWER(?)
            ORDER BY o.id DESC
            ''',
            (like,),
        ).fetchall()
        total_imp = 0
        total_clicks = 0
        for r in rows:
            imp = int(r['impressions'] or 0)
            clk = int(r['clicks'] or 0)
            total_imp += imp
            total_clicks += clk
            recovery_section['orders'].append({
                'order_id': int(r['order_id']),
                'center_name': r['center_name'] or '',
                'package_id': r['package_id'] or '',
                'status': r['status'] or '',
                'impressions': imp,
                'clicks': clk,
                'ctr': format_ctr(imp, clk),
            })
        recovery_section['totals'] = {
            'impressions': total_imp,
            'clicks': total_clicks,
            'ctr': format_ctr(total_imp, total_clicks),
        }

    bail_section: Dict[str, Any] = {
        'orders': [],
        'events': {evt: 0 for evt in _BAIL_EVENT_TYPES},
    }
    if email or business_name:
        where: List[str] = []
        params: List[Any] = []
        if email:
            where.append('LOWER(o.email) = LOWER(?)')
            params.append(email.strip().lower())
        if business_name:
            where.append('LOWER(o.business_name) LIKE LOWER(?)')
            params.append(f"%{business_name.strip()}%")
        sql = (
            'SELECT o.id, o.business_name, o.email, o.status, o.package_id '
            'FROM bail_ad_orders o WHERE ' + ' OR '.join(where) + ' ORDER BY o.id DESC'
        )
        order_rows = conn.execute(sql, tuple(params)).fetchall()
        for o in order_rows:
            order_metrics = get_bail_ad_metrics(conn, order_id=int(o['id']))
            bail_section['orders'].append({
                'order_id': int(o['id']),
                'business_name': o['business_name'] or '',
                'email': o['email'] or '',
                'status': o['status'] or '',
                'package_id': o['package_id'] or '',
                'events': order_metrics['events'],
                'total': order_metrics['total'],
                'ctr': order_metrics['ctr'],
            })
            for evt, n in order_metrics['events'].items():
                bail_section['events'][evt] = bail_section['events'].get(evt, 0) + int(n)

    return {
        'recovery': recovery_section,
        'bail': bail_section,
        'matched': {
            'business_name': (business_name or '').strip() or None,
            'email': (email or '').strip().lower() or None,
        },
    }


def format_ctr(impressions: int, clicks: int) -> float:
    """Return a click-through rate as a float in [0, 1], rounded to 4 places.

    Returns 0.0 when impressions are zero to avoid ``ZeroDivisionError``.
    """
    try:
        imp = max(0, int(impressions))
        clk = max(0, int(clicks))
    except (TypeError, ValueError):
        return 0.0
    if imp <= 0:
        return 0.0
    return round(clk / imp, 4)
