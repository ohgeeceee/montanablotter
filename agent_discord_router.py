#!/usr/bin/env python3
"""
agent_discord_router.py — Route agent output to dedicated Discord channels.

Reads routing map from $DISCORD_ROUTING_PATH (default /etc/discord-routing.json).
Sends via the Discord REST API using $DISCORD_BOT_TOKEN. Pure stdlib (urllib) so it
runs anywhere without extra installs. Retries with exponential backoff on
transient errors. Hard-caps message length at 1900 chars to stay below
Discord's 2000 char limit and to leave headroom for embeds.

Usage:
    from agent_discord_router import AgentDiscordRouter
    router = AgentDiscordRouter()
    router.send("reporter", "Fetched 12 new blotter records.")

CLI:
    python3 agent_discord_router.py <agent_id> '<message>'
    python3 agent_discord_router.py --list                    # show configured agents
    python3 agent_discord_router.py --dry-run <agent_id> ... # do not actually send
"""
from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request

log = logging.getLogger("discord_router")

DEFAULT_ROUTING_PATH = "/etc/discord-routing.json"
DEFAULT_TIMEOUT = 10          # seconds for a single HTTP call
MAX_RETRIES = 4               # ~ initial + 3 retries with backoff
MAX_CONTENT_CHARS = 1900      # Discord limit is 2000; keep headroom
DISCORD_API_BASE = "https://discord.com/api/v10"
USER_AGENT = "MontanaBlotter-DiscordBot (https://montanablotter.com, 2.0)"


def _load_env_file(path: str) -> None:
    """Populate os.environ from a KEY=VALUE file. Idempotent and tolerant of
    blank lines, comments, and inline comments. Used by both this module and
    the bridge at startup so secrets stored in /etc/* can override nothing."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip()
                # strip matched surrounding quotes (single or double)
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        return
    except OSError as exc:
        log.warning("could not read env file %s: %s", path, exc)


def _load_dotenv_if_present() -> None:
    """Best-effort .env load. Optional dep — falls back to parse-from-file."""
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        cwd = os.getcwd()
        for candidate in (".env", os.path.join(cwd, ".env")):
            _load_env_file(candidate)


_load_dotenv_if_present()
# Then prefer an explicit /etc env if one was registered by the caller.
# (We do not auto-load /etc/discord-bridge.env here; systemd provides it.)


class AgentDiscordRouter:
    """Reads a routing JSON and sends messages to Discord channels."""

    def __init__(
        self,
        routing_path: str | None = None,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.routing_path = routing_path or os.environ.get(
            "DISCORD_ROUTING_PATH", DEFAULT_ROUTING_PATH
        )
        self.token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self.timeout = timeout
        self._routing: dict[str, str] = {}
        self._default: str | None = None
        self._load_routing()

    # ------------------------------------------------------------------ config

    def _load_routing(self) -> None:
        try:
            with open(self.routing_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            log.warning("routing file not found: %s", self.routing_path)
            return
        except (json.JSONDecodeError, OSError) as exc:
            log.error("could not parse routing file %s: %s", self.routing_path, exc)
            return

        agents = data.get("agents")
        if not isinstance(agents, dict):
            log.warning(
                "routing file %s missing 'agents' object (got %r)",
                self.routing_path,
                type(agents).__name__,
            )
            return

        cleaned: dict[str, str] = {}
        for k, v in agents.items():
            if isinstance(v, str) and v.startswith("discord:") and v.split(":", 1)[1].isdigit():
                cleaned[str(k)] = v
            else:
                log.warning("ignoring invalid routing entry: %r -> %r", k, v)
        self._routing = cleaned

        default = data.get("default")
        if isinstance(default, str) and default.startswith("discord:") and default.split(":", 1)[1].isdigit():
            self._default = default
        elif default is not None:
            log.warning("ignoring invalid default target: %r", default)

        log.info(
            "loaded routing: %d agents, default=%s",
            len(self._routing),
            self._default or "(none)",
        )

    @property
    def is_ready(self) -> bool:
        return bool(self.token) and (bool(self._routing) or bool(self._default))

    @property
    def agents(self) -> dict[str, str]:
        return dict(self._routing)

    @property
    def default_target(self) -> str | None:
        return self._default

    # ------------------------------------------------------------------- public

    def target_for(self, agent_id: str) -> str | None:
        return self._routing.get(agent_id, self._default)

    def send(self, agent_id: str, message: str) -> dict:
        """Resolve the agent to a channel and send. Returns a result dict."""
        target = self.target_for(agent_id)
        if not target:
            return {"ok": False, "error": f"no routing target for agent {agent_id!r}"}
        if not target.startswith("discord:"):
            return {"ok": False, "error": f"unsupported target scheme: {target}"}
        if not self.token:
            return {"ok": False, "error": "DISCORD_BOT_TOKEN not set"}
        channel_id = target.split(":", 1)[1]
        body = f"[{agent_id}] {message}"
        return self._send_to_channel(channel_id, body)

    # ----------------------------------------------------------------- internal

    def _send_to_channel(self, channel_id: str, content: str) -> dict:
        content = (content or "").strip()
        if len(content) > MAX_CONTENT_CHARS:
            content = content[: MAX_CONTENT_CHARS - 1] + "\u2026"
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        payload = json.dumps({"content": content}).encode("utf-8")

        attempt = 0
        last_err: dict | None = None
        while attempt < MAX_RETRIES:
            attempt += 1
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Authorization": f"Bot {self.token}",
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    return {
                        "ok": True,
                        "status": resp.status,
                        "channel_id": channel_id,
                        "attempt": attempt,
                    }
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_err = {
                    "ok": False,
                    "status": exc.code,
                    "channel_id": channel_id,
                    "body": body[:300],
                    "attempt": attempt,
                }
                # 4xx other than 429 is not retryable
                if 400 <= exc.code < 500 and exc.code != 429:
                    log.error(
                        "discord %s for channel %s (attempt %d): %s",
                        exc.code, channel_id, attempt, body[:200],
                    )
                    return last_err
                # 429 respects Retry-After
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_err = {
                    "ok": False,
                    "error": str(exc),
                    "channel_id": channel_id,
                    "attempt": attempt,
                }

            # exponential backoff with jitter, capped
            base = min(8.0, 0.5 * (2 ** (attempt - 1)))
            wait = wait if wait is not None else base + random.uniform(0, 0.25)
            log.warning(
                "send to channel %s failed (attempt %d/%d), sleeping %.2fs",
                channel_id, attempt, MAX_RETRIES, wait,
            )
            time.sleep(wait)
            wait = None  # consume the 429 hint only on the iteration that read it

        return last_err or {"ok": False, "error": "unknown failure", "channel_id": channel_id}


# ----------------------------------------------------------------------- CLI

def _cli_list(router: AgentDiscordRouter) -> int:
    print(f"routing_path : {router.routing_path}")
    print(f"token_set    : {bool(router.token)}")
    print(f"default      : {router.default_target or '(none)'}")
    print(f"agents ({len(router.agents)}):")
    for name, target in sorted(router.agents.items()):
        print(f"  {name:14} -> {target}")
    return 0


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    dry_run = False
    args = list(argv)
    if "--list" in args:
        return _cli_list(AgentDiscordRouter())
    if "--dry-run" in args:
        dry_run = True
        args.remove("--dry-run")
    if len(args) < 3:
        print(__doc__, file=sys.stderr)
        return 2

    agent_id, message = args[1], " ".join(args[2:])
    router = AgentDiscordRouter()
    if dry_run:
        target = router.target_for(agent_id)
        preview = message if len(message) <= 120 else message[:119] + "\u2026"
        print(json.dumps({
            "dry_run": True,
            "agent": agent_id,
            "target": target,
            "preview": preview,
        }, indent=2))
        return 0 if target else 1

    result = router.send(agent_id, message)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
