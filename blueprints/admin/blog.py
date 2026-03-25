from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required
from werkzeug.utils import secure_filename

from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES, CONTENT_REVIEW_ROLES
from blueprints.admin import admin_bp, require_role, _log_admin_action

# ---------------------------------------------------------------------------
# Helpers that stay in app.py (also used by other routes outside admin/blog):
#   _weekly_digest_workflow_defaults, _latest_weekly_digest, _annual_roundup_years,
#   _search_console_workflow_context, _parse_search_console_csv,
#   _store_search_console_import, _slugify
# ---------------------------------------------------------------------------


@admin_bp.route('/blog')
@login_required
def admin_blog():
    import app as _app_module
    conn = get_db()
    posts = conn.execute(
        'SELECT * FROM blog_posts ORDER BY created_at DESC').fetchall()
    workflow = _app_module._weekly_digest_workflow_defaults(conn)
    conn.close()
    return render_template('admin_blog.html', posts=posts, workflow=workflow)


@admin_bp.route('/blog/workflow', methods=['GET', 'POST'])
@login_required
def admin_blog_workflow():
    import app as _app_module
    if request.method == 'POST':
        upload = request.files.get('search_console_csv')
        if not upload or not upload.filename:
            flash('Choose a Search Console CSV export from the Queries or Pages tab.', 'error')
            return redirect(url_for('admin.admin_blog_workflow'))

        conn = get_db()
        try:
            source_kind, rows = _app_module._parse_search_console_csv(upload)
            source_filename = secure_filename(upload.filename or 'search-console.csv') or 'search-console.csv'
            _app_module._store_search_console_import(conn, source_filename, source_kind, rows)
            conn.commit()
            flash(
                f'Imported {len(rows)} Search Console {source_kind} rows from {source_filename}.',
                'success',
            )
        except ValueError as exc:
            flash(str(exc), 'error')
        finally:
            conn.close()
        return redirect(url_for('admin.admin_blog_workflow'))

    conn = get_db()
    workflow = _app_module._weekly_digest_workflow_defaults(conn)
    latest_weekly_digest = _app_module._latest_weekly_digest(conn)
    recent_posts = conn.execute(
        'SELECT id, title, slug, published, created_at FROM blog_posts ORDER BY created_at DESC LIMIT 8'
    ).fetchall()
    annual_years = _app_module._annual_roundup_years(conn)
    search_console = _app_module._search_console_workflow_context(conn)
    conn.close()
    return render_template(
        'admin_blog_workflow.html',
        workflow=workflow,
        latest_weekly_digest=latest_weekly_digest,
        recent_posts=recent_posts,
        annual_years=annual_years,
        search_console=search_console,
    )


@admin_bp.route('/blog/new', methods=['GET', 'POST'])
@login_required
def admin_blog_new():
    import app as _app_module
    if request.method == 'POST':
        title   = request.form.get('title', '').strip()
        slug    = request.form.get('slug', '').strip() or _app_module._slugify(title)
        body    = request.form.get('body', '').strip()
        excerpt = request.form.get('excerpt', '').strip()
        author  = request.form.get('author', 'Montana Blotter').strip()
        published = 1 if request.form.get('published') else 0
        if not title or not body:
            flash('Title and body are required.', 'error')
            return render_template('admin_blog_edit.html', post=None,
                                   form=request.form)
        conn = get_db()
        try:
            conn.execute(
                'INSERT INTO blog_posts (title, slug, body, excerpt, author, published) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (title, slug, body, excerpt, author, published))
            conn.commit()
            flash('Post published!' if published else 'Post saved as draft.', 'success')
            return redirect(url_for('admin.admin_blog'))
        except Exception as e:
            flash(f'Error: {e}', 'error')
        finally:
            conn.close()
    form = {}
    if request.args.get('template') == 'weekly_digest':
        conn = get_db()
        workflow = _app_module._weekly_digest_workflow_defaults(conn)
        conn.close()
        if workflow:
            form = {
                'title': workflow['title'],
                'slug': workflow['slug'],
                'excerpt': workflow['excerpt'],
                'body': workflow['body'],
                'author': workflow['author'],
                'published': workflow['published'],
            }
        else:
            flash('No weekly snapshot is available yet for a prefilled roundup draft.', 'error')
    return render_template('admin_blog_edit.html', post=None, form=form)


@admin_bp.route('/blog/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_blog_edit(post_id):
    import app as _app_module
    conn = get_db()
    post = conn.execute('SELECT * FROM blog_posts WHERE id=?', (post_id,)).fetchone()
    if not post:
        conn.close()
        return redirect(url_for('admin.admin_blog'))
    if request.method == 'POST':
        title     = request.form.get('title', '').strip()
        slug      = request.form.get('slug', '').strip() or _app_module._slugify(title)
        body      = request.form.get('body', '').strip()
        excerpt   = request.form.get('excerpt', '').strip()
        author    = request.form.get('author', 'Montana Blotter').strip()
        published = 1 if request.form.get('published') else 0
        conn.execute(
            'UPDATE blog_posts SET title=?, slug=?, body=?, excerpt=?, author=?, '
            'published=?, updated_at=datetime("now") WHERE id=?',
            (title, slug, body, excerpt, author, published, post_id))
        conn.commit()
        conn.close()
        flash('Post updated.', 'success')
        return redirect(url_for('admin.admin_blog'))
    conn.close()
    return render_template('admin_blog_edit.html', post=post, form=post)


@admin_bp.route('/blog/<int:post_id>/delete', methods=['POST'])
@login_required
def admin_blog_delete(post_id):
    conn = get_db()
    conn.execute('DELETE FROM blog_posts WHERE id=?', (post_id,))
    conn.commit()
    conn.close()
    flash('Post deleted.', 'success')
    return redirect(url_for('admin.admin_blog'))
