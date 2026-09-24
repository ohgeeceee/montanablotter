"""One business pipeline, existing product tools, and a public county media kit."""
from __future__ import annotations

import logging
import hashlib
import hmac
import sqlite3
import time
from contextlib import closing
from datetime import date
from threading import Lock

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user

import config
from db import get_db, connect_page_views
from blueprints.admin import admin_bp, require_role, _log_admin_action
from services.monetization.advertising_center import (
    STAGES, CATEGORIES, county_list, county_slug, validate_prospect,
    source_groups, source_records, link_group, media_facts,
    validate_inquiry, save_inquiry, build_proposal,
)

log = logging.getLogger(__name__)
READ_ROLES = ('super_admin', 'ops', 'revenue', 'read_only')
WRITE_ROLES = ('super_admin', 'ops', 'revenue')
media_kit_bp = Blueprint('advertising_media', __name__)
_facts_cache = {}
_facts_lock = Lock()


@admin_bp.route('/revenue/advertising')
@require_role(*READ_ROLES)
def advertising_center():
    q = (request.args.get('q') or '').strip()[:120]
    stage = request.args.get('stage', '')
    due = request.args.get('due') == '1'
    source_q = (request.args.get('source_q') or '').strip()[:120]
    where, params = ['1=1'], []
    if q:
        where.append('(business_name LIKE ? OR email LIKE ? OR counties LIKE ?)')
        params.extend(['%' + q + '%'] * 3)
    if stage in STAGES:
        where.append('stage=?')
        params.append(stage)
    if due:
        where.append("next_follow_up != '' AND next_follow_up <= ? AND stage NOT IN ('lost','paused')")
        params.append(date.today().isoformat())
    with closing(get_db()) as conn:
        prospects = [dict(r) for r in conn.execute(f'''SELECT * FROM advertising_prospects
            WHERE {' AND '.join(where)} ORDER BY
            CASE WHEN next_follow_up='' THEN 1 ELSE 0 END, next_follow_up, business_name LIMIT 200''', params)]
        total = conn.execute('SELECT COUNT(*) FROM advertising_prospects').fetchone()[0]
        due_count = conn.execute("""SELECT COUNT(*) FROM advertising_prospects WHERE next_follow_up != ''
            AND next_follow_up <= ? AND stage NOT IN ('lost','paused')""", (date.today().isoformat(),)).fetchone()[0]
        groups = source_groups(conn)
        source_count = len(groups)
        if source_q:
            needle = source_q.casefold()
            groups = [g for g in groups if needle in ' '.join(
                [g['business_name'], *g['emails'], *g['counties']]).casefold()]
        choices = [dict(r) for r in conn.execute('SELECT id,business_name FROM advertising_prospects ORDER BY business_name')]
    return render_template('admin_advertising_center.html', prospects=prospects,
                           total=total, due_count=due_count, groups=groups, choices=choices,
                           q=q, stage=stage, due=due, stages=STAGES, source_q=source_q, source_count=source_count,
                           counties=config.MONTANA_COUNTIES, county_slug=county_slug,
                           can_write=current_user.role in WRITE_ROLES)


@admin_bp.route('/revenue/advertising/new', methods=['GET', 'POST'])
@admin_bp.route('/revenue/advertising/<int:prospect_id>', methods=['GET', 'POST'])
@require_role(*READ_ROLES)
def advertising_prospect(prospect_id=None):
    if (request.method == 'POST' or prospect_id is None) and current_user.role not in WRITE_ROLES:
        abort(403)
    error, status, links, warnings = '', 200, [], []
    with closing(get_db()) as conn:
        record = conn.execute('SELECT * FROM advertising_prospects WHERE id=?', (prospect_id,)).fetchone() if prospect_id else None
        if prospect_id and not record:
            abort(404)
        values = dict(record) if record else {'stage': 'new', 'category': 'general', 'counties': '', 'version': 0}
        if request.method == 'POST':
            values.update(request.form.to_dict())
            values['counties'] = ', '.join(request.form.getlist('counties'))
            try:
                cleaned = validate_prospect(request.form)
                conn.execute('BEGIN IMMEDIATE')
                duplicate = conn.execute('SELECT id FROM advertising_prospects WHERE business_key=? AND id != ?',
                                         (cleaned['business_key'], prospect_id or 0)).fetchone()
                if duplicate:
                    raise ValueError(f'Business already exists as prospect #{duplicate[0]}. Open that record instead of creating a duplicate.')
                if prospect_id:
                    version = request.form.get('version', type=int)
                    sets = ', '.join(f'{key}=?' for key in cleaned)
                    changed = conn.execute(f'''UPDATE advertising_prospects SET {sets},
                        version=version+1, updated_at=datetime('now') WHERE id=? AND version=?''',
                        (*cleaned.values(), prospect_id, version)).rowcount
                    if not changed:
                        raise ValueError('Someone else updated this business. Reload the page before saving again.')
                else:
                    fields = ', '.join(cleaned)
                    placeholders = ', '.join('?' for _ in cleaned)
                    prospect_id = conn.execute(f'INSERT INTO advertising_prospects ({fields}) VALUES ({placeholders})',
                                               tuple(cleaned.values())).lastrowid
                _log_admin_action('advertising_prospect_saved', 'advertising_prospect', prospect_id,
                                  metadata={'stage': cleaned['stage']}, conn=conn)
                conn.commit()
                flash('Business saved. No emails were sent and no paid placements were changed.', 'success')
                return redirect(url_for('.advertising_prospect', prospect_id=prospect_id), code=303)
            except (ValueError, sqlite3.IntegrityError) as exc:
                conn.rollback()
                error = str(exc) if isinstance(exc, ValueError) else 'This business or source is already linked. Reload and review the existing record.'
                status = 400
        if prospect_id:
            refs = {(r[0], r[1]) for r in conn.execute('SELECT source_type,source_id FROM advertising_prospect_sources WHERE prospect_id=?', (prospect_id,))}
            links = [r for r in source_records(conn) if (r['source_type'], r['source_id']) in refs]
        if values.get('email'):
            warnings = [dict(r) for r in conn.execute('SELECT id,business_name FROM advertising_prospects WHERE lower(email)=lower(?) AND id != ?',
                        (values['email'], prospect_id or 0))]
    return render_template('admin_advertising_prospect.html', prospect=values, prospect_id=prospect_id,
                           selected_counties=county_list(values.get('counties')), counties=config.MONTANA_COUNTIES,
                           county_slug=county_slug, stages=STAGES, categories=CATEGORIES, links=links,
                           error=error, warnings=warnings, can_write=current_user.role in WRITE_ROLES), status


@admin_bp.route('/revenue/advertising/link-source', methods=['POST'])
@require_role(*WRITE_ROLES)
def advertising_link_source():
    with closing(get_db()) as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            target = request.form.get('prospect_id', '')
            if target and not target.isdigit():
                raise ValueError('Choose an existing business or create a new one.')
            prospect_id = link_group(conn, request.form.get('source_key', ''), int(target) if target else None)
            _log_admin_action('advertising_source_linked', 'advertising_prospect', prospect_id, conn=conn)
            conn.commit()
        except (ValueError, sqlite3.IntegrityError) as exc:
            conn.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else 'Source already linked. Refresh to see the current record.', 'error')
            return redirect(url_for('.advertising_center'), code=303)
    flash('Source records linked. Existing orders and outreach history are unchanged.', 'success')
    return redirect(url_for('.advertising_prospect', prospect_id=prospect_id), code=303)


@admin_bp.route('/revenue/advertising/<int:prospect_id>/proposal')
@require_role(*READ_ROLES)
def advertising_proposal(prospect_id):
    from blueprints.bail_bond_ads import _bail_ad_packages
    _PACKAGES = [p for p in _bail_ad_packages() if p.get('type')]
    with closing(get_db()) as conn:
        row = conn.execute('SELECT business_name,counties,category FROM advertising_prospects WHERE id=?', (prospect_id,)).fetchone()
    if not row:
        abort(404)
    prospect = dict(row)
    counties = county_list(prospect['counties'])
    county = request.args.get('county', counties[0] if counties else 'Cascade')
    package_id = request.args.get('package', 'custom')
    cycle = request.args.get('cycle', 'monthly')
    error, draft = '', ''
    try:
        draft = build_proposal(prospect, county, package_id, cycle, _PACKAGES)
    except ValueError as exc:
        error = str(exc)
    if request.args.get('format') == 'txt' and not error:
        return Response(draft, mimetype='text/plain', headers={
            'Content-Disposition': f'attachment; filename="montana-blotter-proposal-{prospect_id}.txt"',
            'Cache-Control': 'private, no-store'})
    response = current_app.make_response((render_template('admin_advertising_proposal.html',
        prospect=prospect, prospect_id=prospect_id, counties=config.MONTANA_COUNTIES,
        county=county, packages=_PACKAGES, package_id=package_id, cycle=cycle,
        draft=draft, error=error), 400 if error else 200))
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@media_kit_bp.route('/advertise/media-kit/<slug>/inquire', methods=['POST'])
def county_advertising_inquiry(slug):
    counties = {county_slug(c): c for c in config.MONTANA_COUNTIES}
    if slug not in counties:
        abort(404)
    token, submitted = session.get('_csrf_token', ''), request.form.get('csrf_token', '')
    if not token or not submitted or not hmac.compare_digest(token, submitted):
        return county_media_kit(slug, form_error='Your security token expired. Please try again.',
                               form_values=request.form.to_dict(), response_status=400)
    if request.form.get('company_url'):
        session['media_kit_submitted'] = slug
        return redirect(url_for('.county_media_kit', slug=slug) + '#inquire', code=303)
    try:
        values = validate_inquiry(request.form, counties[slug])
        from app import _client_ip
        ip_hash = hmac.new(str(current_app.secret_key).encode(), (_client_ip() or '').encode(), hashlib.sha256).hexdigest()
        with closing(get_db()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                save_inquiry(conn, values, ip_hash, request.headers.get('User-Agent', ''))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except ValueError as exc:
        return county_media_kit(slug, form_error=str(exc), form_values=request.form.to_dict(), response_status=400)
    except sqlite3.Error:
        log.exception('County advertising inquiry could not be saved')
        return county_media_kit(slug, form_error='We could not save your request. Please try again shortly.',
                               form_values=request.form.to_dict(), response_status=503)
    session['media_kit_submitted'] = slug
    return redirect(url_for('.county_media_kit', slug=slug) + '#inquire', code=303)


@media_kit_bp.route('/advertise/media-kit')
@media_kit_bp.route('/advertise/media-kit/<slug>')
def county_media_kit(slug='cascade', form_error='', form_values=None, response_status=200):
    from blueprints.bail_bond_ads import _bail_ad_packages
    _PACKAGES = [p for p in _bail_ad_packages() if p.get('type')]

    counties = {county_slug(c): c for c in config.MONTANA_COUNTIES}
    if slug not in counties:
        abort(404)
    facts, capacity, unavailable = None, None, False
    # Five-minute, bounded (56 counties) per-worker cache prevents repeated scans.
    with _facts_lock:
        cached = _facts_cache.get(slug)
        if cached and time.monotonic() - cached[0] < 300:
            facts, capacity = cached[1], cached[2]
        else:
            try:
                with closing(get_db()) as conn, closing(connect_page_views()) as pv_conn:
                    started = time.monotonic()
                    pv_conn.set_progress_handler(lambda: int(time.monotonic() - started > 3), 10000)
                    facts = media_facts(conn, pv_conn, counties[slug])
                _facts_cache[slug] = (time.monotonic(), facts, False)
            except sqlite3.Error:
                log.exception('County media-kit data unavailable for %s', slug)
                unavailable = True
    return render_template('advertising_media_kit.html', facts=facts,
                           unavailable=unavailable, county=counties[slug], county_options=counties,
                           packages=_PACKAGES,
                           page_title=f'{counties[slug]} County advertising media kit',
                           meta_description='County advertising options and clearly labeled first-party measurement.',
                           canonical_url=f'https://montanablotter.com/advertise/media-kit/{slug}',
                           inquiry_submitted=session.pop('media_kit_submitted', None) == slug,
                           form_error=form_error, form_values=form_values or {},
                           county_slug_value=slug), response_status, {'Cache-Control': 'private, no-store'}
