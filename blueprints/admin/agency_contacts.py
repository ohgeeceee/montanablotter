"""Admin routes for managing law enforcement agency email contacts and weekly crime briefs."""

from __future__ import annotations

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from blueprints.admin import admin_bp, _log_admin_action
from db import get_db


@admin_bp.route('/audience/contacts')
@login_required
def admin_agency_contacts():
    conn = get_db()
    contacts = conn.execute(
        "SELECT * FROM agency_contacts ORDER BY county, contact_email"
    ).fetchall()
    counties = [r[0] for r in conn.execute(
        "SELECT DISTINCT county FROM blotters WHERE county IS NOT NULL AND county!='' ORDER BY county"
    ).fetchall()]
    conn.close()
    return render_template('admin_agency_contacts.html',
                           contacts=contacts, counties=counties)


@admin_bp.route('/audience/contacts/add', methods=['POST'])
@login_required
def admin_agency_contacts_add():
    county = (request.form.get('county') or '').strip()[:80]
    email_addr = (request.form.get('contact_email') or '').strip()[:200]
    contact_name = (request.form.get('contact_name') or '').strip()[:100]
    agency_name = (request.form.get('agency_name') or '').strip()[:100]
    if not county or not email_addr:
        flash('County and email are required', 'error')
        return redirect(url_for('admin.admin_agency_contacts'))
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO agency_contacts (county, agency_name, contact_email, contact_name) "
            "VALUES (?, ?, ?, ?)",
            (county, agency_name or None, email_addr, contact_name or None)
        )
        conn.commit()
        _log_admin_action('agency_contact_add', 'agency_contacts', county, {'email': email_addr}, conn=conn)
        flash(f'Added contact for {county} County', 'success')
    except Exception as e:
        flash(f'Error adding contact: {e}', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin.admin_agency_contacts'))


@admin_bp.route('/audience/contacts/<int:contact_id>/toggle', methods=['POST'])
@login_required
def admin_agency_contacts_toggle(contact_id: int):
    field = request.form.get('field', 'is_active')
    if field not in ('is_active', 'weekly_brief_enabled'):
        return jsonify({'error': 'invalid field'}), 400
    conn = get_db()
    conn.execute(
        f"UPDATE agency_contacts SET {field} = 1 - {field} WHERE id=?",
        (contact_id,)
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@admin_bp.route('/audience/contacts/<int:contact_id>/delete', methods=['POST'])
@login_required
def admin_agency_contacts_delete(contact_id: int):
    conn = get_db()
    conn.execute("DELETE FROM agency_contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()
    flash('Contact removed', 'success')
    return redirect(url_for('admin.admin_agency_contacts'))


@admin_bp.route('/audience/contacts/send-test', methods=['POST'])
@login_required
def admin_agency_contacts_send_test():
    county = (request.form.get('county') or '').strip()[:80]
    email_addr = (request.form.get('email') or '').strip()[:200]
    if not county or not email_addr:
        return jsonify({'error': 'county and email required'}), 400
    try:
        from services.email.agency_brief import generate_agency_brief_html
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        import config as _cfg

        html_body = generate_agency_brief_html(county)
        msg = MIMEMultipart('alternative')
        msg['From'] = _cfg.SMTP_USER
        msg['To'] = email_addr
        msg['Subject'] = f'[TEST] {county} County — Weekly Crime Brief'
        msg.attach(MIMEText(f'{county} County test brief', 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        smtp = smtplib.SMTP(_cfg.SMTP_SERVER, int(getattr(_cfg, 'SMTP_PORT', 587)))
        try:
            smtp.starttls()
            smtp.login(_cfg.SMTP_USER, _cfg.SMTP_PASSWORD)
            smtp.sendmail(_cfg.SMTP_USER, email_addr, msg.as_string())
        finally:
            smtp.quit()
        return jsonify({'ok': True, 'sent_to': email_addr})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/audience/contacts/send-all', methods=['POST'])
@login_required
def admin_agency_contacts_send_all():
    from services.email.agency_brief import send_weekly_briefs
    result = send_weekly_briefs()
    return jsonify(result)
