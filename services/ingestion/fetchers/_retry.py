"""Shared retry/backoff + alerting helper for jail roster fetches.

Opt-in via env MB_ROSTER_RETRY=1. Pure, unit-testable retry_fetch.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Callable, Any

logger = logging.getLogger(__name__)

BACKOFFS = (2, 4, 8)
MAX_ATTEMPTS = 3
JITTER = 0.5


def _env_enabled() -> bool:
    return os.getenv("MB_ROSTER_RETRY", "").strip().lower() in ("1", "true", "yes", "on")


def retry_fetch(func: Callable, *args, sleep: Callable[[float], None] = time.sleep,
                rand: Callable[[float, float], float] = random.uniform,
                enabled: bool | None = None, **kwargs) -> Any:
    """Call func(*args, **kwargs) with retry/backoff. Returns result or raises last exc."""
    if enabled is None:
        enabled = _env_enabled()
    if not enabled:
        return func(*args, **kwargs)

    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("roster fetch attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc)
            if attempt < MAX_ATTEMPTS:
                delay = BACKOFFS[attempt - 1] + rand(-JITTER, JITTER)
                if delay < 0:
                    delay = 0
                sleep(delay)
    raise last_exc


def _discord_webhook_url() -> str | None:
    # Check env first
    url = os.getenv("DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_ROSTER_WEBHOOK")
    if url:
        return url
    # Try routing file for a webhook-style value (none here); return None
    return None


def alert_failed_fetch(source_id: int, county_name: str, error: str) -> None:
    msg = f"[jail-roster] fetch failed for {county_name} (source_id={source_id}): {error}"
    logger.error(msg)
    url = _discord_webhook_url()
    if url:
        try:
            import requests
            requests.post(url, json={"content": msg}, timeout=10)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("discord webhook failed: %s", exc)
    # fallback log
    log_path = Path("logs/jail_roster_alerts.log")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


__all__ = ["retry_fetch", "alert_failed_fetch", "MAX_ATTEMPTS", "BACKOFFS"]
