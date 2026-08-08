"""LEA Connect — public marketing website.

Serves the LEA Connect brand landing page at /lea_connect/.
Malwarebytes-inspired premium design — dark, authoritative, enterprise-grade.
"""
from flask import Blueprint, redirect, render_template, url_for

lea_connect_bp = Blueprint('lea_connect', __name__, url_prefix='/lea_connect',
                           static_folder=None)

# Legacy redirect: /lea-connect/ -> /lea_connect/
legacy_redirect_bp = Blueprint('lea_connect_legacy', __name__)


@legacy_redirect_bp.route('/lea-connect/')
@legacy_redirect_bp.route('/lea-connect')
def legacy_redirect():
    return redirect(url_for('lea_connect.landing'), 301)


@lea_connect_bp.route('/')
def landing():
    """Main marketing landing page for LEA Connect."""
    return render_template('lea_connect/landing.html')


def register_lea_connect(app):
    """Register the LEA Connect marketing blueprint."""
    app.register_blueprint(lea_connect_bp)
    app.register_blueprint(legacy_redirect_bp)
