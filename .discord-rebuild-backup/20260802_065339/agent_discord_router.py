#!/usr/bin/env python3
"""
agent_discord_router.py — Route agent output to dedicated Discord channels.

Reads ~/.openclaw/discord_routing.json for agent -> channel_id mappings.
Used by openclaw_launcher.py to forward agent stdout/stderr to Discord.

Usage:
    from agent_discord_router import AgentDiscordRouter
    router = AgentDiscordRouter()
    router.send("reporter", "Fetched 12 new blotter records.")
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ROUTING_PATH = os.path.expanduser("~/.openclaw/discord_routing.json")


class AgentDiscordRouter:
    def __init__(self, routing_path: str = ROUTING_PATH):
        self._routing = {}
        self._default = None
        if os.path.exists(routing_path):
            with open(routing_path) as f:
                data = json.load(f)
            self._routing = data.get("agents", {})
            self._default = data.get("default")

    def target_for(self, agent_id: str) -> str | None:
        return self._routing.get(agent_id, self._default)

    def send(self, agent_id: str, message: str) -> dict:
        target = self.target_for(agent_id)
        if not target:
            return {"ok": False, "error": f"No target for agent {agent_id}"}
        if not target.startswith("discord:"):
            return {"ok": False, "error": f"Unsupported target format: {target}"}
        channel_id = target.split(":", 1)[1]
        return self._send_to_discord(channel_id, f"**[{agent_id}]** {message}")

    def _send_to_discord(self, channel_id: str, content: str) -> dict:
        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            return {"ok": False, "error": "DISCORD_BOT_TOKEN not set"}
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        payload = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (https://montanablotter.com, 1.0)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"ok": True, "status": resp.status}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "status": exc.code, "body": exc.read().decode("utf-8")}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    import sys
    router = AgentDiscordRouter()
    if len(sys.argv) >= 3:
        result = router.send(sys.argv[1], sys.argv[2])
        print(json.dumps(result, indent=2))
    else:
        print("Usage: python agent_discord_router.py <agent_id> '<message>'")
        print("Mappings:", router._routing)
