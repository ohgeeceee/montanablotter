"""Standalone token factory (no dependency on the migration module)."""
import secrets


def new_portal_token() -> str:
    """256-bit URL-safe token for /portal/<token>."""
    return secrets.token_urlsafe(32)
