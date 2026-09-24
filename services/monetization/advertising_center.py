"""Business pipeline and aggregate media-kit facts. No outbound messaging."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

import config

STAGES = {'new': 'New lead', 'contacted': 'Contacted', 'proposal_sent': 'Proposal sent',
          'active': 'Active customer', 'lost': 'Lost', 'paused': 'Paused'}
CATEGORIES = {'general': 'Local business', 'bail': 'Bail bonds',
              'recovery': 'Recovery', 'mixed': 'Multiple products'}
# Whitelisted identifiers only. Source rows are never updated by this module.
SOURCES = {
    'advertise_sales_leads': ('Sales inquiry', 'firm_or_agency', 'email', 'county', 'general', ''),
    'bail_ad_orders': ('Bail order', 'business_name', 'email', 'county_targets', 'bail', '/admin/revenue/bail-ads'),
    'bail_ad_inquiries': ('Bail inquiry', 'business_name', 'email', 'counties_served', 'bail', '/admin/revenue/bail-ads'),
    'bail_agency_outreach': ('Bail outreach', 'agency_name', 'email', 'counties', 'bail', '/admin/revenue/bail-ads'),
    'recovery_ad_orders': ('Recovery order', 'center_name', 'email', '', 'recovery', '/admin/revenue/recovery-ads'),
}


def business_key(value):
    # Retain words/suffixes; do not fuzzy-merge businesses with similar names.
    return re.sub(r'[^\w]+', ' ', (value or '').casefold()).strip()


def county_slug(value):
    return re.sub(r'\s+', '-', value.lower().replace('&', 'and').strip())


def county_list(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        parsed = str(value).split(',')
    if not isinstance(parsed, list):
        parsed = str(value).split(',')
    lookup = {county_slug(c): c for c in config.MONTANA_COUNTIES}
    return sorted({lookup[county_slug(str(c))] for c in parsed
                   if county_slug(str(c)) in lookup})


def validate_prospect(form):
    limits = {'business_name': 160, 'contact_name': 120, 'email': 254,
              'phone': 60, 'notes': 4000, 'next_follow_up': 10}
    result = {}
    for field, limit in limits.items():
        value = (form.get(field) or '').strip()
        if len(value) > limit:
            raise ValueError(f'{field.replace("_", " ").title()} is too long (maximum {limit}).')
        result[field] = value
    result['business_key'] = business_key(result['business_name'])
    if not result['business_key']:
        raise ValueError('Enter a business name.')
    result['email'] = result['email'].lower()
    if result['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', result['email']):
        raise ValueError('Enter a valid email address, or leave it blank.')
    if result['next_follow_up']:
        try:
            date.fromisoformat(result['next_follow_up'])
        except ValueError:
            raise ValueError('Follow-up must be a valid date (YYYY-MM-DD).')
    result['category'] = form.get('category', 'general')
    result['stage'] = form.get('stage', 'new')
    if result['category'] not in CATEGORIES or result['stage'] not in STAGES:
        raise ValueError('Choose a valid category and stage.')
    selected = form.getlist('counties') if hasattr(form, 'getlist') else form.get('counties', [])
    if any(c not in config.MONTANA_COUNTIES for c in selected):
        raise ValueError('Choose a Montana county from the list.')
    result['counties'] = ', '.join(sorted(set(selected)))
    return result


def source_records(conn):
    """Explicit safe columns only: no tokens, billing IDs, or consumer leads."""
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    records = []
    for table, (label, name_col, email_col, county_col, category, href) in SOURCES.items():
        if table not in existing:
            continue
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        selected = {'id', name_col, email_col}
        selected.update(c for c in [county_col, 'contact_name', 'phone', 'status', 'name', 'product', 'message', 'package_interest'] if c in columns)
        for row in conn.execute(f'SELECT {", ".join(sorted(selected))} FROM {table}'):
            row = dict(row)
            name = (row.get(name_col) or '').strip()
            if not business_key(name):
                continue
            records.append({'source_type': table, 'source_id': row['id'], 'label': label,
                            'business_name': name, 'business_key': business_key(name),
                            'email': (row.get(email_col) or '').strip().lower(),
                            'phone': row.get('phone') or '',
                            'contact_name': row.get('contact_name') or row.get('name') or '',
                            'counties': county_list(row.get(county_col)),
                            'category': row.get('product') if row.get('product') in CATEGORIES else category,
                            'status': row.get('status') or '',
                            'message': row.get('message') or '',
                            'package_interest': row.get('package_interest') or '',
                            'href': href.format(id=row['id']) if href else ''})
    return records


def source_groups(conn):
    linked = {(r[0], r[1]) for r in conn.execute('SELECT source_type, source_id FROM advertising_prospect_sources')}
    groups = {}
    for record in source_records(conn):
        if (record['source_type'], record['source_id']) in linked:
            continue
        group = groups.setdefault(record['business_key'], {'business_name': record['business_name'],
                                  'key': record['business_key'], 'records': [], 'emails': set(), 'counties': set()})
        group['records'].append(record)
        if record['email']:
            group['emails'].add(record['email'])
        group['counties'].update(record['counties'])
    return sorted(groups.values(), key=lambda g: g['key'])


def link_group(conn, key, prospect_id=None):
    """User-selected group: link to a business, never merge or alter source rows."""
    group = next((g for g in source_groups(conn) if g['key'] == key), None)
    if not group:
        raise ValueError('This source group is already linked or no longer available.')
    if prospect_id:
        target = conn.execute('SELECT id FROM advertising_prospects WHERE id=?', (prospect_id,)).fetchone()
        if not target:
            raise ValueError('Choose an existing business.')
    else:
        target = conn.execute('SELECT id FROM advertising_prospects WHERE business_key=?', (key,)).fetchone()
    if target:
        prospect_id = target['id']
    else:
        categories = {r['category'] for r in group['records']}
        # Ambiguous contact details stay in linked source records for review.
        emails = group['emails']
        cur = conn.execute('''INSERT INTO advertising_prospects
            (business_name,business_key,email,counties,category)
            VALUES (?,?,?,?,?)''', (group['business_name'], key,
                next(iter(emails)) if len(emails) == 1 else '',
                ', '.join(sorted(group['counties'])),
                next(iter(categories)) if len(categories) == 1 else 'mixed'))
        prospect_id = cur.lastrowid
    for record in group['records']:
        conn.execute('''INSERT INTO advertising_prospect_sources(source_type,source_id,prospect_id)
                        VALUES (?,?,?)''', (record['source_type'], record['source_id'], prospect_id))
    return prospect_id


def validate_inquiry(form, county):
    """Advertiser contact request, never a consumer/legal lead or subscription."""
    result = {}
    for field, maximum in [('business_name',160), ('contact_name',120), ('email',254),
                           ('phone',60), ('message',1500)]:
        value = (form.get(field) or '').strip()
        if len(value) > maximum:
            raise ValueError(f'{field.replace("_", " ").title()} is too long (maximum {maximum}).')
        result[field] = value
    if not business_key(result['business_name']) or not result['contact_name']:
        raise ValueError('Please provide your business name and contact name.')
    result['email'] = result['email'].lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', result['email']):
        raise ValueError('Please provide a valid email address.')
    if county not in config.MONTANA_COUNTIES:
        raise ValueError('Choose a valid Montana county.')
    result['county'] = county
    result['product'] = form.get('product', 'general')
    if result['product'] not in {'general','bail','recovery'}:
        raise ValueError('Choose a valid advertising interest.')
    if form.get('contact_ok') != 'yes':
        raise ValueError('Please confirm that we may contact you about this request.')
    return result


def save_inquiry(conn, values, ip_hash, user_agent=''):
    """Caller holds transaction. Repeated identical submissions do not add rows."""
    recent = conn.execute('''SELECT firm_or_agency,name,phone,message FROM advertise_sales_leads
        WHERE source='county_media_kit' AND lower(email)=? AND county=? AND product=?
        AND created_at >= datetime('now','-1 day')''',
        (values['email'], values['county'], values['product'])).fetchall()
    for row in recent:
        if (business_key(row['firm_or_agency']) == business_key(values['business_name'])
                and row['message'] == values['message'] and row['phone'] == values['phone']
                and row['name'] == values['contact_name']):
            return False
    count = conn.execute('''SELECT COUNT(*) FROM advertise_sales_leads WHERE source='county_media_kit'
        AND (ip_hash=? OR lower(email)=?) AND created_at >= datetime('now','-1 hour')''',
        (ip_hash, values['email'])).fetchone()[0]
    if count >= 5:
        raise ValueError('Too many requests in a short period. Please try again in an hour.')
    conn.execute('''INSERT INTO advertise_sales_leads
        (product,name,firm_or_agency,email,phone,county,message,source,ip_hash,user_agent,status)
        VALUES (?,?,?,?,?,?,?,'county_media_kit',?,?,'new')''',
        (values['product'],values['contact_name'],values['business_name'],values['email'],
         values['phone'],values['county'],values['message'],ip_hash,user_agent[:500]))
    return True


def build_proposal(prospect, county, package_id, billing_cycle, packages):
    """Non-binding draft text; excludes internal notes, private source data and guessed results."""
    if county not in config.MONTANA_COUNTIES:
        raise ValueError('Choose a valid Montana county.')
    if billing_cycle not in {'monthly','annual'}:
        raise ValueError('Choose monthly or annual billing.')
    pkg = next((p for p in packages if p['id'] == package_id), None)
    if package_id != 'custom' and pkg is None:
        raise ValueError('Choose a valid package.')
    lines = ['MONTANA BLOTTER — ADVERTISING PROPOSAL DRAFT',
             'Non-binding. Review before sharing. No placement is reserved.', '',
             f'Prepared for: {prospect["business_name"]}',
             f'County: {county}', f'Prepared: {date.today().isoformat()} UTC', '']
    if pkg:
        cents = pkg['price_annual_cents'] if billing_cycle == 'annual' else pkg['price_monthly_cents']
        frequency = 'year, billed annually' if billing_cycle == 'annual' else 'month, billed monthly'
        lines += [f'Placement: {pkg["type"]}',
                  f'Published price: ${cents / 100:,.2f} per {frequency}',
                  pkg['short_description'],
                  'County inventory and eligibility must be confirmed at checkout.', '']
    else:
        lines += ['Placement: Custom local-business or newsletter sponsorship',
                  'Price, placement, and campaign dates: to be agreed in writing.',
                  'No new package price or guaranteed availability is implied.', '']
    lines += ['Current county media kit:',
              f'https://montanablotter.com/advertise/media-kit/{county_slug(county)}', '',
              'Before booking, agree on:',
              '- Campaign dates and exact placement',
              '- Approved creative and clearly labeled advertising',
              '- Any sensitive-content exclusions',
              '- Reporting method and final commercial terms', '',
              'Measurement: county media-kit traffic is raw recorded requests, not verified people',
              'or ad impressions. Phone-link clicks are not confirmed calls; inquiries are not',
              'confirmed customers. No audience, lead volume, or revenue result is guaranteed.', '',
              'Next step: reply with your preferred county, placement, and campaign dates.',
              'This draft does not create an order, charge, subscription, or email.']
    return '\n'.join(lines)


def media_facts(conn, pv_conn, county, now=None):
    now = now or datetime.now(timezone.utc)
    end = now.strftime('%Y-%m-%d %H:%M:%S')
    start = (now - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    slug = county_slug(county)
    prefix = '/county/' + slug
    # Raw requests, not people/impressions. Exact county hub and archive only.
    indices = {r[1] for r in pv_conn.execute('PRAGMA index_list(page_views)')}
    index_hint = 'INDEXED BY idx_page_views_path' if 'idx_page_views_path' in indices else ''
    traffic = pv_conn.execute(f'''SELECT COUNT(*) AS requests, MIN(created_at) AS first_seen,
        MAX(created_at) AS last_seen FROM page_views {index_hint}
        WHERE created_at >= ? AND created_at < ?
        AND (path = ? OR (path >= ? AND path < ?))''',
        (start, end, prefix, prefix + '/', prefix + '0')).fetchone()
    explicit = 0
    for row in conn.execute('SELECT counties, COUNT(*) AS total FROM subscribers WHERE active=1 GROUP BY counties'):
        if county in county_list(row['counties']):
            explicit += row['total']
    return {'county': county, 'slug': slug, 'start': start, 'end': end,
            'requests': traffic['requests'], 'first_seen': traffic['first_seen'],
            'last_seen': traffic['last_seen'], 'county_subscribers': explicit}
