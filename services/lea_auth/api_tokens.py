"""LEA API token generation and JWT utilities."""

import hashlib
import secrets
import time

import jwt


def generate_token() -> str:
    """Generate a cryptographically secure random token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a token using SHA-256 for secure storage."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_jwt(payload: dict, secret: str, expiry_hours: int = 720) -> str:
    """
    Create a signed JWT token (HS256).

    Args:
        payload: Claims to encode (subject, scopes, etc.)
        secret: Shared secret for signing
        expiry_hours: Token lifetime in hours (default 720 = 30 days)

    Returns:
        Encoded JWT string
    """
    token_payload = payload.copy()
    token_payload['iat'] = int(time.time())
    token_payload['exp'] = int(time.time()) + (expiry_hours * 3600)
    return jwt.encode(token_payload, secret, algorithm='HS256')


def verify_jwt(token: str, secret: str) -> dict:
    """
    Verify and decode a JWT token.

    Args:
        token: Encoded JWT string
        secret: Shared secret used for signing

    Returns:
        Decoded payload dict

    Raises:
        jwt.ExpiredSignatureError: Token has expired
        jwt.InvalidTokenError: Token is invalid
    """
    return jwt.decode(token, secret, algorithms=['HS256'])
