# OpenClaw Agent Setup for Montana Blotter

## Overview

This document describes the OpenClaw agent configuration tailored for the Montana Blotter project. Five agents run against the local OpenClaw gateway (`ws://127.0.0.1:18789`) using the `openai-codex/gpt-5.4-mini` model via the Codex provider.

## Agent Roster

| Agent | Role | Workspace | Primary Domain |
|---|---|---|---|
| `main` | Overseer / Dispatcher | `~/.openclaw/agents/main/workspace` | Task decomposition, delegation, planning |
| `reporter` | Data Acquisition | `~/.openclaw/agents/reporter/workspace` | Fetchers, parsers, ingestion workers |
| `scout` | Geospatial Intel | `~/.openclaw/agents/scout/workspace` | Geocoding, trends, agendas, missing persons |
| `clerk` | Data Hygiene | `~/.openclaw/agents/clerk/workspace` | Deduplication, PII scrub, migrations, publishing |
| `bailbot` | Financial Analysis | `~/.openclaw/agents/bailbot/workspace` | Bond tracking, release alerts, court integration |

## Model Configuration

All agents use a single model catalog file (`agent/models.json`) pointing to the Codex provider:

```json
{
  "providers": {
    "codex": {
      "baseUrl": "https://chatgpt.com/backend-api/v1",
      "auth": "token",
      "api": "openai-codex-responses",
      "models": [
        {
          "id": "openai-codex/gpt-5.4-mini",
          "name": "GPT-5.4-Mini",
          "api": "openai-codex-responses",
          "reasoning": true,
          "contextWindow": 272000,
          "maxTokens": 128000
        }
      ]
    }
  },
  "model": { "primary": "openai-codex/gpt-5.4-mini" }
}
```

> **Note:** The fully qualified model ID `openai-codex/gpt-5.4-mini` is required. A bare `gpt-5.4-mini` may resolve to the OpenAI provider and fail without `OPENAI_API_KEY`.

## Workspace Files

Each agent workspace contains:

- `AGENTS.md` — Role, domain, rules, and stack.
- `SOUL.md` — Core logic, state machine, recovery behavior.
- `IDENTITY.md` — Name, vibe, emoji.
- `TOOLS.md` — Local file paths, API endpoints, and cheat-sheet references.

These files are loaded into context by OpenClaw at session start.

## Mission Control Integration

The Flask app includes a Mission Control dashboard (`/admin/mission-control`) that displays real-time agent state. Agents emit heartbeats via:

```bash
python openclaw_heartbeat.py \
  --agent reporter \
  --state working \
  --task "Fetching Gallatin PDF" \
  --step fetch
```

### Heartbeat Script

`openclaw_heartbeat.py` lives in the repo root. It POSTs to:

```
POST /admin/api/mission-control/heartbeat
Header: X-Internal-Mission-Control: local
```

Run once or in a loop:

```bash
# Single pulse
python openclaw_heartbeat.py --agent reporter --state working --task "Scanning feeds"

# Background loop (e.g., inside a tmux session or systemd service)
python openclaw_heartbeat.py --agent reporter --loop --interval 5
```

### State Machine

Valid states: `ready`, `working`, `tool_run`, `waiting`, `blocked`, `done`, `offline`.

Mission Control marks agents:
- `stale` if heartbeat age > 5 seconds
- `offline` if heartbeat age > 20 seconds

## Quick Commands

```bash
# Restart gateway
openclaw gateway restart

# Run an agent with auto-heartbeat (one-shot)
openclaw-run reporter "Fetch latest Bozeman blotter"

# Run an agent with auto-heartbeat (interactive / long-running)
python openclaw_launcher.py --agent reporter --prompt "Continuous fetch loop"

# Emit a manual heartbeat
python openclaw_heartbeat.py --agent reporter --state working --task "Scanning feeds"

# Background heartbeat loop
python openclaw_heartbeat.py --agent reporter --loop --interval 5
```

## Systemd Services

Install persistent agent services with auto-heartbeat:

```bash
sudo /root/montanablotter/scripts/install-openclaw-services.sh
```

Start / stop / status:

```bash
sudo systemctl start openclaw-agent@reporter
sudo systemctl stop openclaw-agent@reporter
sudo systemctl status openclaw-agent@reporter
sudo journalctl -u openclaw-agent@reporter -f
```

Per-agent environment files live in:
- `/root/montanablotter/scripts/openclaw-agent-{main,reporter,scout,clerk,bailbot}.env`

Edit the `AGENT_PROMPT` and `HEARTBEAT_INTERVAL` values there, then restart the service.

## Troubleshooting

- Gateway binds to loopback only (`127.0.0.1:18789`).
- Heartbeat endpoint rejects requests without `X-Internal-Mission-Control: local`.
- All Mission Control routes require admin login (`@login_required`, `@require_role`).
- Secrets remain in `.env`; never commit credentials or runtime artifacts.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `openai-codex/gpt-5.4-mini` not found | Ensure `models.json` uses the fully qualified ID under the `codex` provider. |
| Gateway not responding | `openclaw gateway restart` and verify with `openclaw gateway status`. |
| Heartbeat returns 403 | Confirm `X-Internal-Mission-Control: local` header is present. |
| Agent shows `offline` in dashboard | Check that the heartbeat loop is running and the Flask app is up. |

## Files

- `~/.openclaw/openclaw.json` — Global agent roster and gateway config.
- `~/.openclaw/agents/<id>/agent/models.json` — Per-agent model catalog.
- `~/.openclaw/agents/<id>/workspace/{AGENTS,SOUL,IDENTITY,TOOLS}.md` — Agent context.
- `/root/montanablotter/openclaw_heartbeat.py` — Heartbeat emitter.
- `/root/montanablotter/agent_mission_control.py` — Registry and snapshot service.
- `/root/montanablotter/services/agents/mission_control.py` — Registry and snapshot service (current location).
- `/root/montanablotter/blueprints/admin/mission_control.py` — Admin routes.
