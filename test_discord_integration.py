#!/usr/bin/env python3
"""
Test script for Discord integration.
Verifies all agent channels are reachable.
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

from agent_discord_router import AgentDiscordRouter

def test_all_channels():
    router = AgentDiscordRouter()
    agents = [
        "main", "reporter", "scout", "clerk", "bailbot",
        "orchestrator", "scraper", "analyst", "publisher", "unknown"
    ]
    
    results = {}
    for agent in agents:
        result = router.send(agent, f"Integration test from {agent}")
        results[agent] = result
        status = "OK" if result.get("ok") else "FAIL"
        print(f"{agent:15} -> {status}")
    
    # Summary
    ok_count = sum(1 for r in results.values() if r.get("ok"))
    total = len(agents)
    print(f"\n{ok_count}/{total} channels working")
    
    return 0 if ok_count == total else 1

if __name__ == "__main__":
    sys.exit(test_all_channels())
