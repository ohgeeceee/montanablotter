"""LEA (Law Enforcement Agency) self-service panel blueprint.

Phase 3: Agency Dashboard UI — incident submission, batch CSV upload,
blotter history views, and login/auth.
"""

import csv
import io
import sqlite3
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import config
from db import get_db
from services.lea_auth import user_auth

lea_panel_bp = Blueprint("lea_panel", __name__, url_prefix="/panel")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db():
    """Return a DB connection (wraps from db import get_db)."""
    return get_db()


def _lookup_agency_by_slug(county_slug: str):
    """Return the lea_agencies row for the given county_slug or None."""
    conn = _get_db()
    row = conn.execute(
        "SELECT id, org_name, agency_type, county_name, county_slug, verification_status "
        "FROM lea_agencies WHERE county_slug = ?",
        (county_slug,),
    ).fetchone()
    return dict(row) if row else None


def _lookup_user(user_id: int):
    """Return the lea_users row for the given user_id or None."""
    conn = _get_db()
    row = conn.execute(
        "SELECT id, agency_id, username, email, full_name, role "
        "FROM lea_users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------


def login_required(f):
    """Decorator: require LEA user session and verify agency/county match."""

    @wraps(f)
    def decorated_function(county_slug=None, *args, **kwargs):
        if "lea_user_id" not in session:
            return redirect(url_for("lea_panel.login"))

        user = _lookup_user(session["lea_user_id"])
        if not user:
            session.clear()
            return redirect(url_for("lea_panel.login"))

        # Verify the user's agency matches the requested county_slug
        agency = _lookup_agency_by_slug(county_slug) if county_slug else None
        if county_slug and (not agency or user["agency_id"] != agency["id"]):
            return render_template("lea/error.html",
                                   message="You do not have access to this agency's dashboard."), 403

        return f(county_slug=county_slug, *args, **kwargs)

    return decorated_function


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@lea_panel_bp.route("/login", methods=["GET", "POST"])
def login():
    """LEA user login page and handler."""
    if request.method == "GET":
        return render_template("lea/login.html")

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        return render_template("lea/login.html",
                               error="Username and password are required."), 400

    conn = _get_db()
    row = conn.execute(
        "SELECT id, agency_id, password_hash, is_active "
        "FROM lea_users WHERE username = ?",
        (username,),
    ).fetchone()

    if not row:
        return render_template("lea/login.html",
                               error="Invalid username or password."), 401

    user_id, agency_id, pwd_hash, is_active = row

    if not is_active:
        return render_template("lea/login.html",
                               error="Account is deactivated. Contact your agency admin."), 403

    if not user_auth.verify_password(password, pwd_hash):
        return render_template("lea/login.html",
                               error="Invalid username or password."), 401

    # Update last login
    conn.execute(
        "UPDATE lea_users SET last_login_at = datetime('now'), last_login_ip = ? WHERE id = ?",
        (request.remote_addr or "", user_id),
    )
    conn.commit()

    # Set session
    session["lea_user_id"] = user_id
    session["lea_agency_id"] = agency_id
    session.permanent = True

    # Get agency county_slug for redirect
    agency = conn.execute(
        "SELECT county_slug FROM lea_agencies WHERE id = ?",
        (agency_id,),
    ).fetchone()

    if agency:
        return redirect(url_for("lea_panel.dashboard", county_slug=agency["county_slug"]))

    return redirect(url_for("lea_panel.dashboard", county_slug="unknown"))


@lea_panel_bp.route("/logout")
def logout():
    """Logout — clear LEA session."""
    session.pop("lea_user_id", None)
    session.pop("lea_agency_id", None)
    return redirect(url_for("lea_panel.login"))


@lea_panel_bp.route("/<county_slug>/")
@login_required
def dashboard(county_slug):
    """Agency dashboard home with stats and recent submissions."""
    agency = _lookup_agency_by_slug(county_slug)
    user = _lookup_user(session["lea_user_id"])

    conn = _get_db()
    agency_id = agency["id"]

    # Stats
    total_submissions = conn.execute(
        "SELECT COUNT(*) FROM lea_blotter_drafts WHERE agency_id = ?",
        (agency_id,),
    ).fetchone()[0]

    draft_count = conn.execute(
        "SELECT COUNT(*) FROM lea_blotter_drafts WHERE agency_id = ? AND submission_status = 'draft'",
        (agency_id,),
    ).fetchone()[0]

    published_count = conn.execute(
        "SELECT COUNT(*) FROM lea_blotter_drafts WHERE agency_id = ? AND submission_status = 'published'",
        (agency_id,),
    ).fetchone()[0]

    # Recent submissions (last 10)
    recent = conn.execute(
        """SELECT id, incident_date, incident_time, cad_number, incident_location_block,
                  submission_status, created_at
           FROM lea_blotter_drafts
           WHERE agency_id = ?
           ORDER BY created_at DESC
           LIMIT 10""",
        (agency_id,),
    ).fetchall()

    return render_template(
        "lea/dashboard.html",
        agency=agency,
        user=user,
        total_submissions=total_submissions,
        draft_count=draft_count,
        published_count=published_count,
        recent_submissions=recent,
        county_slug=county_slug,
    )


@lea_panel_bp.route("/<county_slug>/submit", methods=["GET", "POST"])
@login_required
def submit_incident(county_slug):
    """Single incident submission form and handler."""
    agency = _lookup_agency_by_slug(county_slug)
    user = _lookup_user(session["lea_user_id"])

    if request.method == "GET":
        return render_template(
            "lea/submit_incident.html",
            agency=agency,
            user=user,
            county_slug=county_slug,
            errors=None,
            data={},
        )

    # POST: validate and insert
    data = {
        "incident_date": request.form.get("incident_date", "").strip(),
        "incident_time": request.form.get("incident_time", "").strip(),
        "cad_number": request.form.get("cad_number", "").strip(),
        "case_number": request.form.get("case_number", "").strip(),
        "primary_offense_mca": request.form.get("primary_offense_mca", "").strip(),
        "incident_location_block": request.form.get("incident_location_block", "").strip(),
        "public_narrative": request.form.get("public_narrative", "").strip(),
        "arresting_agency": request.form.get("arresting_agency", "").strip(),
        "responding_officer": request.form.get("responding_officer", "").strip(),
    }

    errors = {}

    # Validation
    if not data["incident_date"]:
        errors["incident_date"] = "Incident date is required."

    if len(data.get("public_narrative", "")) > 5000:
        errors["public_narrative"] = "Narrative must be under 5000 characters."

    if errors:
        return render_template(
            "lea/submit_incident.html",
            agency=agency,
            user=user,
            county_slug=county_slug,
            errors=errors,
            data=data,
        ), 400

    # Insert into lea_blotter_drafts
    conn = _get_db()
    conn.execute(
        """INSERT INTO lea_blotter_drafts
           (agency_id, submitted_by_user_id, incident_date, incident_time,
            cad_number, case_number, primary_offense_mca,
            incident_location_block, public_narrative,
            arresting_agency, responding_officer, submission_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')""",
        (
            agency["id"],
            user["id"],
            data["incident_date"],
            data["incident_time"] or None,
            data["cad_number"] or None,
            data["case_number"] or None,
            data["primary_offense_mca"] or None,
            data["incident_location_block"] or None,
            data["public_narrative"] or None,
            data["arresting_agency"] or None,
            data["responding_officer"] or None,
        ),
    )
    conn.commit()

    flash("Incident submitted successfully as draft.", "success")
    return redirect(url_for("lea_panel.dashboard", county_slug=county_slug))


@lea_panel_bp.route("/<county_slug>/batch-upload", methods=["GET", "POST"])
@login_required
def batch_upload(county_slug):
    """Batch CSV upload form and handler."""
    agency = _lookup_agency_by_slug(county_slug)
    user = _lookup_user(session["lea_user_id"])

    if request.method == "GET":
        return render_template(
            "lea/batch_upload.html",
            agency=agency,
            user=user,
            county_slug=county_slug,
            errors=None,
            preview_rows=None,
            summary=None,
        )

    # POST: parse CSV
    if "csv_file" not in request.files:
        return render_template(
            "lea/batch_upload.html",
            agency=agency,
            user=user,
            county_slug=county_slug,
            errors="No file uploaded.",
            preview_rows=None,
            summary=None,
        ), 400

    file = request.files["csv_file"]
    if file.filename == "" or not file.filename.lower().endswith(".csv"):
        return render_template(
            "lea/batch_upload.html",
            agency=agency,
            user=user,
            county_slug=county_slug,
            errors="Please upload a valid CSV file.",
            preview_rows=None,
            summary=None,
        ), 400

    # Parse CSV
    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
        reader = csv.DictReader(stream)

        required_fields = {"incident_date"}
        if not required_fields.issubset(reader.fieldnames or []):
            return render_template(
                "lea/batch_upload.html",
                agency=agency,
                user=user,
                county_slug=county_slug,
                errors="CSV must contain at least an 'incident_date' column.",
                preview_rows=None,
                summary=None,
            ), 400

        rows = []
        row_errors = []
        for idx, row in enumerate(reader, start=1):
            if not row.get("incident_date", "").strip():
                row_errors.append(f"Row {idx}: missing incident_date")
                continue
            rows.append(row)

        if not rows:
            return render_template(
                "lea/batch_upload.html",
                agency=agency,
                user=user,
                county_slug=county_slug,
                errors="No valid rows found in CSV." if not row_errors else f"CSV errors: {'; '.join(row_errors)}",
                preview_rows=None,
                summary=None,
            ), 400

        # Show preview with first 20 rows
        preview_rows = rows[:20]
        total_rows = len(rows)

        return render_template(
            "lea/batch_upload.html",
            agency=agency,
            user=user,
            county_slug=county_slug,
            errors=None,
            preview_rows=preview_rows,
            summary={"total": total_rows, "preview_count": len(preview_rows), "parse_errors": row_errors},
            data={"confirmed": request.form.get("confirmed")},
        )

    except Exception as exc:
        return render_template(
            "lea/batch_upload.html",
            agency=agency,
            user=user,
            county_slug=county_slug,
            errors=f"Failed to parse CSV: {exc}",
            preview_rows=None,
            summary=None,
        ), 400


@lea_panel_bp.route("/<county_slug>/batch-upload/confirm", methods=["POST"])
@login_required
def batch_upload_confirm(county_slug):
    """Confirm and insert parsed CSV rows into lea_blotter_drafts."""
    agency = _lookup_agency_by_slug(county_slug)
    user = _lookup_user(session["lea_user_id"])

    csv_data_raw = request.form.get("csv_data", "").strip()
    if not csv_data_raw:
        flash("No CSV data received.", "error")
        return redirect(url_for("lea_panel.batch_upload", county_slug=county_slug))

    try:
        reader = csv.DictReader(io.StringIO(csv_data_raw))
        conn = _get_db()
        inserted = 0

        for row in reader:
            if not row.get("incident_date", "").strip():
                continue
            conn.execute(
                """INSERT INTO lea_blotter_drafts
                   (agency_id, submitted_by_user_id, incident_date, incident_time,
                    cad_number, case_number, primary_offense_mca,
                    incident_location_block, public_narrative,
                    arresting_agency, responding_officer, submission_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')""",
                (
                    agency["id"],
                    user["id"],
                    row.get("incident_date", "").strip(),
                    row.get("incident_time", "").strip() or None,
                    row.get("cad_number", "").strip() or None,
                    row.get("case_number", "").strip() or None,
                    row.get("primary_offense_mca", "").strip() or None,
                    row.get("incident_location_block", "").strip() or None,
                    row.get("public_narrative", "").strip() or None,
                    row.get("arresting_agency", "").strip() or None,
                    row.get("responding_officer", "").strip() or None,
                ),
            )
            inserted += 1

        conn.commit()
        flash(f"Successfully imported {inserted} incidents.", "success")
    except Exception as exc:
        flash(f"Import failed: {exc}", "error")

    return redirect(url_for("lea_panel.dashboard", county_slug=county_slug))


@lea_panel_bp.route("/<county_slug>/history")
@login_required
def blotter_history(county_slug):
    """Blotter history with search/filter by status, date range, and CAD number."""
    agency = _lookup_agency_by_slug(county_slug)
    user = _lookup_user(session["lea_user_id"])

    conn = _get_db()
    agency_id = agency["id"]

    # Filters
    status_filter = request.args.get("status", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    cad_search = request.args.get("cad_number", "").strip()

    query = """SELECT id, incident_date, incident_time, cad_number, case_number,
                      primary_offense_mca, incident_location_block, submission_status,
                      created_at, updated_at
               FROM lea_blotter_drafts
               WHERE agency_id = ?"""
    params = [agency_id]

    if status_filter and status_filter != "all":
        query += " AND submission_status = ?"
        params.append(status_filter)

    if date_from:
        query += " AND incident_date >= ?"
        params.append(date_from)

    if date_to:
        query += " AND incident_date <= ?"
        params.append(date_to)

    if cad_search:
        query += " AND cad_number LIKE ?"
        params.append(f"%{cad_search}%")

    query += " ORDER BY incident_date DESC, created_at DESC LIMIT 100"

    submissions = conn.execute(query, params).fetchall()

    return render_template(
        "lea/blotter_history.html",
        agency=agency,
        user=user,
        county_slug=county_slug,
        submissions=submissions,
        status_filter=status_filter,
        date_from=date_from,
        date_to=date_to,
        cad_search=cad_search,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_lea_panel(app):
    """Register the LEA panel blueprint with the Flask app."""
    # Ensure session is configured
    app.config.setdefault("SECRET_KEY", config.SECRET_KEY)
    app.permanent_session_lifetime = 86400  # 24 hours
    app.register_blueprint(lea_panel_bp)
