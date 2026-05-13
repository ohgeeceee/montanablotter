from __future__ import annotations

import sqlite3
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config
import db


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_spending_alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_record_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(subscription_id, source_type, source_record_id)
        )
        """
    )
    try:
        conn.execute("ALTER TABLE watchdog_spending_alert_events ADD COLUMN sent_at TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _send_email(to_addr: str, subject: str, body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = (getattr(config, "SMTP_USER", "") or "").strip()
    msg["To"] = to_addr
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(config.SMTP_SERVER, int(getattr(config, "SMTP_PORT", 587) or 587), timeout=20) as smtp:
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.sendmail(config.SMTP_USER, [to_addr], msg.as_string())


def dispatch_watchdog_spending_alerts(limit: int = 100) -> dict[str, int]:
    conn = db.connect_db()
    _ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT e.id, e.message, e.created_at, s.email
        FROM watchdog_spending_alert_events e
        JOIN watchdog_subscriptions s ON s.id = e.subscription_id
        WHERE (e.sent_at IS NULL OR e.sent_at = '')
          AND s.status = 'active'
        ORDER BY e.created_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    sent = 0
    failed = 0
    for row in rows:
        try:
            subject = "Watchdog spending alert"
            body = f"{row['message']}\n\nGenerated at: {row['created_at']}\n\nDashboard: https://montanablotter.com/watchdog"
            _send_email(row["email"], subject, body)
            conn.execute(
                "UPDATE watchdog_spending_alert_events SET sent_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
            sent += 1
        except Exception:
            failed += 1

    conn.commit()
    conn.close()
    return {"processed": len(rows), "sent": sent, "failed": failed}


if __name__ == "__main__":
    print(dispatch_watchdog_spending_alerts())
