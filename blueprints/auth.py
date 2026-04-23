from __future__ import annotations

from datetime import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

from db import get_db


auth_bp = Blueprint('auth', __name__)


def register_auth_blueprint(app):
    """Register the auth blueprint onto the Flask app."""
    app.register_blueprint(auth_bp)


def _app():
    import app as _app_module
    return _app_module


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route('/register', methods=['GET', 'POST'])
def public_register():
    m = _app()
    if m._get_public_user():
        return redirect(m._safe_next_url(request.values.get('next'), fallback='/'))

    conn = get_db()
    counties = m._all_subscription_counties(conn)
    next_url = m._safe_next_url(request.values.get('next'), fallback='/')
    form_values = {
        'display_name': '',
        'email': '',
        'subscribe_digest': False,
        'selected_counties': [],
    }

    if request.method == 'POST':
        # reCAPTCHA v3 verification
        recaptcha_token = request.form.get('g-recaptcha-response', '')
        if not m.verify_recaptcha(recaptcha_token, action='register'):
            flash('Security verification failed. Please try again.', 'error')
            return render_template(
                'register.html',
                counties=counties,
                next_url=next_url,
                form_values=form_values,
                page_title='Create Account',
                meta_description='Create a Montana Blotter account to comment and manage email subscription preferences.',
                canonical_url=f'{m.BASE_URL}/register',
                og_title='Create Account | Montana Blotter',
                og_description='Create a Montana Blotter account to comment and manage email subscription preferences.',
                active_nav='register',
                current_year=datetime.now().year,
            )

        display_name = (request.form.get('display_name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        confirm_password = request.form.get('confirm_password') or ''
        subscribe_digest = bool(request.form.get('subscribe_digest'))
        selected_counties = m._selected_counties_from_form()
        form_values = {
            'display_name': display_name,
            'email': email,
            'subscribe_digest': subscribe_digest,
            'selected_counties': selected_counties,
        }

        if len(display_name) < 2:
            flash('Choose a display name with at least 2 characters.', 'error')
        elif not email or '@' not in email:
            flash('Enter a valid email address.', 'error')
        elif len(password) < 8:
            flash('Use a password with at least 8 characters.', 'error')
        elif password != confirm_password:
            flash('Password confirmation does not match.', 'error')
        else:
            existing = conn.execute(
                'SELECT id FROM public_users WHERE email = ?',
                (email,),
            ).fetchone()
            if existing:
                flash('That email is already registered. Log in instead.', 'error')
            else:
                password_hash = m.bcrypt.generate_password_hash(password).decode('utf-8')
                cursor = conn.execute(
                    '''
                    INSERT INTO public_users (
                        email,
                        password_hash,
                        display_name,
                        subscription_counties,
                        subscribe_digest
                    ) VALUES (?, ?, ?, ?, ?)
                    ''',
                    (
                        email,
                        password_hash,
                        display_name[:120],
                        ','.join(selected_counties),
                        1 if subscribe_digest else 0,
                    ),
                )
                m._sync_public_user_subscription_from_donations(conn, cursor.lastrowid, email)
                if subscribe_digest:
                    m._upsert_digest_subscription(conn, email, selected_counties, source='account_registration')
                    m._record_subscribe_event('subscribe_success', source='account_registration', page_path=request.path, email=email)
                conn.commit()
                m._set_public_user_session(cursor.lastrowid)
                flash('Account created. Your comments will enter moderation before they go live.', 'success')
                conn.close()
                return redirect(next_url)

    conn.close()
    return render_template(
        'register.html',
        counties=counties,
        next_url=next_url,
        form_values=form_values,
        page_title='Create Account',
        meta_description='Create a Montana Blotter account to comment and manage email subscription preferences.',
        canonical_url=f'{m.BASE_URL}/register',
        og_title='Create Account | Montana Blotter',
        og_description='Create a Montana Blotter account to comment and manage email subscription preferences.',
        active_nav='register',
        current_year=datetime.now().year,
    )


@auth_bp.route('/login', methods=['GET', 'POST'])
def public_login():
    m = _app()
    if m._get_public_user():
        return redirect(m._safe_next_url(request.values.get('next'), fallback='/'))

    next_url = m._safe_next_url(request.values.get('next'), fallback='/')
    email_value = (request.form.get('email') or request.args.get('email') or '').strip().lower()
    if request.method == 'POST':
        # reCAPTCHA v3 verification
        recaptcha_token = request.form.get('g-recaptcha-response', '')
        if not m.verify_recaptcha(recaptcha_token, action='login'):
            flash('Security verification failed. Please try again.', 'error')
            return render_template(
                'login.html',
                next_url=next_url,
                email_value=email_value,
                page_title='Log In',
                meta_description='Log in to your Montana Blotter account to comment and manage subscriptions.',
                canonical_url=f'{m.BASE_URL}/login',
                og_title='Log In | Montana Blotter',
                og_description='Log in to your Montana Blotter account to comment and manage subscriptions.',
                active_nav='login',
                current_year=datetime.now().year,
            )

        password = request.form.get('password') or ''
        conn = get_db()
        row = conn.execute(
            'SELECT * FROM public_users WHERE email = ?',
            (email_value,),
        ).fetchone()
        password_valid = bool(row and m.bcrypt.check_password_hash(row['password_hash'], password))
        is_active_account = bool(row and row['is_active'])
        if password_valid and is_active_account:
            m._sync_public_user_subscription_from_donations(conn, row['id'], email_value)
            conn.execute(
                'UPDATE public_users SET last_login_at = datetime(\'now\') WHERE id = ?',
                (row['id'],),
            )
            conn.commit()
            conn.close()
            m._set_public_user_session(row['id'])
            flash('Signed in successfully.', 'success')
            return redirect(next_url)
        conn.close()
        if password_valid and not is_active_account:
            flash('This account has been disabled.', 'error')
        else:
            flash('Invalid email or password.', 'error')

    return render_template(
        'login.html',
        next_url=next_url,
        email_value=email_value,
        page_title='Log In',
        meta_description='Log in to your Montana Blotter account to comment and manage subscriptions.',
        canonical_url=f'{m.BASE_URL}/login',
        og_title='Log In | Montana Blotter',
        og_description='Log in to your Montana Blotter account to comment and manage subscriptions.',
        active_nav='login',
        current_year=datetime.now().year,
    )


@auth_bp.route('/logout')
def public_logout():
    m = _app()
    m._clear_public_user_session()
    flash('Signed out.', 'success')
    return redirect(m._safe_next_url(request.args.get('next'), fallback='/'))


@auth_bp.route('/dashboard')
def public_dashboard():
    m = _app()
    public_user = m._get_public_user()
    admin_user = current_user if getattr(current_user, 'is_authenticated', False) else None
    if not public_user and not admin_user:
        return redirect(url_for('.public_login', next=request.full_path))

    county = (request.args.get('county') or '').strip()[:80]
    q = (request.args.get('q') or '').strip()[:120]
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 50

    conn = get_db()
    where = []
    params = []
    if county:
        where.append('records.county = ?')
        params.append(county)
    if q:
        term = f'%{q}%'
        where.append(
            '('
            'COALESCE(records.incident_type, \'\') LIKE ? OR '
            'COALESCE(records.incident, \'\') LIKE ? OR '
            'COALESCE(records.details, \'\') LIKE ? OR '
            'COALESCE(records.location, \'\') LIKE ? OR '
            'COALESCE(records.county, \'\') LIKE ?'
            ')'
        )
        params.extend([term, term, term, term, term])

    where_sql = f"WHERE {' AND '.join(where)}" if where else ''
    total = conn.execute(
        f'SELECT COUNT(*) FROM records {where_sql}',
        params,
    ).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    records = conn.execute(
        f'''
        SELECT records.*,
               COALESCE(blotters.filename, '') AS filename
        FROM records
        LEFT JOIN blotters ON records.blotter_id = blotters.id
        {where_sql}
        ORDER BY datetime(records.created_at) DESC, records.date DESC, records.id DESC
        LIMIT ? OFFSET ?
        ''',
        params + [per_page, (page - 1) * per_page],
    ).fetchall()
    county_rows = conn.execute(
        '''
        SELECT county, COUNT(*) AS record_count
        FROM records
        WHERE county IS NOT NULL AND trim(county) != ''
        GROUP BY county
        ORDER BY record_count DESC, county ASC
        LIMIT 16
        '''
    ).fetchall()
    conn.close()

    county_options = [row['county'] for row in county_rows]
    can_manage_uploads = bool(admin_user and getattr(admin_user, 'can_access_admin', False))
    can_view_full_details = can_manage_uploads or bool(public_user and getattr(public_user, 'is_subscribed', False))
    logout_url = '/admin/logout' if can_manage_uploads else '/logout'

    return render_template(
        'dashboard.html',
        county=county,
        q=q,
        page=page,
        per_page=per_page,
        records=records,
        total=total,
        total_pages=total_pages,
        county_options=county_options,
        can_manage_uploads=can_manage_uploads,
        can_view_full_details=can_view_full_details,
        logout_url=logout_url,
        public_user=public_user,
        current_year=datetime.now().year,
    )


@auth_bp.route('/account')
def public_account():
    m = _app()
    public_user = m._get_public_user()
    if not public_user:
        return redirect(url_for('.public_login', next=request.full_path))

    conn = get_db()
    m._sync_public_user_subscription_from_donations(conn, public_user.id, public_user.email)
    refreshed_user = m._load_public_user(public_user.id, conn=conn)
    conn.commit()
    conn.close()
    g.public_user = refreshed_user

    return render_template(
        'public_account.html',
        account_user=refreshed_user,
        page_title='Account',
        meta_description='Review your Montana Blotter account and subscriber access status.',
        canonical_url=f'{m.BASE_URL}/account',
        og_title='Account | Montana Blotter',
        og_description='Review your Montana Blotter account and subscriber access status.',
        active_nav='account',
        current_year=datetime.now().year,
    )
