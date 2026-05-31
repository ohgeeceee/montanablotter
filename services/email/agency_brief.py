"""
Weekly crime brief emails sent to law enforcement agency contacts.
Generates personalized HTML summaries per county and sends via SMTP.
All email HTML uses inline styles only — required for Gmail/Outlook compatibility.
"""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import config

DB_PATH = getattr(config, 'DB_PATH', '/root/montanablotter/blotter.db')

_ISO = (
    "(CASE WHEN length(r.date)=8 "
    "THEN '20'||substr(r.date,7,2)||'-'||substr(r.date,1,2)||'-'||substr(r.date,4,2) "
    "ELSE r.date END)"
)

CATEGORY_LABELS = {
    'dui': 'DUI', 'violent': 'Violent Crime', 'domestic': 'Domestic',
    'drug': 'Drug', 'weapons': 'Weapons', 'property': 'Property Crime',
    'traffic': 'Traffic', 'welfare': 'Welfare Check', 'public_order': 'Public Order',
    'other': 'Other',
}
CATEGORY_COLORS = {
    'dui': '#f97316', 'violent': '#ef4444', 'domestic': '#ec4899', 'drug': '#a855f7',
    'weapons': '#6366f1', 'property': '#3b82f6', 'traffic': '#06b6d4',
    'welfare': '#10b981', 'public_order': '#84cc16', 'other': '#94a3b8',
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _county_stats(conn: sqlite3.Connection, county: str, days: int = 7) -> dict:
    """Pull incident counts and charge breakdown for a county over the last N days."""
    date_from = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')
    date_prev = (datetime.utcnow() - timedelta(days=days * 2)).strftime('%Y-%m-%d')

    this_week = conn.execute(
        f"SELECT COUNT(*) FROM records r WHERE r.county=? AND {_ISO}>=?",
        (county, date_from)
    ).fetchone()[0]

    last_week = conn.execute(
        f"SELECT COUNT(*) FROM records r WHERE r.county=? AND {_ISO}>=? AND {_ISO}<?",
        (county, date_prev, date_from)
    ).fetchone()[0]

    top_cats = conn.execute(
        f"SELECT charge_category, COUNT(*) AS n FROM records r "
        f"WHERE r.county=? AND {_ISO}>=? AND charge_category IS NOT NULL "
        f"GROUP BY charge_category ORDER BY n DESC LIMIT 5",
        (county, date_from)
    ).fetchall()

    top_types = conn.execute(
        f"SELECT incident_type, COUNT(*) AS n FROM records r "
        f"WHERE r.county=? AND {_ISO}>=? AND incident_type IS NOT NULL "
        f"GROUP BY incident_type ORDER BY n DESC LIMIT 5",
        (county, date_from)
    ).fetchall()

    return {
        'county': county,
        'this_week': int(this_week or 0),
        'last_week': int(last_week or 0),
        'top_categories': [{'cat': r['charge_category'], 'count': int(r['n'])} for r in top_cats],
        'top_types': [{'type': r['incident_type'], 'count': int(r['n'])} for r in top_types],
        'date_from': date_from,
    }


def _pct_change_str(this_week: int, last_week: int) -> tuple[str, str]:
    """Returns (display string, color hex) for week-over-week change."""
    if last_week == 0:
        return ('New data', '#94a3b8')
    pct = round((this_week - last_week) / last_week * 100)
    if pct > 0:
        return (f'&#8593; {pct}% vs last week', '#ef4444')
    elif pct < 0:
        return (f'&#8595; {abs(pct)}% vs last week', '#10b981')
    return ('No change vs last week', '#94a3b8')


def generate_agency_brief_html(county: str, days: int = 7) -> str:
    """
    Build a self-contained HTML email for one county's weekly crime brief.
    All styles are inline — required for Gmail/Outlook.
    """
    conn = _connect()
    stats = _county_stats(conn, county, days)
    conn.close()

    week_label = (datetime.utcnow() - timedelta(days=days)).strftime('%B %-d, %Y')
    week_end   = datetime.utcnow().strftime('%B %-d, %Y')
    slug = county.lower().replace(' ', '-').replace("'", '')
    county_url = f'https://montanablotter.com/county/{slug}'
    atlas_url  = 'https://montanablotter.com/crime-atlas'
    unsub_url  = f'https://montanablotter.com/agency-brief/unsubscribe?county={slug}'

    change_str, change_color = _pct_change_str(stats['this_week'], stats['last_week'])

    cats = stats['top_categories']
    max_cat = cats[0]['count'] if cats else 1
    cat_rows = ''
    for c in cats:
        label = CATEGORY_LABELS.get(c['cat'], c['cat'])
        color = CATEGORY_COLORS.get(c['cat'], '#94a3b8')
        bar_w = max(4, round(c['count'] / max_cat * 160))
        cat_rows += (
            f'<tr>'
            f'<td style="padding:4px 8px 4px 0;font-size:12px;color:#94a3b8;width:110px;vertical-align:middle">{label}</td>'
            f'<td style="padding:4px 0;vertical-align:middle"><div style="background:{color};width:{bar_w}px;height:8px;border-radius:3px;display:inline-block"></div></td>'
            f'<td style="padding:4px 0 4px 8px;font-size:12px;color:#e2e8f0;font-weight:600">{c["count"]}</td>'
            f'</tr>'
        )

    type_items = ''.join(
        f'<li style="margin-bottom:4px;font-size:12px;color:#94a3b8">'
        f'<span style="color:#e2e8f0">{t["type"]}</span> '
        f'<span style="color:#38bdf8;font-weight:600">({t["count"]})</span></li>'
        for t in stats['top_types']
    ) or '<li style="font-size:12px;color:#64748b">No incident data this week</li>'

    cat_table = (
        f'<table cellpadding="0" cellspacing="0">{cat_rows}</table>'
        if cat_rows else
        '<div style="font-size:12px;color:#64748b">No charge category data this week</div>'
    )

    return f'''<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{county} County Weekly Crime Brief</title>
</head>
<body style="margin:0;padding:0;background:#0b0f19;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f19;padding:20px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#121827;border:1px solid #1e293b;border-radius:10px;overflow:hidden">
  <tr>
    <td style="background:#0e7490;padding:20px 24px">
      <div style="font-size:11px;color:#bae6fd;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px">Montana Blotter &#8212; Agency Intelligence Brief</div>
      <div style="font-size:22px;font-weight:700;color:#ffffff">{county} County</div>
      <div style="font-size:12px;color:#bae6fd;margin-top:4px">Week of {week_label} &#8212; {week_end}</div>
    </td>
  </tr>
  <tr>
    <td style="padding:24px 24px 16px">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="background:#1a2233;border-radius:10px;padding:18px 20px;text-align:center">
            <div style="font-size:42px;font-weight:700;color:#38bdf8;line-height:1">{stats['this_week']}</div>
            <div style="font-size:13px;color:#94a3b8;margin-top:4px">incidents this week</div>
            <div style="font-size:13px;color:{change_color};margin-top:6px;font-weight:600">{change_str}</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td style="padding:0 24px 20px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.6px;margin-bottom:10px">Charge Breakdown</div>
      {cat_table}
    </td>
  </tr>
  <tr>
    <td style="padding:0 24px 20px;border-top:1px solid #1e293b">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.6px;margin-top:16px;margin-bottom:10px">Top Incident Types</div>
      <ul style="margin:0;padding-left:16px">{type_items}</ul>
    </td>
  </tr>
  <tr>
    <td style="padding:16px 24px;border-top:1px solid #1e293b">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td width="48%" style="padding-right:8px">
            <a href="{county_url}" style="display:block;background:#38bdf8;color:#0b0f19;text-decoration:none;text-align:center;padding:10px 12px;border-radius:7px;font-size:13px;font-weight:700">
              View {county} Incidents &#8594;
            </a>
          </td>
          <td width="48%" style="padding-left:8px">
            <a href="{atlas_url}" style="display:block;background:#1e293b;color:#e2e8f0;text-decoration:none;text-align:center;padding:10px 12px;border-radius:7px;font-size:13px;font-weight:600;border:1px solid #334155">
              Montana Crime Atlas &#8594;
            </a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  <tr>
    <td style="padding:14px 24px;border-top:1px solid #1e293b;background:#0b1220;text-align:center">
      <div style="font-size:11px;color:#475569">
        MontanaBlotter.com &#8212; Montana public safety data<br>
        <a href="{unsub_url}" style="color:#475569">Unsubscribe from weekly briefs</a>
      </div>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>'''


def send_weekly_briefs() -> dict:
    """
    Send weekly crime briefs to all active agency contacts.
    Skips contacts sent to within the last 6 days.
    Returns {sent, failed, skipped} summary.
    """
    conn = _connect()
    contacts = conn.execute(
        "SELECT id, county, agency_name, contact_email, contact_name "
        "FROM agency_contacts "
        "WHERE is_active=1 AND weekly_brief_enabled=1 "
        "AND (last_sent_at IS NULL OR last_sent_at < datetime('now', '-6 days'))"
    ).fetchall()

    smtp_user   = getattr(config, 'SMTP_USER', '')
    smtp_pass   = getattr(config, 'SMTP_PASSWORD', '')
    smtp_server = getattr(config, 'SMTP_SERVER', '')
    smtp_port   = int(getattr(config, 'SMTP_PORT', 587))

    if not smtp_server or not smtp_user:
        logging.warning("agency_brief: SMTP not configured, skipping send")
        conn.close()
        return {'sent': 0, 'failed': 0, 'skipped': len(contacts)}

    sent = failed = skipped = 0
    for contact in contacts:
        county = contact['county']
        email_addr = contact['contact_email']
        try:
            html_body  = generate_agency_brief_html(county)
            plain_body = (
                f"{county} County — Weekly Crime Brief\n"
                f"View full report: https://montanablotter.com/county/{county.lower().replace(' ','-')}\n"
                f"Montana Crime Atlas: https://montanablotter.com/crime-atlas"
            )
            subject = (
                f"{county} County — Weekly Crime Brief | "
                f"{datetime.utcnow().strftime('%B %-d, %Y')}"
            )
            msg = MIMEMultipart('alternative')
            msg['From'] = smtp_user
            to_field = f"{contact['contact_name']} <{email_addr}>" if contact['contact_name'] else email_addr
            msg['To'] = to_field
            msg['Subject'] = subject
            msg.attach(MIMEText(plain_body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))

            smtp = smtplib.SMTP(smtp_server, smtp_port)
            try:
                smtp.starttls()
                smtp.login(smtp_user, smtp_pass)
                smtp.sendmail(smtp_user, email_addr, msg.as_string())
            finally:
                smtp.quit()

            conn.execute(
                "UPDATE agency_contacts SET last_sent_at=datetime('now') WHERE id=?",
                (contact['id'],)
            )
            conn.commit()
            sent += 1
            logging.info(f"agency_brief: sent to {email_addr} ({county})")
        except Exception as e:
            failed += 1
            logging.error(f"agency_brief: failed for {email_addr} ({county}): {e}")

    conn.close()
    return {'sent': sent, 'failed': failed, 'skipped': skipped}
