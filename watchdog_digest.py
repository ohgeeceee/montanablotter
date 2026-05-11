"""Weekly Watchdog Digest Generator.

Runs every Sunday to generate a personalized transparency digest for each
active Watchdog subscriber using Gemini Flash (free tier).

Usage:
    python watchdog_digest.py          # generate + print
    python watchdog_digest.py --send   # generate + email via SMTP
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import config
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "blotter.db")

# ---------------------------------------------------------------------------
# Gemini helper
# ---------------------------------------------------------------------------

def _get_gemini():
    import google.generativeai as genai
    key = getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return None
    genai.configure(api_key=key)
    return genai.GenerativeModel("gemini-flash-latest")


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

def _recent_records(conn, counties: list[str], days: int = 7) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    query = "SELECT * FROM records WHERE date >= ? "
    params: list = [cutoff]

    if counties:
        placeholders = ",".join("?" * len(counties))
        query += f" AND county IN ({placeholders})"
        params.extend(counties)

    query += " ORDER BY date DESC LIMIT 200"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _recent_salaries(conn, counties: list[str]) -> list[dict]:
    query = "SELECT * FROM public_salaries WHERE 1=1"
    params: list = []
    if counties:
        placeholders = ",".join("?" * len(counties))
        query += f" AND county IN ({placeholders})"
        params.extend(counties)
    query += " ORDER BY salary DESC LIMIT 20"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _keyword_matches(conn, sub_id: int, records: list[dict]) -> list[dict]:
    """Filter records that match any of the subscriber's keyword alerts."""
    kw_rows = conn.execute(
        "SELECT keyword FROM watchdog_keyword_alerts WHERE subscription_id = ? AND active = 1",
        (sub_id,),
    ).fetchall()
    keywords = [r["keyword"].lower() for r in kw_rows]
    if not keywords:
        return []

    matches = []
    for rec in records:
        text = json.dumps(rec).lower()
        for kw in keywords:
            if kw in text:
                matches.append(rec)
                break
    return matches


# ---------------------------------------------------------------------------
# Digest generation
# ---------------------------------------------------------------------------

def generate_digest(sub: dict, records: list[dict], salaries: list[dict],
                    keyword_hits: list[dict]) -> str | None:
    model = _get_gemini()
    if not model:
        print("  [skip] No GEMINI_API_KEY configured.")
        return None

    counties_label = sub.get("counties") or "all Montana counties"
    beats_label = sub.get("beats") or "general public records"

    # Trim data for prompt context
    records_summary = json.dumps(records[:50], default=str)[:8000]
    salary_summary = json.dumps(salaries[:10], default=str)[:3000]
    kw_summary = json.dumps(keyword_hits[:20], default=str)[:3000]

    prompt = f"""You are writing a weekly public records digest for a journalist/watchdog 
covering {counties_label} on the beat of {beats_label}.

Summarize the most newsworthy items from this week's data.
Highlight: unusual patterns, high-profile names, significant charges,
large contracts, salary anomalies.

Format: 5–8 bullet points, each concise and with an indication of which 
record it references (date, type, county).

This week's incident/arrest records:
{records_summary}

Top salary records on file:
{salary_summary}

Keyword alert matches this week:
{kw_summary}

If there is nothing newsworthy, still provide 3–5 general observations 
about public records activity levels across these counties."""

    try:
        resp = model.generate_content(prompt)
        return resp.text.strip()
    except Exception as exc:
        print(f"  [error] Gemini generation failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(send_email: bool = False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    today = datetime.now().strftime("%Y-%m-%d")

    subs = conn.execute(
        "SELECT * FROM watchdog_subscriptions WHERE status = 'active' AND digest_enabled = 1"
    ).fetchall()

    if not subs:
        print("No active Watchdog subscribers with digest enabled.")
        conn.close()
        return

    for row in subs:
        sub = dict(row)
        print(f"\n{'='*60}")
        print(f"Generating digest for: {sub['email']} ({sub.get('organization') or 'individual'})")

        counties = [c.strip() for c in (sub.get("counties") or "").split(",") if c.strip()]
        records = _recent_records(conn, counties)
        salaries = _recent_salaries(conn, counties)
        kw_hits = _keyword_matches(conn, sub["id"], records)

        print(f"  Records this week: {len(records)}")
        print(f"  Keyword hits: {len(kw_hits)}")

        digest_text = generate_digest(sub, records, salaries, kw_hits)
        if not digest_text:
            print("  [skip] No digest generated.")
            continue

        # Store digest
        conn.execute(
            "INSERT INTO watchdog_digests (subscription_id, digest_date, content) VALUES (?, ?, ?)",
            (sub["id"], today, digest_text),
        )
        conn.commit()

        print(f"  Digest stored ({len(digest_text)} chars).")
        print(f"\n{digest_text}\n")

        if send_email:
            _send_digest_email(sub, digest_text, today)


def _send_digest_email(sub: dict, digest_text: str, date_str: str):
    """Send digest via SMTP (best-effort)."""
    import smtplib
    from email.mime.text import MIMEText

    smtp_server = getattr(config, "SMTP_SERVER", "") or os.getenv("MB_SMTP_SERVER", "")
    smtp_port = int(getattr(config, "SMTP_PORT", 0) or os.getenv("MB_SMTP_PORT", "587"))
    smtp_user = getattr(config, "SMTP_USER", "") or os.getenv("MB_SMTP_USER", "")
    smtp_pass = getattr(config, "SMTP_PASSWORD", "") or os.getenv("MB_SMTP_PASSWORD", "")

    if not all([smtp_server, smtp_user, smtp_pass]):
        print("  [skip] SMTP not configured, skipping email.")
        return

    subject = f"Montana Blotter Watchdog Digest — {date_str}"
    body = f"""Weekly Watchdog Digest
{date_str}
{'='*50}

{digest_text}

---
Montana Blotter — montanablotter.com
Manage your subscription: https://montanablotter.com/watchdog/dashboard/{sub['token']}
"""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = sub["email"]

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"  [sent] Digest emailed to {sub['email']}")
    except Exception as exc:
        print(f"  [error] Failed to email: {exc}")


if __name__ == "__main__":
    do_send = "--send" in sys.argv
    run(send_email=do_send)
