"""Admin endpoints for geocoding backfill and status monitoring."""

from flask import Blueprint, jsonify, request, session, redirect, url_for

admin_geocode_bp = Blueprint('admin_geocode', __name__)


def _require_admin():
    return session.get('admin_logged_in') or session.get('user_id')


@admin_geocode_bp.route('/admin/geocode/status')
def admin_geocode_status():
    if not _require_admin():
        return redirect(url_for('auth.admin_login'))
    import sqlite3, os
    db_path = os.getenv('MB_DB_PATH', '/root/montanablotter/blotter.db')
    conn = sqlite3.connect(db_path)
    total = conn.execute(
        "SELECT COUNT(*) FROM records WHERE location IS NOT NULL AND trim(location) != ''"
    ).fetchone()[0]
    geocoded = conn.execute(
        "SELECT COUNT(*) FROM incident_geocodes WHERE lat IS NOT NULL"
    ).fetchone()[0]
    conn.close()
    return jsonify({
        'total_with_location': total,
        'geocoded': geocoded,
        'pending': total - geocoded,
        'pct_complete': round(geocoded / max(total, 1) * 100, 1),
    })


@admin_geocode_bp.route('/admin/geocode/backfill', methods=['POST'])
def admin_geocode_backfill():
    if not _require_admin():
        return jsonify({'error': 'unauthorized'}), 401
    batch_size = 200
    if request.is_json:
        batch_size = int(request.json.get('batch_size', 200))
    elif request.form.get('batch_size'):
        batch_size = int(request.form.get('batch_size', 200))
    batch_size = max(1, min(500, batch_size))

    from services.geo.pipeline import backfill_geocodes
    result = backfill_geocodes(batch_size=batch_size)
    return jsonify(result)
