"""Admin AdSense settings.

The owner has no shell access, so the ad slot IDs (which only exist inside the
AdSense console) are entered here and written to .env rather than by hand.
Slot IDs are not secret but are still never echoed back.
"""

from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

from blueprints.admin import admin_bp, require_role, _log_admin_action
from blueprints.admin.env_file import read_env, write_env
from utils.auth_constants import ADMIN_MANAGEMENT_ROLES

SLOT_KEYS = [
    "MB_ADSENSE_SLOT_HEADER",
    "MB_ADSENSE_SLOT_SIDEBAR",
    "MB_ADSENSE_SLOT_INCONTENT",
    "MB_ADSENSE_SLOT_FOOTER",
    "MB_ADSENSE_SLOT_ARTICLE",
]

LABELS = {
    "MB_ADSENSE_SLOT_HEADER": ("Header leaderboard", "under the masthead, every page"),
    "MB_ADSENSE_SLOT_SIDEBAR": ("Sidebar", "reserved — no sidebar in the current layouts"),
    "MB_ADSENSE_SLOT_INCONTENT": ("In-content", "top of the article / content area"),
    "MB_ADSENSE_SLOT_FOOTER": ("Footer", "every page"),
    "MB_ADSENSE_SLOT_ARTICLE": ("End of article", "below the content"),
}

EDITABLE = ["MB_ADSENSE_ENABLED", "MB_ADSENSE_CLIENT_ID", *SLOT_KEYS]


@admin_bp.route("/system/ads", methods=["GET", "POST"])
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_adsense_settings():
    current = read_env()

    if request.method == "POST":
        updates = {}
        enabled = (request.form.get("MB_ADSENSE_ENABLED") or "").strip().lower()
        updates["MB_ADSENSE_ENABLED"] = "true" if enabled in {"1", "true", "yes", "on"} else "false"

        client_id = (request.form.get("MB_ADSENSE_CLIENT_ID") or "").strip()
        if client_id:
            updates["MB_ADSENSE_CLIENT_ID"] = client_id

        for key in SLOT_KEYS:
            raw = (request.form.get(key) or "").strip()
            # Slot IDs are numeric; ignore anything that isn't.
            if raw and not raw.isdigit():
                flash(f"{key} must be a numeric ad slot ID.", "error")
                return redirect(url_for("admin.admin_adsense_settings"))
            updates[key] = raw

        try:
            write_env(updates)
        except OSError as exc:
            flash(f"Could not write .env: {exc}", "error")
            return redirect(url_for("admin.admin_adsense_settings"))

        _log_admin_action(
            "adsense.settings.update",
            target_type="env",
            metadata={"keys": sorted(updates)},
        )
        flash(
            "AdSense settings saved. Restart mtblotter for them to take effect. "
            "Units render only when their slot ID is set.",
            "success",
        )
        return redirect(url_for("admin.admin_adsense_settings"))

    slots = []
    for key in SLOT_KEYS:
        label, hint = LABELS[key]
        value = current.get(key, "")
        slots.append(
            {
                "key": key,
                "label": label,
                "hint": hint,
                "value": value,
                "set": bool(value),
            }
        )

    return render_template(
        "admin_adsense_settings.html",
        slots=slots,
        enabled=current.get("MB_ADSENSE_ENABLED", "true").lower() in {"1", "true", "yes", "on"},
        client_id=current.get("MB_ADSENSE_CLIENT_ID", ""),
        page_title="AdSense Settings",
        current_year=2026,
    )
