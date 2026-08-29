from __future__ import annotations

from flask import render_template

from blueprints.admin import admin_bp, require_role
from db import get_db


@admin_bp.route('/content/license-sanctions')
@require_role('super_admin', 'admin')
def admin_license_sanctions():
    conn = get_db()
    try:
        rows = conn.execute(
            '''
            SELECT
                ls.id, ls.name, ls.board, ls.action_taken, ls.effective_date,
                ls.is_active, ls.created_at, ls.updated_at,
                lss.board_name, lss.last_status
            FROM license_sanctions ls
            LEFT JOIN license_sanction_sources lss ON ls.source_id = lss.id
            ORDER BY ls.updated_at DESC
            LIMIT 500
            '''
        ).fetchall()
        sources = conn.execute('SELECT * FROM license_sanction_sources ORDER BY board_name').fetchall()
        return render_template('admin_license_sanctions.html', rows=rows, sources=sources)
    finally:
        conn.close()
