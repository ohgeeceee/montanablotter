"""CRM service layer: portals, email-log context, billing rollup reads.

Pure functions over a sqlite connection — no Flask imports here so tests
and CLI can reuse. Outbound email is NEVER sent from this module; it only
logs what an operator sent elsewhere (MISSION red line).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

PORTAL_TIERS = {
    'banner': 'Banner',
    'featured_listing': 'Featured Listing',
    'exclusive_county_sponsor': 'Exclusive County Sponsor',
}
PORTAL_STATUSES = ('invited', 'active', 'suspended', 'closed')

_TOKEN_RE = re.compile(r'^[A-Za-z0-9_\-]{20,128}$')
_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{1,63}$')


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def valid_token(token: str) -> bool:
    return bool(_TOKEN_RE.match(token or ''))


def normalize_slug(slug: str) -> str:
    slug = re.sub(r'[^a-z0-9\-]+', '-', (slug or '').strip().lower()).strip('-')[:64]
    return slug if _SLUG_RE.match(slug) else ''


def open_portal(conn, lead_id: int, tier: str, slug: str = '',
                token_factory=None, *, order_id: int | None = None) -> dict:
    """Create (or return existing) portal for a converted lead. Idempotent
    per lead+tier. order_id links the paid ad order (lawyer_ad_orders when
    tier is lawyer, bail_ad_orders otherwise) so metrics/billing/directory
    joins resolve. Returns {portal_id, access_token, slug, created}."""
    if tier not in PORTAL_TIERS:
        raise ValueError('unknown tier')
    lead = conn.execute('SELECT id FROM advertising_prospects WHERE id = ?',
                        (lead_id,)).fetchone()
    if lead is None:
        raise ValueError('unknown lead')
    table = ''
    if order_id is not None:
        for cand in ('lawyer_ad_orders', 'bail_ad_orders'):
            if conn.execute(f'SELECT 1 FROM {cand} WHERE id = ?',
                            (int(order_id),)).fetchone():
                table = cand
                break
        if not table:
            raise ValueError('unknown order')
    row = conn.execute(
        'SELECT * FROM client_portals WHERE lead_id = ? AND tier = ?',
        (lead_id, tier)).fetchone()
    if row:
        if order_id is not None and row['primary_order_id'] != int(order_id):
            conn.execute(
                'UPDATE client_portals SET primary_order_table = ?, '
                'primary_order_id = ?, updated_at = ? WHERE id = ?',
                (table, int(order_id), utcnow(), row['id']))
            conn.commit()
        return {'portal_id': row['id'], 'access_token': row['access_token'],
                'slug': row['slug'] or '', 'created': False}
    if token_factory is None:
        from services.crm.portal_tokens import new_portal_token as token_factory
    slug = normalize_slug(slug)
    if slug and conn.execute('SELECT 1 FROM client_portals WHERE slug = ?',
                             (slug,)).fetchone():
        slug = ''
    token = token_factory()
    cur = conn.execute(
        'INSERT INTO client_portals (lead_id, access_token, slug, tier, '
        'primary_order_table, primary_order_id) VALUES (?,?,?,?,?,?)',
        (lead_id, token, slug or None, tier,
         table if order_id is not None else '',
         int(order_id) if order_id is not None else None))
    conn.commit()
    return {'portal_id': cur.lastrowid, 'access_token': token,
            'slug': slug, 'created': True}


def set_portal_status(conn, portal_id: int, status: str) -> bool:
    if status not in PORTAL_STATUSES:
        raise ValueError('unknown status')
    cur = conn.execute(
        'UPDATE client_portals SET portal_status = ?, updated_at = ? WHERE id = ?',
        (status, utcnow(), portal_id))
    conn.commit()
    return cur.rowcount > 0


def log_email(conn, lead_id: int, direction: str, subject: str, body: str,
              from_address: str = '', to_address: str = '',
              tracking_id: str = '', provider: str = 'manual',
              send_status: str = 'logged') -> int:
    if direction not in ('inbound', 'outbound'):
        raise ValueError('direction')
    if send_status not in ('logged', 'sent', 'bounced', 'failed'):
        raise ValueError('send_status')
    cur = conn.execute(
        '''INSERT INTO crm_email_logs
           (lead_id, direction, subject, body, from_address, to_address,
            tracking_id, provider, send_status)
           VALUES (?,?,?,?,?,?,?,?,?)''',
        (lead_id, direction, subject[:500], body[:20000],
         from_address[:254], to_address[:254], tracking_id or None,
         provider, send_status))
    conn.commit()
    return cur.lastrowid


def email_thread(conn, lead_id: int, limit: int = 50):
    return conn.execute(
        'SELECT * FROM crm_email_logs WHERE lead_id = ? '
        'ORDER BY sent_at DESC, id DESC LIMIT ?', (lead_id, limit)).fetchall()


def billing_rollup(conn, lead_id: int | None = None):
    """Read-only view rows for a lead (portal join) or the whole book."""
    if lead_id is not None:
        return conn.execute(
            '''SELECT s.* FROM crm_subscriptions s
               JOIN client_portals p ON p.id = s.portal_id
               WHERE p.lead_id = ?''', (lead_id,)).fetchall()
    return conn.execute(
        'SELECT * FROM crm_subscriptions ORDER BY vertical, client_name').fetchall()


def invoice_rollup(conn, lead_id: int | None = None):
    if lead_id is not None:
        return conn.execute(
            '''SELECT i.* FROM crm_invoices i
               JOIN client_portals p ON p.primary_order_table =
                    CASE i.vertical WHEN 'lawyer' THEN 'lawyer_ad_orders'
                                    WHEN 'bail' THEN 'bail_ad_orders' END
                 AND p.primary_order_id = i.order_id
               WHERE p.lead_id = ? ORDER BY i.issued_at DESC''',
            (lead_id,)).fetchall()
    return conn.execute('SELECT * FROM crm_invoices ORDER BY issued_at DESC').fetchall()


# ---------------------------------------------------------------------------
# Section 4: self-service portal features (listing editor, metrics,
# lead receipt, billing links). All reads/writes go through the portal row;
# a client edit NEVER touches the provider order (billing) row.
# ---------------------------------------------------------------------------

PORTAL_FEATURES = ('listing_editor', 'metrics', 'lead_receipt', 'billing')


def listing_overrides(conn) -> dict:
    """Portal listing edits keyed for the public directory join (§A5).

    Returns {order_row_id: {logo, bio, phone, link}} per vertical:
    {'lawyer': {...}, 'bail': {...}}. Only portals that are live, editor-
    enabled, and still attached to an active order appear. Editors never
    write the order row, so the directory is the only place overrides
    surface — this function is the single join point.
    """
    out: dict[str, dict] = {'lawyer': {}, 'bail': {}}
    try:
        rows = conn.execute(
        '''
        SELECT p.primary_order_table AS tbl, p.primary_order_id AS oid,
               p.logo_url, p.bio, p.display_phone, p.direct_link
        FROM client_portals p
        WHERE p.portal_status = 'active'
          AND p.features LIKE '%listing_editor%'
          AND p.listing_updated_at IS NOT NULL
        ''').fetchall()
    except sqlite3.OperationalError:
        return out   # CRM schema not applied yet — directory renders unmodified
    for r in rows:
        key = 'lawyer' if r['tbl'] == 'lawyer_ad_orders' else \
              'bail' if r['tbl'] == 'bail_ad_orders' else None
        if not key or not r['oid']:
            continue
        # Only overlay edits whose order is still active — an expired
        # subscription drops the sponsored card and its overrides with it.
        live = conn.execute(
            f'SELECT 1 FROM {r["tbl"]} WHERE id = ? AND status = ?',
            (r['oid'], 'active')).fetchone()
        if not live:
            continue
        out[key][r['oid']] = {
            'logo': (r['logo_url'] or '').strip(),
            'bio': (r['bio'] or '').strip(),
            'phone': (r['display_phone'] or '').strip(),
            'link': (r['direct_link'] or '').strip(),
        }
    return out


def apply_directory_overrides(conn, listings, vertical) -> list:
    """Overlay portal listing edits onto a directory listing set (§A5).

    vertical: 'lawyer' | 'bail'. Missing key `order_id` (lawyer) or `id`
    (bail) links a listing row back to client_portals.primary_order_id.
    """
    ov_map = listing_overrides(conn).get(vertical, {})
    if not ov_map:
        return listings
    for item in listings:
        oid = item.get('order_id') or item.get('id')
        ov = ov_map.get(oid)
        if not ov:
            continue
        if vertical == 'lawyer':
            apply_listing_override(item, ov, desc_key='description')
        else:
            apply_listing_override(
                item, ov, desc_key='body_copy', link_key='target_url',
                phone_href_key='phone_href', sms_href_key='sms_href',
                phone_formatter=_tel_token)
    return listings


def _tel_token(phone: str) -> str:
    digits = re.sub(r'\D', '', phone or '')
    return ('1' + digits) if len(digits) == 10 else digits


def apply_listing_override(listing: dict, ov: dict, *, logo_key='logo_path',
                           desc_key=None, phone_key='phone', link_key='target_url',
                           phone_href_key=None, sms_href_key=None,
                           phone_formatter=None) -> dict:
    """Overlay one portal's edits onto a directory listing dict (mutates)."""
    if not ov:
        return listing
    if ov['logo']:
        listing[logo_key] = ov['logo']
    if ov['bio'] and desc_key:
        listing[desc_key] = ov['bio']
    if ov['phone']:
        listing[phone_key] = ov['phone']
        if phone_formatter:
            tok = phone_formatter(ov['phone'])
            if phone_href_key:
                listing[phone_href_key] = f'tel:{tok}' if tok else ''
            if sms_href_key:
                listing[sms_href_key] = f'sms:{tok}' if tok else ''
    if ov['link']:
        listing[link_key] = ov['link']
    return listing

_MAX_BIO = 1500
_MAX_URL = 300
_PHONE_RE = re.compile(r'^[\d ()\-+.]{3,30}$')
_URL_RE = re.compile(r'^https?://[^\s<>"]+$', re.I)


def _clean(text, limit):
    return (text or '').strip()[:limit]


def update_listing(conn, portal_id: int, *, bio='', display_phone='',
                   direct_line='', direct_link='', logo_url=None) -> bool:
    """Self-service Profile & Directory Listing Editor save.

    Validates shape only (length, phone chars, http(s) link) — the public
    directory renderer escapes on output. logo_url is set by the route
    layer after saving the upload; None means 'leave unchanged'.
    Raises ValueError on the first invalid field.
    """
    bio = _clean(bio, _MAX_BIO)
    display_phone = _clean(display_phone, 30)
    direct_line = _clean(direct_line, 30)
    direct_link = _clean(direct_link, _MAX_URL)
    for label, val in (('phone', display_phone), ('direct line', direct_line)):
        if val and not _PHONE_RE.match(val):
            raise ValueError(f'invalid {label}')
    if direct_link and not _URL_RE.match(direct_link):
        raise ValueError('direct link must start with http:// or https://')
    row = conn.execute('SELECT id FROM client_portals WHERE id = ?',
                       (portal_id,)).fetchone()
    if row is None:
        raise ValueError('unknown portal')
    sets = ['bio = ?', 'display_phone = ?', 'direct_line = ?',
            'direct_link = ?', 'listing_updated_at = ?', 'updated_at = ?']
    args = [bio, display_phone, direct_line, direct_link, utcnow(), utcnow()]
    if logo_url is not None:
        sets.append('logo_url = ?')
        args.append(_clean(logo_url, _MAX_URL))
    args.append(portal_id)
    conn.execute(f'UPDATE client_portals SET {", ".join(sets)} WHERE id = ?', args)
    conn.commit()
    return True


def set_features(conn, portal_id: int, features) -> bool:
    """Admin feature toggles per sponsor (Tab 2 control). Unknown names
    raise rather than silently vanishing — a typo'd feature must not look
    like a successful save."""
    wanted = tuple(dict.fromkeys(features))
    unknown = [f for f in wanted if f not in PORTAL_FEATURES]
    if unknown:
        raise ValueError(f'unknown features: {", ".join(unknown)}')
    if not wanted:
        raise ValueError('at least one feature must stay enabled')
    conn.execute('UPDATE client_portals SET features = ?, updated_at = ? WHERE id = ?',
                 (','.join(wanted), utcnow(), portal_id))
    conn.commit()
    return True


def feature_enabled(portal_row, feature: str) -> bool:
    return feature in (portal_row['features'] or '').split(',')


def _event_source(portal_row):
    """(table, order_id, kind) for ad event rollup; None if unlinked."""
    table = portal_row['primary_order_table']
    oid = portal_row['primary_order_id']
    if not oid:
        return None
    if table == 'bail_ad_orders':
        return ('bail_ad_events', oid, 'bail', 'created_at')
    if table == 'lawyer_ad_orders':
        return ('lawyer_listing_events', oid, 'lawyer', 'occurred_at')
    return None


def portal_metrics(conn, portal_row, days: int = 30) -> dict:
    """Impressions / clicks / calls / leads, totals + county-level exposure,
    read live from the existing per-vertical event tables.

    bail_ad_events.event_type: impression|click|call|text|lead (ts: created_at).
    lawyer_listing_events.event_type: impression|click|call|lead (ts: occurred_at).
    Missing tables (older deploys) degrade to zeros rather than 500.
    """
    out = {'totals': {'impressions': 0, 'clicks': 0, 'calls': 0, 'leads': 0},
           'counties': [], 'days': days, 'source': None}
    src = _event_source(portal_row)
    if not src:
        return out
    table, oid, kind, ts_col = src
    if kind == 'bail':
        bucket = {'impression': 'impressions', 'click': 'clicks',
                  'call': 'calls', 'text': 'calls', 'lead': 'leads'}
    else:
        # app-side metric_columns in blueprints/lawyer_ads.py:
        # impression|click|call|lead
        bucket = {'impression': 'impressions', 'click': 'clicks',
                  'call': 'calls', 'lead': 'leads'}
    type_col = 'event_type'
    where = (f'{type_col} IN ({",".join("?" * len(bucket))}) '
             f'AND {ts_col} >= datetime("now", ?)')
    params = [oid] + list(bucket) + [f'-{int(days)} days']
    try:
        rows = conn.execute(
            f'SELECT COALESCE(county,"") AS county, {type_col} AS et, '
            f'COUNT(*) AS n FROM {table} WHERE order_id = ? AND {where} '
            f'GROUP BY county, {type_col}', params).fetchall()
    except Exception:      # no such table — pre-event-schema deploy
        return out
    out['source'] = table
    # County raw-event data is dirty (referrer strings leak in); only the 56
    # real counties get their own row — everything else rolls into "Other".
    from services.crm.prospecting.counties import MT_COUNTIES
    valid = {c.casefold(): c for c in MT_COUNTIES}

    def _bucket_name(raw):
        raw = (raw or '').strip()
        if not raw:
            return 'Unspecified'
        return valid.get(raw.casefold(), 'Other')

    per_county: dict[str, dict] = {}
    for r in rows:
        key = bucket.get(r['et'])
        if not key:
            continue
        out['totals'][key] += r['n']
        name = _bucket_name(r['county'])
        c = per_county.setdefault(name, {'county': name, 'impressions': 0,
                                         'clicks': 0, 'calls': 0, 'leads': 0})
        c[key] += r['n']

    out['counties'] = sorted(per_county.values(),
                             key=lambda x: -x['impressions'])
    return out


def lead_receipt(conn, portal_row, limit: int = 50) -> list:
    """Inquiries/leads generated by this client's ad placement.

    Source of truth: {vertical}_consumer_leads — consumers who clicked the
    CTA on the sponsor's ad / directory slot; the routing job stamps the
    recipient order ids into routed_order_ids (comma list, hence the
    ',' || x || ',' LIKE match).

    NOTE: bail_ad_inquiries / lawyer_ad_inquiries are *advertiser*
    interest forms (future CRM leads), NOT end-consumer inquiries — they
    deliberately do not belong on a sponsor's receipt.

    Contact detail is included: this is a receipt for the advertiser who
    paid for the lead; the consumer leads already carry opt-in consent.
    """
    src = _event_source(portal_row)
    if not src:
        return []
    _, oid, kind = src[0], src[1], src[2]
    table = 'bail_consumer_leads' if kind == 'bail' else 'lawyer_consumer_leads'
    detail = 'jail_facility' if kind == 'bail' else 'case_type'
    try:
        return conn.execute(
            f'''SELECT 'lead' AS kind, full_name AS person, phone, email,
                       county, {detail} AS detail, notes, created_at
                FROM {table}
                WHERE ',' || COALESCE(routed_order_ids,'') || ',' LIKE ?
                ORDER BY created_at DESC LIMIT ?''',
            (f'%,{oid},%', limit)).fetchall()
    except Exception:      # no such table — vertical not deployed yet
        return []


def stripe_link(conn, portal_row) -> str:
    """provider_customer_id for this portal's order (billing section)."""
    table = portal_row['primary_order_table']
    oid = portal_row['primary_order_id']
    if table not in ('lawyer_ad_orders', 'bail_ad_orders') or not oid:
        return ''
    try:
        row = conn.execute(
            f'SELECT provider_customer_id AS c FROM {table} WHERE id = ?',
            (oid,)).fetchone()
    except Exception:
        return ''
    return (row['c'] or '') if row else ''


def admin_clients(conn, search: str = '', status: str = '') -> list:
    """Tab 2: Client Roster & Portals — active portals with order link
    info, features, and the vanity-or-token URL segment."""
    where, params = ['1=1'], []
    if search:
        where.append('ap.business_name LIKE ?')
        params.append(f'%{search.strip()}%')
    if status:
        where.append('cp.portal_status = ?')
        params.append(status)
    return conn.execute(
        f'''SELECT cp.id, cp.tier, cp.portal_status, cp.features,
                   cp.slug, cp.access_token, cp.created_at,
                   ap.id AS lead_id, ap.business_name, ap.stage
            FROM client_portals cp
            JOIN advertising_prospects ap ON ap.id = cp.lead_id
            WHERE {' AND '.join(where)}
            ORDER BY cp.updated_at DESC''', params).fetchall()


def mrr_summary(conn) -> dict:
    """Tab 3: active MRR from crm_subscriptions. Monthly is amount_cents;
    annual/quarterly are pro-rated to a monthly figure. Stripe remains the
    source of truth — this is a read rollup only."""
    rows = billing_rollup(conn)
    monthly_cents = 0
    active = 0
    by_status: dict[str, int] = {}
    for r in rows:
        status = (r['status'] or '').lower()
        by_status[status] = by_status.get(status, 0) + 1
        if status not in ('active', 'trialing'):
            continue
        active += 1
        cents = r['amount_cents'] or 0
        cycle = (r['billing_cycle'] or 'monthly').lower()
        if cycle == 'annual':
            monthly_cents += cents // 12
        elif cycle == 'quarterly':
            monthly_cents += cents // 3
        else:
            monthly_cents += cents
    return {'mrr_cents': monthly_cents, 'active_subscriptions': active,
            'by_status': by_status}


def portal_context(conn, token_or_slug: str):
    """Resolve a portal by token (always) or vanity slug. None if invalid,
    closed/suspended, or not found."""
    token_or_slug = (token_or_slug or '').strip()
    if not (valid_token(token_or_slug) or normalize_slug(token_or_slug) == token_or_slug):
        return None
    row = conn.execute(
        'SELECT cp.*, ap.business_name, ap.email AS lead_email, ap.phone AS lead_phone, '
        '       ap.counties, ap.stage '
        'FROM client_portals cp JOIN advertising_prospects ap ON ap.id = cp.lead_id '
        'WHERE (cp.access_token = ? OR cp.slug = ?) '
        "AND cp.portal_status IN ('invited','active')",
        (token_or_slug, token_or_slug)).fetchone()
    return row
