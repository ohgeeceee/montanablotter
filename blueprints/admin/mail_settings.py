"""Admin mail configuration.

Lets the site owner set outbound mail credentials from the admin UI instead of
editing .env by hand. Values are written to .env atomically and are never
echoed back into the page, the audit log, or the session.

Only a fixed allow-list of mail keys can be written.
"""

from __future__ import annotations

import os
import smtplib
import ssl
import stat
from pathlib import Path

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required

import config
from blueprints.admin import admin_bp, require_role, _log_admin_action
from utils.auth_constants import ADMIN_MANAGEMENT_ROLES

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# The only keys this page may write. Anything else in the POST is ignored.
MAIL_KEYS = [
    "MB_SMTP_SERVER",
    "MB_SMTP_PORT",
    "MB_SMTP_USER",
    "MB_SMTP_PASSWORD",
    "MB_ROSTER_SMTP_SERVER",
    "MB_ROSTER_SMTP_PORT",
    "MB_ROSTER_SMTP_USER",
    "MB_ROSTER_SMTP_PASSWORD",
]

# Secrets are shown only as a length, never as a value.
SECRET_KEYS = {"MB_SMTP_PASSWORD", "MB_ROSTER_SMTP_PASSWORD"}


def _read_env() -> dict:
    values = {}
    if not ENV_PATH.is_file():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env(updates: dict) -> None:
    """Atomically update allow-listed keys, preserving file mode."""
    lines = []
    if ENV_PATH.is_file():
        lines = ENV_PATH.read_text(encoding="utf-8", errors="replace").splitlines()

    mode = stat.S_IMODE(ENV_PATH.stat().st_mode) if ENV_PATH.exists() else 0o600

    seen = set()
    out = []
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, ENV_PATH)


def _test_login(server: str, port: str, user: str, password: str) -> tuple[bool, str]:
    """Try an SMTP STARTTLS login. Never includes the password in the result."""
    if not server or not user or not password:
        return False, "server, user and password are all required"
    try:
        smtp = smtplib.SMTP(server, int(port or 587), timeout=20)
        smtp.starttls(context=ssl.create_default_context())
        try:
            smtp.login(user, password)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        return True, "Authenticated"
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"authentication rejected ({exc.smtp_code})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


@admin_bp.route("/system/mail", methods=["GET", "POST"])
@login_required
@require_role(*ADMIN_MANAGEMENT_ROLES)
def admin_mail_settings():
    current = _read_env()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "test":
            ok, detail = _test_login(
                request.form.get("smtp_server", ""),
                request.form.get("smtp_port", "587"),
                request.form.get("smtp_user", ""),
                request.form.get("smtp_password", ""),
            )
            if ok:
                flash(f"SMTP test passed — {detail}.", "success")
            else:
                flash(f"SMTP test failed — {detail}.", "error")
            return redirect(url_for("admin.admin_mail_settings"))

        updates = {}
        for key in MAIL_KEYS:
            raw = request.form.get(key)
            if raw is None:
                continue
            raw = raw.strip()
            # An empty secret field means "leave unchanged".
            if key in SECRET_KEYS and not raw and current.get(key):
                continue
            if key.endswith("_PORT"):
                if raw and not raw.isdigit():
                    flash(f"{key} must be a number.", "error")
                    return redirect(url_for("admin.admin_mail_settings"))
                raw = raw or "587"
            updates[key] = raw

        if not updates:
            flash("Nothing to save.", "info")
            return redirect(url_for("admin.admin_mail_settings"))

        try:
            _write_env(updates)
        except OSError as exc:
            flash(f"Could not write .env: {exc}", "error")
            return redirect(url_for("admin.admin_mail_settings"))

        _log_admin_action(
            "mail.settings.update",
            target_type="env",
            metadata={"keys": sorted(updates)},
        )
        # Config is read at process start, so a restart is required.
        flash(
            "Mail settings saved. Restart mtblotter for them to take effect "
            "(the password itself is never displayed).",
            "success",
        )
        return redirect(url_for("admin.admin_mail_settings"))

    fields = []
    for key in MAIL_KEYS:
        value = current.get(key, "")
        fields.append(
            {
                "key": key,
                "value": ("" if key in SECRET_KEYS else value),
                "secret": key in SECRET_KEYS,
                "set": bool(value),
                "length": len(value) if value else 0,
                "placeholder": "leave blank to keep current" if key in SECRET_KEYS else "",
            }
        )

    return render_template(
        "admin_mail_settings.html",
        fields=fields,
        env_path=str(ENV_PATH),
        page_title="Mail Settings",
        current_year=2026,
    )
