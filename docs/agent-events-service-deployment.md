# Agent Events Service Deployment Note

Run the existing Flask app and the new FastAPI agent-events service as two separate processes that share the same Redis instance.

## Local dev

```bash
# Flask app
./venv/bin/python app.py

# FastAPI sidecar
MB_REDIS_HOST=127.0.0.1 MB_REDIS_PORT=6379 \
  ./venv/bin/python -m uvicorn agent_events_service:app --host 0.0.0.0 --port 8010
```

## Production

- Keep Flask on its current port 5000.
- Run the FastAPI service behind its own process manager unit on port 8010.
- Point both services at the same Redis URL via `MB_REDIS_URL` or `MB_REDIS_HOST` / `MB_REDIS_PORT` / `MB_REDIS_DB`.
- If the dashboard is served from a different origin, set:
  - `MB_AGENT_EVENTS_WS_URL`
  - `MB_AGENT_EVENTS_APPROVAL_ENDPOINT`

## systemd examples

Example unit files live in:
- `/root/montanablotter/ops/systemd/flask-app.service`
- `/root/montanablotter/ops/systemd/agent-events.service`

Install them like this:

```bash
sudo cp /root/montanablotter/ops/systemd/flask-app.service /etc/systemd/system/montanablotter.service
sudo cp /root/montanablotter/ops/systemd/agent-events.service /etc/systemd/system/montanablotter-agent-events.service
sudo systemctl daemon-reload
sudo systemctl enable --now montanablotter.service
sudo systemctl enable --now montanablotter-agent-events.service
```

## What to verify

- `GET /api/monitoring/agents/bootstrap` returns `ok: true`.
- `WS /ws/agents` connects and receives a snapshot event.
- Posting a review from the Flask dashboard publishes an event into Redis and appears in the live feed.
