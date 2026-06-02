"""Helper for looking up the active sponsor of a county's digest.

Used by weekly_snapshot.py and morning_briefing.py to prepend a
"Presented by ..." block to per-county digest emails when there's an
active sponsor.
"""
from __future__ import annotations

import sqlite3
from typing import Optional


def get_active_sponsor(conn: sqlite3.Connection, county: str) -> Optional[sqlite3.Row]:
    """Return the active sponsor row for a county, or None."""
    if not county:
        return None
    return conn.execute(
        '''
        SELECT * FROM sponsored_digests
        WHERE county = ? AND is_active = 1
          AND (starts_on IS NULL OR starts_on <= date('now'))
          AND (expires_on IS NULL OR expires_on >= date('now'))
        ORDER BY id DESC
        LIMIT 1
        ''',
        (county,),
    ).fetchone()


def render_sponsor_block(sponsor_row: sqlite3.Row) -> str:
    """Return a small HTML/markdown block for the top of a digest email."""
    if not sponsor_row:
        return ''
    name = sponsor_row['sponsor_name'] or ''
    url = sponsor_row['sponsor_url'] or ''
    pitch = sponsor_row['sponsor_pitch'] or ''
    link_open = f'<a href="{url}" target="_blank" rel="noopener" style="color:#1d4ed8;text-decoration:underline">' if url else ''
    link_close = '</a>' if url else ''
    pitch_html = f' &mdash; {pitch}' if pitch else ''
    return (
        f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;'
        f'padding:10px 14px;margin:0 0 18px 0;font-size:13px;color:#475569">'
        f'<strong>Presented by</strong> {link_open}{name}{link_close}{pitch_html}'
        f'</div>'
    )
