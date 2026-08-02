"""LEA Portal — public landing page and login gateway.

Routes:
  /leaportal          — Public landing page explaining the LEA program
  /leaportal/login    — LEA agency user login (redirects to /panel/login)
  /leaportal/admin    — Admin staff login (redirects to /admin/lea-management)
"""
from flask import Blueprint, redirect, render_template, url_for

lea_portal_bp = Blueprint('lea_portal', __name__, url_prefix='/leaportal')


@lea_portal_bp.route('/')
def landing():
    """Public landing page for the LEA Portal."""
    return render_template('lea_portal/landing.html')


@lea_portal_bp.route('/login')
def login_redirect():
    """Redirect to LEA agency panel login."""
    return redirect(url_for('lea_panel.login'))


@lea_portal_bp.route('/admin')
def admin_redirect():
    """Redirect to admin LEA management dashboard."""
    return redirect(url_for('admin.lea_dashboard'))


def register_lea_portal(app):
    """Register the LEA portal blueprint."""
    app.register_blueprint(lea_portal_bp)
