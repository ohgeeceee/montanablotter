"""
Admin panel — Email Campaigns tab.

Endpoints:
  GET  /admin/email                  — campaign dashboard (templates + recent sends)
  GET  /admin/email/compose          — compose new campaign
  POST /admin/email/compose          — save draft / send campaign
  GET  /admin/email/templates         — template library
  GET  /admin/email/templates/<id>/json — JSON for AJAX loader
  POST /admin/email/templates         — create / update template
  POST /admin/email/templates/<id>/delete — delete template
  GET  /admin/email/log               — sent campaign log
  POST /admin/email/send-test        — send a test email to admin's own address
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from blueprints.admin import admin_bp, require_role, _log_admin_action
from db import get_db
import config

from utils.auth_constants import ADMIN_ACCESS_ROLES, EMAIL_OPS_SEND_ROLES

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SMTP helper (mirrors lawyer_outreach._send_email / audience patterns)
# ---------------------------------------------------------------------------

def _smtp_settings():
    """Lazy access to config.SMTP_* so unit tests can patch config."""
    return {
        'server': getattr(config, 'SMTP_SERVER', ''),
        'port': int(getattr(config, 'SMTP_PORT', 0) or 0),
        'user': getattr(config, 'SMTP_USER', getattr(config, 'EMAIL_USER', '')),
        'password': getattr(config, 'SMTP_PASSWORD', getattr(config, 'EMAIL_PASSWORD', '')),
    }


def _send_email(to_addr: str, subject: str, body: str, html_body: str | None = None) -> tuple[bool, str]:
    """Send one outbound email. Returns (ok, error_message)."""
    s = _smtp_settings()
    if not (s['server'] and s['port'] and s['user'] and s['password']):
        return False, 'smtp_not_configured'
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"Montana Blotter <{s['user']}>"
    msg['To'] = to_addr
    msg.attach(MIMEText(body, 'plain'))
    if html_body:
        msg.attach(MIMEText(html_body, 'html'))
    try:
        with smtplib.SMTP(s['server'], s['port']) as server:
            server.starttls()
            server.login(s['user'], s['password'])
            server.sendmail(s['user'], to_addr, msg.as_string())
        return True, ''
    except Exception as e:
        log.warning("email campaign send failed to %s: %s", to_addr, e)
        return False, str(e)[:200]


# ---------------------------------------------------------------------------
# Recipient lookup helpers
# ---------------------------------------------------------------------------

AUDIENCE_LABELS = {
    'lawyers':       'Attorneys & Law Firms',
    'bail_bondsmen': 'Bail Bondsmen & Bail Agencies',
    'clients':       'Subscribers & Registered Users',
    'courts':        'Courts & Judicial Offices',
    'police':        'Police Departments & Sheriff Offices',
}

AUDIENCE_DESCRIPTIONS = {
    'lawyers':       'Subscribers whose agency_name contains law, attorney, legal, firm, or counsel.',
    'bail_bondsmen': 'Subscribers whose agency_name contains bail, bond, surety, or bailiff.',
    'clients':       'All active subscribers plus registered public_users with valid email addresses.',
    'courts':        'Agencies in the emailed_agencies table whose name contains court, district, judicia, clerk, or mt.gov judicial addresses.',
    'police':        'Agencies in the emailed_agencies table whose name contains police, sheriff, pd, LEO, or records@ addresses.',
}


def _count_recipients(conn, audience: str) -> int:
    """Return the number of distinct email addresses for a given audience segment."""
    if audience == 'lawyers':
        return conn.execute(
            """SELECT COUNT(DISTINCT email) FROM subscribers
               WHERE active = 1
                 AND agency_name IS NOT NULL
                 AND (
                     agency_name LIKE '%law%' OR agency_name LIKE '%attorney%'
                     OR agency_name LIKE '%legal%' OR agency_name LIKE '%firm%'
                     OR agency_name LIKE '%counsel%' OR agency_name LIKE '%esquire%'
                 )"""
        ).fetchone()[0]
    if audience == 'bail_bondsmen':
        return conn.execute(
            """SELECT COUNT(DISTINCT email) FROM subscribers
               WHERE active = 1
                 AND agency_name IS NOT NULL
                 AND (
                     agency_name LIKE '%bail%' OR agency_name LIKE '%bond%'
                     OR agency_name LIKE '%surety%' OR agency_name LIKE '%bailiff%'
                 )"""
        ).fetchone()[0]
    if audience == 'clients':
        return conn.execute(
            """SELECT COUNT(DISTINCT email) FROM (
                   SELECT email FROM subscribers WHERE active = 1 AND email IS NOT NULL AND email != ''
                   UNION
                   SELECT email FROM public_users WHERE is_active = 1 AND email IS NOT NULL AND email != ''
               )"""
        ).fetchone()[0]
    if audience == 'courts':
        return conn.execute(
            """SELECT COUNT(DISTINCT email_address) FROM emailed_agencies
               WHERE email_address IS NOT NULL AND email_address != ''
                 AND (
                     LOWER(agency_name) LIKE '%court%' OR LOWER(agency_name) LIKE '%judicia%'
                     OR LOWER(agency_name) LIKE '%district%' OR LOWER(agency_name) LIKE '%clerk%'
                     OR LOWER(email_address) LIKE '%judicia%' OR LOWER(email_address) LIKE '%court%'
                     OR LOWER(email_address) LIKE '%.gov%'
                 )"""
        ).fetchone()[0]
    if audience == 'police':
        return conn.execute(
            """SELECT COUNT(DISTINCT email_address) FROM emailed_agencies
               WHERE email_address IS NOT NULL AND email_address != ''
                 AND (
                     LOWER(agency_name) LIKE '%police%' OR LOWER(agency_name) LIKE '%sheriff%'
                     OR LOWER(agency_name) LIKE '% pd%' OR LOWER(agency_name) LIKE '%leo%'
                     OR LOWER(agency_name) LIKE '%sheriff%' OR LOWER(email_address) LIKE '%sheriff%'
                     OR LOWER(email_address) LIKE '%police%' OR LOWER(email_address) LIKE '%pd%'
                     OR LOWER(email_address) LIKE '%records%'
                 )"""
        ).fetchone()[0]
    return 0


def _sample_recipients(conn, audience: str, limit: int = 5) -> list[dict]:
    """Return a small sample of email addresses for preview."""
    if audience == 'lawyers':
        rows = conn.execute(
            """SELECT DISTINCT email, agency_name FROM subscribers
               WHERE active = 1
                 AND agency_name IS NOT NULL
                 AND (
                     agency_name LIKE '%law%' OR agency_name LIKE '%attorney%'
                     OR agency_name LIKE '%legal%' OR agency_name LIKE '%firm%'
                     OR agency_name LIKE '%counsel%' OR agency_name LIKE '%esquire%'
                 )
               ORDER BY agency_name LIMIT ?""",
            (limit,),
        ).fetchall()
        return [{'email': r['email'], 'name': r['agency_name'] or ''} for r in rows]
    if audience == 'bail_bondsmen':
        rows = conn.execute(
            """SELECT DISTINCT email, agency_name FROM subscribers
               WHERE active = 1
                 AND agency_name IS NOT NULL
                 AND (
                     agency_name LIKE '%bail%' OR agency_name LIKE '%bond%'
                     OR agency_name LIKE '%surety%' OR agency_name LIKE '%bailiff%'
                 )
               ORDER BY agency_name LIMIT ?""",
            (limit,),
        ).fetchall()
        return [{'email': r['email'], 'name': r['agency_name'] or ''} for r in rows]
    if audience == 'clients':
        rows = conn.execute(
            """SELECT DISTINCT email, COALESCE(agency_name, display_name) AS name FROM (
                   SELECT email, agency_name, '' AS display_name FROM subscribers WHERE active = 1 AND email IS NOT NULL
                   UNION ALL
                   SELECT email, '' AS agency_name, display_name FROM public_users WHERE is_active = 1 AND email IS NOT NULL
               )
               ORDER BY name LIMIT ?""",
            (limit,),
        ).fetchall()
        return [{'email': r['email'], 'name': r['name'] or ''} for r in rows]
    if audience == 'courts':
        rows = conn.execute(
            """SELECT DISTINCT email_address AS email, agency_name AS name FROM emailed_agencies
               WHERE email_address IS NOT NULL AND email_address != ''
                 AND (
                     LOWER(agency_name) LIKE '%court%' OR LOWER(agency_name) LIKE '%judicia%'
                     OR LOWER(agency_name) LIKE '%district%' OR LOWER(agency_name) LIKE '%clerk%'
                     OR LOWER(email_address) LIKE '%judicia%' OR LOWER(email_address) LIKE '%court%'
                     OR LOWER(email_address) LIKE '%.gov%'
                 )
               ORDER BY agency_name LIMIT ?""",
            (limit,),
        ).fetchall()
        return [{'email': r['email'], 'name': r['name'] or ''} for r in rows]
    if audience == 'police':
        rows = conn.execute(
            """SELECT DISTINCT email_address AS email, agency_name AS name FROM emailed_agencies
               WHERE email_address IS NOT NULL AND email_address != ''
                 AND (
                     LOWER(agency_name) LIKE '%police%' OR LOWER(agency_name) LIKE '%sheriff%'
                     OR LOWER(agency_name) LIKE '% pd%' OR LOWER(agency_name) LIKE '%leo%'
                     OR LOWER(agency_name) LIKE '%sheriff%' OR LOWER(email_address) LIKE '%sheriff%'
                     OR LOWER(email_address) LIKE '%police%' OR LOWER(email_address) LIKE '%pd%'
                     OR LOWER(email_address) LIKE '%records%'
                 )
               ORDER BY agency_name LIMIT ?""",
            (limit,),
        ).fetchall()
        return [{'email': r['email'], 'name': r['name'] or ''} for r in rows]
    return []


def _collect_recipient_emails(conn, audience: str, extra_emails: str = '') -> list[str]:
    """Return the full list of recipient emails for a campaign send."""
    emails: set[str] = set()

    if audience == 'lawyers':
        rows = conn.execute(
            """SELECT DISTINCT email FROM subscribers
               WHERE active = 1
                 AND agency_name IS NOT NULL
                 AND (
                     agency_name LIKE '%law%' OR agency_name LIKE '%attorney%'
                     OR agency_name LIKE '%legal%' OR agency_name LIKE '%firm%'
                     OR agency_name LIKE '%counsel%' OR agency_name LIKE '%esquire%'
                 )"""
        ).fetchall()
        for r in rows:
            if r['email']:
                emails.add(r['email'].strip().lower())
    elif audience == 'bail_bondsmen':
        rows = conn.execute(
            """SELECT DISTINCT email FROM subscribers
               WHERE active = 1
                 AND agency_name IS NOT NULL
                 AND (
                     agency_name LIKE '%bail%' OR agency_name LIKE '%bond%'
                     OR agency_name LIKE '%surety%' OR agency_name LIKE '%bailiff%'
                 )"""
        ).fetchall()
        for r in rows:
            if r['email']:
                emails.add(r['email'].strip().lower())
    elif audience == 'clients':
        rows = conn.execute(
            """SELECT DISTINCT email FROM (
                   SELECT email FROM subscribers WHERE active = 1 AND email IS NOT NULL AND email != ''
                   UNION
                   SELECT email FROM public_users WHERE is_active = 1 AND email IS NOT NULL AND email != ''
               )"""
        ).fetchall()
        for r in rows:
            if r['email']:
                emails.add(r['email'].strip().lower())
    elif audience == 'courts':
        rows = conn.execute(
            """SELECT DISTINCT email_address FROM emailed_agencies
               WHERE email_address IS NOT NULL AND email_address != ''
                 AND (
                     LOWER(agency_name) LIKE '%court%' OR LOWER(agency_name) LIKE '%judicia%'
                     OR LOWER(agency_name) LIKE '%district%' OR LOWER(agency_name) LIKE '%clerk%'
                     OR LOWER(email_address) LIKE '%judicia%' OR LOWER(email_address) LIKE '%court%'
                     OR LOWER(email_address) LIKE '%.gov%'
                 )"""
        ).fetchall()
        for r in rows:
            if r['email_address']:
                emails.add(r['email_address'].strip().lower())
    elif audience == 'police':
        rows = conn.execute(
            """SELECT DISTINCT email_address FROM emailed_agencies
               WHERE email_address IS NOT NULL AND email_address != ''
                 AND (
                     LOWER(agency_name) LIKE '%police%' OR LOWER(agency_name) LIKE '%sheriff%'
                     OR LOWER(agency_name) LIKE '% pd%' OR LOWER(agency_name) LIKE '%leo%'
                     OR LOWER(agency_name) LIKE '%sheriff%' OR LOWER(email_address) LIKE '%sheriff%'
                     OR LOWER(email_address) LIKE '%police%' OR LOWER(email_address) LIKE '%pd%'
                     OR LOWER(email_address) LIKE '%records%'
                 )"""
        ).fetchall()
        for r in rows:
            if r['email_address']:
                emails.add(r['email_address'].strip().lower())

    # Merge any manually-entered extra emails
    if extra_emails:
        for raw in extra_emails.split(','):
            raw = raw.strip().lower()
            if '@' in raw and len(raw) < 254:
                emails.add(raw)

    return sorted(emails)


# ---------------------------------------------------------------------------
# Default high-converting templates (seeded on first load)
# ---------------------------------------------------------------------------

DEFAULT_TEMPLATES = [
    {
        'name': 'Lawyer Outreach - Premium Subscription Pitch',
        'audience': 'lawyers',
        'subject': 'Montana court records, arrests, and case data - direct access for your firm',
        'body': """Dear [Name],

Montana Blotter tracks public safety, court, and arrest records across all 56 Montana counties - updated daily. Thousands of attorneys, bail bondsmen, and legal professionals already rely on it to stay ahead of cases in their markets.

I'd like to offer your firm direct access to our Pro tier:

  - 12 months of searchable history
  - Statewide alerts across unlimited counties
  - Daily case and arrest email digests by county
  - Name, case number, charge, and keyword monitoring
  - Watchlists with status-change notifications
  - CSV and PDF exports for case files

The Pro plan runs $19.99/month or $199/year - and you can start with a free 7-day trial.

Would you like me to set up a trial for your firm? Reply to this email and I'll get you set up today.

[Your Name]
Montana Blotter - Public Records, Made Useful
https://montanablotter.com""",
        'notes': 'Leads with specific Montana coverage and the Pro feature set. Short, professional, and action-oriented.',
    },
    {
        'name': 'Bail Bondsman Outreach - Daily Arrest Digest + Lead Access',
        'audience': 'bail_bondsmen',
        'subject': 'Daily arrest digests for Montana counties - early leads for your bail business',
        'body': """Dear [Name],

Montana Blotter publishes daily arrest and booking digests from sheriff's offices and police departments across Montana - covering all 56 counties.

I'm reaching out because bail bondsmen and agencies use our data to:

  - Get early notice of arrests in their coverage areas
  - Monitor specific jails, counties, and charge types
  - Track repeat offenders and case status changes
  - Find defendants who need bonding assistance faster

Our Pro plan ($19.99/month or $199/year) includes:

  - Daily email digests for every county you choose
  - Real-time alerts when someone is booked in a monitored jail
  - Full 12-month searchable history
  - Name, charge, booking date, and facility details
  - CSV exports for your case management

I can set up a 7-day trial so you can see the coverage in your counties before you commit. Would that be useful?

[Your Name]
Montana Blotter
https://montanablotter.com""",
        'notes': "Speaks directly to the bail bondsman's workflow - early leads, jail monitoring, repeat offenders. Clear ROI.",
    },
    {
        'name': 'Client Onboarding - Welcome to Montana Blotter',
        'audience': 'clients',
        'subject': 'Welcome to Montana Blotter - your public safety dashboard for Montana',
        'body': """Hi [Name],

Welcome to Montana Blotter - your free, public-facing window into police blotters, jail bookings, court records, and public safety data from across Montana.

Here's what you can do today, for free:

  - Browse daily police blotters from counties across Montana
  - Search jail booking rosters by county, name, or date
  - Set up free daily email digests for the counties you care about
  - Follow missing persons alerts and public safety notices

Upgrade to Blotter Plus ($5.99/month or $59/year) when you need:

  - 12 months of history instead of 7 days
  - Up to 5 counties with county, city, and agency alerts
  - 5 name or keyword watchlists
  - 10 saved searches

Go Pro ($19.99/month or $199/year) for:

  - Unlimited counties and statewide alerts
  - Full archive access and case tracking
  - CSV and PDF exports
  - Priority support

Explore the site: https://montanablotter.com

Questions? Just reply - I'm here to help.

-[Your Name]
Montana Blotter""",
        'notes': 'Warm, helpful, and value-first. Shows the free tier immediately, then tiers as natural upgrades - not a hard sell.',
    },
    {
        'name': 'Client Retention - Your Montana Blotter Dashboard Is Waiting',
        'audience': 'clients',
        'subject': "Your Montana Blotter alerts are active - here's what's new",
        'body': """Hi [Name],

Your Montana Blotter account is active and your alerts are running. Here's a quick update on what you have access to and what's changed recently:

What's new on Montana Blotter:

  - More counties are publishing daily blotter data - check the counties you follow
  - New missing persons alerts are posted daily - turn on push or email alerts for the counties you care about
  - Court records and case tracking are expanding - watch for case status updates in your saved searches

Your current plan: [Plan Name]

If you're on Free and want more, Blotter Plus adds 12 months of history, up to 5 counties with alerts, watchlists, and saved searches for $5.99/month.

If you want the full picture - statewide alerts, full archive, CSV exports, and case tracking - Pro is $19.99/month.

Log in anytime: https://montanablotter.com/login

-[Your Name]
Montana Blotter""",
        'notes': 'Soft retention touch. Reminds the subscriber what they have, surfaces new coverage, and offers tiers as an upsell - not a renewal threat.',
    },
    {
        'name': 'Court Outreach - Public Record Partnership Request',
        'audience': 'courts',
        'subject': 'Montana Blotter - public access to court and arrest records across Montana',
        'body': """Dear [Court Contact Name],

Montana Blotter is a public-interest project that aggregates and publishes daily police blotters, jail bookings, court filings, and public safety records from counties across Montana - all in one searchable place.

We'd like to establish a direct, respectful working relationship with [Court/County Name] so that:

  - Public court and arrest records are easier for residents to find
  - Our coverage of [County] is accurate and complete
  - We can flag any records-access issues to the right office quickly

Specifically, we're requesting:

  - A point of contact for public-records questions about [County] court records
  - Information on any public portal, RSS feed, or bulk data access for court docket or booking data
  - Clarification on any access restrictions we should respect when republishing public data

We publish only public records and always credit the originating agency. Our goal is to make Montana's public data more visible and useful - not to interfere with official channels.

If it's easier, I'm happy to hop on a brief call. Either way, thank you for your time.

[Your Name]
Montana Blotter
https://montanablotter.com
[Your Email]""",
        'notes': 'Respectful, specific, and low-pressure. Positions MB as a public-access partner, not a records harasser. Asks for a point of contact and data-access info.',
    },
    {
        'name': 'Police Department Outreach - Blotter Feed & Operational Partnership',
        'audience': 'police',
        'subject': 'Montana Blotter - daily blotter feed + public safety updates from your agency',
        'body': """Dear [Agency Contact Name],

Montana Blotter publishes daily police blotters and jail booking data from law enforcement agencies across Montana - free for the public to search and follow.

I'm reaching out to [Agency Name] because we want to make sure our coverage of your agency is accurate, complete, and respectful of your operations. Specifically:

  - We publish your public blotter and booking data with clear attribution to [Agency Name]
  - We'd like a point of contact for public-records questions so errors can be corrected quickly
  - If your agency publishes blotter PDFs, CSV exports, or a public portal, we can pull from that directly - reducing manual work on both sides
  - We can set up a daily digest feed to records@montanablotter.com if that's easier than a portal

Our goal is straightforward: make your public safety data easier for Montana residents to find, while keeping your agency in control of how it's presented.

If you have a preferred contact, data format, or any restrictions we should know about, please let me know. I'm happy to work around your process.

Thank you for your time and for the work you do in [County].

[Your Name]
Montana Blotter
https://montanablotter.com
[Your Email]""",
        'notes': 'Operational tone. Focuses on data accuracy, attribution, and a low-friction feed path (PDF/CSV/portal to records@). Positions MB as helpful, not extractive.',
    },
]


# ---------------------------------------------------------------------------
# Template CRUD + seed
# ---------------------------------------------------------------------------

def _ensure_template_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_templates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            audience        TEXT NOT NULL,
            subject         TEXT NOT NULL,
            body            TEXT NOT NULL,
            notes           TEXT DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_campaigns (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_name       TEXT NOT NULL,
            template_id         INTEGER,
            audience            TEXT NOT NULL,
            subject             TEXT NOT NULL,
            body                TEXT NOT NULL,
            html_body           TEXT,
            status              TEXT NOT NULL DEFAULT 'draft',
            sent_at             TEXT,
            sent_by             TEXT,
            total_recipients    INTEGER NOT NULL DEFAULT 0,
            success_count       INTEGER NOT NULL DEFAULT 0,
            failure_count       INTEGER NOT NULL DEFAULT 0,
            failure_emails      TEXT DEFAULT '',
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)


def _seed_default_templates(conn):
    """Insert the 6 built-in templates if the table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM email_templates").fetchone()[0]
    if count > 0:
        return
    for t in DEFAULT_TEMPLATES:
        conn.execute(
            "INSERT INTO email_templates (name, audience, subject, body, notes) VALUES (?, ?, ?, ?, ?)",
            (t['name'], t['audience'], t['subject'], t['body'], t.get('notes', '')),
        )
    conn.commit()


def _render_template(body: str, context: dict[str, str]) -> str:
    """Replace [Placeholder] tokens in a template body with context values."""
    result = body
    for key, value in context.items():
        token = f'[{key}]'
        result = result.replace(token, value)
    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_bp.route('/email', strict_slashes=False)
@admin_bp.route('/email/dashboard')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_email_dashboard():
    """Campaign dashboard - template library summary + recent sends."""
    conn = get_db()
    _ensure_template_schema(conn)
    _seed_default_templates(conn)

    templates = conn.execute(
        "SELECT id, name, audience, subject, updated_at, LENGTH(body) AS body_length FROM email_templates ORDER BY updated_at DESC"
    ).fetchall()
    templates = [dict(r) for r in templates]

    recent = conn.execute(
        """SELECT id, campaign_name, audience, subject, status,
                  total_recipients, success_count, failure_count,
                  sent_at, sent_by, created_at
           FROM email_campaigns
           ORDER BY sent_at DESC NULLS LAST, created_at DESC
           LIMIT 25"""
    ).fetchall()
    recent = [dict(r) for r in recent]

    audience_counts = {}
    for aud in AUDIENCE_LABELS:
        audience_counts[aud] = _count_recipients(conn, aud)

    conn.close()

    return render_template(
        'admin_email_dashboard.html',
        templates=templates,
        recent=recent,
        audience_counts=audience_counts,
    )


@admin_bp.route('/email/compose', strict_slashes=False)
@login_required
@require_role(*EMAIL_OPS_SEND_ROLES)
def admin_email_compose():
    """Compose a new email campaign."""
    conn = get_db()
    _ensure_template_schema(conn)
    _seed_default_templates(conn)

    template_id = request.args.get('template_id', type=int)
    selected_template = None
    if template_id:
        row = conn.execute("SELECT * FROM email_templates WHERE id = ?", (template_id,)).fetchone()
        if row:
            selected_template = dict(row)

    audience = request.args.get('audience', 'clients')
    audience = audience if audience in AUDIENCE_LABELS else 'clients'

    recipient_count = _count_recipients(conn, audience)
    sample = _sample_recipients(conn, audience, limit=5)

    conn.close()

    return render_template(
        'admin_email_compose.html',
        selected_template=selected_template,
        audience=audience,
        recipient_count=recipient_count,
        samplerecipients=sample,
    )


@admin_bp.route('/email/compose', methods=['POST'])
@login_required
@require_role(*EMAIL_OPS_SEND_ROLES)
def admin_email_compose_submit():
    """Save as draft, send test, or send the campaign."""
    campaign_name = (request.form.get('campaign_name') or 'Untitled Campaign').strip()[:120]
    template_id = request.form.get('template_id', type=int)
    audience = request.form.get('audience', 'clients')
    audience = audience if audience in AUDIENCE_LABELS else 'clients'
    subject = (request.form.get('subject') or '').strip()[:250]
    body = (request.form.get('body') or '').strip()
    html_body = (request.form.get('html_body') or '').strip()
    action = (request.form.get('action') or 'save_draft').strip().lower()
    extra_emails = (request.form.get('extra_emails') or '').strip()

    if not subject:
        flash('Subject is required.', 'error')
        return redirect(url_for('admin.admin_email_compose', template_id=template_id, audience=audience))
    if not body:
        flash('Email body is required.', 'error')
        return redirect(url_for('admin.admin_email_compose', template_id=template_id, audience=audience))

    conn = get_db()
    _ensure_template_schema(conn)
    _seed_default_templates(conn)

    recipient_emails = _collect_recipient_emails(conn, audience, extra_emails)
    recipient_count = len(recipient_emails)

    if action == 'save_draft':
        conn.execute(
            """INSERT INTO email_campaigns
               (campaign_name, template_id, audience, subject, body, html_body,
                status, total_recipients, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, datetime('now'), datetime('now'))""",
            (campaign_name, template_id or None, audience, subject, body, html_body or None, recipient_count),
        )
        conn.commit()
        conn.close()
        flash(f'Draft saved ({recipient_count} potential recipients).', 'success')
        return redirect(url_for('admin.admin_email_dashboard'))

    if action == 'send_test':
        custom_recipient = (request.form.get('test_recipient') or '').strip().lower()
        if not custom_recipient or '@' not in custom_recipient:
            custom_recipient = getattr(current_user, 'email', None) or ''
        if not custom_recipient:
            conn.close()
            flash('No test recipient available. Log in with an email address or enter one above.', 'error')
            return redirect(url_for('admin.admin_email_compose', template_id=template_id, audience=audience))
        ok, err = _send_email(custom_recipient, subject, body, html_body or None)
        conn.close()
        if ok:
            flash(f'Test email sent to {custom_recipient}.', 'success')
        else:
            flash(f'Test email failed: {err}', 'error')
        return redirect(url_for('admin.admin_email_compose', template_id=template_id, audience=audience))

    # Full send
    if recipient_count == 0:
        conn.close()
        flash(f'No recipients found for audience "{audience}". Add extra emails or choose a different audience.', 'error')
        return redirect(url_for('admin.admin_email_compose', template_id=template_id, audience=audience))

    # Insert campaign row first so we have an id to update
    cursor = conn.execute(
        """INSERT INTO email_campaigns
           (campaign_name, template_id, audience, subject, body, html_body,
            status, total_recipients, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'sending', ?, datetime('now'), datetime('now'))""",
        (campaign_name, template_id or None, audience, subject, body, html_body or None, recipient_count),
    )
    campaign_id = cursor.lastrowid
    conn.commit()

    success_count = 0
    failure_count = 0
    failure_emails: list[str] = []
    sent_by = getattr(current_user, 'username', 'admin')

    for i, email in enumerate(recipient_emails):
        context: dict[str, str] = {'Name': '', 'Agency': '', 'County': '', 'AgencyName': '', 'Plan': ''}

        sub = conn.execute(
            "SELECT agency_name, counties, subscriber_plan FROM subscribers WHERE email = ?",
            (email,),
        ).fetchone()
        if sub:
            context['Name'] = (sub['agency_name'] or '').split()[0] if sub['agency_name'] else 'there'
            context['Agency'] = sub['agency_name'] or ''
            context['County'] = sub['counties'] or ''
            context['AgencyName'] = sub['agency_name'] or ''
            context['Plan'] = sub['subscriber_plan'] or ''
        else:
            pu = conn.execute(
                "SELECT display_name, subscriber_plan FROM public_users WHERE email = ?",
                (email,),
            ).fetchone()
            if pu:
                context['Name'] = (pu['display_name'] or '').split()[0] if pu['display_name'] else 'there'
                context['Plan'] = pu['subscriber_plan'] or ''

        agency = conn.execute(
            "SELECT agency_name FROM emailed_agencies WHERE email_address = ?",
            (email,),
        ).fetchone()
        if agency:
            context['Agency'] = agency['agency_name'] or ''
            context['AgencyName'] = agency['agency_name'] or ''

        personalized_body = _render_template(body, context)
        personalized_subject = _render_template(subject, context)

        ok, err = _send_email(email, personalized_subject, personalized_body, html_body or None)
        if ok:
            success_count += 1
        else:
            failure_count += 1
            failure_emails.append(f'{email} ({err})')

        if (i + 1) % 50 == 0:
            log.info("email campaign %s: %d/%d sent, %d failed", campaign_name, i + 1, recipient_count, failure_count)

    failure_str = '; '.join(failure_emails[:50])
    status = 'sent' if failure_count == 0 else 'partial'

    conn.execute(
        """UPDATE email_campaigns SET status = ?, sent_at = datetime('now'), sent_by = ?,
           success_count = ?, failure_count = ?, failure_emails = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (status, sent_by, success_count, failure_count, failure_str, campaign_id),
    )
    conn.commit()
    conn.close()

    if failure_count == 0:
        flash(f'Campaign sent: {success_count} delivered (of {recipient_count} recipients).', 'success')
    else:
        flash(f'Campaign sent: {success_count} delivered, {failure_count} failed (of {recipient_count} recipients).', 'warning')
    return redirect(url_for('admin.admin_email_dashboard'))


@admin_bp.route('/email/templates')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_email_templates():
    """Template library - browse, create, edit."""
    conn = get_db()
    _ensure_template_schema(conn)
    _seed_default_templates(conn)

    search = (request.args.get('q') or '').strip()[:80]
    audience_filter = (request.args.get('audience') or 'all')
    templates = conn.execute(
        """SELECT id, name, audience, subject, notes, updated_at, LENGTH(body) AS body_length
           FROM email_templates
           WHERE (? = 'all' OR audience = ?)
             AND (? = '' OR name LIKE ? OR subject LIKE ? OR body LIKE ?)
           ORDER BY updated_at DESC""",
        (audience_filter, audience_filter, search, f'%{search}%', f'%{search}%', f'%{search}%'),
    ).fetchall()
    templates = [dict(r) for r in templates]

    audiences = sorted(set(t['audience'] for t in templates))

    conn.close()

    return render_template(
        'admin_email_templates.html',
        templates=templates,
        audiences=audiences,
        search=search,
        audience_filter=audience_filter,
    )


@admin_bp.route('/email/templates', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_email_templates_save():
    """Create a new template or update an existing one."""
    template_id = request.form.get('template_id', type=int)
    name = (request.form.get('name') or '').strip()[:120]
    audience = (request.form.get('audience') or 'clients').strip()
    audience = audience if audience in AUDIENCE_LABELS else 'clients'
    subject = (request.form.get('subject') or '').strip()[:250]
    body = (request.form.get('body') or '').strip()
    notes = (request.form.get('notes') or '').strip()[:500]

    if not name:
        flash('Template name is required.', 'error')
        return redirect(url_for('admin.admin_email_templates'))
    if not subject:
        flash('Subject is required.', 'error')
        return redirect(url_for('admin.admin_email_templates'))
    if not body:
        flash('Body is required.', 'error')
        return redirect(url_for('admin.admin_email_templates'))

    conn = get_db()
    _ensure_template_schema(conn)

    if template_id:
        conn.execute(
            """UPDATE email_templates SET name = ?, audience = ?, subject = ?,
               body = ?, notes = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (name, audience, subject, body, notes, template_id),
        )
        conn.commit()
        flash(f'Template "{name}" updated.', 'success')
    else:
        conn.execute(
            "INSERT INTO email_templates (name, audience, subject, body, notes) VALUES (?, ?, ?, ?, ?)",
            (name, audience, subject, body, notes),
        )
        conn.commit()
        flash(f'Template "{name}" created.', 'success')

    conn.close()
    return redirect(url_for('admin.admin_email_templates'))


@admin_bp.route('/email/templates/<int:template_id>/json')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_email_template_json(template_id):
    """Return a single template as JSON for the compose-page AJAX loader."""
    conn = get_db()
    _ensure_template_schema(conn)
    row = conn.execute(
        "SELECT id, name, audience, subject, body, html_body, notes FROM email_templates WHERE id = ?",
        (template_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {'error': 'not_found'}, 404
    return {
        'id': row['id'],
        'name': row['name'],
        'audience': row['audience'],
        'subject': row['subject'],
        'body': row['body'],
        'html_body': row['html_body'] or '',
        'notes': row['notes'],
    }


@admin_bp.route('/email/templates/<int:template_id>/delete', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_email_template_delete(template_id):
    """Delete a template."""
    conn = get_db()
    _ensure_template_schema(conn)

    row = conn.execute("SELECT name FROM email_templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        conn.close()
        flash('Template not found.', 'error')
        return redirect(url_for('admin.admin_email_templates'))

    conn.execute("DELETE FROM email_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    flash(f'Template "{row["name"]}" deleted.', 'success')
    return redirect(url_for('admin.admin_email_templates'))


@admin_bp.route('/email/log')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_email_log():
    """Sent campaign log - filterable list of past sends."""
    conn = get_db()
    _ensure_template_schema(conn)

    search = (request.args.get('q') or '').strip()[:80]
    status_filter = (request.args.get('status') or 'all')
    audience_filter = (request.args.get('audience') or 'all')

    query = """SELECT id, campaign_name, audience, subject, status,
                    total_recipients, success_count, failure_count,
                    sent_at, sent_by, created_at
               FROM email_campaigns
               WHERE (? = 'all' OR status = ?)
                 AND (? = 'all' OR audience = ?)
                 AND (? = '' OR campaign_name LIKE ? OR subject LIKE ?)
               ORDER BY sent_at DESC NULLS LAST, created_at DESC
               LIMIT 100"""
    rows = conn.execute(
        query,
        (status_filter, status_filter, audience_filter, audience_filter, search, f'%{search}%', f'%{search}%'),
    ).fetchall()
    campaigns = [dict(r) for r in rows]

    conn.close()

    return render_template(
        'admin_email_log.html',
        campaigns=campaigns,
        search=search,
        status_filter=status_filter,
        audience_filter=audience_filter,
    )
