"""LEA user authentication helpers."""

import bcrypt


def hash_password(password: str) -> str:
    """Hash a password using bcrypt (cost=12)."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False
