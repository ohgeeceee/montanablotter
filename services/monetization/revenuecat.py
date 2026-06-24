"""
RevenueCat mobile purchase verification.

Uses the RevenueCat REST API to validate a customer's active entitlements and
mirror them into the local public_users subscription state.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)

REVENUECAT_API_BASE = "https://api.revenuecat.com/v1"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.REVENUECAT_SECRET_API_KEY}",
        "Content-Type": "application/json",
    }


def fetch_subscriber(app_user_id: str) -> dict[str, Any] | None:
    """Fetch a RevenueCat subscriber record. Returns None on failure."""
    if not config.REVENUECAT_SECRET_API_KEY:
        logger.warning("RevenueCat secret API key not configured; skipping verification")
        return None
    if not app_user_id:
        return None

    url = f"{REVENUECAT_API_BASE}/subscribers/{app_user_id}"
    try:
        response = requests.get(url, headers=_headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("RevenueCat subscriber fetch failed for %s: %s", app_user_id, exc)
        return None


def has_premium_entitlement(subscriber: dict[str, Any] | None) -> bool:
    """Return True if the subscriber has an active premium entitlement."""
    if not subscriber:
        return False
    entitlements = subscriber.get("subscriber", {}).get("entitlements", {})
    premium = entitlements.get(config.REVENUECAT_PREMIUM_ENTITLEMENT_ID)
    if not premium:
        return False
    return premium.get("is_active") is True
