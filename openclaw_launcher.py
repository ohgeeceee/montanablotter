#!/usr/bin/env python3
"""
openclaw_launcher.py — Launch an OpenClaw agent with auto-heartbeat + Discord routing.

This wraps `openclaw agent` (or any long-running agent command) and spawns a
heartbeat emitter that posts to Mission Control every N seconds while the
agent process is alive.  It also tees agent stdout/stderr to a dedicated
Discord channel via the Hermes gateway (HTTP → local Hermes → Discord).

Usage:
    python openclaw_launcher.py --agent reporter --prompt "Fetch Bozeman blotter"
    python openclaw_launcher.py --agent scout --prompt "Geocode batch" --interval 3
    python openclaw_launcher.py --agent clerk --prompt "Run migrations" --once

Environment:
    MISSION_CONTROL_URL  default http://127.0.0.1:5000/admin/api/mission-control/heartbeat
    HERMES_GATEWAY_URL   default http://127.0.0.1:18789
    OPENCLAW_AGENT_ARGS  extra args passed to `openclaw agent`
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

# Load .env so API keys are available to the child openclaw process
import config

_DEFAULT_URL = "http://127.0.0.1:5000/admin/api/mission-control/heartbeat"
_HERMES_URL = os.getenv("HERMES_GATEWAY_URL", "http://127.0.0.1:18789")
_ROUTING_PATH = os.path.expanduser("~/.openclaw/discord_routing.json")


def _load_routing(path: str = _ROUTING_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _target_for(agent_id: str, routing: dict) -> str | None:
    return routing.get("agents", {}).get(agent_id, routing.get("default"))


def _post_heartbeat(payload: dict, url: str) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Mission-Control": "local",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": exc.read().decode("utf-8")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _post_to_discord(agent_id: str, message: str, routing: dict) -> dict:
    target = _target_for(agent_id, routing)
    if not target:
        return {"ok": False, "error": f"No Discord target for agent {agent_id}"}
    if not target.startswith("discord:"):
        return {"ok": False, "error": f"Unsupported target format: {target}"}
    channel_id = target.split(":", 1)[1]

    # Hermes has a native send_message endpoint when running as the gateway
    # Fallback: write to a well-known FIFO / socket that Hermes monitors
    payload = {
        "action": "send_message",
        "target": f"discord:{channel_id}",
        "message": f"**[{agent_id}]** {message}",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_HERMES_URL}/api/v1/deliver",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"ok": True, "status": resp.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": exc.read().decode("utf-8")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _heartbeat_loop(
    agent_id: str,
    runtime: str,
    interval: int,
    url: str,
    stop_event: threading.Event,
    state_provider,
) -> None:
    """Background thread: emit heartbeats until stop_event is set."""
    while not stop_event.is_set():
        payload = state_provider()
        payload["agent_id"] = agent_id
        payload["runtime"] = runtime
        payload["source_kind"] = "heartbeat"
        result = _post_heartbeat(payload, url)
        if not result.get("ok"):
            print(f"[heartbeat error] {result}", file=sys.stderr)
        stop_event.wait(interval)


def _discord_emitter_loop(
    agent_id: str,
    routing: dict,
    q: queue.Queue,
    stop_event: threading.Event,
) -> None:
    """Background thread: drain the log queue and post to Discord."""
    while not stop_event.is_set():
        try:
            line = q.get(timeout=1)
        except queue.Empty:
            continue
        if line is None:
            break
        result = _post_to_discord(agent_id, line, routing)
        if not result.get("ok"):
            # 404 means the gateway deliver endpoint isn't available — suppress
            # per-line noise; a single warning was already logged at startup.
            if result.get("status") != 404:
                print(f"[discord error] {result}", file=sys.stderr)


class _LineBuffer:
    """Buffer text and emit whole lines to a queue."""

    def __init__(self, q: queue.Queue):
        self.q = q
        self._buf = ""

    def write(self, data: str) -> None:
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self.q.put(line)

    def flush(self) -> None:
        if self._buf:
            self.q.put(self._buf)
            self._buf = ""


def _build_openclaw_cmd(agent_id: str, prompt: str, extra_args: list[str]) -> list[str]:
    cmd = ["openclaw", "agent", "--agent", agent_id, "--message", prompt]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch an OpenClaw agent with auto-heartbeat")
    parser.add_argument("--agent", required=True, help="Agent ID")
    parser.add_argument("--prompt", required=True, help="Prompt / message for the agent")
    parser.add_argument("--interval", type=int, default=5, help="Heartbeat interval in seconds")
    parser.add_argument("--runtime", default="openclaw", help="Runtime name")
    parser.add_argument("--url", default=os.getenv("MISSION_CONTROL_URL", _DEFAULT_URL), help="Heartbeat endpoint")
    parser.add_argument("--once", action="store_true", help="Run one agent turn then exit (no persistent loop)")
    parser.add_argument("--extra", default="", help="Extra args for openclaw agent (space-separated)")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running")
    parser.add_argument("--discord", action="store_true", default=True, help="Forward output to Discord channel")
    args = parser.parse_args()

    # Only one OpenClaw agent at a time (cron stagger can overlap on long runs).
    _global_lock_fh = open("/tmp/openclaw_global.lock", "w")
    try:
        fcntl.flock(_global_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(
            "[openclaw_launcher] another OpenClaw agent is already running, skipping this run.",
            file=sys.stderr,
        )
        return 0

    # Prevent concurrent runs of the same agent type.
    _lock_fh = open(f"/tmp/openclaw_agent_{args.agent}.lock", "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"[openclaw_launcher] {args.agent} already running, skipping this run.", file=sys.stderr)
        return 0

    extra_args = args.extra.split() if args.extra else []
    if args.once:
        extra_args.append("--json")
        extra_args.append("--local")  # bypass broken gateway, run embedded

    agent_cmd = _build_openclaw_cmd(args.agent, args.prompt, extra_args)

    # Shared mutable state for the heartbeat thread to read
    heartbeat_state = {
        "state": "working",
        "current_task": args.prompt[:120],
        "step_label": "agent_run",
        "problem_id": "",
        "last_tool": "",
        "detail_text": "",
    }

    def state_provider():
        return dict(heartbeat_state)

    if args.dry_run:
        print("Agent command:", " ".join(agent_cmd))
        print(f"Heartbeat every {args.interval}s to {args.url}")
        return 0

    routing = _load_routing()
    log_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    # Heartbeat thread
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(args.agent, args.runtime, args.interval, args.url, stop_event, state_provider),
        daemon=True,
    )
    hb_thread.start()

    # Discord emitter thread
    discord_thread = threading.Thread(
        target=_discord_emitter_loop,
        args=(args.agent, routing, log_queue, stop_event),
        daemon=True,
    )
    if args.discord and routing:
        discord_thread.start()

    print(f"[launcher] Starting agent {args.agent}", file=sys.stderr)
    print(f"[launcher] Heartbeat every {args.interval}s → {args.url}", file=sys.stderr)
    if args.discord and routing:
        target = _target_for(args.agent, routing)
        print(f"[launcher] Discord routing → {target}", file=sys.stderr)
        # Probe the deliver endpoint once so failures are visible at startup, not per-line.
        probe = _post_to_discord(args.agent, "[launcher] agent started", routing)
        if not probe.get("ok"):
            print(f"[launcher] Discord delivery unavailable ({probe}) — output will not be forwarded", file=sys.stderr)

    line_buf = _LineBuffer(log_queue)
    exit_code = 1
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            agent_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            line_buf.write(line)
        proc.wait()
        exit_code = proc.returncode or 0
    except KeyboardInterrupt:
        print("[launcher] Interrupted, terminating agent...", file=sys.stderr)
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        exit_code = 130
    finally:
        line_buf.flush()
        # Final heartbeat: done or offline
        heartbeat_state["state"] = "done" if exit_code == 0 else "offline"
        heartbeat_state["current_task"] = f"Agent exited with code {exit_code}"
        _post_heartbeat(state_provider(), args.url)
        log_queue.put(None)  # Signal discord thread to exit
        stop_event.set()
        hb_thread.join(timeout=args.interval + 2)
        discord_thread.join(timeout=5)

    print(f"[launcher] Agent {args.agent} finished with code {exit_code}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
