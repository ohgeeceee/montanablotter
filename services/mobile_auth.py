"""
Mobile user authentication tokens.

Provides long-lived bearer tokens for the Montana Blotter mobile app. Tokens are
hashed in the database (SHA-256) and scoped to a public_users row. They can be
revoked server-side.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MobileUserToken:
    id: int
    public_user_id: int
    token_hash: str
    name: str | None
    is_active: bool
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_mobile_token(*, prefix: str = "mbm_") -> str:
    """Generate a new plaintext mobile auth token."""
    return f"{prefix}{secrets.token_urlsafe(32)}"


def create_mobile_user_token(
    conn: sqlite3.Connection,
    public_user_id: int,
    name: str | None = None,
) -> tuple[str, int]:
    """Create a token for a user. Returns (plaintext_token, token_id)."""
    plaintext = generate_mobile_token()
    token_hash = _hash_token(plaintext)
    cursor = conn.execute(
        """
        INSERT INTO public_user_api_tokens (public_user_id, token_hash, name, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (public_user_id, token_hash, name),
    )
    conn.commit()
    return plaintext, cursor.lastrowid


def get_token_by_plaintext(conn: sqlite3.Connection, token: str) -> MobileUserToken | None:
    """Look up an active token from its plaintext value."""
    if not token:
        return None
    token_hash = _hash_token(token)
    row = conn.execute(
        """
        SELECT id, public_user_id, token_hash, name, is_active, created_at, last_used_at, revoked_at
        FROM public_user_api_tokens
        WHERE token_hash = ? AND is_active = 1 AND revoked_at IS NULL
        """,
        (token_hash,),
    ).fetchone()
    if not row:
        return None
    return MobileUserToken(
        id=row["id"],
        public_user_id=row["public_user_id"],
        token_hash=row["token_hash"],
        name=row["name"],
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )


def revoke_mobile_user_token(conn: sqlite3.Connection, token_id: int) -> bool:
    """Revoke a token by id. Returns True if a row was updated."""
    cursor = conn.execute(
        """
        UPDATE public_user_api_tokens
        SET is_active = 0, revoked_at = datetime('now')
        WHERE id = ?
        """,
        (token_id,),
    )
    conn.commit()
    return cursor.rowcount > 0


def touch_mobile_user_token(conn: sqlite3.Connection, token_id: int) -> None:
    """Update the last_used_at timestamp for a token."""
    conn.execute(
        """
        UPDATE public_user_api_tokens
        SET last_used_at = datetime('now')
        WHERE id = ?
        """,
        (token_id,),
    )
    conn.commit()


def load_public_user(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """Load a public_users row as a dict."""
    row = conn.execute(
        "SELECT * FROM public_users WHERE id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def validate_mobile_bearer_token(conn: sqlite3.Connection, auth_header: str | None) -> MobileUserToken | None:
    """Extract and validate a Bearer token from an Authorization header."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:].strip()
    return get_token_by_plaintext(conn, token)
