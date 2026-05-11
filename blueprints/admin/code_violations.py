from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required

from db import get_db

admin_code_violations_bp = Blueprint('admin_code_violations', __name__)


@admin_code_violations_bp.route('/code-violations')
@login_required
def admin_code_violations_dashboard():
    conn = get_db()
    try:
        sources = conn.execute('SELECT * FROM code_violation_sources ORDER BY city').fetchall()
        counts = conn.execute('''
            SELECT source_id, COUNT(*) AS total,
                   SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count
            FROM code_violations
            GROUP BY source_id
        ''').fetchall()
        count_map = {r['source_id']: {'total': r['total'], 'open': r['open_count']} for r in counts}
        return render_template('admin_code_violations.html', sources=sources, count_map=count_map)
    finally:
        conn.close()
