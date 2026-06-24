"""3D Hub — single MontanaBlotter admin blueprint.

Provides synchronous intake, quoting, and slicing endpoints for the 3D
print service. All files live inside the MontanaBlotter project tree
(data/threedhub/); the old standalone /root/3dhub root has been removed.

Routes:
    GET  /admin/3dhub/status          health + fleet status
    POST /admin/3dhub/intake          upload STL/OBJ/3MF and get mesh report
    POST /admin/3dhub/quote           quote a mesh or sliced report
    POST /admin/3dhub/slice           slice an uploaded STL to gcode
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    jsonify,
    make_response,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from services.threedhub.estimate import quote_from_mesh, quote_from_slicer
from services.threedhub.mesh_report import inspect_mesh
from services.threedhub.prusa_slicer import slice_mesh

threedhub_bp = Blueprint("threedhub", __name__)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (all under the MontanaBlotter project tree)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
INBOX_DIR = PROJECT_ROOT / "data" / "threedhub" / "inbox"
SLICED_DIR = PROJECT_ROOT / "data" / "threedhub" / "sliced"
WORKSPACE_DIR = PROJECT_ROOT / "data" / "threedhub" / "workspaces"
CONFIG_DIR = PROJECT_ROOT / "configs" / "threedhub"
FLEET_PATH = CONFIG_DIR / "fleet.json"

INBOX_DIR.mkdir(parents=True, exist_ok=True)
SLICED_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTS = {".stl", ".obj", ".3mf", ".ply", ".off", ".glb", ".gltf"}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _require_admin() -> bool:
    """Match the loose admin check used by other standalone admin blueprints.

    Covers legacy admin flag, Flask-Login's default _user_id key, and a
    generic user_id key.
    """
    return bool(
        session.get("admin_logged_in")
        or session.get("user_id")
        or session.get("_user_id")
    )


def _admin_or_401():
    if not _require_admin():
        wants_json = bool(
            request.is_json
            or request.files
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )
        if wants_json:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return redirect(url_for("admin.admin_login"))
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_fleet() -> list[dict]:
    if not FLEET_PATH.exists():
        return []
    try:
        return json.loads(FLEET_PATH.read_text())
    except json.JSONDecodeError:
        logger.exception("fleet.json is corrupt")
        return []


def _save_job_envelope(envelope: dict) -> None:
    inbox = WORKSPACE_DIR / "inbox.jsonl"
    with inbox.open("a") as f:
        f.write(json.dumps(envelope, default=str) + "\n")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
_STATUS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>3D Print Hub — Fleet Status</title>
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 2rem; background: #0f172a; color: #e2e8f0; }
    .container { max-width: 900px; margin: 0 auto; }
    h1 { color: #38bdf8; }
    .card { background: #1e293b; border-radius: 8px; padding: 1.25rem; margin: 1rem 0; }
    .label { color: #94a3b8; font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .value { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, monospace; font-size: 0.9rem; word-break: break-all; }
    table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
    th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #334155; }
    th { color: #94a3b8; }
    .ok { color: #4ade80; }
    .back { display: inline-block; margin-top: 1rem; color: #38bdf8; text-decoration: none; }
    .back:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="container">
    <h1>3D Print Hub</h1>
    <div class="card">
      <p class="label">Status</p>
      <p class="ok">● Operational</p>
    </div>
    <div class="card">
      <p class="label">Fleet</p>
      {% if fleet %}
      <table>
        <tr><th>Printer</th><th>Status</th></tr>
        {% for printer in fleet %}
        <tr>
          <td>{{ printer.get('name', 'Unnamed') }}</td>
          <td>{{ printer.get('status', 'unknown') }}</td>
        </tr>
        {% endfor %}
      </table>
      {% else %}
      <p>No printers configured.</p>
      {% endif %}
    </div>
    <div class="card">
      <p class="label">Paths</p>
      <p class="value">Fleet config: {{ fleet_path }}</p>
      <p class="value">Inbox: {{ inbox_dir }}</p>
      <p class="value">Sliced: {{ sliced_dir }}</p>
    </div>
    <a class="back" href="/admin">&larr; Back to admin hub</a>
  </div>
</body>
</html>
"""


@threedhub_bp.route("/admin/3dhub/status")
def threedhub_status():
    """Health check and current fleet status (HTML for browsers, JSON for API)."""
    auth = _admin_or_401()
    if auth:
        return auth
    fleet = _load_fleet()
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    wants_html = best == "text/html" or (
        "text/html" in request.accept_mimetypes and "application/json" not in request.accept_mimetypes
    )
    if wants_html:
        html = render_template_string(
            _STATUS_HTML,
            fleet=fleet,
            fleet_path=str(FLEET_PATH),
            inbox_dir=str(INBOX_DIR),
            sliced_dir=str(SLICED_DIR),
        )
        return make_response(html)
    return jsonify({
        "ok": True,
        "fleet": fleet,
        "fleet_path": str(FLEET_PATH),
        "inbox_dir": str(INBOX_DIR),
        "sliced_dir": str(SLICED_DIR),
    })


@threedhub_bp.route("/admin/3dhub/intake", methods=["POST"])
def threedhub_intake():
    """Accept a 3D model upload and return a mesh inspection report."""
    auth = _admin_or_401()
    if auth:
        return auth

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "missing 'file' field"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        return jsonify({"ok": False, "error": f"unsupported format: {ext}"}), 400

    job_id = request.form.get("job_id") or str(uuid.uuid4())
    submitted_by = request.form.get("submitted_by", "unknown")
    customer_notes = request.form.get("customer_notes", "")

    dest = INBOX_DIR / f"{job_id}{ext}"
    f.save(dest)
    if dest.stat().st_size > MAX_UPLOAD_BYTES:
        dest.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": f"file > {MAX_UPLOAD_BYTES} bytes"}), 413

    envelope = {
        "job_id": job_id,
        "file_path": str(dest),
        "submitted_by": submitted_by,
        "customer_notes": customer_notes,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_job_envelope(envelope)

    try:
        report = inspect_mesh(dest)
    except Exception as e:
        logger.exception("mesh inspection failed for %s", dest)
        return jsonify({
            "ok": False,
            "job_id": job_id,
            "error": f"mesh inspection failed: {e}",
            "envelope": envelope,
        }), 422

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "envelope": envelope,
        "mesh_report": json.loads(report.to_json()),
    }), 202


@threedhub_bp.route("/admin/3dhub/quote", methods=["POST"])
def threedhub_quote():
    """Generate a quote from a mesh report or a prior slice report."""
    auth = _admin_or_401()
    if auth:
        return auth

    body = request.get_json(silent=True) or request.form.to_dict()
    material = (body.get("material") or "pla").lower()
    layer_height = float(body.get("layer_height_mm", 0.2))
    post_processing = bool(body.get("post_processing"))
    rush = bool(body.get("rush"))

    source = body.get("source", "mesh")
    try:
        if source == "sliced":
            q = quote_from_slicer(
                material,
                float(body["filament_g"]),
                float(body["print_time_s"]),
                layer_height_mm=layer_height,
                post_processing=post_processing,
                rush=rush,
            )
        else:
            q = quote_from_mesh(
                material,
                float(body["volume_mm3"]),
                [float(x) for x in str(body["bbox_size_mm"]).strip("[]").split(",")],
                layer_height_mm=layer_height,
                post_processing=post_processing,
                rush=rush,
            )
    except Exception as e:
        logger.exception("quote failed")
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, "quote": json.loads(q.to_json())})


@threedhub_bp.route("/admin/3dhub/slice", methods=["POST"])
def threedhub_slice():
    """Slice an uploaded STL to gcode using the configured fleet profile."""
    auth = _admin_or_401()
    if auth:
        return auth

    body = request.get_json(silent=True) or request.form.to_dict()
    job_id = body.get("job_id")
    file_path = body.get("file_path")

    if not job_id and not file_path:
        return jsonify({"ok": False, "error": "job_id or file_path required"}), 400

    if file_path:
        stl = Path(file_path)
    else:
        matches = list(INBOX_DIR.glob(f"{job_id}.*"))
        if not matches:
            return jsonify({"ok": False, "error": "job file not found"}), 404
        stl = matches[0]

    if not stl.exists():
        return jsonify({"ok": False, "error": "file not found"}), 404

    fleet = _load_fleet()
    printer_id = body.get("printer_id")
    profile = None
    if printer_id and fleet:
        for p in fleet:
            if p.get("id") == printer_id:
                profile = p.get("profile")
                break

    out_gcode = SLICED_DIR / f"{job_id or stl.stem}.gcode"

    try:
        report = slice_mesh(stl, out_gcode, profile=profile)
    except Exception as e:
        logger.exception("slicing failed for %s", stl)
        return jsonify({"ok": False, "error": str(e)}), 422

    return jsonify({
        "ok": True,
        "slice_report": json.loads(report.to_json()),
    })
