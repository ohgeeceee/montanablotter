import os

from flask import render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required
from werkzeug.utils import secure_filename

from blueprints.admin import admin_bp
from db import get_db


ALLOWED_EXTENSIONS = {'pdf', 'xlsx', 'xls', 'csv', 'json'}


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@admin_bp.route('/code-violations')
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


@admin_bp.route('/code-violations/upload', methods=['POST'])
@login_required
def admin_code_violations_upload():
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('admin.admin_code_violations_dashboard'))

    file = request.files['file']
    source_key = request.form.get('source_key', '').strip()

    if file.filename == '' or not source_key:
        flash('File and source are required', 'error')
        return redirect(url_for('admin.admin_code_violations_dashboard'))

    if not _allowed_file(file.filename):
        flash('Invalid file type. Allowed: PDF, Excel, CSV, JSON', 'error')
        return redirect(url_for('admin.admin_code_violations_dashboard'))

    filename = secure_filename(file.filename)
    upload_dir = current_app.config.get('UPLOAD_FOLDER', '/root/montanablotter/uploads')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)
    file.save(filepath)

    try:
        conn = get_db()
        try:
            source = conn.execute(
                'SELECT * FROM code_violation_sources WHERE source_key = ?',
                (source_key,),
            ).fetchone()
            if not source:
                flash(f'Source "{source_key}" not found', 'error')
                return redirect(url_for('admin.admin_code_violations_dashboard'))

            from services.ingestion.code_violations import ingest_records
            import json

            ext = filename.rsplit('.', 1)[1].lower()
            if ext in ('xlsx', 'xls'):
                import pandas as pd
                df = pd.read_excel(filepath)
                records = df.fillna('').to_dict(orient='records')
            elif ext == 'csv':
                import csv
                with open(filepath, newline='', encoding='utf-8') as f:
                    records = list(csv.DictReader(f))
            elif ext == 'json':
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                records = data if isinstance(data, list) else data.get('records', [])
            elif ext == 'pdf':
                from services.ingestion.code_violations import _parse_pdf
                records = _parse_pdf(filepath)
            else:
                records = []

            result = ingest_records(
                conn,
                source_key=source['source_key'],
                display_name=source['display_name'],
                city=source['city'],
                records=records,
            )
            flash(
                f"Ingested {result['inserted']} new, updated {result['updated']} records "
                f"from {source['display_name']}",
                'success',
            )
        finally:
            conn.close()
    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')

    return redirect(url_for('admin.admin_code_violations_dashboard'))
