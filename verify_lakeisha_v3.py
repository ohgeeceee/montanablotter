#!/usr/bin/env python3
import glob
import json
import sqlite3
import sys

from flask_bcrypt import check_password_hash, generate_password_hash

DB = "/root/montanablotter/blotter.db"
EMAIL = "lakeishaj.clark@gmail.com"
DISPLAY_NAME = "LaKeisha"

matches = glob.glob("/root/montanablotter/.lakeisha-cred.*")
if len(matches) != 1:
    raise RuntimeError(f"expected exactly one credential file, found {len(matches)}")
cred_path = matches[0]
with open(cred_path, "r", encoding="ascii") as fh:
    password = fh.read().strip()
if len(password) < 16 or any(ch.isspace() for ch in password):
    raise RuntimeError("credential failed validation")

conn = sqlite3.connect(DB, timeout=90)
conn.row_factory = sqlite3.Row
try:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")

    public = conn.execute(
        "SELECT * FROM public_users WHERE lower(trim(email)) = lower(trim(?))",
        (EMAIL,),
    ).fetchone()
    admin = conn.execute(
        "SELECT * FROM users WHERE lower(trim(email)) = lower(trim(?))",
        (EMAIL,),
    ).fetchone()
    if not public or not admin:
        raise RuntimeError("expected both a public_user and paired users row")

    public_password_ok = bool(check_password_hash(public["password_hash"], password))
    legacy_password_ok = bool(check_password_hash(admin["password"], password))
    public_paid_access = bool(
        public["is_active"]
        and public["is_subscribed"]
        and public["subscriber_plan"] == "warrant_access"
        and public["subscription_status"] in ("active", "trialing")
    )
    admin_access_roles = {"super_admin", "ops", "editor", "revenue", "read_only"}
    admin_access = (admin["role"] or "").strip() in admin_access_roles
    audit = conn.execute(
        """
        SELECT id, action, target_type, target_id, ip_address, metadata_json, timestamp
          FROM audit_logs
         WHERE target_type = 'public_user' AND target_id = ?
         ORDER BY id DESC LIMIT 1
        """,
        (str(public["id"]),),
    ).fetchone()

    summary = {
        "public_user_id": int(public["id"]),
        "email": public["email"],
        "display_name": public["display_name"],
        "public_password_ok": public_password_ok,
        "public_paid_access": public_paid_access,
        "subscriber_plan": public["subscriber_plan"],
        "subscription_status": public["subscription_status"],
        "is_active": int(public["is_active"]),
        "is_subscribed": int(public["is_subscribed"]),
        "subscription_activated_at": public["subscription_activated_at"],
        "subscription_canceled_at": public["subscription_canceled_at"],
        "legacy_user_id": int(admin["id"]),
        "legacy_username": admin["username"],
        "legacy_membership": admin["membership"],
        "legacy_role": admin["role"],
        "legacy_password_ok": legacy_password_ok,
        "admin_access": admin_access,
        "audit_id": int(audit["id"]) if audit else None,
        "audit_action": audit["action"] if audit else None,
        "audit_metadata": audit["metadata_json"] if audit else None,
    }
    print(json.dumps(summary, indent=2, default=str))

    if not (
        public_password_ok
        and legacy_password_ok
        and public_paid_access
        and not admin_access
        and audit is not None
    ):
        raise SystemExit(2)
finally:
    conn.close()
