from __future__ import annotations

from datetime import UTC, datetime

from flask import abort, jsonify, render_template, request
from flask_login import login_required

from agent_mission_control import build_snapshot, recent_events, upsert_agent_heartbeat
from blueprints.admin import admin_bp, require_role
from db import connect_db
from utils.auth_constants import ADMIN_ACCESS_ROLES


def _db():
    return connect_db(timeout_seconds=0.75, busy_timeout_ms=750)


def _snapshot_payload() -> dict[str, object]:
    conn = _db()
    try:
        snapshot = build_snapshot(conn)
    finally:
        conn.close()
    if "captured_at" in snapshot:
        return snapshot
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "agents": snapshot.get("agents", []),
    }


def _is_local_heartbeat(request_headers) -> bool:
    return (request_headers.get("X-Internal-Mission-Control") or "").strip() == "local"


@admin_bp.route("/mission-control")
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_mission_control():
    return render_template("admin_mission_control.html", snapshot=_snapshot_payload())


@admin_bp.route("/mission-control/runbook")
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_mission_control_runbook():
    return render_template("admin_mission_control_runbook.html")


@admin_bp.route("/api/mission-control/snapshot")
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_mission_control_snapshot():
    return jsonify(_snapshot_payload())


@admin_bp.route("/api/mission-control/events")
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_mission_control_events():
    conn = _db()
    try:
        payload = {"events": recent_events(conn, limit=80)}
    finally:
        conn.close()
    return jsonify(payload)


@admin_bp.route("/api/mission-control/heartbeat", methods=["POST"])
def mission_control_heartbeat():
    if not _is_local_heartbeat(request.headers):
        abort(403)
    payload = request.get_json(silent=True) or {}
    if not payload.get("agent_id"):
        return jsonify({"ok": False, "error": "agent_id is required"}), 400
    conn = _db()
    try:
        upsert_agent_heartbeat(conn, payload)
    finally:
        conn.close()
    return jsonify({"ok": True}), 202
