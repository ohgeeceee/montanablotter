"""Admin routes for the social-share log.

GET /admin/social-shares — list recent share attempts (manual + auto).
GET /admin/social-shares/post/<id> — shares for a specific post.
"""
from __future__ import annotations

from flask import render_template, request

from db import get_db

from . import admin_bp, require_role


@admin_bp.route('/content/social/shares', methods=['GET'])
@require_role()
def social_shares_list():
    """Recent share attempts across all platforms."""
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 50
    offset = (page - 1) * per_page
    platform = (request.args.get('platform') or '').strip()[:20]
    status = (request.args.get('status') or '').strip()[:20]

    where, params = [], []
    if platform:
        where.append('ssl.platform = ?'); params.append(platform)
    if status:
        where.append('ssl.status = ?'); params.append(status)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    conn = get_db()
    total = conn.execute(
        f'SELECT COUNT(*) AS n FROM social_share_log ssl {where_sql}', params
    ).fetchone()['n']
    rows = conn.execute(
        f'''
        SELECT ssl.*, p.title AS post_title, p.seo_slug
        FROM social_share_log ssl
        LEFT JOIN posts p ON p.id = ssl.post_id
        {where_sql}
        ORDER BY ssl.created_at DESC, ssl.id DESC
        LIMIT ? OFFSET ?
        ''',
        params + [per_page, offset],
    ).fetchall()

    platform_rows = conn.execute(
        '''
        SELECT platform, status, COUNT(*) AS n
        FROM social_share_log
        GROUP BY platform, status
        ORDER BY platform, status
        '''
    ).fetchall()
    conn.close()

    return render_template(
        'admin_social_shares.html',
        rows=rows,
        total=total,
        page=page,
        per_page=per_page,
        platform=platform,
        status=status,
        platform_summary=platform_rows,
    )


@admin_bp.route('/content/social/shares/post/<int:post_id>', methods=['GET'])
@require_role()
def social_shares_for_post(post_id):
    conn = get_db()
    rows = conn.execute(
        '''
        SELECT * FROM social_share_log
        WHERE post_id = ?
        ORDER BY created_at DESC
        ''',
        (post_id,),
    ).fetchall()
    post = conn.execute(
        'SELECT id, title, seo_slug FROM posts WHERE id = ?', (post_id,)
    ).fetchone()
    conn.close()
    return render_template(
        'admin_social_shares_post.html',
        rows=rows,
        post=post,
    )
