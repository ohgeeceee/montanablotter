#!/usr/bin/env python3
"""
openclaw_launcher.py — Launch an OpenClaw agent with auto-heartbeat.

This wraps `openclaw agent` (or any long-running agent command) and spawns a
heartbeat emitter that posts to Mission Control every N seconds while the
agent process is alive.

Usage:
    python openclaw_launcher.py --agent reporter --prompt "Fetch Bozeman blotter"
    python openclaw_launcher.py --agent scout --prompt "Geocode batch" --interval 3
    python openclaw_launcher.py --agent clerk --prompt "Run migrations" --once

Environment:
    MISSION_CONTROL_URL  default http://127.0.0.1:5000/admin/api/mission-control/heartbeat
    OPENCLAW_AGENT_ARGS  extra args passed to `openclaw agent`
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

_DEFAULT_URL = "http://127.0.0.1:5000/admin/api/mission-control/heartbeat"


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
    args = parser.parse_args()

    extra_args = args.extra.split() if args.extra else []
    if args.once:
        extra_args.append("--json")

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

    stop_event = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(args.agent, args.runtime, args.interval, args.url, stop_event, state_provider),
        daemon=True,
    )
    hb_thread.start()

    print(f"[launcher] Starting agent {args.agent}", file=sys.stderr)
    print(f"[launcher] Heartbeat every {args.interval}s → {args.url}", file=sys.stderr)

    try:
        proc = subprocess.Popen(agent_cmd, stdout=sys.stdout, stderr=sys.stderr, text=True)
        while proc.poll() is None:
            time.sleep(0.5)
        exit_code = proc.returncode or 0
    except KeyboardInterrupt:
        print("[launcher] Interrupted, terminating agent...", file=sys.stderr)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        exit_code = 130
    finally:
        # Final heartbeat: done or offline
        heartbeat_state["state"] = "done" if exit_code == 0 else "offline"
        heartbeat_state["current_task"] = f"Agent exited with code {exit_code}"
        _post_heartbeat(state_provider(), args.url)
        stop_event.set()
        hb_thread.join(timeout=args.interval + 2)

    print(f"[launcher] Agent {args.agent} finished with code {exit_code}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
