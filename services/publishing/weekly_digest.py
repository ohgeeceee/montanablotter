"""
Weekly Digest — emails a week-in-review summary to all active subscribers.
Covers the 7 days ending on the most recent Sunday. Runs Monday 7:45am via cron.
Reuses SMTP, digest tracking, and HTML helpers from morning_briefing.
"""

import argparse
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import escape

import config
from services.publishing.morning_briefing import (
    ADMIN_EMAIL,
    BASE_URL,
    _ensure_digest_tables,
    _finish_digest_run,
    _record_digest_recipient,
    _slugify,
    get_db,
    group_posts_by_county,
    send_email,
)


def _last_sunday(ref: date) -> date:
    """Return the most recent Sunday on or before ref."""
    days_since_sunday = (ref.weekday() + 1) % 7
    return ref - timedelta(days=days_since_sunday)


def get_posts_for_week(end_date_str: str, counties: list[str] | None = None) -> list:
    """Fetch posts whose incident_date or created_at falls in the 7-day window ending end_date."""
    conn = get_db()
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    start_dt = end_dt - timedelta(days=6)
    start_str = start_dt.isoformat()

    sql = """
        SELECT p.id, p.title, p.summary, p.agency_name, p.county, p.incident_date
        FROM posts p
        WHERE (
            (p.incident_date >= ? AND p.incident_date <= ?)
            OR (DATE(p.created_at) >= ? AND DATE(p.created_at) <= ?)
        )
    """
    params: list = [start_str, end_date_str, start_str, end_date_str]

    if counties:
        placeholders = ",".join("?" * len(counties))
        sql += f" AND p.county IN ({placeholders})"
        params.extend(counties)

    sql += " ORDER BY p.incident_date, p.county, p.created_at"
    posts = conn.execute(sql, params).fetchall()
    conn.close()
    return posts


def build_weekly_html(
    posts: list,
    start_date_str: str,
    end_date_str: str,
    unsubscribe_url: str | None = None,
) -> str:
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
    if start_dt.month == end_dt.month:
        week_label = f"{start_dt.strftime('%b %-d')}–{end_dt.strftime('%-d, %Y')}"
    else:
        week_label = f"{start_dt.strftime('%b %-d')}–{end_dt.strftime('%b %-d, %Y')}"

    county_counts = Counter((p["county"] or "Unknown") for p in posts)
    county_total = len(county_counts)
    top_counties = county_counts.most_common(3)
    grouped = group_posts_by_county(posts)

    top_counties_html = ""
    if top_counties:
        badges = []
        for name, count in top_counties:
            slug = _slugify(name)
            badges.append(
                f'<a href="{BASE_URL}/county/{slug}" style="display:inline-block;margin:2px 4px 2px 0;'
                f"background:#eff6ff;color:#1d4ed8;border-radius:20px;padding:3px 10px;"
                f'font-size:13px;font-weight:700;text-decoration:none;">'
                f"{escape(name)} ({count})</a>"
            )
        top_counties_html = (
            '<p style="margin:8px 0 0;color:#64748b;font-size:13px;">'
            "Top counties: " + " ".join(badges) + "</p>"
        )

    html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
<h2 style="color:#1e293b;margin:0 0 4px;">Montana Blotter: Week in Review</h2>
<p style="color:#64748b;margin:0 0 2px;"><strong>Week of {week_label}</strong></p>
<p style="color:#64748b;margin:0;">{len(posts)} report{"s" if len(posts) != 1 else ""} across {county_total} count{"ies" if county_total != 1 else "y"}</p>
{top_counties_html}
<p style="margin:14px 0 0;">
    <a href="{BASE_URL}/counties" style="display:inline-block;background:#2563eb;color:#ffffff;padding:10px 14px;border-radius:8px;text-decoration:none;font-weight:700;">Browse county pages</a>
    <a href="{BASE_URL}/arrests" style="display:inline-block;margin-left:8px;background:#eff6ff;color:#1d4ed8;padding:10px 14px;border-radius:8px;text-decoration:none;font-weight:700;">View arrest log</a>
</p>
<hr style="border:1px solid #e2e8f0;margin:20px 0;">
"""

    for county_name, county_posts in grouped.items():
        county_slug = _slugify(county_name)
        county_heading = (
            f'<a href="{BASE_URL}/county/{county_slug}" style="color:#1e293b;text-decoration:none;">'
            f"{escape(county_name)} County</a>"
            if county_name != "Unknown"
            else "Unknown County"
        )
        html += f"""
<h3 style="color:#1e293b;margin:0 0 4px;">{county_heading}</h3>
<p style="color:#64748b;font-size:13px;margin:0 0 14px;">{len(county_posts)} report{"s" if len(county_posts) != 1 else ""}</p>
"""
        for post in county_posts:
            agency = escape(post["agency_name"] or post["county"] or "Unknown Agency")
            summary_raw = post["summary"] or ""
            summary_lines = summary_raw.split("\n")
            formatted_lines = []
            for line in summary_lines:
                line = line.strip()
                if not line:
                    continue
                if re.match(
                    r"^(\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[–-]\s*|\d{1,2}:\d{2}\s*[–-])",
                    line,
                    re.IGNORECASE,
                ):
                    parts = line.split("–", 1)
                    if len(parts) == 2:
                        time_part = parts[0].strip()
                        rest = parts[1].strip()
                        formatted_lines.append(
                            f'<div style="margin:4px 0;padding:6px 8px;background:#f8fafc;border-left:3px solid #2563eb;border-radius:4px;">'
                            f'<strong style="color:#1e293b;">{escape(time_part)}</strong> – {escape(rest)}'
                            f"</div>"
                        )
                    else:
                        formatted_lines.append(
                            f'<div style="margin:4px 0;padding:6px 8px;background:#f8fafc;border-left:3px solid #2563eb;border-radius:4px;">{escape(line)}</div>'
                        )
                elif line.startswith("The ") and "responded to" in line:
                    formatted_lines.append(
                        f'<p style="color:#475569;font-weight:600;margin:12px 0 8px;">{escape(line)}</p>'
                    )
                elif line.startswith("// Historical"):
                    formatted_lines.append(
                        f'<p style="color:#94a3b8;font-size:12px;font-style:italic;margin:12px 0 0;border-top:1px solid #e2e8f0;padding-top:8px;">{escape(line)}</p>'
                    )
                else:
                    formatted_lines.append(
                        f'<p style="color:#374151;margin:2px 0;">{escape(line)}</p>'
                    )
            summary_html = (
                "\n".join(formatted_lines)
                if formatted_lines
                else escape(summary_raw).replace("\n", "<br>")
            )
            title = escape(post["title"] or "Daily Activity Report")
            post_url = f"{BASE_URL}/post/{post['id']}"
            html += f"""
<div style="padding:0 0 14px;">
    <h4 style="color:#1e293b;margin:0 0 6px;">{title}</h4>
    <p style="color:#64748b;font-size:13px;margin:0 0 8px;">{agency} &mdash; {escape(post["incident_date"] or end_date_str)}</p>
    <div style="color:#374151;line-height:1.6;margin:0;">{summary_html}</div>
    <p style="margin:12px 0 0;">
        <a href="{post_url}" style="display:inline-block;background:#111827;color:#ffffff;padding:8px 12px;border-radius:7px;text-decoration:none;font-weight:700;font-size:13px;">Read full report</a>
    </p>
</div>
"""
        html += '<hr style="border:1px solid #e2e8f0;">'

    html += f'<p style="color:#94a3b8;font-size:12px;margin-top:24px;"><a href="{BASE_URL}" style="color:#3b82f6;">montanablotter.com</a>'
    if unsubscribe_url:
        html += f' &mdash; <a href="{unsubscribe_url}" style="color:#94a3b8;">Unsubscribe</a>'
    html += "</p></div>"
    return html


def _create_weekly_run(
    conn: sqlite3.Connection,
    *,
    target_date: str,
    subject: str,
    preview_posts: int,
    preview_subscribers: int,
    initiated_by: str = "cron",
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO digest_runs (
            kind, target_date, audience, status, subject, preview_posts,
            preview_subscribers, initiated_by, notes, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            "weekly_digest",
            target_date,
            "subscribers",
            "running",
            (subject or "").strip()[:255],
            int(preview_posts or 0),
            int(preview_subscribers or 0),
            initiated_by,
            "Scheduled weekly digest run.",
        ),
    )
    return cursor.lastrowid


def run_weekly_digest(
    dry_run: bool = False,
    target_end_date: str | None = None,
    initiated_by: str = "cron",
) -> dict:
    """Send week-in-review digest to all active subscribers.

    Returns a summary dict with sent/skipped/failed counts.
    """
    if target_end_date:
        end_date = datetime.strptime(target_end_date, "%Y-%m-%d").date()
    else:
        end_date = _last_sunday(datetime.now(timezone.utc).date())

    start_date = end_date - timedelta(days=6)
    end_date_str = end_date.isoformat()
    start_date_str = start_date.isoformat()

    start_label = start_date.strftime("%b %-d")
    end_label = end_date.strftime("%b %-d, %Y")
    subject = (
        f"Montana Blotter: Week in Review – {start_label}–{end_label}"
        .replace("\r", "").replace("\n", "")
    )

    print(f"Weekly digest: {start_date_str} → {end_date_str}")

    conn = get_db()
    _ensure_digest_tables(conn)

    subscribers = conn.execute(
        "SELECT email, counties, token FROM subscribers WHERE active=1"
    ).fetchall()

    all_posts = get_posts_for_week(end_date_str)
    print(f"  {len(all_posts)} total posts | {len(subscribers)} active subscribers")

    if dry_run:
        print("  dry-run — no emails sent")
        conn.close()
        return {"sent_count": 0, "skipped_count": len(subscribers), "failed_count": 0, "dry_run": True}

    run_id = _create_weekly_run(
        conn,
        target_date=end_date_str,
        subject=subject,
        preview_posts=len(all_posts),
        preview_subscribers=len(subscribers),
        initiated_by=initiated_by,
    )
    conn.commit()
    conn.close()

    sent = skipped = failed = 0
    run_conn = get_db()

    # Admin gets full unfiltered digest
    if all_posts:
        admin_html = build_weekly_html(all_posts, start_date_str, end_date_str)
        try:
            send_email(ADMIN_EMAIL, subject, admin_html)
            print(f"  Admin copy sent ({len(all_posts)} posts)")
        except Exception as exc:
            print(f"  Admin copy failed: {exc}")

    for sub in subscribers:
        county_filter = [c.strip() for c in (sub["counties"] or "").split(",") if c.strip()]
        sub_posts = get_posts_for_week(end_date_str, county_filter or None)
        if not sub_posts:
            skipped += 1
            _record_digest_recipient(
                run_conn, run_id, sub["email"], sub["counties"] or "",
                "skipped", post_count=0,
                error_message="No matching posts for subscriber counties.",
            )
            continue
        unsub_url = f"{BASE_URL}/unsubscribe?token={sub['token']}"
        html = build_weekly_html(sub_posts, start_date_str, end_date_str, unsubscribe_url=unsub_url)
        try:
            send_email(sub["email"], subject, html)
            sent += 1
            _record_digest_recipient(
                run_conn, run_id, sub["email"], sub["counties"] or "",
                "sent", post_count=len(sub_posts),
            )
        except Exception as exc:
            print(f"  Failed → {sub['email']}: {exc}")
            failed += 1
            _record_digest_recipient(
                run_conn, run_id, sub["email"], sub["counties"] or "",
                "failed", post_count=len(sub_posts), error_message=str(exc),
            )

    final_status = "completed"
    if failed and sent:
        final_status = "completed_with_errors"
    elif failed and not sent:
        final_status = "failed"

    _finish_digest_run(
        run_conn, run_id,
        status=final_status,
        sent_count=sent,
        skipped_count=skipped,
        failed_count=failed,
        notes="Scheduled weekly digest run.",
    )
    run_conn.commit()
    run_conn.close()

    print(f"  Subscriber weekly digest: {sent} sent, {skipped} skipped, {failed} failed")
    return {"sent_count": sent, "skipped_count": skipped, "failed_count": failed, "dry_run": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send weekly subscriber digest")
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no emails sent")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Override the end date (default: last Sunday)")
    args = parser.parse_args()

    results = run_weekly_digest(dry_run=args.dry_run, target_end_date=args.date)
    if results["dry_run"]:
        print("Dry run complete.")
    else:
        print(f"Done: {results['sent_count']} sent, {results['skipped_count']} skipped, {results['failed_count']} failed.")
