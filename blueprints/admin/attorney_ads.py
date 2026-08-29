"""
Admin panel — Attorney directory listings and sponsorship claims.
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from db import get_db
from blueprints.admin import admin_bp, _log_admin_action


_TIER_CHOICES = {'': 'Free/Bronze', 'silver': 'Silver', 'gold': 'Gold'}
_CLAIM_STATUS_CHOICES = {'pending', 'approved', 'rejected'}


def _counties_from_text(text: str) -> list[str]:
    return [c.strip() for c in (text or '').split(',') if c.strip()]


@admin_bp.route('/revenue/attorney-ads')
@login_required
def admin_attorney_ads():
    from init_db import ensure_attorney_ad_schema
    conn = get_db()
    ensure_attorney_ad_schema(conn)

    q = (request.args.get('q') or '').strip()[:120]
    county_filter = (request.args.get('county') or '').strip().lower()
    status_filter = (request.args.get('status') or '').strip().lower()

    listings_query = '''
        SELECT id, county, name, firm, phone, email, website, practice_areas,
               blurb, tagline, is_active, sponsored, sponsor_tier, sort_order
        FROM attorney_referrals
        WHERE 1=1
    '''
    listings_params: list = []
    if q:
        listings_query += ' AND (name LIKE ? OR firm LIKE ? OR county LIKE ?)'
        like = f'%{q}%'
        listings_params.extend([like, like, like])
    if county_filter:
        listings_query += ' AND lower(county) = ?'
        listings_params.append(county_filter)
    if status_filter == 'active':
        listings_query += ' AND is_active = 1'
    elif status_filter == 'inactive':
        listings_query += ' AND is_active = 0'
    elif status_filter == 'sponsored':
        listings_query += ' AND sponsored = 1'
    listings_query += ' ORDER BY county, sponsored DESC, sort_order ASC, name ASC LIMIT 500'
    listings = [dict(r) for r in conn.execute(listings_query, listings_params).fetchall()]

    claims = [dict(r) for r in conn.execute(
        '''
        SELECT id, firm_name, contact_name, contact_email, contact_phone,
               counties_served, tier_requested, website, practice_areas, blurb,
               mt_bar_number, status, admin_notes, created_at
        FROM attorney_sponsored_claims
        ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC
        LIMIT 200
        '''
    ).fetchall()]

    counties = [dict(r) for r in conn.execute(
        '''
        SELECT DISTINCT county_name, county_slug
        FROM jail_bookings
        WHERE county_name IS NOT NULL AND county_name != ''
        ORDER BY county_name
        '''
    ).fetchall()]

    stats = dict(conn.execute(
        '''
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active,
            SUM(CASE WHEN sponsored = 1 THEN 1 ELSE 0 END) AS sponsored,
            COUNT(DISTINCT county) AS counties
        FROM attorney_referrals
        '''
    ).fetchone())
    pending_claims = conn.execute(
        "SELECT COUNT(*) FROM attorney_sponsored_claims WHERE status = 'pending'"
    ).fetchone()[0]

    conn.close()
    return render_template(
        'admin_attorney_ads.html',
        listings=listings,
        claims=claims,
        counties=counties,
        stats=stats,
        pending_claims=pending_claims,
        q=q,
        county_filter=county_filter,
        status_filter=status_filter,
        tier_choices=_TIER_CHOICES,
        current_year=datetime.now().year,
    )


@admin_bp.route('/revenue/attorney-ads/listing/new', methods=['GET', 'POST'])
@login_required
def admin_attorney_ads_listing_new():
    if request.method == 'POST':
        data = _listing_form_data(request)
        if not data['name'] or not data['county']:
            flash('Name and county are required.', 'error')
            return redirect(url_for('.admin_attorney_ads_listing_new'))
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO attorney_referrals
              (county, name, firm, phone, email, website, practice_areas, blurb,
               tagline, is_active, sort_order, sponsored, sponsor_tier, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ''',
            (
                data['county'], data['name'], data['firm'], data['phone'], data['email'],
                data['website'], data['practice_areas'], data['blurb'], data['tagline'],
                data['is_active'], data['sort_order'], data['sponsored'], data['sponsor_tier'],
            ),
        )
        conn.commit()
        listing_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        _log_admin_action('attorney_listing_create', 'attorney_referral', listing_id, conn=conn)
        conn.close()
        flash('Attorney listing created.', 'success')
        return redirect(url_for('.admin_attorney_ads'))
    return render_template('admin_attorney_ads_edit.html', listing=None, tier_choices=_TIER_CHOICES,
                           current_year=datetime.now().year)


@admin_bp.route('/revenue/attorney-ads/listing/<int:listing_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_attorney_ads_listing_edit(listing_id):
    conn = get_db()
    listing = conn.execute(
        'SELECT * FROM attorney_referrals WHERE id = ?', (listing_id,)
    ).fetchone()
    if not listing:
        conn.close()
        return render_template('404.html'), 404

    if request.method == 'POST':
        data = _listing_form_data(request)
        conn.execute(
            '''
            UPDATE attorney_referrals
            SET county = ?, name = ?, firm = ?, phone = ?, email = ?, website = ?,
                practice_areas = ?, blurb = ?, tagline = ?, is_active = ?,
                sort_order = ?, sponsored = ?, sponsor_tier = ?
            WHERE id = ?
            ''',
            (
                data['county'], data['name'], data['firm'], data['phone'], data['email'],
                data['website'], data['practice_areas'], data['blurb'], data['tagline'],
                data['is_active'], data['sort_order'], data['sponsored'], data['sponsor_tier'],
                listing_id,
            ),
        )
        conn.commit()
        _log_admin_action('attorney_listing_edit', 'attorney_referral', listing_id, conn=conn)
        conn.close()
        flash('Attorney listing updated.', 'success')
        return redirect(url_for('.admin_attorney_ads'))

    conn.close()
    return render_template(
        'admin_attorney_ads_edit.html',
        listing=dict(listing),
        tier_choices=_TIER_CHOICES,
        current_year=datetime.now().year,
    )


@admin_bp.route('/revenue/attorney-ads/listing/<int:listing_id>/status', methods=['POST'])
@login_required
def admin_attorney_ads_listing_status(listing_id):
    is_active = 1 if request.form.get('is_active') else 0
    conn = get_db()
    conn.execute('UPDATE attorney_referrals SET is_active = ? WHERE id = ?', (is_active, listing_id))
    conn.commit()
    _log_admin_action('attorney_listing_status', 'attorney_referral', listing_id,
                      metadata={'is_active': is_active}, conn=conn)
    conn.close()
    flash('Listing status updated.', 'success')
    return redirect(url_for('.admin_attorney_ads'))


@admin_bp.route('/revenue/attorney-ads/listing/<int:listing_id>/tier', methods=['POST'])
@login_required
def admin_attorney_ads_listing_tier(listing_id):
    tier = (request.form.get('sponsor_tier') or '').strip()
    if tier not in {'', 'silver', 'gold'}:
        flash('Invalid tier.', 'error')
        return redirect(url_for('.admin_attorney_ads'))
    conn = get_db()
    conn.execute(
        'UPDATE attorney_referrals SET sponsor_tier = ?, sponsored = ? WHERE id = ?',
        (tier or None, 1 if tier else 0, listing_id),
    )
    conn.commit()
    _log_admin_action('attorney_listing_tier', 'attorney_referral', listing_id,
                      metadata={'tier': tier}, conn=conn)
    conn.close()
    flash('Sponsor tier updated.', 'success')
    return redirect(url_for('.admin_attorney_ads'))


@admin_bp.route('/revenue/attorney-ads/listing/<int:listing_id>/delete', methods=['POST'])
@login_required
def admin_attorney_ads_listing_delete(listing_id):
    conn = get_db()
    conn.execute('DELETE FROM attorney_referrals WHERE id = ?', (listing_id,))
    conn.commit()
    _log_admin_action('attorney_listing_delete', 'attorney_referral', listing_id, conn=conn)
    conn.close()
    flash('Listing deleted.', 'success')
    return redirect(url_for('.admin_attorney_ads'))


@admin_bp.route('/revenue/attorney-ads/claim/<int:claim_id>/status', methods=['POST'])
@login_required
def admin_attorney_ads_claim_status(claim_id):
    status = (request.form.get('status') or '').strip().lower()
    if status not in _CLAIM_STATUS_CHOICES:
        flash('Invalid claim status.', 'error')
        return redirect(url_for('.admin_attorney_ads'))

    admin_notes = (request.form.get('admin_notes') or '').strip()[:1200]
    conn = get_db()
    claim = conn.execute(
        'SELECT * FROM attorney_sponsored_claims WHERE id = ?', (claim_id,)
    ).fetchone()
    if not claim:
        conn.close()
        return render_template('404.html'), 404

    conn.execute(
        '''
        UPDATE attorney_sponsored_claims
        SET status = ?, admin_notes = ?, reviewed_at = datetime('now')
        WHERE id = ?
        ''',
        (status, admin_notes, claim_id),
    )

    if status == 'approved':
        counties = _counties_from_text(claim['counties_served'])
        if not counties:
            counties = ['']
        for county in counties:
            existing = conn.execute(
                '''
                SELECT id FROM attorney_referrals
                WHERE lower(county) = lower(?) AND lower(name) = lower(?) AND lower(COALESCE(firm, '')) = lower(?)
                LIMIT 1
                ''',
                (county, claim['contact_name'], claim['firm_name'] or ''),
            ).fetchone()
            tagline = _build_tagline_from_claim(claim)
            blurb = claim['blurb'] or f'Criminal defense attorney serving {county} County.'
            if existing:
                conn.execute(
                    '''
                    UPDATE attorney_referrals
                    SET firm = ?, phone = ?, email = ?, website = ?,
                        practice_areas = ?, blurb = ?, tagline = ?,
                        is_active = 1, sponsored = 1, sponsor_tier = ?
                    WHERE id = ?
                    ''',
                    (
                        claim['firm_name'], claim['contact_phone'], claim['contact_email'],
                        claim['website'], claim['practice_areas'], blurb, tagline,
                        claim['tier_requested'], existing['id'],
                    ),
                )
            else:
                conn.execute(
                    '''
                    INSERT INTO attorney_referrals
                      (county, name, firm, phone, email, website, practice_areas, blurb,
                       tagline, is_active, sort_order, sponsored, sponsor_tier, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 10, 1, ?, datetime('now'))
                    ''',
                    (
                        county, claim['contact_name'], claim['firm_name'],
                        claim['contact_phone'], claim['contact_email'], claim['website'],
                        claim['practice_areas'], blurb, tagline, claim['tier_requested'],
                    ),
                )

    conn.commit()
    _log_admin_action('attorney_claim_status', 'attorney_sponsored_claim', claim_id,
                      metadata={'status': status}, conn=conn)
    conn.close()
    flash(f'Claim {status}.', 'success')
    return redirect(url_for('.admin_attorney_ads'))


@admin_bp.route('/revenue/attorney-ads/import-csv', methods=['POST'])
@login_required
def admin_attorney_ads_import_csv():
    upload = request.files.get('csv_file')
    if not upload or upload.filename == '':
        flash('No CSV file uploaded.', 'error')
        return redirect(url_for('.admin_attorney_ads'))

    from scripts.attorney_outreach.import_target_list import import_attorneys
    stream = io.StringIO(upload.stream.read().decode('utf-8'), newline='')
    # import_attorneys expects a path; reuse its row parsing by writing to a temp file.
    fd, tmp_path = tempfile.mkstemp(suffix='.csv')
    try:
        os.close(fd)
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(stream.getvalue())
        conn = get_db()
        counts = import_attorneys(conn, tmp_path, dry_run=False)
        conn.close()
    finally:
        os.unlink(tmp_path)

    flash(
        f'CSV imported: {counts["inserted"]} inserted, {counts["updated"]} updated, '
        f'{counts["skipped_sponsored"]} sponsored rows preserved.',
        'success',
    )
    return redirect(url_for('.admin_attorney_ads'))


def _listing_form_data(request) -> dict[str, object]:
    return {
        'county': (request.form.get('county') or '').strip()[:80],
        'name': (request.form.get('name') or '').strip()[:120],
        'firm': (request.form.get('firm') or '').strip()[:120] or None,
        'phone': (request.form.get('phone') or '').strip()[:40] or None,
        'email': (request.form.get('email') or '').strip()[:120] or None,
        'website': (request.form.get('website') or '').strip()[:255] or None,
        'practice_areas': (request.form.get('practice_areas') or '').strip()[:500] or None,
        'blurb': (request.form.get('blurb') or '').strip()[:1000] or None,
        'tagline': (request.form.get('tagline') or '').strip()[:120] or None,
        'is_active': 1 if request.form.get('is_active') else 0,
        'sort_order': int(request.form.get('sort_order') or 100),
        'sponsored': 1 if request.form.get('sponsored') else 0,
        'sponsor_tier': (request.form.get('sponsor_tier') or '').strip() or None,
    }


def _build_tagline_from_claim(claim: sqlite3.Row) -> str:
    areas = (claim['practice_areas'] or '').strip()
    if areas:
        parts = [a.strip() for a in areas.split(',') if a.strip()][:2]
        if parts:
            return f"{' & '.join(parts)} attorney"
    return 'Criminal defense attorney'
