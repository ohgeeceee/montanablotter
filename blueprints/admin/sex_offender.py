from flask import Blueprint, render_template
from flask_login import login_required

from db import get_db

admin_sex_offender_bp = Blueprint('admin_sex_offender', __name__)


@admin_sex_offender_bp.route('/sex-offender')
@login_required
def admin_sex_offender_dashboard():
    conn = get_db()
    try:
        snapshots = conn.execute(
            'SELECT * FROM sex_offender_snapshots ORDER BY snapshot_date DESC LIMIT 20'
        ).fetchall()
        total_active = conn.execute(
            "SELECT COUNT(*) AS c FROM sex_offenders WHERE status = 'active'"
        ).fetchone()['c']
        total_subscribers = conn.execute(
            'SELECT COUNT(*) AS c FROM sex_offender_alert_subscriptions WHERE is_active = 1'
        ).fetchone()['c']
        return render_template(
            'admin_sex_offender.html',
            snapshots=snapshots,
            total_active=total_active,
            total_subscribers=total_subscribers,
        )
    finally:
        conn.close()
