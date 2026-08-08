#!/usr/bin/env python3
"""
test_discord_integration.py — Smoke checks for the Discord integration.

This is intentionally NON-DESTRUCTIVE. It does not post messages to live
channels. Use it to confirm configuration is loaded correctly.

Usage:
    python3 test_discord_integration.py
    python3 test_discord_integration.py --live       # actually post to default channel
    python3 test_discord_integration.py --live --all # post to every configured channel

Exit codes:
    0  all checks passed
    1  one or more checks failed
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from agent_discord_router import AgentDiscordRouter


CHECKS: list[tuple[str, str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, detail, ok))
    flag = "PASS" if ok else "FAIL"
    suffix = f" -- {detail}" if detail else ""
    print(f"  [{flag}] {name}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="actually POST messages to Discord (off by default)")
    parser.add_argument("--all", action="store_true",
                        help="with --live: send to every configured agent channel")
    args = parser.parse_args()

    print("discord integration smoke test (live posts disabled by default)")
    print("--" * 32)

    router = AgentDiscordRouter()

    # --- configuration checks (always)
    check("routing file loaded", bool(router.agents) or bool(router.default_target),
          f"path={router.routing_path} agents={len(router.agents)} default={router.default_target}")
    check("bot token configured", bool(router.token))

    # --- routing shape (always)
    known = {"main", "reporter", "scout", "clerk", "bailbot",
             "orchestrator", "scraper", "analyst", "publisher"}
    missing = sorted(known - set(router.agents.keys()))
    check("expected agents present", not missing,
          f"missing={missing}" if missing else "all 9 expected agents present")

    # --- simulated send (always; no network)
    check("target resolution works", bool(router.target_for("main")),
          f"main -> {router.target_for('main')}")
    check("default fallback works", bool(router.target_for("nonexistent-agent")),
          f"unknown -> {router.default_target}")

    # --- live send (opt-in)
    if args.live:
        if not router.is_ready:
            print("ERROR: --live requested but router is not ready (token or routing missing)")
            return 1
        targets = list(router.agents.items()) if args.all else [("main", router.target_for("main") or "")]
        ok = 0
        for agent_id, target in targets:
            res = router.send(agent_id, f"smoke test from {agent_id} (you can delete this)")
            if res.get("ok"):
                check(f"live send -> {agent_id}", True, f"status={res.get('status')}")
                ok += 1
            else:
                check(f"live send -> {agent_id}", False, json.dumps(res)[:160])
        print(f"\nlive: {ok}/{len(targets)} succeeded")
    else:
        print("\n(skipped live POSTs; pass --live to enable)")

    # --- summary
    passed = sum(1 for _, _, ok in CHECKS if ok)
    total = len(CHECKS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
