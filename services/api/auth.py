"""
api_auth.py — API key management, validation, and rate limiting for Montana Blotter.

Design goals:
- Stateless validation where possible (keys are hashed, not stored plaintext)
- SQLite-backed request logging for quota enforcement
- Tiered limits: free / pro / enterprise
- Admin-only key creation/revocation (no self-service signup yet)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from functools import wraps
from typing import Callable

from flask import abort, g, has_request_context, request

import config

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MB_API_ADMIN_SECRET = getattr(config, "MB_API_ADMIN_SECRET", os.getenv("MB_API_ADMIN_SECRET", "")).strip()

# Tier defaults (requests per window)
DEFAULT_TIERS: dict[str, dict[str, int]] = {
    "free": {"window_seconds": 86400, "max_requests": 100},
    "pro": {"window_seconds": 86400, "max_requests": 1000},
    "enterprise": {"window_seconds": 86400, "max_requests": 100_000},
}

# Override from config if present
_tier_cfg = getattr(config, "MB_API_TIERS", None)
if isinstance(_tier_cfg, dict):
    DEFAULT_TIERS.update(_tier_cfg)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ApiClient:
    id: int
    name: str
    tier: str
    key_hash: str
    is_active: int
    created_at: str
    revoked_at: str | None


# ---------------------------------------------------------------------------
# Key generation & hashing
# ---------------------------------------------------------------------------

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

def _client_ip() -> str:
    """
    Best-effort client IP extraction for rate limiting / logging.

    Note: This trusts reverse-proxy headers. Ensure the edge proxy strips
    untrusted values from the public internet.
    """
    cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf_ip:
        return cf_ip
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return (request.remote_addr or "").strip()


def generate_api_key(*, prefix: str = "mb_") -> str:
    """Generate a new random API key. Return the plaintext key (store hash only)."""
    token = secrets.token_urlsafe(32)
    return f"{prefix}{token}"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    # Reuse Flask g if available, otherwise connect directly
    if has_request_context() and hasattr(g, "_api_auth_db"):
        return g._api_auth_db
    from db import get_db as _app_get_db
    return _app_get_db()


def ensure_api_auth_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS api_clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'free',
            key_hash TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            revoked_at TEXT
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_clients_key_hash ON api_clients(key_hash)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_clients_active ON api_clients(is_active)"
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS api_request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status_code INTEGER,
            ip_hash TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_logs_client_time ON api_request_logs(client_id, created_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_logs_path ON api_request_logs(path, created_at)"
    )

    # Add columns if missing (idempotent migration)
    for col, definition in [
        ("tier", "TEXT NOT NULL DEFAULT 'free'"),
        ("revoked_at", "TEXT"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE api_clients ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass

    conn.commit()


# ---------------------------------------------------------------------------
# Client CRUD
# ---------------------------------------------------------------------------

def create_client(conn: sqlite3.Connection, name: str, tier: str = "free") -> tuple[str, int]:
    """Create a new API client. Returns (plaintext_key, client_id)."""
    plaintext = generate_api_key()
    key_hash = _hash_key(plaintext)
    cursor = conn.execute(
        """
        INSERT INTO api_clients (name, tier, key_hash, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (name, tier, key_hash),
    )
    conn.commit()
    return plaintext, cursor.lastrowid


def revoke_client(conn: sqlite3.Connection, client_id: int) -> bool:
    cursor = conn.execute(
        """
        UPDATE api_clients
        SET is_active = 0, revoked_at = datetime('now')
        WHERE id = ?
        """,
        (client_id,),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_clients(conn: sqlite3.Connection) -> list[ApiClient]:
    rows = conn.execute(
        """
        SELECT id, name, tier, key_hash, is_active, created_at, revoked_at
        FROM api_clients
        ORDER BY created_at DESC
        """
    ).fetchall()
    return [ApiClient(**dict(r)) for r in rows]


def get_client_by_key_hash(conn: sqlite3.Connection, key_hash: str) -> ApiClient | None:
    row = conn.execute(
        """
        SELECT id, name, tier, key_hash, is_active, created_at, revoked_at
        FROM api_clients
        WHERE key_hash = ?
        LIMIT 1
        """,
        (key_hash,),
    ).fetchone()
    if row is None:
        return None
    return ApiClient(**dict(row))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _extract_bearer_token() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    # Fallback: ?api_key= query param (less secure, convenient for testing)
    return request.args.get("api_key", "").strip() or None


def validate_request(conn: sqlite3.Connection | None = None) -> ApiClient | None:
    """Validate the current request's API key. Returns client or None."""
    if not has_request_context():
        return None
    token = _extract_bearer_token()
    if not token:
        return None
    key_hash = _hash_key(token)
    db = conn or _get_db()
    client = get_client_by_key_hash(db, key_hash)
    if client is None or not client.is_active:
        return None
    return client


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def _count_requests_in_window(conn: sqlite3.Connection, client_id: int, window_seconds: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM api_request_logs
        WHERE client_id = ?
          AND created_at >= datetime('now', ? || ' seconds')
        """,
        (client_id, -window_seconds),
    ).fetchone()
    return int(row[0]) if row else 0

def _count_anonymous_requests_in_window(conn: sqlite3.Connection, ip_hash: str, window_seconds: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) FROM api_request_logs
        WHERE client_id IS NULL
          AND ip_hash = ?
          AND created_at >= datetime('now', ? || ' seconds')
          AND path LIKE '/api/%'
        """,
        (ip_hash, -window_seconds),
    ).fetchone()
    return int(row[0]) if row else 0


def check_rate_limit(conn: sqlite3.Connection, client: ApiClient) -> tuple[bool, dict]:
    """Return (allowed: bool, headers: dict)."""
    tier_cfg = DEFAULT_TIERS.get(client.tier, DEFAULT_TIERS["free"])
    window = tier_cfg["window_seconds"]
    max_req = tier_cfg["max_requests"]
    used = _count_requests_in_window(conn, client.id, window)
    remaining = max(0, max_req - used)
    headers = {
        "X-RateLimit-Limit": str(max_req),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Window": str(window),
    }
    if used >= max_req:
        return False, headers
    return True, headers


def log_request(conn: sqlite3.Connection, client_id: int | None, status_code: int | None = None) -> None:
    if has_request_context():
        ip = _client_ip()
        ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16] if ip else None
        method = request.method
        path = request.path
    else:
        ip_hash = None
        method = ""
        path = ""
    conn.execute(
        """
        INSERT INTO api_request_logs (client_id, method, path, status_code, ip_hash)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            client_id,
            method,
            path,
            status_code,
            ip_hash,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def require_api_key(
    *,
    tier: str | None = None,
    allow_anonymous: bool = False,
    anonymous_quota: dict[str, int] | None = None,
) -> Callable:
    """
    Decorator to protect a Flask route with API key auth + rate limiting.

    - tier: if set, require at least this tier (free < pro < enterprise)
    - allow_anonymous: if True, permit requests without a key (subject to anonymous_quota)
    - anonymous_quota: overrides DEFAULT_TIERS["free"] for anonymous requests
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not has_request_context():
                return f(*args, **kwargs)

            db = _get_db()
            client = validate_request(db)

            # Anonymous path
            if client is None:
                if not allow_anonymous:
                    abort(401, description="API key required. Provide it via Authorization: Bearer <key> or ?api_key=<key>")
                anon_cfg = anonymous_quota or DEFAULT_TIERS["free"]
                # Anonymous rate limit by IP hash (per-IP).
                ip = _client_ip()
                ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
                used = _count_anonymous_requests_in_window(db, ip_hash, anon_cfg["window_seconds"])
                remaining = max(0, anon_cfg["max_requests"] - used)
                g._api_rate_headers = {
                    "X-RateLimit-Limit": str(anon_cfg["max_requests"]),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Window": str(anon_cfg["window_seconds"]),
                }
                if used >= anon_cfg["max_requests"]:
                    abort(429, description="Rate limit exceeded. Upgrade to a paid API plan.")
                g._api_client = None
                return f(*args, **kwargs)

            # Tier check
            tier_order = {"free": 0, "pro": 1, "enterprise": 2}
            if tier and tier_order.get(client.tier, 0) < tier_order.get(tier, 0):
                abort(403, description=f"This endpoint requires tier '{tier}' or higher.")

            # Rate limit check
            allowed, headers = check_rate_limit(db, client)
            g._api_rate_headers = headers
            if not allowed:
                abort(429, description="Rate limit exceeded.")

            g._api_client = client
            return f(*args, **kwargs)
        return wrapper
    return decorator


def inject_rate_headers(response) -> None:
    """After-request hook to add rate limit headers. Call from app.after_request."""
    headers = getattr(g, "_api_rate_headers", None)
    if headers:
        for k, v in headers.items():
            response.headers[k] = v
    return None


def after_api_request(response) -> None:
    """Log the request and inject headers. Register as app.after_request."""
    inject_rate_headers(response)
    client = getattr(g, "_api_client", None)
    try:
        if not has_request_context():
            return response
        if not request.path.startswith("/api/"):
            return response
        db = _get_db()
        log_request(db, client.id if client else None, response.status_code)
    except Exception:
        # Never fail the response because logging failed
        pass
    return response
