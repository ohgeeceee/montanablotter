from __future__ import annotations

import smtplib
import sqlite3
from email.mime.text import MIMEText
from typing import Iterable

import config


def app_setting_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    try:
        row = conn.execute(
            'SELECT value FROM app_settings WHERE key = ?',
            (key,),
        ).fetchone()
    except sqlite3.DatabaseError:
        return default
    if not row or row['value'] in (None, ''):
        return default
    try:
        return int(row['value'])
    except (TypeError, ValueError):
        return default


def collect_alert_recipients(conn: sqlite3.Connection) -> list[str]:
    recipients: list[str] = []

    configured = getattr(config, 'ADMIN_ALERT_EMAILS', ()) or ()
    if isinstance(configured, str):
        configured = [part.strip() for part in configured.split(',') if part.strip()]
    for entry in configured:
        email = (entry or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)

    try:
        user_columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info('users')").fetchall()
        }
        if 'email' in user_columns:
            for row in conn.execute(
                """
                SELECT DISTINCT lower(email) AS email
                FROM users
                WHERE membership = 'pro'
                  AND email IS NOT NULL
                  AND trim(email) != ''
                """
            ).fetchall():
                email = (row['email'] or '').strip().lower()
                if email and '@' in email and email not in recipients:
                    recipients.append(email)
    except sqlite3.DatabaseError:
        pass

    fallback = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
    if fallback and '@' in fallback and fallback not in recipients:
        recipients.append(fallback)

    return recipients


def send_plaintext_email(recipients: Iterable[str], subject: str, body: str) -> bool:
    smtp_user = (getattr(config, 'SMTP_USER', '') or '').strip()
    smtp_password = (getattr(config, 'SMTP_PASSWORD', '') or '').strip()
    smtp_server = (getattr(config, 'SMTP_SERVER', '') or '').strip()
    smtp_port = int(getattr(config, 'SMTP_PORT', 587) or 587)
    target_list = [value.strip().lower() for value in recipients if value and '@' in value]
    if not target_list or not (smtp_user and smtp_password and smtp_server):
        return False

    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = ', '.join(target_list)
    try:
        smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(smtp_user, target_list, msg.as_string())
        smtp.quit()
        return True
    except Exception:
        return False
