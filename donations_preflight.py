#!/usr/bin/env python3
"""
Donations preflight checks for production launch readiness.
"""

import sqlite3
import sys
import os
from pathlib import Path


def _load_env_file(env_path: Path) -> bool:
    if not env_path.exists():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))
    return True


def _bootstrap_env() -> None:
    script_dir = Path(__file__).resolve().parent
    _load_env_file(script_dir / ".env")
    _load_env_file(Path.cwd() / ".env")


_bootstrap_env()
import config


def _status(flag: bool) -> str:
    return "PASS" if flag else "FAIL"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def main() -> int:
    checks = []

    donations_enabled = bool(getattr(config, "DONATIONS_ENABLED", False))
    stripe_pk = bool(getattr(config, "STRIPE_PUBLISHABLE_KEY", ""))
    stripe_sk = bool(getattr(config, "STRIPE_SECRET_KEY", ""))
    stripe_whsec = bool(getattr(config, "STRIPE_WEBHOOK_SECRET", ""))
    checkout_ready = stripe_pk and stripe_sk

    checks.append(("Donations feature flag enabled", donations_enabled))
    checks.append(("Stripe checkout keys configured", checkout_ready))
    checks.append(("Stripe webhook secret configured", stripe_whsec))

    schema_ready = False
    stale_webhooks = 0
    webhook_errors_24h = 0
    webhook_events_24h = 0

    try:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row

        has_donations = _table_exists(conn, "donations")
        has_events = _table_exists(conn, "donation_events")
        has_webhooks = _table_exists(conn, "payment_webhook_events")
        schema_ready = has_donations and has_events and has_webhooks
        checks.append(("Donation schema present", schema_ready))

        if has_webhooks:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS webhook_events_24h,
                    COALESCE(SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END), 0) AS webhook_errors_24h,
                    COALESCE(SUM(CASE WHEN processed = 0 AND created_at <= datetime('now', '-10 minutes') THEN 1 ELSE 0 END), 0) AS stale_webhooks_10m
                FROM payment_webhook_events
                WHERE created_at >= datetime('now', '-24 hours')
                """
            ).fetchone()
            webhook_events_24h = int(row["webhook_events_24h"] or 0)
            webhook_errors_24h = int(row["webhook_errors_24h"] or 0)
            stale_webhooks = int(row["stale_webhooks_10m"] or 0)
    except sqlite3.Error as exc:
        checks.append((f"Database connectivity ({exc})", False))
    else:
        conn.close()

    checks.append(("No stale unprocessed webhooks (10m+)", stale_webhooks == 0))

    print("Montana Blotter Donations Preflight")
    print(f"Database: {config.DB_PATH}")
    print("")
    for label, flag in checks:
        print(f"[{_status(flag)}] {label}")
    print("")
    print(f"Webhook events (24h): {webhook_events_24h}")
    print(f"Webhook errors (24h): {webhook_errors_24h}")
    print(f"Stale unprocessed webhooks (10m+): {stale_webhooks}")

    launch_ready = all(flag for _, flag in checks)
    print("")
    print(f"Launch readiness: {'READY' if launch_ready else 'BLOCKED'}")
    if not launch_ready:
        print("Action: resolve failed checks before enabling public donations.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
