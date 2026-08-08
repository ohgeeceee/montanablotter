#!/usr/bin/env python3
"""
discord_webhook_server.py — FastAPI server to receive webhooks and forward to Discord.

Provides endpoints for external services to send messages to Discord channels
without needing the bot token directly.

Usage:
    uvicorn discord_webhook_server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os
import json
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from agent_discord_router import AgentDiscordRouter

app = FastAPI(title="Discord Webhook Bridge")
router = AgentDiscordRouter()

# Simple API key auth (set DISCORD_WEBHOOK_API_KEY in .env)
API_KEY = os.getenv("DISCORD_WEBHOOK_API_KEY")

class MessageRequest(BaseModel):
    agent: str
    message: str
    channel_id: Optional[str] = None

@app.post("/send")
async def send_message(
    req: MessageRequest,
    x_api_key: Optional[str] = Header(None)
):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    if req.channel_id:
        # Direct channel override
        result = router._send_to_discord(req.channel_id, f"**[{req.agent}]** {req.message}")
    else:
        result = router.send(req.agent, req.message)
    
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result)
    
    return {"ok": True, "agent": req.agent}

@app.get("/health")
async def health():
    return {"status": "ok", "router_loaded": bool(router._routing)}

@app.get("/agents")
async def list_agents():
    return {
        "agents": list(router._routing.keys()),
        "default": router._default
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
