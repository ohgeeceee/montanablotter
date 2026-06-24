from flask import render_template, request
from blueprints.admin import admin_bp, require_role


@admin_bp.route('/3dhub/status')
@require_role('super_admin', 'admin')
def admin_3dhub_status():
    context = {
        'service': '3dhub',
        'status': 'operational',
        'endpoints': [
            '/admin/3dhub/status',
            '/admin/3dhub/intake',
            '/admin/3dhub/quote',
            '/admin/3dhub/slice',
        ],
    }
    return render_template('admin/3dhub_status.html', context=context)


@admin_bp.route('/3dhub/intake', methods=['GET', 'POST'])
@require_role('super_admin', 'admin')
def admin_3dhub_intake():
    if request.method == 'POST':
        pass
    return render_template('admin/3dhub_intake.html')


@admin_bp.route('/3dhub/quote', methods=['GET', 'POST'])
@require_role('super_admin', 'admin')
def admin_3dhub_quote():
    if request.method == 'POST':
        pass
    return render_template('admin/3dhub_quote.html')


@admin_bp.route('/3dhub/slice', methods=['GET', 'POST'])
@require_role('super_admin', 'admin')
def admin_3dhub_slice():
    if request.method == 'POST':
        pass
    return render_template('admin/3dhub_slice.html')
