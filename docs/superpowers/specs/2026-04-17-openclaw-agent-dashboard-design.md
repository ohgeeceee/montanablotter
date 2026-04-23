# Openclaw Agent Dashboard — Design Spec
**Date:** 2026-04-17
**Status:** Approved

## Overview

A real-time agent monitoring dashboard embedded in the montanablotter admin panel at `/admin/agents`. Shows live activity, per-agent status, and conversation history for the 5 openclaw agents (main, reporter, scout, clerk, bailbot) running on the same host.

---

## Architecture

### New files
| File | Purpose |
|---|---|
| `blueprints/admin/agents.py` | Routes + log parsing logic |
| `templates/admin/agents.html` | Dashboard template |

### Modified files
| File | Change |
|---|---|
| `blueprints/admin/__init__.py` | Register `agents_bp` blueprint |
| `app.py` | One-line blueprint import if needed |

### Routes
| Route | Auth | Purpose |
|---|---|---|
| `GET /admin/agents` | `@login_required` | Dashboard page |
| `GET /admin/agents/stream` | `@login_required` | SSE live feed |

---

## Data Flow

```
openclaw gateway process
  → /tmp/openclaw/openclaw-YYYY-MM-DD.log  (JSONL, rolling daily)
       → read on page load → agent status cards + per-agent history
  → subprocess.Popen(['openclaw', 'logs', '--follow'])
       → Flask SSE generator (/admin/agents/stream)
            → browser EventSource → live feed panel (auto-scroll)
```

The subprocess is started fresh per SSE connection and cleaned up when the client disconnects. No background threads are kept alive between requests.

---

## Log Parsing

Log file: `/tmp/openclaw/openclaw-YYYY-MM-DD.log` — one JSON object per line.

Key fields extracted per line:
- `time` — ISO timestamp
- `_meta.name` — agent name (e.g. `"reporter"`, `"scout"`)
- `_meta.logLevelName` — `ERROR`, `WARN`, `INFO`, `DEBUG`, `TRACE`
- `0` — primary message string (openclaw uses positional keys)
- Any additional positional keys (`1`, `2`, ...) joined as context

**Agent status** — derived from log recency per agent name:
- **Active** — log entry from this agent within the last 30 seconds
- **Idle** — last entry > 30s but < 10 minutes ago
- **Unknown** — no entry in today's log file, or log file absent

**History** — last 100 log lines per agent, newest-first, parsed from today's log file on page load. Refreshes on manual reload or agent card click.

---

## UI Layout

```
┌─────────────────────────────────────────────────────────┐
│  Agent Status                                           │
│  ┌──────┐ ┌────────┐ ┌──────┐ ┌───────┐ ┌────────┐    │
│  │ main │ │reporter│ │scout │ │ clerk │ │bailbot │    │
│  │  ●   │ │  ●     │ │  ○   │ │  ○    │ │  ●     │    │
│  │active│ │ idle   │ │unk.  │ │ unk.  │ │active  │    │
│  └──────┘ └────────┘ └──────┘ └───────┘ └────────┘    │
├─────────────────────────────────────────────────────────┤
│  Live Feed   [Filter: all main reporter scout clerk bailbot]  [▶ pause] │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ 05:42:11 [reporter] Starting blotter scan...        │ │
│  │ 05:42:12 [reporter] Fetching Gallatin PDF...        │ │
│  │ 05:42:15 [main]     Received task from reporter     │ │
│  │ ...                                                 │ │
│  └─────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│  History: [main] [reporter] [scout] [clerk] [bailbot]   │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ (last 100 entries for selected agent tab)           │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

**Agent cards:**
- Colored status dot: green (active), yellow (idle), gray (unknown)
- Agent name + model (`claude-sonnet-4-6`)
- Last-seen timestamp (relative, e.g. "3s ago")
- Last log message preview (truncated to 60 chars)
- Click card → jumps to that agent's history tab

**Live feed panel:**
- `EventSource` connects to `/admin/agents/stream`
- New lines appended at bottom; auto-scroll keeps newest visible (pause button stops scroll without closing SSE)
- Per-agent color coding (5 distinct colors)
- Agent name filter toggles (click to show/hide per agent)
- Pause/resume button — stops auto-scroll without closing the SSE connection
- Max 500 lines kept in DOM (older lines pruned)

**History tabs:**
- One tab per agent
- Rendered from page-load data (no additional request)
- Shows: timestamp, level badge (color-coded), message
- ERROR/WARN entries highlighted

---

## SSE Endpoint Implementation

```python
@agents_bp.route('/admin/agents/stream')
@login_required
def agents_stream():
    def generate():
        proc = subprocess.Popen(
            ['openclaw', 'logs', '--follow'],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                yield f'data: {line}\n\n'
        finally:
            proc.terminate()
            proc.wait()
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
```

`X-Accel-Buffering: no` is required to prevent nginx from buffering the SSE stream.

---

## Security

- All routes `@login_required` — no unauthenticated access
- Subprocess uses list form (no shell=True) — no injection surface
- Log file path is hardcoded constant — not derived from user input
- HTML output uses Jinja2 auto-escaping; JS log content inserted via `textContent` (not `innerHTML`)
- SSE connection is tied to request lifecycle via `stream_with_context` — subprocess is always cleaned up on disconnect

---

## Error Handling

- If `openclaw` binary not found: SSE sends one error event then closes; page shows a banner with path hint
- If log file absent: agent cards show "unknown" status; history tabs show empty state message
- If log line is not valid JSON: silently skipped (malformed line passed through as raw text in live feed)
- SSE reconnects automatically via browser's `EventSource` retry logic (default 3s backoff)

---

## Out of Scope

- Sending messages to agents (read-only dashboard)
- Historical data beyond today's log file
- Multi-day log browsing
- Alerting / notifications on agent errors
