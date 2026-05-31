from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from flask import abort, jsonify, redirect, request, url_for
from flask_login import login_required

from services.agents.mission_control import build_snapshot, recent_events, upsert_agent_heartbeat
from blueprints.admin import admin_bp, require_role
from db import connect_db
from utils.auth_constants import ADMIN_ACCESS_ROLES


def _db():
    return connect_db()


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
    return redirect(url_for("admin.admin_command_center"))


@admin_bp.route("/mission-control/runbook")
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_mission_control_runbook():
    return redirect(url_for("admin.admin_command_center_runbook"))


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
    except sqlite3.OperationalError as exc:
        conn.close()
        return jsonify({"ok": False, "error": str(exc)}), 503
    finally:
        conn.close()
    return jsonify({"ok": True}), 202
