# Montana Blotter VPS Discord Setup

This file documents the **Discord integration** running on the Montana Blotter
VPS as of `2026-08-02`. It covers two services:

1. `discord-bridge.service` — FastAPI/uvicorn bridge that takes authenticated
   HTTP `POST /send` requests and forwards them to Discord channels.
2. `discord-healthcheck.service` — Discord bot that responds to `!up <site>` and
   `!sites` commands.

Both are managed by systemd, draw secrets from `/etc/*-env` files, and share a
routing config at `/etc/discord-routing.json`.

---

## Layout

```
/etc/
├── discord-bridge.env          # perms 600; bot token + bridge api key
├── discord-bridge.env.example  # perms 644; template for the above
├── discord-healthcheck.env     # perms 600; bot token + SITES_JSON
├── discord-healthcheck.env.example  # perms 644; template for the above
└── discord-routing.json        # perms 644; agents -> discord channel ids

/etc/systemd/system/
├── discord-bridge.service      # uvicorn on 127.0.0.1:8090
└── discord-healthcheck.service # /opt/discord-healthcheck/bot.py

/root/montanablotter/
├── agent_discord_router.py     # shared routing logic (stdlib only)
├── discord_webhook_server.py   # FastAPI bridge
├── test_discord_integration.py # smoke test (no live posts unless --live)
└── .discord-rebuild-backup/    # backups of every replaced file

/root/.openclaw/
└── discord_routing.json        # symlink -> /etc/discord-routing.json

/opt/discord-healthcheck/
├── bot.py                      # !up / !sites commands
└── venv/                       # discord.py + aiohttp

/var/log/discord-bridge/        # placeholder for future file logging
```

---

## Secrets

The **bot token** lives in two places right now:

1. `/etc/discord-bridge.env` — required by the bridge. Currently filled in
   for the bridge service.
2. `/root/montanablotter/.env` — the legacy location, picked up via
   `python-dotenv` when the bridge runs from the project working directory.

**Token priority** (highest first):

1. `DISCORD_BOT_TOKEN` set in `/etc/discord-bridge.env`.
2. `DISCORD_BOT_TOKEN` set in `/root/montanablotter/.env`.
3. None — bridge reports `token_configured=false` in `/health` and refuses
   to forward.

If the token in `.env` is stale or revoked, the bridge will receive `401
Unauthorized` from Discord and return `502 Bad Gateway` to its caller. To
rotate the token:

```bash
sudo -n systemctl edit discord-bridge.service   # add Environment= line, OR
sudo -n $EDITOR /etc/discord-bridge.env       # set DISCORD_BOT_TOKEN=...
sudo -n systemctl restart discord-bridge.service
```

---

## Service endpoints

The bridge listens on `http://127.0.0.1:8090` and is **not** exposed
externally. Put nginx or caddy in front if you need it public.

| Method | Path     | Auth          | Purpose                                       |
|--------|----------|---------------|-----------------------------------------------|
| GET    | /health  | none          | Status JSON; **never returns the token**       |
| GET    | /agents  | none          | Routing map (channel IDs are public)          |
| POST   | /send    | `X-API-Key`   | Forward `{agent, message}` (or `channel_id`) |

### Example: send via curl

```bash
KEY=$(sudo -n cat /etc/discord-bridge.env | awk -F= '/^DISCORD_BRIDGE_API_KEY/{print $2}')

curl -sS -X POST http://127.0.0.1:8090/send \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${KEY}" \
  -d '{"agent":"reporter","message":"Fetched 12 new blotter records."}'
```

### Example: send via Python

```python
from agent_discord_router import AgentDiscordRouter
router = AgentDiscordRouter()
print(router.send("reporter", "Hello from Python"))
```

---

## Service commands

```bash
systemctl status discord-bridge.service
systemctl status discord-healthcheck.service
sudo -n systemctl restart discord-bridge.service
sudo -n journalctl -u discord-bridge.service -f
```

The bridge uses `StartLimitBurst=10 / StartLimitIntervalSec=60`; the
healthcheck uses `StartLimitBurst=5 / StartLimitIntervalSec=300`. Both
services log to `journalctl` under `discord-bridge` / `discord-healthcheck`.

---

## Adding a new agent

Add an entry to `/etc/discord-routing.json`:

```json
{
  "agents": {
    "newagent": "discord:123456789012345678"
  }
}
```

Then verify the bot has been invited to the channel and can post there.
No service restart is needed — the bridge hot-reloads the routing file at
startup. To apply changes live:

```bash
sudo -n systemctl restart discord-bridge.service
```

---

## Healthcheck bot

`discord-healthcheck.service` runs `!up <site>` and `!sites` against a list of
URLs configured in `SITES_JSON`. The bot requires:

```env
DISCORD_BOT_TOKEN=...
SITES_JSON={"main": "https://montanablotter.com", "agendas": "https://agendas.montanablotter.com"}
```

To enable it, populate `/etc/discord-healthcheck.env` and:

```bash
sudo -n systemctl start discord-healthcheck.service
sudo -n journalctl -u discord-healthcheck.service -f
```

The bot is currently **stopped** because the file is in placeholder state.

---

## Smoke tests

```bash
# Configuration-only (does NOT post to Discord)
./venv/bin/python3 test_discord_integration.py

# Actually post a message to every channel
./venv/bin/python3 test_discord_integration.py --live --all
```

Expected (configuration-only):

```
5/5 checks passed
(skipped live POSTs; pass --live to enable)
```

---

## Post-rebuild status (as of 2026-08-02)

| Item                                       | State                                |
|--------------------------------------------|--------------------------------------|
| `discord-bridge.service`                   | active (running)                     |
| `/health` token + routing                  | loaded (9 agents, 1 default)         |
| `DISCORD_BRIDGE_API_KEY`                   | generated and stored in env file     |
| API key saved to                           | `/tmp/discord-bridge-api-key.txt`    |
| `discord-healthcheck.service`              | enabled, **stopped** (env is placeholder) |
| Discord upstream                           | returns 401 with the legacy `.env` token; **user action needed to rotate token** |
| Backups of original files                  | `/root/montanablotter/.discord-rebuild-backup/20260802_065346/` |
