"""Watchdog Alert Subscription — premium tier for journalists & civic orgs.

$24.99/month (50 % for verified nonprofits).
Includes unlimited keyword alerts, salary access, weekly AI digest, CSV export.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import sqlite3
from datetime import datetime, timedelta

import stripe
from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

import config

watchdog_bp = Blueprint("watchdog", __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WATCHDOG_PRICE_CENTS = 2499          # $24.99
WATCHDOG_NONPROFIT_CENTS = 1249      # 50% discount
WATCHDOG_TIER = "watchdog"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db():
    from db import get_db
    return get_db()


def _stripe_keys():
    return {
        "secret_key": (getattr(config, "STRIPE_SECRET_KEY", "") or "").strip(),
        "publishable_key": (getattr(config, "STRIPE_PUBLISHABLE_KEY", "") or "").strip(),
    }


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ---------------------------------------------------------------------------
# Public marketing page
# ---------------------------------------------------------------------------

@watchdog_bp.route("/watchdog")
def watchdog_landing():
    """Public landing page explaining the Watchdog tier."""
    keys = _stripe_keys()
    return render_template(
        "watchdog_landing.html",
        price_display="24.99",
        nonprofit_price_display="12.49",
        stripe_ready=bool(keys["secret_key"] and keys["publishable_key"]),
        active_nav="watchdog",
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Signup / Stripe checkout
# ---------------------------------------------------------------------------

@watchdog_bp.route("/watchdog/subscribe", methods=["POST"])
def watchdog_subscribe():
    """Create a Watchdog subscription via Stripe Checkout."""
    email = _normalize_email(request.form.get("email"))
    name = (request.form.get("name") or "").strip()[:120]
    organization = (request.form.get("organization") or "").strip()[:200]
    counties = (request.form.get("counties") or "").strip()[:500]
    beats = (request.form.get("beats") or "").strip()[:500]
    is_nonprofit = request.form.get("is_nonprofit") == "1"

    if not email or "@" not in email:
        return render_template("watchdog_landing.html",
                               error="Valid email required.",
                               price_display="24.99",
                               nonprofit_price_display="12.49",
                               stripe_ready=True,
                               active_nav="watchdog",
                               current_year=datetime.now().year)

    keys = _stripe_keys()
    if not keys["secret_key"]:
        return render_template("watchdog_landing.html",
                               error="Payments are not configured yet.",
                               price_display="24.99",
                               nonprofit_price_display="12.49",
                               stripe_ready=False,
                               active_nav="watchdog",
                               current_year=datetime.now().year)

    stripe.api_key = keys["secret_key"]
    token = _generate_token()
    amount = WATCHDOG_NONPROFIT_CENTS if is_nonprofit else WATCHDOG_PRICE_CENTS

    # Persist subscription record (pending)
    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO watchdog_subscriptions
                (email, name, organization, tier, counties, beats, is_nonprofit, token, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(email, tier) DO UPDATE SET
                name = excluded.name,
                organization = excluded.organization,
                counties = excluded.counties,
                beats = excluded.beats,
                is_nonprofit = excluded.is_nonprofit,
                token = excluded.token,
                status = 'pending',
                updated_at = datetime('now')
            """,
            (email, name, organization, WATCHDOG_TIER, counties, beats,
             1 if is_nonprofit else 0, token),
        )
        conn.commit()
    finally:
        conn.close()

    base_url = (getattr(config, "BASE_URL", "") or "").strip() or request.host_url.rstrip("/")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": "Montana Blotter — Watchdog Tier"
                                + (" (Nonprofit)" if is_nonprofit else ""),
                    },
                    "unit_amount": amount,
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            success_url=f"{base_url}/watchdog/success?session_id={{CHECKOUT_SESSION_ID}}&token={token}",
            cancel_url=f"{base_url}/watchdog?canceled=1",
            customer_email=email,
            metadata={
                "flow": "watchdog",
                "token": token,
                "is_nonprofit": "1" if is_nonprofit else "0",
            },
        )
    except Exception as exc:
        return render_template("watchdog_landing.html",
                               error=f"Checkout unavailable: {exc}",
                               price_display="24.99",
                               nonprofit_price_display="12.49",
                               stripe_ready=True,
                               active_nav="watchdog",
                               current_year=datetime.now().year)

    return redirect(session.url)


# ---------------------------------------------------------------------------
# Post-checkout
# ---------------------------------------------------------------------------

@watchdog_bp.route("/watchdog/success")
def watchdog_success():
    token = (request.args.get("token") or "").strip()[:128]
    session_id = (request.args.get("session_id") or "").strip()

    if token and session_id:
        conn = _get_db()
        conn.execute(
            """
            UPDATE watchdog_subscriptions
            SET status = 'active', verified = 1, updated_at = datetime('now')
            WHERE token = ? AND status = 'pending'
            """,
            (token,),
        )
        conn.commit()
        conn.close()

    return render_template(
        "watchdog_success.html",
        token=token,
        active_nav="watchdog",
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Dashboard (token-gated)
# ---------------------------------------------------------------------------

@watchdog_bp.route("/watchdog/dashboard/<token>")
def watchdog_dashboard(token):
    conn = _get_db()
    row = conn.execute(
        "SELECT * FROM watchdog_subscriptions WHERE token = ? AND status = 'active'",
        (token,),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)

    sub = dict(row)

    # Load keyword alerts
    alerts = [
        dict(r) for r in conn.execute(
            "SELECT * FROM watchdog_keyword_alerts WHERE subscription_id = ? ORDER BY created_at DESC",
            (sub["id"],),
        ).fetchall()
    ]

    # Load recent digests
    digests = [
        dict(r) for r in conn.execute(
            "SELECT * FROM watchdog_digests WHERE subscription_id = ? ORDER BY digest_date DESC LIMIT 12",
            (sub["id"],),
        ).fetchall()
    ]

    conn.close()

    return render_template(
        "watchdog_dashboard.html",
        sub=sub,
        alerts=alerts,
        digests=digests,
        active_nav="watchdog",
        current_year=datetime.now().year,
    )


# ---------------------------------------------------------------------------
# Keyword alert management (AJAX)
# ---------------------------------------------------------------------------

@watchdog_bp.route("/watchdog/dashboard/<token>/alerts", methods=["POST"])
def watchdog_add_alert(token):
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM watchdog_subscriptions WHERE token = ? AND status = 'active'",
        (token,),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)

    sub_id = row["id"]
    keyword = (request.form.get("keyword") or "").strip()[:200]
    sources = (request.form.get("data_sources") or "all").strip()[:200]

    if not keyword:
        conn.close()
        return jsonify({"error": "Keyword is required"}), 400

    conn.execute(
        "INSERT INTO watchdog_keyword_alerts (subscription_id, keyword, data_sources) VALUES (?, ?, ?)",
        (sub_id, keyword, sources),
    )
    conn.commit()
    conn.close()

    return redirect(url_for("watchdog.watchdog_dashboard", token=token))


@watchdog_bp.route("/watchdog/dashboard/<token>/alerts/<int:alert_id>/delete", methods=["POST"])
def watchdog_delete_alert(token, alert_id):
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM watchdog_subscriptions WHERE token = ? AND status = 'active'",
        (token,),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)

    conn.execute(
        "DELETE FROM watchdog_keyword_alerts WHERE id = ? AND subscription_id = ?",
        (alert_id, row["id"]),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("watchdog.watchdog_dashboard", token=token))


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@watchdog_bp.route("/watchdog/dashboard/<token>/export")
def watchdog_csv_export(token):
    """Export current search results as CSV with AI story-angle column."""
    conn = _get_db()
    row = conn.execute(
        "SELECT id, counties FROM watchdog_subscriptions WHERE token = ? AND status = 'active'",
        (token,),
    ).fetchone()
    if not row:
        conn.close()
        abort(404)

    q = (request.args.get("q") or "").strip()
    source = (request.args.get("source") or "all").strip()

    query = "SELECT * FROM records WHERE 1=1"
    params = []

    counties_str = row["counties"] or ""
    if counties_str:
        county_list = [c.strip() for c in counties_str.split(",") if c.strip()]
        if county_list:
            placeholders = ",".join("?" * len(county_list))
            query += f" AND county IN ({placeholders})"
            params.extend(county_list)

    if q:
        query += " AND (details LIKE ? OR summary LIKE ? OR incident_type LIKE ? OR location LIKE ?)"
        params.extend([f"%{q}%"] * 4)

    if source == "fwp":
        query += " AND officer = 'MT FWP'"
    elif source == "salaries":
        # Export from salary table instead
        salary_rows = conn.execute(
            "SELECT name, agency, position, salary, county, year FROM public_salaries ORDER BY salary DESC LIMIT 5000"
        ).fetchall()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "Agency", "Position", "Salary", "County", "Year", "Story Angle"])
        for sr in salary_rows:
            sr = dict(sr)
            angle = f"Public salary of ${sr['salary']:,.0f} for {sr['position']} at {sr['agency']}" if sr.get("salary") else ""
            writer.writerow([sr["name"], sr["agency"], sr["position"],
                             f"${sr['salary']:,.2f}" if sr.get("salary") else "",
                             sr["county"], sr["year"], angle])

        resp = make_response(output.getvalue())
        resp.headers["Content-Disposition"] = "attachment; filename=watchdog_salaries_export.csv"
        resp.headers["Content-Type"] = "text/csv"
        return resp

    query += " ORDER BY date DESC, time DESC LIMIT 5000"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Time", "County", "Incident Type", "Location", "Details", "Officer", "Story Angle"])
    for r in rows:
        r = dict(r)
        details_text = (r.get("details") or r.get("summary") or "")[:300]
        angle = f"Potential story: {r.get('incident_type', 'Incident')} in {r.get('county', 'Unknown')} County"
        writer.writerow([
            r.get("date"), r.get("time"), r.get("county"),
            r.get("incident_type"), r.get("location"),
            details_text, r.get("officer"), angle,
        ])

    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = f"attachment; filename=watchdog_export_{datetime.now().strftime('%Y%m%d')}.csv"
    resp.headers["Content-Type"] = "text/csv"
    return resp


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register_watchdog_blueprint(app):
    app.register_blueprint(watchdog_bp)
