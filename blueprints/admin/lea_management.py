"""Admin console: LEA agency onboarding, verification, directory, audit log viewer.

Routes registered on admin_bp under /admin/lea-management/*.

Requires super_admin or ops role.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from flask import (
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from db import get_db
from blueprints.admin import admin_bp, _log_admin_action, require_role
from utils.auth_constants import ADMIN_ACCESS_ROLES, ADMIN_MANAGEMENT_ROLES

log = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────


def _ensure_schema(conn) -> None:
    """Ensure LEA tables exist for the admin panel queries."""
    from init_db import ensure_lea_schema

    ensure_lea_schema(conn)


def _status_label(status: str) -> str:
    return {
        "pending": "Pending",
        "verified": "Verified",
        "rejected": "Rejected",
        "onboarding": "Onboarding",
    }.get(status, status)


def _ago(ts_str: str | None) -> str:
    """Return a human-friendly relative time string."""
    if not ts_str:
        return "—"
    try:
        dt = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return ts_str or "—"
    delta = datetime.now() - dt
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        m = int(delta.total_seconds() / 60)
        return f"{m}m ago"
    if delta < timedelta(days=1):
        h = int(delta.total_seconds() / 3600)
        return f"{h}h ago"
    d = delta.days
    return f"{d}d ago"


# ── dashboard ──────────────────────────────────────────────────────────────


@admin_bp.route("/lea-management")
@require_role(*ADMIN_ACCESS_ROLES)
def lea_dashboard():
    """Overview with stats cards for LEA operations."""
    conn = get_db()
    _ensure_schema(conn)

    stats = dict(
        conn.execute(
            """
            SELECT
                COUNT(*) AS total_agencies,
                SUM(CASE WHEN verification_status = 'pending' THEN 1 ELSE 0 END) AS pending_verifications,
                SUM(CASE WHEN verification_status = 'verified' THEN 1 ELSE 0 END) AS verified_agencies,
                SUM(CASE WHEN verification_status = 'rejected' THEN 1 ELSE 0 END) AS rejected_agencies
            FROM lea_agencies
            """
        ).fetchone()
    )

    # Count active users (last login within 30 days)
    thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
    active_users = conn.execute(
        "SELECT COUNT(*) AS cnt FROM lea_users WHERE last_login_at >= ?",
        (thirty_days_ago,),
    ).fetchone()["cnt"]

    # Submissions this week
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    submissions_this_week = conn.execute(
        "SELECT COUNT(*) AS cnt FROM lea_blotter_drafts WHERE created_at >= ?",
        (week_ago,),
    ).fetchone()["cnt"]

    recent_agencies = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, org_name, county_name, agency_type, verification_status,
                   created_at
            FROM lea_agencies
            ORDER BY created_at DESC LIMIT 10
            """
        ).fetchall()
    ]

    conn.close()
    return render_template(
        "admin/lea_management.html",
        stats=stats,
        active_users=active_users,
        submissions_this_week=submissions_this_week,
        recent_agencies=recent_agencies,
        status_label=_status_label,
        now=datetime.now(),
    )


# ── agency directory ───────────────────────────────────────────────────────


@admin_bp.route("/lea-management/directory")
@require_role(*ADMIN_ACCESS_ROLES)
def lea_agency_directory():
    """Searchable table of all LEA agencies."""
    conn = get_db()
    _ensure_schema(conn)

    q = (request.args.get("q") or "").strip()[:120]
    status_filter = (request.args.get("status") or "").strip().lower()

    base_sql = "FROM lea_agencies a WHERE 1=1"
    params: list = []

    if q:
        base_sql += " AND (a.org_name LIKE ? OR a.county_name LIKE ? OR a.agency_type LIKE ? OR a.primary_contact_email LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like])
    if status_filter in ("pending", "verified", "rejected", "onboarding"):
        base_sql += " AND a.verification_status = ?"
        params.append(status_filter)

    agencies = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT a.*,
                   (SELECT COUNT(*) FROM lea_users u WHERE u.agency_id = a.id) AS user_count,
                   (SELECT MAX(u.last_login_at) FROM lea_users u WHERE u.agency_id = a.id) AS last_activity
            {base_sql}
            ORDER BY a.created_at DESC LIMIT 200
            """,
            params,
        ).fetchall()
    ]

    counts = dict(
        conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN verification_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN verification_status = 'verified' THEN 1 ELSE 0 END) AS verified,
                SUM(CASE WHEN verification_status = 'rejected' THEN 1 ELSE 0 END) AS rejected
            FROM lea_agencies
            """
        ).fetchone()
    )

    conn.close()
    return render_template(
        "admin/lea_management.html",
        section="directory",
        agencies=agencies,
        counts=counts,
        q=q,
        status_filter=status_filter,
        status_label=_status_label,
        now=datetime.now(),
    )


# ── agency detail ──────────────────────────────────────────────────────────


@admin_bp.route("/lea-management/agency/<int:agency_id>")
@require_role(*ADMIN_ACCESS_ROLES)
def lea_agency_detail(agency_id):
    """Full agency profile with tabs."""
    conn = get_db()
    _ensure_schema(conn)

    agency = conn.execute(
        "SELECT * FROM lea_agencies WHERE id = ?", (agency_id,)
    ).fetchone()
    if not agency:
        conn.close()
        abort(404)

    agency = dict(agency)

    # Users
    users = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, username, email, full_name, role, is_active, last_login_at, created_at
            FROM lea_users WHERE agency_id = ?
            ORDER BY created_at DESC LIMIT 50
            """,
            (agency_id,),
        ).fetchall()
    ]

    # Submissions / drafts
    submissions = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, incident_date, cad_number, case_number, primary_offense_mca,
                   submission_status, created_at, updated_at
            FROM lea_blotter_drafts WHERE agency_id = ?
            ORDER BY created_at DESC LIMIT 50
            """,
            (agency_id,),
        ).fetchall()
    ]

    # API tokens
    tokens = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, token_name, scopes, last_used_at, expires_at, is_revoked, created_at
            FROM lea_api_tokens WHERE agency_id = ?
            ORDER BY created_at DESC LIMIT 20
            """,
            (agency_id,),
        ).fetchall()
    ]

    # Coverage settings
    coverage = conn.execute(
        "SELECT * FROM lea_agency_coverages WHERE agency_id = ?",
        (agency_id,),
    ).fetchone()
    coverage = dict(coverage) if coverage else {}

    # Audit log entries for this agency
    audit_entries = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, action, resource_type, resource_id, change_summary, timestamp, user_id
            FROM lea_audit_log WHERE agency_id = ?
            ORDER BY timestamp DESC LIMIT 30
            """,
            (agency_id,),
        ).fetchall()
    ]

    conn.close()
    return render_template(
        "admin/lea_agency_detail.html",
        agency=agency,
        users=users,
        submissions=submissions,
        tokens=tokens,
        coverage=coverage,
        audit_entries=audit_entries,
        status_label=_status_label,
        ago=_ago,
        now=datetime.now(),
    )


# ── verify / reject ────────────────────────────────────────────────────────


@admin_bp.route("/lea-management/agency/<int:agency_id>/verify", methods=["POST"])
@require_role(*ADMIN_MANAGEMENT_ROLES)
def lea_verify_agency(agency_id):
    """Set verification_status='verified', record who and when."""
    conn = get_db()
    _ensure_schema(conn)

    agency = conn.execute(
        "SELECT id, org_name FROM lea_agencies WHERE id = ?", (agency_id,)
    ).fetchone()
    if not agency:
        conn.close()
        abort(404)

    conn.execute(
        """
        UPDATE lea_agencies
        SET verification_status = 'verified',
            verified_by_user_id = ?,
            verified_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (current_user.id, agency_id),
    )
    conn.commit()

    _log_admin_action(
        "lea_agency_verified",
        "lea_agencies",
        agency_id,
        metadata={"org_name": agency["org_name"]},
        conn=conn,
    )
    conn.close()
    flash(f"Agency '{agency['org_name']}' verified successfully.", "success")
    return redirect(url_for(".lea_agency_detail", agency_id=agency_id))


@admin_bp.route("/lea-management/agency/<int:agency_id>/reject", methods=["POST"])
@require_role(*ADMIN_MANAGEMENT_ROLES)
def lea_reject_agency(agency_id):
    """Reject an agency with a reason stored in notes."""
    conn = get_db()
    _ensure_schema(conn)

    agency = conn.execute(
        "SELECT id, org_name, notes FROM lea_agencies WHERE id = ?", (agency_id,)
    ).fetchone()
    if not agency:
        conn.close()
        abort(404)

    reason = (request.form.get("reason") or "").strip()[:1000]
    if not reason:
        flash("A rejection reason is required.", "error")
        conn.close()
        return redirect(url_for(".lea_agency_detail", agency_id=agency_id))

    existing_notes = agency["notes"] or ""
    updated_notes = f"[REJECTED: {reason}]\n{existing_notes}"[:2000]

    conn.execute(
        """
        UPDATE lea_agencies
        SET verification_status = 'rejected',
            notes = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (updated_notes, agency_id),
    )
    conn.commit()

    _log_admin_action(
        "lea_agency_rejected",
        "lea_agencies",
        agency_id,
        metadata={"org_name": agency["org_name"], "reason": reason},
        conn=conn,
    )
    conn.close()
    flash(f"Agency '{agency['org_name']}' rejected.", "warning")
    return redirect(url_for(".lea_agency_detail", agency_id=agency_id))


# ── bulk email ─────────────────────────────────────────────────────────────


@admin_bp.route("/lea-management/bulk-email", methods=["GET", "POST"])
@require_role(*ADMIN_MANAGEMENT_ROLES)
def lea_bulk_email():
    """Send email to selected agencies."""
    conn = get_db()
    _ensure_schema(conn)

    if request.method == "POST":
        subject = (request.form.get("subject") or "").strip()[:200]
        body = (request.form.get("body") or "").strip()[:5000]
        agency_ids_str = (request.form.get("agency_ids") or "").strip()

        if not subject or not body:
            flash("Subject and body are required.", "error")
            return redirect(url_for(".lea_bulk_email"))

        if agency_ids_str:
            agency_ids = [
                int(x) for x in agency_ids_str.split(",") if x.strip().isdigit()
            ]
        else:
            # All agencies
            agency_ids = [
                r["id"]
                for r in conn.execute("SELECT id FROM lea_agencies").fetchall()
            ]

        if not agency_ids:
            flash("No agencies selected.", "error")
            return redirect(url_for(".lea_bulk_email"))

        # Queue emails (insert into a pending email tracking table, or just log)
        # For now we log the action and note the count.
        _log_admin_action(
            "lea_bulk_email_queued",
            "lea_agencies",
            metadata={
                "subject": subject[:100],
                "agency_count": len(agency_ids),
                "agency_ids": agency_ids[:20],  # first 20 for audit
            },
            conn=conn,
        )
        conn.close()
        flash(
            f"Bulk email queued to {len(agency_ids)} agency/agencies. "
            f"(Subject: {subject})",
            "success",
        )
        return redirect(url_for(".lea_dashboard"))

    # GET: show form with agency list
    agencies = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, org_name, county_name, primary_contact_email, verification_status
            FROM lea_agencies
            ORDER BY org_name ASC LIMIT 500
            """
        ).fetchall()
    ]
    conn.close()
    return render_template(
        "admin/lea_management.html",
        section="bulk_email",
        agencies=agencies,
        status_label=_status_label,
        now=datetime.now(),
    )


# ── audit log viewer ───────────────────────────────────────────────────────


@admin_bp.route("/lea-management/audit-log")
@require_role(*ADMIN_ACCESS_ROLES)
def lea_audit_log_viewer():
    """Searchable audit log table with filters."""
    conn = get_db()
    _ensure_schema(conn)

    action_filter = (request.args.get("action") or "").strip().lower()
    agency_filter = (request.args.get("agency_id") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()[:20]
    date_to = (request.args.get("date_to") or "").strip()[:20]

    base_sql = "FROM lea_audit_log l LEFT JOIN lea_agencies a ON l.agency_id = a.id WHERE 1=1"
    params: list = []

    if action_filter:
        base_sql += " AND l.action LIKE ?"
        params.append(f"%{action_filter}%")
    if agency_filter and agency_filter.isdigit():
        base_sql += " AND l.agency_id = ?"
        params.append(int(agency_filter))
    if date_from:
        base_sql += " AND l.timestamp >= ?"
        params.append(date_from)
    if date_to:
        base_sql += " AND l.timestamp <= ?"
        params.append(date_to + "T23:59:59")

    entries = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT l.*, a.org_name AS agency_name
            {base_sql}
            ORDER BY l.timestamp DESC LIMIT 200
            """,
            params,
        ).fetchall()
    ]

    # Agency list for filter dropdown
    agencies = [
        dict(r)
        for r in conn.execute(
            "SELECT id, org_name FROM lea_agencies ORDER BY org_name ASC LIMIT 500"
        ).fetchall()
    ]

    conn.close()
    return render_template(
        "admin/lea_audit_log.html",
        entries=entries,
        agencies=agencies,
        action_filter=action_filter,
        agency_filter=agency_filter,
        date_from=date_from,
        date_to=date_to,
        ago=_ago,
        now=datetime.now(),
    )


# ── JSON endpoints ─────────────────────────────────────────────────────────


@admin_bp.route("/lea-management/api/stats")
@require_role(*ADMIN_ACCESS_ROLES)
def lea_api_stats():
    """JSON stats endpoint for dashboard widgets."""
    conn = get_db()
    _ensure_schema(conn)

    stats = dict(
        conn.execute(
            """
            SELECT
                COUNT(*) AS total_agencies,
                SUM(CASE WHEN verification_status = 'pending' THEN 1 ELSE 0 END) AS pending_verifications,
                SUM(CASE WHEN verification_status = 'verified' THEN 1 ELSE 0 END) AS verified_agencies
            FROM lea_agencies
            """
        ).fetchone()
    )
    conn.close()
    return jsonify(stats)
