#!/usr/bin/env python3
"""Provisioning script for LaKeisha Clark as a paid (non-admin) warrant-access
user on the montanablotter.com production database.

Generates a temporary password, writes it to a per-run credential file under
`/root/montanablotter`, then atomically updates the user's `public_users` row
plus the paired legacy `users` row, records the change in `audit_logs`, and
verifies the resulting account state. The credential file is deleted before
the script exits; only the bcrypt hashes persist in the database.

Run: /root/montanablotter/venv/bin/python /root/montanablotter/provision_lakeisha_paid.py
"""

from __future__ import annotations

import json
import os
import secrets
import string
import sys

import sqlite3
from flask_bcrypt import generate_password_hash

DB = "/root/montanablotter/blotter.db"
EMAIL = "lakeishaj.clark@gmail.com"
DISPLAY_NAME = "LaKeisha"

CRED_GLOB = "/root/montanablotter/.lakeisha-cred.*"


def find_credential_file() -> str:
    import glob
    matches = sorted(glob.glob(CRED_GLOB))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one credential file, found {matches!r}")
    return matches[0]


def main() -> int:
    cred_path = find_credential_file()
    with open(cred_path, "r", encoding="ascii") as fh:
        password = fh.read()
    if password.endswith("\n"):
        password = password[:-1]
    if len(password) < 16 or any(ch.isspace() for ch in password):
        raise RuntimeError("credential failed local validation")

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
            raise RuntimeError("expected both a public_user and paired users row to exist")

        password_hash = generate_password_hash(password).decode("utf-8")
        public_id = int(public["id"])
        admin_id = int(admin["id"])

        conn.execute(
            """
            UPDATE public_users
               SET email = ?,
                   password_hash = ?,
                   display_name = ?,
                   is_active = 1,
                   is_subscribed = 1,
                   subscriber_plan = 'warrant_access',
                   subscription_status = 'active',
                   stripe_subscription_id = '',
                   subscription_activated_at = COALESCE(subscription_activated_at, datetime('now')),
                   subscription_canceled_at = NULL
             WHERE id = ?
            """,
            (EMAIL, password_hash, DISPLAY_NAME, public_id),
        )
        public_action = "updated" if public["password_hash"] != "" and public["password_hash"] != password_hash else "created"

        previous_role = (admin["role"] or "").strip()
        conn.execute(
            """
            UPDATE users
               SET password = ?,
                   membership = 'pro',
                   is_active = 1
             WHERE id = ?
            """,
            (password_hash, admin_id),
        )
        admin_action = "updated"
        new_role = previous_role

        metadata = json.dumps(
            {
                "email": EMAIL,
                "public_user_id": public_id,
                "admin_user_id": admin_id,
                "plan": "warrant_access",
                "grant_type": "manual",
                "admin_role_unchanged": previous_role,
            },
            separators=(",", ":"),
        )
        conn.execute(
            """
            INSERT INTO audit_logs (user_id, action, target_type, target_id, ip_address, metadata_json)
            VALUES (NULL, ?, 'public_user', ?, 'local-admin', ?)
            """,
            (f"manual_paid_user_{public_action}", str(public_id), metadata),
        )
        conn.commit()

        refreshed = conn.execute(
            """
            SELECT id, email, display_name, is_active, is_subscribed,
                   subscriber_plan, subscription_status,
                   CASE WHEN COALESCE(stripe_subscription_id, '') <> ''
                        THEN 1 ELSE 0 END AS stripe_ref_present,
                   subscription_activated_at, subscription_canceled_at,
                   created_at, last_login_at
              FROM public_users WHERE id = ?
            """,
            (public_id,),
        ).fetchone()
        refreshed_admin = conn.execute(
            "SELECT id, username, role, membership, is_active FROM users WHERE id = ?",
            (admin_id,),
        ).fetchone()

        print("== public_users row ==")
        print(json.dumps({k: refreshed[k] for k in refreshed.keys()}, indent=2, default=str))
        print("== users row ==")
        print(json.dumps({k: refreshed_admin[k] for k in refreshed_admin.keys()}, indent=2, default=str))
        print("== summary ==")
        print(json.dumps(
            {
                "public_user_id": public_id,
                "public_action": public_action,
                "admin_user_id": admin_id,
                "admin_action": admin_action,
                "public_paid_access": bool(
                    refreshed["is_active"]
                    and refreshed["is_subscribed"]
                    and refreshed["subscriber_plan"] == "warrant_access"
                    and refreshed["subscription_status"] in ("active", "trialing")
                ),
                "admin_role": refreshed_admin["role"],
                "admin_membership": refreshed_admin["membership"],
                "previous_role": previous_role,
            },
            indent=2,
        ))

        if previous_role == "" or "admin" in previous_role.lower():
            print("REJECTED: refused to grant or alter an admin role", file=sys.stderr)
            return 2
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
