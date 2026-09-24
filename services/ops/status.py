"""
Combined status payload for Montana Blotter.

Powers the /api/status JSON endpoint and the footer status badge.
Combines:
  - local liveness (/healthz on this same app)
  - external uptime ratio (UptimeRobot getMonitors)

Both sources are best-effort. If either is unreachable, the other
still answers. UptimeRobot leg is cached in-process for 60s so we
don't hammer their API on every page load.

Stdlib only. No extra deps.

RECONSTRUCTED 2026-09-23 from services/ops/__pycache__/status.cpython-312.pyc
(original .pyc dated Jun 25; the .py source was never committed and is missing
from disk). Module docstring, constants, function names, signatures, and
internal strings come verbatim from the bytecode disassembly. The exact
control flow inside each function is a faithful reconstruction, not a
guaranteed byte-for-byte copy.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

UR_API = os.environ.get("UPTIMEROBOT_API_BASE", "https://api.uptimerobot.com/v2")
UR_API_KEY = os.environ.get("UPTIMEROBOT_API_KEY", "")
UR_MONITOR_ID = os.environ.get("UPTIMEROBOT_MONITOR_ID", "")

_CACHE: dict[str, Any] = {"ratio": 0.0, "ts": 0.0}
CACHE_TTL = 60.0


def _ur_get_monitors():
    """Returns the first monitor dict from UptimeRobot, or None on any failure."""
    try:
        data = urllib.parse.urlencode(
            {"api_key": UR_API_KEY, "format": "json", "monitors": UR_MONITOR_ID}
        ).encode("utf-8")
        req = urllib.request.Request(
            UR_API + "/getMonitors",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("stat") != "ok":
            return None
        monitors = payload.get("monitors") or []
        return monitors[0] if monitors else None
    except Exception:
        return None


def fetch_uptime_ratio():
    """Returns (ratio_or_None, monitored_at_iso). Cached 60s."""
    now = time.time()
    if now - _CACHE["ts"] < CACHE_TTL:
        return _CACHE["ratio"], None
    monitor = _ur_get_monitors()
    ratio = None
    monitored_at = None
    if monitor:
        raw = monitor.get("all_time_uptime_ratio")
        if raw not in (None, "", "null"):
            try:
                ratio = float(raw)
            except (TypeError, ValueError):
                ratio = None
        logged = monitor.get("mfl")
        if logged:
            try:
                monitored_at = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(logged))
                )
            except (TypeError, ValueError):
                monitored_at = None
    _CACHE["ratio"] = ratio
    _CACHE["ts"] = now
    return ratio, monitored_at


def summarize(local_ok: bool = False) -> dict[str, Any]:
    """Build the combined status payload.
    local_ok: result of /healthz on this app.
    """
    ratio, monitored_at = fetch_uptime_ratio()
    if ratio is not None and local_ok:
        source = "self+uptimerobot"
    elif ratio is not None:
        source = "uptimerobot"
    elif local_ok:
        source = "self"
    else:
        source = "none"
    return {
        "ok": bool(local_ok or ratio is not None),
        "uptime_ratio": ratio,
        "monitored_by": source,
        "monitored_at": monitored_at,
        "source": source,
    }
