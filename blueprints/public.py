"""Compatibility shim for legacy public blueprint imports.

Public routes are defined in app.py. This module remains only so existing
imports of `register_public_blueprint` do not fail.
"""

from flask import Blueprint

public_bp = Blueprint('public_legacy', __name__)


def register_public_blueprint(app):
    """No-op: legacy public blueprint registration entrypoint."""
    return app
