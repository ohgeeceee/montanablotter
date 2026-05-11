from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

import config

SELFIE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
SELFIE_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads", "client_checkins")
ALERT_POLL_SECONDS = 30


def ensure_bondsman_command_center_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    for col, definition in [
        ("is_subscribed", "INTEGER NOT NULL DEFAULT 0"),
        ("subscriber_plan", "TEXT NOT NULL DEFAULT 'free'"),
        ("stripe_subscription_id", "TEXT"),
        ("subscription_status", "TEXT NOT NULL DEFAULT ''"),
        ("subscription_activated_at", "TEXT"),
        ("subscription_canceled_at", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE public_users ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass

    try:
        cursor.execute("ALTER TABLE jail_bookings ADD COLUMN date_of_birth TEXT")
    except sqlite3.OperationalError:
        pass

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bondsman_watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER NOT NULL,
            watch_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            date_of_birth TEXT,
            notes TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (public_user_id) REFERENCES public_users(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bondsman_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER NOT NULL,
            watchlist_id INTEGER,
            booking_id INTEGER NOT NULL,
            alert_type TEXT NOT NULL DEFAULT 'watch_match',
            alert_status TEXT NOT NULL DEFAULT 'open',
            match_confidence TEXT NOT NULL DEFAULT 'name_only',
            matched_name TEXT NOT NULL,
            matched_date_of_birth TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            acknowledged_at TEXT,
            FOREIGN KEY (public_user_id) REFERENCES public_users(id) ON DELETE CASCADE,
            FOREIGN KEY (watchlist_id) REFERENCES bondsman_watchlists(id) ON DELETE SET NULL,
            FOREIGN KEY (booking_id) REFERENCES jail_bookings(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bondsman_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_user_id INTEGER NOT NULL,
            booking_id INTEGER NOT NULL UNIQUE,
            defendant_name TEXT NOT NULL,
            date_of_birth TEXT,
            bond_amount_cents INTEGER,
            court_date TEXT,
            payment_status TEXT NOT NULL DEFAULT 'pending',
            case_status TEXT NOT NULL DEFAULT 'active',
            notes TEXT DEFAULT '',
            checkin_token TEXT NOT NULL UNIQUE,
            last_checkin_at TEXT,
            last_checkin_latitude REAL,
            last_checkin_longitude REAL,
            last_checkin_selfie_path TEXT,
            claimed_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (public_user_id) REFERENCES public_users(id) ON DELETE CASCADE,
            FOREIGN KEY (booking_id) REFERENCES jail_bookings(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bondsman_client_checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            latitude REAL,
            longitude REAL,
            selfie_path TEXT,
            submitted_at TEXT DEFAULT (datetime('now')),
            notes TEXT DEFAULT '',
            FOREIGN KEY (case_id) REFERENCES bondsman_cases(id) ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bondsman_watchlists_lookup "
        "ON bondsman_watchlists(public_user_id, normalized_name, is_active)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bondsman_alerts_feed "
        "ON bondsman_alerts(public_user_id, alert_status, created_at DESC)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bondsman_alerts_unique_match "
        "ON bondsman_alerts(public_user_id, watchlist_id, booking_id, alert_type)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bondsman_cases_user "
        "ON bondsman_cases(public_user_id, case_status, court_date)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_bondsman_checkins_case "
        "ON bondsman_client_checkins(case_id, submitted_at DESC)"
    )


def _normalize_person_name(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", " ", (value or "").upper())
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_date_of_birth(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _booking_probably_bondable(charges_summary: str) -> bool:
    text = (charges_summary or "").upper()
    blocked_tokens = (
        "DOC",
        "FED HOLD",
        "FEDERAL HOLD",
        "USMS",
        "ICE",
        "NO BOND",
        "PAROLE",
        "PROBATION HOLD",
        "TRANSPORT",
        "HOLD FOR AGENCY",
    )
    return not any(token in text for token in blocked_tokens)


def _case_checkin_overdue(case_row: sqlite3.Row) -> bool:
    court_date = (case_row["court_date"] or "").strip()
    if not court_date:
        return False
    try:
        due_at = datetime.fromisoformat(court_date.replace("Z", "+00:00"))
    except ValueError:
        try:
            due_at = datetime.strptime(court_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return False
    now = datetime.now(timezone.utc)
    remaining = due_at - now
    return remaining.total_seconds() <= 48 * 3600 and (case_row["last_checkin_at"] or "").strip() == ""


def _serialize_alert(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "alertType": row["alert_type"],
        "alertStatus": row["alert_status"],
        "matchConfidence": row["match_confidence"],
        "matchedName": row["matched_name"],
        "matchedDateOfBirth": row["matched_date_of_birth"] or "",
        "createdAt": row["created_at"],
        "bookingId": row["booking_id"],
        "countyName": row["county_name"],
        "defendantName": row["person_name"],
        "bookingAt": row["booking_at"] or "",
        "chargesSummary": row["charges_summary"] or "",
    }


def _serialize_lead(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "bookingId": row["id"],
        "countyName": row["county_name"],
        "facilityName": row["facility_name"],
        "defendantName": row["person_name"],
        "dateOfBirth": row["date_of_birth"] or "",
        "bookingAt": row["booking_at"] or "",
        "chargesSummary": row["charges_summary"] or "",
        "bondableStatus": "likely_bondable" if _booking_probably_bondable(row["charges_summary"] or "") else "review_hold",
        "sourceUrl": row["source_url"] or "",
    }


def _serialize_case(row: sqlite3.Row) -> dict[str, Any]:
    bond_amount = row["bond_amount_cents"]
    return {
        "caseId": row["id"],
        "bookingId": row["booking_id"],
        "defendantName": row["defendant_name"],
        "dateOfBirth": row["date_of_birth"] or "",
        "bondAmountCents": bond_amount,
        "courtDate": row["court_date"] or "",
        "paymentStatus": row["payment_status"],
        "caseStatus": row["case_status"],
        "notes": row["notes"] or "",
        "checkinToken": row["checkin_token"],
        "lastCheckinAt": row["last_checkin_at"] or "",
        "lastKnownLatitude": row["last_checkin_latitude"],
        "lastKnownLongitude": row["last_checkin_longitude"],
        "lastCheckinSelfiePath": row["last_checkin_selfie_path"] or "",
        "claimedAt": row["claimed_at"],
        "isForfeitureRisk": _case_checkin_overdue(row),
    }


def build_command_center_payload(conn: sqlite3.Connection, public_user_id: int) -> dict[str, Any]:
    watchlists = conn.execute(
        """
        SELECT id, watch_name, date_of_birth, notes, is_active, created_at
        FROM bondsman_watchlists
        WHERE public_user_id = ?
        ORDER BY is_active DESC, created_at DESC
        """,
        (public_user_id,),
    ).fetchall()
    alerts = conn.execute(
        """
        SELECT a.*, jb.person_name, jb.booking_at, jb.county_name, jb.charges_summary
        FROM bondsman_alerts a
        JOIN jail_bookings jb ON jb.id = a.booking_id
        WHERE a.public_user_id = ?
        ORDER BY a.created_at DESC
        LIMIT 25
        """,
        (public_user_id,),
    ).fetchall()
    leads = conn.execute(
        """
        SELECT jb.*
        FROM jail_bookings jb
        LEFT JOIN bondsman_cases bc
            ON bc.booking_id = jb.id AND bc.public_user_id = ?
        WHERE jb.is_current = 1
          AND bc.id IS NULL
        ORDER BY COALESCE(jb.booking_at, jb.first_seen_at) DESC
        LIMIT 40
        """,
        (public_user_id,),
    ).fetchall()
    cases = conn.execute(
        """
        SELECT *
        FROM bondsman_cases
        WHERE public_user_id = ?
        ORDER BY claimed_at DESC
        """,
        (public_user_id,),
    ).fetchall()
    locations = [
        {
            "caseId": row["id"],
            "defendantName": row["defendant_name"],
            "latitude": row["last_checkin_latitude"],
            "longitude": row["last_checkin_longitude"],
            "lastCheckinAt": row["last_checkin_at"] or "",
        }
        for row in cases
        if row["last_checkin_latitude"] is not None and row["last_checkin_longitude"] is not None
    ]

    return {
        "watchlists": [
            {
                "id": row["id"],
                "watchName": row["watch_name"],
                "dateOfBirth": row["date_of_birth"] or "",
                "notes": row["notes"] or "",
                "isActive": bool(row["is_active"]),
                "createdAt": row["created_at"],
            }
            for row in watchlists
        ],
        "alerts": [_serialize_alert(row) for row in alerts],
        "leads": [_serialize_lead(row) for row in leads],
        "cases": [_serialize_case(row) for row in cases],
        "locations": locations,
        "pollIntervalSeconds": ALERT_POLL_SECONDS,
    }


def run_watchlist_match_cycle(db_path: str | None = None, *, public_user_id: int | None = None) -> dict[str, int]:
    own_conn = False
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path or config.DB_PATH, timeout=getattr(config, "DB_TIMEOUT_SECONDS", 30))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_bondsman_command_center_schema(conn)
        own_conn = True

        watchlists_sql = """
            SELECT *
            FROM bondsman_watchlists
            WHERE is_active = 1
        """
        params: list[Any] = []
        if public_user_id is not None:
            watchlists_sql += " AND public_user_id = ?"
            params.append(public_user_id)
        watchlists = conn.execute(watchlists_sql, tuple(params)).fetchall()

        bookings = conn.execute(
            """
            SELECT id, person_name, date_of_birth
            FROM jail_bookings
            WHERE is_current = 1
            """
        ).fetchall()

        created_alerts = 0
        inspected_pairs = 0
        for watchlist in watchlists:
            watch_name = _normalize_person_name(watchlist["watch_name"])
            watch_dob = _normalize_date_of_birth(watchlist["date_of_birth"] or "")
            for booking in bookings:
                inspected_pairs += 1
                booking_name = _normalize_person_name(booking["person_name"])
                if not watch_name or watch_name != booking_name:
                    continue
                booking_dob = _normalize_date_of_birth(booking["date_of_birth"] or "")
                if watch_dob and booking_dob and watch_dob != booking_dob:
                    continue
                confidence = "name_and_dob" if watch_dob and booking_dob and watch_dob == booking_dob else "name_only"
                try:
                    conn.execute(
                        """
                        INSERT INTO bondsman_alerts (
                            public_user_id,
                            watchlist_id,
                            booking_id,
                            alert_type,
                            alert_status,
                            match_confidence,
                            matched_name,
                            matched_date_of_birth
                        ) VALUES (?, ?, ?, 'watch_match', 'open', ?, ?, ?)
                        """,
                        (
                            watchlist["public_user_id"],
                            watchlist["id"],
                            booking["id"],
                            confidence,
                            booking["person_name"],
                            booking_dob,
                        ),
                    )
                    created_alerts += 1
                except sqlite3.IntegrityError:
                    continue

        conn.commit()
        return {
            "watchlists": len(watchlists),
            "bookings": len(bookings),
            "inspected_pairs": inspected_pairs,
            "created_alerts": created_alerts,
        }
    finally:
        if own_conn and conn is not None:
            conn.close()


def register_bondsman_command_center(app, *, get_db, get_public_user, base_url: str) -> None:
    def _wants_json() -> bool:
        return bool(
            request.is_json
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.accept_mimetypes.best == "application/json"
        )

    def _validate_subscriber_csrf() -> tuple[bool, Any]:
        session_token = session.get("_csrf_token")
        submitted_token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if session_token and submitted_token and hmac.compare_digest(session_token, submitted_token):
            return True, None
        if _wants_json():
            return False, (jsonify({"ok": False, "error": "csrf_validation_failed"}), 400)
        flash("Security token validation failed. Refresh the page and try again.", "error")
        return False, redirect(request.referrer or url_for("bondsman_command_center"))

    def _subscriber_required(json_mode: bool = False):
        def decorator(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                public_user = get_public_user()
                if not public_user:
                    if json_mode:
                        return jsonify({"ok": False, "error": "authentication_required"}), 401
                    return redirect(url_for("auth.public_login", next=request.full_path))
                if not getattr(public_user, "is_subscribed", False):
                    if json_mode:
                        return jsonify({"ok": False, "error": "subscription_required"}), 403
                    flash("This command center is available to subscribed bondsman accounts only.", "error")
                    return redirect("/donate?source=bondsman_command_center&campaign=bondsman_command_center")
                return fn(public_user, *args, **kwargs)

            return wrapped

        return decorator

    def _save_selfie(upload) -> str:
        if not upload or not upload.filename:
            return ""
        filename = secure_filename(upload.filename)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in SELFIE_EXTENSIONS:
            return ""
        os.makedirs(SELFIE_UPLOAD_DIR, exist_ok=True)
        stored_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}.{ext}"
        upload.save(os.path.join(SELFIE_UPLOAD_DIR, stored_name))
        return f"/uploads/client_checkins/{stored_name}"

    @app.route("/bondsman/command-center")
    @_subscriber_required(json_mode=False)
    def bondsman_command_center(public_user):
        conn = get_db()
        try:
            ensure_bondsman_command_center_schema(conn)
            payload = build_command_center_payload(conn, public_user.id)
        finally:
            conn.close()
        return render_template(
            "bondsman_command_center.html",
            page_title="Bondsman Command Center",
            meta_description="Subscriber-only command center for bondsmen managing watchlists, leads, cases, and defendant check-ins.",
            canonical_url=f"{base_url.rstrip('/')}/bondsman/command-center",
            og_title="Bondsman Command Center | Montana Blotter",
            og_description="Manage defendant watchlists, booking alerts, bondable leads, and active cases.",
            active_nav="bondsman_command_center",
            command_center_payload=payload,
            current_year=datetime.now().year,
        )

    @app.route("/api/bondsman/bootstrap")
    @_subscriber_required(json_mode=True)
    def api_bondsman_bootstrap(public_user):
        conn = get_db()
        try:
            ensure_bondsman_command_center_schema(conn)
            payload = build_command_center_payload(conn, public_user.id)
        finally:
            conn.close()
        return jsonify({"ok": True, "data": payload})

    @app.route("/api/bondsman/watchlists", methods=["POST"])
    @_subscriber_required(json_mode=True)
    def api_bondsman_watchlists_create(public_user):
        is_valid, failure = _validate_subscriber_csrf()
        if not is_valid:
            return failure
        body = request.get_json(silent=True) or {}
        watch_name = (body.get("watchName") or "").strip()
        date_of_birth = _normalize_date_of_birth(body.get("dateOfBirth") or "")
        notes = (body.get("notes") or "").strip()[:500]
        if len(watch_name) < 3:
            return jsonify({"ok": False, "error": "watch_name_too_short"}), 400
        conn = get_db()
        try:
            ensure_bondsman_command_center_schema(conn)
            conn.execute(
                """
                INSERT INTO bondsman_watchlists (
                    public_user_id, watch_name, normalized_name, date_of_birth, notes
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (public_user.id, watch_name[:140], _normalize_person_name(watch_name), date_of_birth, notes),
            )
            conn.commit()
            payload = build_command_center_payload(conn, public_user.id)
        finally:
            conn.close()
        return jsonify({"ok": True, "data": payload})

    @app.route("/api/bondsman/watchlists/<int:watchlist_id>", methods=["PATCH", "DELETE"])
    @_subscriber_required(json_mode=True)
    def api_bondsman_watchlists_update(public_user, watchlist_id: int):
        is_valid, failure = _validate_subscriber_csrf()
        if not is_valid:
            return failure
        conn = get_db()
        try:
            ensure_bondsman_command_center_schema(conn)
            row = conn.execute(
                "SELECT * FROM bondsman_watchlists WHERE id = ? AND public_user_id = ?",
                (watchlist_id, public_user.id),
            ).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "watchlist_not_found"}), 404
            if request.method == "DELETE":
                conn.execute("DELETE FROM bondsman_watchlists WHERE id = ?", (watchlist_id,))
            else:
                body = request.get_json(silent=True) or {}
                watch_name = (body.get("watchName") or row["watch_name"]).strip()[:140]
                date_of_birth = _normalize_date_of_birth(body.get("dateOfBirth") or row["date_of_birth"] or "")
                notes = (body.get("notes") or row["notes"] or "").strip()[:500]
                is_active = 1 if body.get("isActive", True) else 0
                conn.execute(
                    """
                    UPDATE bondsman_watchlists
                    SET watch_name = ?, normalized_name = ?, date_of_birth = ?, notes = ?,
                        is_active = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (watch_name, _normalize_person_name(watch_name), date_of_birth, notes, is_active, watchlist_id),
                )
            conn.commit()
            payload = build_command_center_payload(conn, public_user.id)
        finally:
            conn.close()
        return jsonify({"ok": True, "data": payload})

    @app.route("/api/bondsman/alerts/<int:alert_id>/acknowledge", methods=["POST"])
    @_subscriber_required(json_mode=True)
    def api_bondsman_alert_acknowledge(public_user, alert_id: int):
        is_valid, failure = _validate_subscriber_csrf()
        if not is_valid:
            return failure
        conn = get_db()
        try:
            ensure_bondsman_command_center_schema(conn)
            conn.execute(
                """
                UPDATE bondsman_alerts
                SET alert_status = 'acknowledged',
                    acknowledged_at = datetime('now')
                WHERE id = ? AND public_user_id = ?
                """,
                (alert_id, public_user.id),
            )
            conn.commit()
            payload = build_command_center_payload(conn, public_user.id)
        finally:
            conn.close()
        return jsonify({"ok": True, "data": payload})

    @app.route("/api/bondsman/leads/<int:booking_id>/claim", methods=["POST"])
    @_subscriber_required(json_mode=True)
    def api_bondsman_claim_lead(public_user, booking_id: int):
        is_valid, failure = _validate_subscriber_csrf()
        if not is_valid:
            return failure
        conn = get_db()
        try:
            ensure_bondsman_command_center_schema(conn)
            booking = conn.execute(
                """
                SELECT id, person_name, date_of_birth
                FROM jail_bookings
                WHERE id = ? AND is_current = 1
                """,
                (booking_id,),
            ).fetchone()
            if not booking:
                return jsonify({"ok": False, "error": "booking_not_found"}), 404
            existing = conn.execute(
                "SELECT id FROM bondsman_cases WHERE booking_id = ? AND public_user_id = ?",
                (booking_id, public_user.id),
            ).fetchone()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO bondsman_cases (
                        public_user_id,
                        booking_id,
                        defendant_name,
                        date_of_birth,
                        checkin_token
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        public_user.id,
                        booking_id,
                        booking["person_name"],
                        booking["date_of_birth"],
                        secrets.token_urlsafe(18),
                    ),
                )
                conn.commit()
            payload = build_command_center_payload(conn, public_user.id)
        finally:
            conn.close()
        return jsonify({"ok": True, "data": payload})

    @app.route("/api/bondsman/cases/<int:case_id>", methods=["PATCH"])
    @_subscriber_required(json_mode=True)
    def api_bondsman_case_update(public_user, case_id: int):
        is_valid, failure = _validate_subscriber_csrf()
        if not is_valid:
            return failure
        body = request.get_json(silent=True) or {}
        conn = get_db()
        try:
            ensure_bondsman_command_center_schema(conn)
            current_case = conn.execute(
                "SELECT * FROM bondsman_cases WHERE id = ? AND public_user_id = ?",
                (case_id, public_user.id),
            ).fetchone()
            if not current_case:
                return jsonify({"ok": False, "error": "case_not_found"}), 404
            defendant_name = (body.get("defendantName") or current_case["defendant_name"]).strip()[:140]
            payment_status = (body.get("paymentStatus") or current_case["payment_status"]).strip()[:40] or "pending"
            case_status = (body.get("caseStatus") or current_case["case_status"]).strip()[:40] or "active"
            court_date = (body.get("courtDate") or current_case["court_date"] or "").strip()[:40]
            notes = (body.get("notes") or current_case["notes"] or "").strip()[:1000]
            bond_amount = body.get("bondAmountCents", current_case["bond_amount_cents"])
            conn.execute(
                """
                UPDATE bondsman_cases
                SET defendant_name = ?,
                    payment_status = ?,
                    case_status = ?,
                    court_date = ?,
                    notes = ?,
                    bond_amount_cents = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (defendant_name, payment_status, case_status, court_date, notes, bond_amount, case_id),
            )
            conn.commit()
            payload = build_command_center_payload(conn, public_user.id)
        finally:
            conn.close()
        return jsonify({"ok": True, "data": payload})

    @app.route("/checkin/<token>", methods=["GET", "POST"])
    def bondsman_client_checkin(token: str):
        conn = get_db()
        try:
            ensure_bondsman_command_center_schema(conn)
            case_row = conn.execute(
                """
                SELECT bc.*, pu.display_name AS bondsman_name
                FROM bondsman_cases bc
                JOIN public_users pu ON pu.id = bc.public_user_id
                WHERE bc.checkin_token = ?
                """,
                (token,),
            ).fetchone()
            if not case_row:
                return render_template("404.html"), 404
            if request.method == "POST":
                latitude_raw = (request.form.get("latitude") or "").strip()
                longitude_raw = (request.form.get("longitude") or "").strip()
                notes = (request.form.get("notes") or "").strip()[:500]
                try:
                    latitude = float(latitude_raw)
                    longitude = float(longitude_raw)
                except ValueError:
                    latitude = None
                    longitude = None
                selfie_path = _save_selfie(request.files.get("selfie"))
                conn.execute(
                    """
                    INSERT INTO bondsman_client_checkins (
                        case_id, latitude, longitude, selfie_path, notes
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (case_row["id"], latitude, longitude, selfie_path, notes),
                )
                conn.execute(
                    """
                    UPDATE bondsman_cases
                    SET last_checkin_at = datetime('now'),
                        last_checkin_latitude = ?,
                        last_checkin_longitude = ?,
                        last_checkin_selfie_path = COALESCE(NULLIF(?, ''), last_checkin_selfie_path),
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (latitude, longitude, selfie_path, case_row["id"]),
                )
                conn.commit()
                flash("Check-in submitted. Your bondsman can review it now.", "success")
                return redirect(request.path)
        finally:
            conn.close()
        return render_template(
            "client_checkin.html",
            page_title="Client Check-In",
            meta_description="Secure defendant check-in for Montana Blotter bondsman accounts.",
            canonical_url=f"{base_url.rstrip('/')}/checkin/{token}",
            og_title="Client Check-In | Montana Blotter",
            og_description="Upload a selfie and GPS coordinates for your bondsman.",
            active_nav="bondsman_checkin",
            checkin_case=case_row,
            current_year=datetime.now().year,
        )
