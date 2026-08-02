"""LEA Portal — public landing page, signup form, stats, embed.

Routes:
  /leaportal              — Public landing page
  /leaportal/register     — Agency interest form (POST)
  /leaportal/stats        — Live stats JSON (agency count, published incidents)
  /leaportal/embed        — Embed snippet for agency websites
  /leaportal/login        — Redirect to LEA panel login
  /leaportal/admin        — Redirect to admin LEA management
"""
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from db import get_db

lea_portal_bp = Blueprint('lea_portal', __name__, url_prefix='/leaportal')


def _live_stats():
    """Return dict of live stats for the landing page."""
    conn = get_db()
    agency_count = conn.execute(
        "SELECT COUNT(*) FROM lea_agencies WHERE verification_status IN ('verified', 'pending')"
    ).fetchone()[0]
    published_count = conn.execute(
        "SELECT COUNT(*) FROM lea_blotter_drafts WHERE submission_status = 'published'"
    ).fetchone()[0]
    new_signups = conn.execute(
        "SELECT COUNT(*) FROM lea_registration_interest WHERE status = 'new'"
    ).fetchone()[0]
    return {
        'agency_count': agency_count,
        'published_count': published_count,
        'new_signups': new_signups,
        'total_registrations': agency_count + new_signups,
    }


@lea_portal_bp.route('/')
def landing():
    """Public landing page for the LEA Portal."""
    stats = _live_stats()
    return render_template('lea_portal/landing.html', stats=stats)


@lea_portal_bp.route('/register', methods=['POST'])
def register_interest():
    """Handle agency interest form submission."""
    agency_name = (request.form.get('agency_name') or '').strip()
    county = (request.form.get('county') or '').strip()
    contact_name = (request.form.get('contact_name') or '').strip()
    contact_email = (request.form.get('contact_email') or '').strip()
    contact_phone = (request.form.get('contact_phone') or '').strip()
    agency_type = (request.form.get('agency_type') or 'sheriff').strip()
    message = (request.form.get('message') or '').strip()

    errors = []
    if not agency_name:
        errors.append('Agency name is required')
    if not county:
        errors.append('County is required')
    if not contact_name:
        errors.append('Contact name is required')
    if not contact_email:
        errors.append('Contact email is required')

    if errors:
        stats = _live_stats()
        return render_template('lea_portal/landing.html', stats=stats,
                               form_errors=errors, form_data=request.form), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO lea_registration_interest (agency_name, county_name, contact_name, contact_email, contact_phone, agency_type, message) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agency_name, county, contact_name, contact_email, contact_phone or None, agency_type, message or None)
    )
    conn.commit()

    stats = _live_stats()
    return render_template('lea_portal/landing.html', stats=stats,
                           form_success=f"Thanks {contact_name}! We'll reach out to {agency_name} soon.")


@lea_portal_bp.route('/stats')
def stats_json():
    """Live stats as JSON (for embed)."""
    return jsonify(_live_stats())


@lea_portal_bp.route('/embed')
def embed_snippet():
    """Display the embed badge code for agency websites."""
    stats = _live_stats()
    return render_template('lea_portal/embed.html', stats=stats)


@lea_portal_bp.route('/login')
def login_redirect():
    return redirect(url_for('lea_panel.login'))


@lea_portal_bp.route('/admin')
def admin_redirect():
    return redirect(url_for('admin.lea_dashboard'))


def register_lea_portal(app):
    app.register_blueprint(lea_portal_bp)
