#!/usr/bin/env python3
"""
discord_webhook_server.py — FastAPI bridge: HTTP API -> Discord channels.

Two endpoints:

* POST /send   {agent, message, channel_id?}  (x-api-key auth required)
* GET  /health                                returns router status
* GET  /agents                                returns routing map

Designed to run under systemd with a /etc/discord-bridge.env providing:
    DISCORD_BOT_TOKEN       Bot token
    DISCORD_BRIDGE_API_KEY  Random secret for /send (required)

The bot token is required; the API key is required in production.
If either is missing, /health will report status=degraded and /send will
refuse traffic with 503 until configured.

Usage:
    uvicorn discord_webhook_server:app --host 127.0.0.1 --port 8090
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

# Stdlib env file loader (avoids the original fragile parser)
from agent_discord_router import _load_env_file  # type: ignore

# Best-effort dotenv fallback for dev usage.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Apply explicit /etc file if present (systemd may have already set the env,
# but loading here makes the dev workflow ergonomic as well).
for _candidate in ("/etc/discord-bridge.env", os.path.expanduser("~/.discord-bridge.env")):
    _load_env_file(_candidate)

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from agent_discord_router import AgentDiscordRouter

log = logging.getLogger("discord_bridge")

# ----------------------------------------------------------------- constants

API_KEY = os.environ.get("DISCORD_BRIDGE_API_KEY", "").strip()
# Default-bind to localhost only — this is a bridge, not a public endpoint.
DEFAULT_HOST = os.environ.get("DISCORD_BRIDGE_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("DISCORD_BRIDGE_PORT", "8090"))

# --------------------------------------------------------------- app + router

app = FastAPI(
    title="Montana Blotter Discord Bridge",
    version="2.0.0",
    # The default FastAPI docs at /docs and /redoc are nice for debugging;
    # we leave them on but they bind to localhost only.
)
router = AgentDiscordRouter()


# ----------------------------------------------------------------- requests

class SendRequest(BaseModel):
    agent: str = Field(..., min_length=1, max_length=64,
                       description="Logical agent name; routed via /etc/discord-routing.json")
    message: str = Field(..., min_length=1, max_length=1900,
                         description="Plain-text message body")
    channel_id: Optional[str] = Field(
        None, description="Optional raw Discord channel ID; bypasses routing",
        pattern=r"^\d{5,25}$",
    )


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok" if router.is_ready else "degraded",
        "router_loaded": bool(router.agents) or bool(router.default_target),
        "agents": len(router.agents),
        "default": router.default_target,
        "token_configured": bool(router.token),
        "api_key_configured": bool(API_KEY),
    }


@app.get("/agents")
async def list_agents() -> dict:
    return {
        "agents": router.agents,
        "default": router.default_target,
    }


def _check_api_key(x_api_key: Optional[str]) -> None:
    """Enforce /send auth. If no API key is configured we still serve traffic
    but log a loud warning at startup and once per request."""
    if not API_KEY:
        # In production this is a misconfiguration. Refuse rather than silently
        # allowing open access. The /health endpoint will report degraded.
        raise HTTPException(
            status_code=503,
            detail="DISCORD_BRIDGE_API_KEY not configured; refusing /send",
        )
    if not secrets.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(status_code=401, detail="invalid api key")


@app.post("/send")
async def send_message(req: SendRequest, x_api_key: Optional[str] = Header(None)) -> dict:
    _check_api_key(x_api_key)

    if req.channel_id:
        # Bypass routing; send directly to the requested channel.
        result = router._send_to_channel(req.channel_id, f"[{req.agent}] {req.message}")
    else:
        result = router.send(req.agent, req.message)

    if not result.get("ok"):
        # Surface Discord's error so upstream callers can diagnose.
        raise HTTPException(status_code=502, detail=result)
    return {"ok": True, "agent": req.agent, "result": result}


# ------------------------------------------------------------ error handlers

@app.exception_handler(Exception)
async def unhandled_exception_handler(_request, exc: Exception) -> dict:  # type: ignore
    log.exception("unhandled exception: %s", exc)
    return {"ok": False, "error": "internal_error", "detail": str(exc)[:200]}


# --------------------------------------------------------------- entrypoint

def _log_config_once() -> None:
    log.info("discord bridge starting")
    log.info("  routing_path : %s", router.routing_path)
    log.info("  token_set    : %s", bool(router.token))
    log.info("  api_key_set  : %s", bool(API_KEY))
    log.info("  agents       : %d", len(router.agents))
    log.info("  default      : %s", router.default_target)


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    _log_config_once()
    uvicorn.run(
        "discord_webhook_server:app",
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        access_log=True,
    )
else:
    # uvicorn imports this module via the import-string path; log on import too.
    import logging as _l
    _l.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    _log_config_once()
