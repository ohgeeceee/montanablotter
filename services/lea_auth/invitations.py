"""LEA invitation creation and acceptance flow."""

import sqlite3
from datetime import datetime, timedelta, timezone

from services.lea_auth.api_tokens import generate_token, hash_token


def create_invitation(
    conn: sqlite3.Connection,
    agency_id: int,
    email: str,
    role: str,
    invited_by_user_id: int,
) -> dict:
    """
    Create a new invitation for a user to join an agency.

    Generates a token, stores it (hashed), and sets expiry to 7 days.

    Returns:
        Dict with 'id', 'email', 'role', 'token' (plaintext, for email link)
    """
    token = generate_token()
    token_hash = hash_token(token)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO lea_invitations
           (agency_id, email, role, token, expires_at, invited_by_user_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (agency_id, email, role, token_hash, expires_at, invited_by_user_id),
    )
    conn.commit()

    return {
        'id': cursor.lastrowid,
        'email': email,
        'role': role,
        'token': token,
        'expires_at': expires_at,
    }


def get_invitation(conn: sqlite3.Connection, token: str) -> dict | None:
    """
    Fetch a pending invitation by its plaintext token.

    Hashes the token and looks up in the database.

    Returns:
        Row dict if found and not accepted, None otherwise.
    """
    token_hash = hash_token(token)
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    cursor.execute(
        "SELECT * FROM lea_invitations WHERE token = ? AND accepted_at IS NULL",
        (token_hash,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def accept_invitation(
    conn: sqlite3.Connection,
    token: str,
    password_hash: str,
) -> dict:
    """
    Accept an invitation: verify token, check expiry, create user.

    Args:
        conn: Database connection
        token: Plaintext invitation token
        password_hash: bcrypt hash of the user's chosen password

    Returns:
        Dict with 'user_id', 'agency_id', 'email', 'role'

    Raises:
        ValueError: Token not found, already accepted, expired, or agency missing
    """
    invitation = get_invitation(conn, token)
    if not invitation:
        raise ValueError("Invitation not found or already accepted")

    # Check expiry
    expires_at = datetime.fromisoformat(invitation['expires_at'])
    if expires_at < datetime.now(timezone.utc):
        raise ValueError("Invitation has expired")

    # Verify agency still exists
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    cursor.execute(
        "SELECT id, org_name FROM lea_agencies WHERE id = ?",
        (invitation['agency_id'],),
    )
    agency = cursor.fetchone()
    if not agency:
        raise ValueError("Agency no longer exists")

    # Create user
    import secrets as _secrets

    username = invitation['email'].split('@')[0]
    # Ensure unique username
    base_username = username
    suffix = 1
    while True:
        cursor.execute(
            "SELECT id FROM lea_users WHERE username = ?",
            (username,),
        )
        if not cursor.fetchone():
            break
        username = f"{base_username}{suffix}"
        suffix += 1

    cursor.execute(
        """INSERT INTO lea_users
           (agency_id, username, email, full_name, password_hash, role)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            invitation['agency_id'],
            username,
            invitation['email'],
            invitation['email'].split('@')[0],
            password_hash,
            invitation['role'],
        ),
    )
    conn.commit()
    user_id = cursor.lastrowid

    # Mark invitation as accepted
    cursor.execute(
        "UPDATE lea_invitations SET accepted_at = datetime('now') WHERE id = ?",
        (invitation['id'],),
    )
    conn.commit()

    return {
        'user_id': user_id,
        'agency_id': invitation['agency_id'],
        'email': invitation['email'],
        'role': invitation['role'],
    }
