#!/usr/bin/env python3
"""
openclaw_heartbeat.py — Emit agent heartbeats to Mission Control.

Usage:
    python openclaw_heartbeat.py --agent reporter --state working --task "Fetching Gallatin PDF"

Or run as a background loop from a systemd service or tmux session:
    python openclaw_heartbeat.py --agent reporter --loop --interval 5

Environment:
    MISSION_CONTROL_URL  default http://127.0.0.1:5000/admin/api/mission-control/heartbeat
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
import urllib.error
import json


_DEFAULT_URL = "http://127.0.0.1:5000/admin/api/mission-control/heartbeat"


def _post_heartbeat(
    agent_id: str,
    state: str,
    current_task: str = "",
    step_label: str = "",
    problem_id: str = "",
    last_tool: str = "",
    detail_text: str = "",
    runtime: str = "openclaw",
    url: str | None = None,
) -> dict:
    payload = {
        "agent_id": agent_id,
        "state": state,
        "current_task": current_task,
        "step_label": step_label,
        "problem_id": problem_id,
        "last_tool": last_tool,
        "detail_text": detail_text,
        "runtime": runtime,
        "source_kind": "heartbeat",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url or _DEFAULT_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Mission-Control": "local",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"ok": True, "status": resp.status, "body": resp.read().decode("utf-8")}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": exc.read().decode("utf-8")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit OpenClaw agent heartbeats to Mission Control")
    parser.add_argument("--agent", required=True, help="Agent ID (main, reporter, scout, clerk, bailbot)")
    parser.add_argument("--state", default="working", help="Agent state")
    parser.add_argument("--task", default="", help="Current task description")
    parser.add_argument("--step", default="", help="Step label")
    parser.add_argument("--problem", default="", help="Problem ID")
    parser.add_argument("--tool", default="", help="Last tool used")
    parser.add_argument("--detail", default="", help="Detail text / raw excerpt")
    parser.add_argument("--runtime", default="openclaw", help="Runtime name")
    parser.add_argument("--url", default=os.getenv("MISSION_CONTROL_URL", _DEFAULT_URL), help="Heartbeat endpoint URL")
    parser.add_argument("--loop", action="store_true", help="Run in a loop")
    parser.add_argument("--interval", type=int, default=5, help="Loop interval in seconds")
    args = parser.parse_args()

    if args.loop:
        print(f"Heartbeat loop started for {args.agent} every {args.interval}s → {args.url}", file=sys.stderr)
        while True:
            result = _post_heartbeat(
                agent_id=args.agent,
                state=args.state,
                current_task=args.task,
                step_label=args.step,
                problem_id=args.problem,
                last_tool=args.tool,
                detail_text=args.detail,
                runtime=args.runtime,
                url=args.url,
            )
            if not result.get("ok"):
                print(f"[heartbeat error] {result}", file=sys.stderr)
            time.sleep(args.interval)
    else:
        result = _post_heartbeat(
            agent_id=args.agent,
            state=args.state,
            current_task=args.task,
            step_label=args.step,
            problem_id=args.problem,
            last_tool=args.tool,
            detail_text=args.detail,
            runtime=args.runtime,
            url=args.url,
        )
        print(json.dumps(result))
        return 0 if result.get("ok") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
