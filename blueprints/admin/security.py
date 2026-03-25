from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from flask import flash, redirect, render_template, request, session, url_for
from flask_bcrypt import check_password_hash
from flask_login import current_user, login_required, login_user, logout_user

import config
from db import get_db
from utils.app_settings import _app_setting_int
from utils.auth_constants import ADMIN_ACCESS_ROLES, ADMIN_MANAGEMENT_ROLES, ROLE_LABELS
from blueprints.admin import admin_bp, _client_ip, _log_admin_action, require_role


# ---------------------------------------------------------------------------
# Private helpers (security-only, not used outside this module)
# ---------------------------------------------------------------------------

def _parse_sqlite_timestamp(value):
    """Parse a SQLite datetime string into a datetime object."""
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _admin_login_max_attempts(conn=None) -> int:
    return _app_setting_int(
        'admin_login_max_attempts',
        config.ADMIN_LOGIN_MAX_ATTEMPTS,
        1,
        20,
        conn=conn,
    )


def _admin_login_window_minutes(conn=None) -> int:
    return _app_setting_int(
        'admin_login_window_minutes',
        config.ADMIN_LOGIN_WINDOW_MINUTES,
        1,
        240,
        conn=conn,
    )


def _admin_login_lockout_minutes(conn=None) -> int:
    return _app_setting_int(
        'admin_login_lockout_minutes',
        config.ADMIN_LOGIN_LOCKOUT_MINUTES,
        1,
        240,
        conn=conn,
    )


def _login_rate_limited(conn, username: str, ip_address: str):
    window_minutes = _admin_login_window_minutes(conn=conn)
    max_attempts = _admin_login_max_attempts(conn=conn)
    lockout_minutes = _admin_login_lockout_minutes(conn=conn)
    window = f'-{window_minutes} minutes'
    row = conn.execute(
        '''
        SELECT COUNT(*) AS failures, MAX(created_at) AS last_failure
        FROM auth_login_attempts
        WHERE success = 0
          AND created_at >= datetime('now', ?)
          AND (username = ? OR ip_address = ?)
        ''',
        (window, username, ip_address),
    ).fetchone()

    failures = int(row['failures'] or 0)
    if failures < max_attempts:
        return False, 0

    last_failure = _parse_sqlite_timestamp(row['last_failure'])
    if not last_failure:
        return True, lockout_minutes * 60

    unlock_time = last_failure + timedelta(minutes=lockout_minutes)
    remaining = int((unlock_time - datetime.utcnow()).total_seconds())
    if remaining > 0:
        return True, remaining
    return False, 0


def _record_login_attempt(conn, username: str, ip_address: str, success: bool):
    conn.execute(
        '''
        INSERT INTO auth_login_attempts (username, ip_address, success)
        VALUES (?, ?, ?)
        ''',
        (username, ip_address, 1 if success else 0),
    )
    conn.commit()


def _active_super_admin_count(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM users WHERE role = 'super_admin' AND COALESCE(is_active, 1) = 1"
    ).fetchone()
    return int(row['cnt'] or 0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        ip_address = _client_ip()

        conn = get_db()
        is_limited, retry_seconds = _login_rate_limited(conn, username, ip_address)
        if is_limited:
            conn.close()
            retry_minutes = max(1, (retry_seconds + 59) // 60)
            flash(f'Too many login attempts. Try again in about {retry_minutes} minute(s).')
            return render_template('admin_login.html'), 429

        user_row = conn.execute(
            'SELECT * FROM users WHERE username = ?',
            (username,),
        ).fetchone()
        password_valid = bool(user_row and check_password_hash(user_row['password'], password))
        is_active_account = bool(user_row and user_row['is_active'])
        has_admin_role = bool(user_row and (user_row['role'] or '').strip() in ADMIN_ACCESS_ROLES)
        is_valid = bool(password_valid and is_active_account and has_admin_role)
        _record_login_attempt(conn, username, ip_address, is_valid)

        if is_valid:
            conn.execute(
                'UPDATE users SET last_login_at = datetime(\'now\') WHERE id = ?',
                (user_row['id'],),
            )
            _log_admin_action(
                'auth.login',
                target_type='user',
                target_id=user_row['id'],
                metadata={'role': user_row['role']},
                user_id=user_row['id'],
                conn=conn,
            )
            conn.commit()
            conn.close()
            session.clear()
            session['_csrf_token'] = secrets.token_urlsafe(32)
            from app import User
            login_user(User.from_row(user_row))
            return redirect(url_for('admin.admin_dashboard'))

        conn.close()
        if password_valid and not is_active_account:
            flash('This account has been disabled.')
        elif password_valid and not has_admin_role:
            flash('This account is not authorized for the admin panel.')
        else:
            flash('Invalid credentials')

    return render_template('admin_login.html')


@admin_bp.route('/logout')
@login_required
def admin_logout():
    _log_admin_action(
        'auth.logout',
        target_type='user',
        target_id=current_user.id,
        metadata={'role': getattr(current_user, 'role', '')},
    )
    logout_user()
    session.clear()
    return redirect(url_for('index'))


@admin_bp.route('/security/users')
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_security_users():
    q = (request.args.get('q') or '').strip()[:100]
    role_filter = (request.args.get('role') or '').strip()
    status_filter = (request.args.get('status') or 'active').strip().lower()
    if status_filter not in {'active', 'inactive', 'all'}:
        status_filter = 'active'

    where = []
    params = []
    if q:
        where.append('(u.username LIKE ? OR COALESCE(u.email, \'\') LIKE ?)')
        like = f'%{q}%'
        params.extend([like, like])
    if role_filter in ROLE_LABELS:
        where.append('u.role = ?')
        params.append(role_filter)
    if status_filter == 'active':
        where.append('COALESCE(u.is_active, 1) = 1')
    elif status_filter == 'inactive':
        where.append('COALESCE(u.is_active, 1) = 0')

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''
    conn = get_db()
    users = conn.execute(
        f'''
        SELECT
            u.id,
            u.username,
            u.email,
            u.role,
            COALESCE(u.is_active, 1) AS is_active,
            u.last_login_at,
            COALESCE(u.mfa_enabled, 0) AS mfa_enabled,
            u.created_at,
            (
                SELECT COUNT(*)
                FROM auth_login_attempts ala
                WHERE ala.username = u.username
                  AND ala.success = 0
                  AND ala.created_at >= datetime('now', '-24 hours')
            ) AS failed_logins_24h
        FROM users u
        {where_sql}
        ORDER BY COALESCE(u.is_active, 1) DESC, u.role ASC, u.username ASC
        ''',
        params,
    ).fetchall()
    super_admin_count = _active_super_admin_count(conn)
    conn.close()
    return render_template(
        'admin_users.html',
        users=users,
        q=q,
        role_filter=role_filter,
        status_filter=status_filter,
        role_options=ROLE_LABELS,
        super_admin_count=super_admin_count,
    )


@admin_bp.route('/security/users/<int:user_id>/role', methods=['POST'])
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_security_user_role(user_id):
    new_role = (request.form.get('role') or '').strip()
    if new_role not in ROLE_LABELS:
        flash('Choose a valid role.', 'error')
        return redirect(url_for('admin.admin_security_users'))

    conn = get_db()
    target = conn.execute(
        'SELECT id, username, role, COALESCE(is_active, 1) AS is_active FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    if not target:
        conn.close()
        flash('User not found.', 'error')
        return redirect(url_for('admin.admin_security_users'))

    if target['role'] == 'super_admin' and new_role != 'super_admin' and int(target['is_active'] or 0) == 1 and _active_super_admin_count(conn) <= 1:
        conn.close()
        flash('You cannot demote the last active super admin.', 'error')
        return redirect(url_for('admin.admin_security_users'))

    if target['role'] == new_role:
        conn.close()
        flash('Role unchanged.', 'warning')
        return redirect(url_for('admin.admin_security_users'))

    conn.execute('UPDATE users SET role = ? WHERE id = ?', (new_role, user_id))
    _log_admin_action(
        'security.role_changed',
        target_type='user',
        target_id=user_id,
        metadata={'username': target['username'], 'from': target['role'], 'to': new_role},
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash(f"Updated role for {target['username']} to {ROLE_LABELS[new_role]}.", 'success')
    return redirect(url_for('admin.admin_security_users'))


@admin_bp.route('/security/users/<int:user_id>/status', methods=['POST'])
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_security_user_status(user_id):
    requested = (request.form.get('is_active') or '').strip()
    new_is_active = 1 if requested == '1' else 0

    conn = get_db()
    target = conn.execute(
        'SELECT id, username, role, COALESCE(is_active, 1) AS is_active FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    if not target:
        conn.close()
        flash('User not found.', 'error')
        return redirect(url_for('admin.admin_security_users'))

    if target['id'] == current_user.id and new_is_active == 0:
        conn.close()
        flash('You cannot disable your own account.', 'error')
        return redirect(url_for('admin.admin_security_users'))

    if target['role'] == 'super_admin' and int(target['is_active'] or 0) == 1 and new_is_active == 0 and _active_super_admin_count(conn) <= 1:
        conn.close()
        flash('You cannot disable the last active super admin.', 'error')
        return redirect(url_for('admin.admin_security_users'))

    if int(target['is_active'] or 0) == new_is_active:
        conn.close()
        flash('Account status unchanged.', 'warning')
        return redirect(url_for('admin.admin_security_users'))

    conn.execute('UPDATE users SET is_active = ? WHERE id = ?', (new_is_active, user_id))
    _log_admin_action(
        'security.account_status_changed',
        target_type='user',
        target_id=user_id,
        metadata={'username': target['username'], 'from': int(target['is_active'] or 0), 'to': new_is_active},
        conn=conn,
    )
    conn.commit()
    conn.close()
    flash(f"{'Enabled' if new_is_active else 'Disabled'} account for {target['username']}.", 'success')
    return redirect(url_for('admin.admin_security_users'))


@admin_bp.route('/security/audit')
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_security_audit():
    q = (request.args.get('q') or '').strip()[:100]
    actor_filter = (request.args.get('actor') or '').strip()[:80]
    action_filter = (request.args.get('action') or '').strip()[:120]

    where = []
    params = []
    if q:
        where.append('(al.action LIKE ? OR COALESCE(al.target_type, \'\') LIKE ? OR COALESCE(al.target_id, \'\') LIKE ? OR COALESCE(al.metadata_json, \'\') LIKE ?)')
        like = f'%{q}%'
        params.extend([like, like, like, like])
    if actor_filter:
        where.append('COALESCE(u.username, \'\') = ?')
        params.append(actor_filter)
    if action_filter:
        where.append('al.action = ?')
        params.append(action_filter)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''
    conn = get_db()
    rows = conn.execute(
        f'''
        SELECT
            al.id,
            al.timestamp,
            al.action,
            al.target_type,
            al.target_id,
            al.ip_address,
            al.metadata_json,
            u.username AS actor_username
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        {where_sql}
        ORDER BY datetime(al.timestamp) DESC, al.id DESC
        LIMIT 250
        ''',
        params,
    ).fetchall()
    actor_options = conn.execute(
        '''
        SELECT DISTINCT COALESCE(u.username, '') AS username
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        WHERE COALESCE(u.username, '') != ''
        ORDER BY username ASC
        '''
    ).fetchall()
    action_options = conn.execute(
        '''
        SELECT DISTINCT action
        FROM audit_logs
        WHERE action IS NOT NULL AND action != ''
        ORDER BY action ASC
        '''
    ).fetchall()
    conn.close()
    return render_template(
        'admin_audit_log.html',
        rows=rows,
        q=q,
        actor_filter=actor_filter,
        action_filter=action_filter,
        actor_options=[row['username'] for row in actor_options],
        action_options=[row['action'] for row in action_options],
    )
