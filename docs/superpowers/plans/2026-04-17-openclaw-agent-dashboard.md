# Openclaw Agent Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real-time agent monitoring dashboard at `/admin/agents` inside the montanablotter admin panel, showing live activity, status, and conversation history for all 5 openclaw agents.

**Architecture:** Flask SSE endpoint tails `openclaw logs --follow` subprocess output and streams JSONL events to the browser via `EventSource`. Page-load snapshot parses `/tmp/openclaw/openclaw-YYYY-MM-DD.log` for agent status cards and history tabs. All routes are `@login_required`.

**Tech Stack:** Python/Flask, `subprocess.Popen`, Flask `stream_with_context`, vanilla JS `EventSource`, Tailwind CSS (CDN, matches existing admin templates).

---

## Log Format Reference

Each line in `/tmp/openclaw/openclaw-YYYY-MM-DD.log` is a JSON object:
```json
{
  "0": "primary message",
  "1": "lane=session:agent:reporter:main durationMs=123 ...",
  "_meta": { "name": "{\"subsystem\":\"diagnostic\"}", "logLevelName": "INFO", ... },
  "time": "2026-04-17T05:42:11.000+00:00"
}
```

**Agent name extraction** (in order of priority):
1. Regex `lane=session:agent:(\w+):` in any string field value
2. Fall back to `"system"`

**Known agent IDs:** `main`, `reporter`, `scout`, `clerk`, `bailbot`

**Log path constant:** `OPENCLAW_LOG_PATH = Path(f"/tmp/openclaw/openclaw-{date.today().isoformat()}.log")`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `blueprints/admin/agents.py` | Routes + log parsing |
| Create | `templates/admin_agents.html` | Dashboard UI |
| Modify | `blueprints/admin/__init__.py` | Register agents sub-module |

---

## Task 1: Log parser utility functions

**Files:**
- Create: `blueprints/admin/agents.py`

- [ ] **Step 1: Create the file with parse helpers**

```python
# blueprints/admin/agents.py
from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Response, render_template
from flask_login import login_required
from flask import stream_with_context

from blueprints.admin import admin_bp

KNOWN_AGENTS = ['main', 'reporter', 'scout', 'clerk', 'bailbot']
_AGENT_RE = re.compile(r'lane=session:agent:(\w+):')


def _log_path() -> Path:
    return Path(f"/tmp/openclaw/openclaw-{date.today().isoformat()}.log")


def _extract_agent(entry: dict) -> str:
    """Return agent name from a parsed log entry, or 'system'."""
    for key, val in entry.items():
        if key == '_meta':
            continue
        if isinstance(val, str):
            m = _AGENT_RE.search(val)
            if m:
                return m.group(1)
    return 'system'


def _parse_entry(raw: str) -> dict | None:
    """Parse a JSONL log line. Returns None if invalid."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return {'agent': 'system', 'level': 'INFO', 'time': '', 'msg': raw, 'raw': raw}
    agent = _extract_agent(entry)
    meta = entry.get('_meta') or {}
    level = meta.get('logLevelName', 'INFO')
    ts = entry.get('time', '')
    # Build human-readable message from positional keys 0, 1, 2...
    parts = []
    for k in sorted(k for k in entry if k.isdigit()):
        val = entry[k]
        if isinstance(val, str) and val.strip():
            parts.append(val.strip()[:200])
    msg = ' | '.join(parts) if parts else ''
    return {'agent': agent, 'level': level, 'time': ts, 'msg': msg, 'raw': raw}
```

- [ ] **Step 2: Verify the file parses cleanly**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "from blueprints.admin.agents import _parse_entry, _extract_agent; print('OK')"
```
Expected output: `OK`

---

## Task 2: Agent status snapshot function

**Files:**
- Modify: `blueprints/admin/agents.py`

- [ ] **Step 1: Add snapshot function**

Append to `blueprints/admin/agents.py`:

```python
def _agent_snapshot() -> dict:
    """
    Read today's log file and return:
      {
        'agents': {name: {'status': 'active'|'idle'|'unknown', 'last_seen': str, 'last_msg': str}},
        'history': {name: [entry, ...]}   # last 100 per agent, newest-first
      }
    """
    now = datetime.now(timezone.utc)
    log_path = _log_path()

    agents = {name: {'status': 'unknown', 'last_seen': '', 'last_msg': ''} for name in KNOWN_AGENTS}
    history: dict[str, list] = {name: [] for name in KNOWN_AGENTS}

    if not log_path.exists():
        return {'agents': agents, 'history': history}

    all_entries: list[dict] = []
    try:
        with log_path.open(encoding='utf-8', errors='replace') as fh:
            for line in fh:
                entry = _parse_entry(line)
                if entry:
                    all_entries.append(entry)
    except OSError:
        return {'agents': agents, 'history': history}

    # Process newest-first for status, oldest-first already in list
    for entry in reversed(all_entries):
        name = entry['agent']
        if name not in KNOWN_AGENTS:
            continue
        ts_str = entry['time']
        if ts_str and not agents[name]['last_seen']:
            agents[name]['last_seen'] = ts_str
            agents[name]['last_msg'] = entry['msg'][:60]
            try:
                ts = datetime.fromisoformat(ts_str)
                diff = (now - ts).total_seconds()
                if diff < 30:
                    agents[name]['status'] = 'active'
                elif diff < 600:
                    agents[name]['status'] = 'idle'
                else:
                    agents[name]['status'] = 'seen'
            except ValueError:
                agents[name]['status'] = 'seen'

    # Build per-agent history (last 100, newest-first)
    for entry in reversed(all_entries):
        name = entry['agent']
        if name in KNOWN_AGENTS and len(history[name]) < 100:
            history[name].append(entry)

    return {'agents': agents, 'history': history}
```

- [ ] **Step 2: Quick smoke test**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "
from blueprints.admin.agents import _agent_snapshot
snap = _agent_snapshot()
print('agents:', list(snap['agents'].keys()))
print('history counts:', {k: len(v) for k, v in snap['history'].items()})
"
```
Expected: prints agents list and history entry counts (non-zero if log file exists).

---

## Task 3: Route handlers

**Files:**
- Modify: `blueprints/admin/agents.py`

- [ ] **Step 1: Add dashboard route**

Append to `blueprints/admin/agents.py`:

```python
@admin_bp.route('/agents')
@login_required
def admin_agents():
    snapshot = _agent_snapshot()
    return render_template('admin_agents.html', snapshot=snapshot, known_agents=KNOWN_AGENTS)
```

- [ ] **Step 2: Add SSE stream route**

Append to `blueprints/admin/agents.py`:

```python
@admin_bp.route('/agents/stream')
@login_required
def admin_agents_stream():
    def generate():
        try:
            proc = subprocess.Popen(
                ['openclaw', 'logs', '--follow'],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError:
            yield 'data: {"error": "openclaw binary not found"}\n\n'
            return
        try:
            for raw_line in proc.stdout:
                entry = _parse_entry(raw_line)
                if entry is None:
                    continue
                yield f'data: {json.dumps(entry)}\n\n'
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
```

- [ ] **Step 3: Register the agents sub-module in `__init__.py`**

Open `blueprints/admin/__init__.py` and add `agents` to the import block inside `register_admin_blueprint`:

```python
def register_admin_blueprint(app):
    from blueprints.admin import agents      # noqa: F401  ← add this line
    from blueprints.admin import audience    # noqa: F401
    # ... rest unchanged
```

- [ ] **Step 4: Verify routes register without error**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "
import app as a
with a.app.test_client() as c:
    rules = [r.rule for r in a.app.url_map.iter_rules() if 'agents' in r.rule]
    print(rules)
"
```
Expected: `['/admin/agents', '/admin/agents/stream']`

---

## Task 4: Dashboard template

**Files:**
- Create: `templates/admin_agents.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}

{% block content %}
<div class="max-w-7xl mx-auto px-4 py-6">
  <div class="flex items-center justify-between mb-6">
    <div>
      <h1 class="text-2xl font-bold text-gray-900">Agent Monitor</h1>
      <p class="text-sm text-gray-500 mt-1">Openclaw agents — real-time activity</p>
    </div>
    <span id="conn-badge" class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-gray-100 text-gray-500">
      <span class="w-2 h-2 rounded-full bg-gray-400"></span> connecting…
    </span>
  </div>

  {# ── Agent status cards ── #}
  <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
    {% for name in known_agents %}
    {% set info = snapshot.agents[name] %}
    <div class="agent-card bg-white rounded-lg border border-gray-200 p-4 cursor-pointer hover:border-blue-400 transition"
         data-agent="{{ name }}" onclick="selectAgent('{{ name }}')">
      <div class="flex items-center gap-2 mb-2">
        <span class="status-dot w-2.5 h-2.5 rounded-full
          {% if info.status == 'active' %}bg-green-500
          {% elif info.status == 'idle' %}bg-yellow-400
          {% elif info.status == 'seen' %}bg-blue-300
          {% else %}bg-gray-300{% endif %}"></span>
        <span class="font-semibold text-gray-800 text-sm">{{ name }}</span>
      </div>
      <div class="text-xs text-gray-400 mb-1 status-label">
        {% if info.status == 'active' %}active
        {% elif info.status == 'idle' %}idle
        {% elif info.status == 'seen' %}seen
        {% else %}unknown{% endif %}
      </div>
      <div class="text-xs text-gray-500 truncate last-msg" title="{{ info.last_msg }}">
        {{ info.last_msg or '—' }}
      </div>
      <div class="text-xs text-gray-300 mt-1 last-seen">
        {{ info.last_seen[:19].replace('T',' ') if info.last_seen else '' }}
      </div>
    </div>
    {% endfor %}
  </div>

  {# ── Live feed ── #}
  <div class="bg-white rounded-lg border border-gray-200 mb-6">
    <div class="flex items-center justify-between px-4 py-2 border-b border-gray-100">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-sm font-semibold text-gray-700 mr-1">Live Feed</span>
        {% for name in known_agents %}
        <button onclick="toggleAgent('{{ name }}')"
                id="filter-{{ name }}"
                class="agent-filter-btn px-2 py-0.5 rounded text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50 active-filter"
                data-agent="{{ name }}">{{ name }}</button>
        {% endfor %}
      </div>
      <button id="pause-btn" onclick="togglePause()"
              class="text-xs px-3 py-1 rounded bg-gray-100 hover:bg-gray-200 text-gray-600 font-medium">⏸ Pause</button>
    </div>
    <div id="live-feed"
         class="font-mono text-xs bg-gray-950 text-gray-300 p-3 h-72 overflow-y-auto">
      <div class="text-gray-600 italic">Waiting for events…</div>
    </div>
  </div>

  {# ── Per-agent history tabs ── #}
  <div class="bg-white rounded-lg border border-gray-200">
    <div class="flex border-b border-gray-100 overflow-x-auto">
      {% for name in known_agents %}
      <button onclick="showHistory('{{ name }}')"
              id="tab-{{ name }}"
              class="history-tab px-4 py-2 text-sm font-medium text-gray-500 hover:text-gray-800 whitespace-nowrap
                     {% if loop.first %}border-b-2 border-blue-500 text-gray-900{% endif %}">
        {{ name }}
        <span class="ml-1 text-xs text-gray-400">({{ snapshot.history[name]|length }})</span>
      </button>
      {% endfor %}
    </div>
    {% for name in known_agents %}
    <div id="history-{{ name }}" class="history-panel p-4 {% if not loop.first %}hidden{% endif %}">
      {% if snapshot.history[name] %}
      <table class="w-full text-xs font-mono">
        <tbody>
          {% for entry in snapshot.history[name] %}
          <tr class="border-b border-gray-50 hover:bg-gray-50
                     {% if entry.level == 'ERROR' %}bg-red-50
                     {% elif entry.level == 'WARN' %}bg-yellow-50{% endif %}">
            <td class="py-1 pr-3 text-gray-400 whitespace-nowrap w-36">
              {{ entry.time[:19].replace('T',' ') if entry.time else '' }}
            </td>
            <td class="py-1 pr-3 w-14">
              <span class="px-1 rounded
                {% if entry.level == 'ERROR' %}bg-red-100 text-red-700
                {% elif entry.level == 'WARN' %}bg-yellow-100 text-yellow-700
                {% elif entry.level == 'DEBUG' %}bg-gray-100 text-gray-500
                {% else %}bg-blue-50 text-blue-600{% endif %}">
                {{ entry.level }}
              </span>
            </td>
            <td class="py-1 text-gray-700 break-all">{{ entry.msg }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <p class="text-sm text-gray-400 italic py-4">No log entries for {{ name }} in today's log file.</p>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</div>

<script>
const AGENT_COLORS = {
  main:     'text-purple-400',
  reporter: 'text-blue-400',
  scout:    'text-green-400',
  clerk:    'text-yellow-400',
  bailbot:  'text-pink-400',
  system:   'text-gray-500',
};

const LEVEL_COLORS = {
  ERROR: 'text-red-400',
  WARN:  'text-yellow-400',
  DEBUG: 'text-gray-600',
  TRACE: 'text-gray-700',
  INFO:  '',
};

let paused = false;
let activeFilters = new Set({{ known_agents | tojson }});
const MAX_LINES = 500;

function togglePause() {
  paused = !paused;
  document.getElementById('pause-btn').textContent = paused ? '▶ Resume' : '⏸ Pause';
}

function toggleAgent(name) {
  if (activeFilters.has(name)) {
    activeFilters.delete(name);
    document.getElementById('filter-' + name).classList.remove('active-filter', 'bg-blue-100', 'border-blue-400', 'text-blue-700');
  } else {
    activeFilters.add(name);
    document.getElementById('filter-' + name).classList.add('active-filter', 'bg-blue-100', 'border-blue-400', 'text-blue-700');
  }
}

function selectAgent(name) {
  showHistory(name);
  document.getElementById('history-' + name).scrollIntoView({behavior: 'smooth', block: 'start'});
}

function showHistory(name) {
  document.querySelectorAll('.history-panel').forEach(p => p.classList.add('hidden'));
  document.querySelectorAll('.history-tab').forEach(t => {
    t.classList.remove('border-b-2', 'border-blue-500', 'text-gray-900');
  });
  document.getElementById('history-' + name).classList.remove('hidden');
  document.getElementById('tab-' + name).classList.add('border-b-2', 'border-blue-500', 'text-gray-900');
}

// SSE live feed
const feed = document.getElementById('live-feed');
const es = new EventSource('/admin/agents/stream');

es.addEventListener('open', () => {
  document.getElementById('conn-badge').innerHTML =
    '<span class="w-2 h-2 rounded-full bg-green-500"></span> connected';
  document.getElementById('conn-badge').className =
    'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-green-50 text-green-700';
  feed.querySelector('.italic')?.remove();
});

es.addEventListener('error', () => {
  document.getElementById('conn-badge').innerHTML =
    '<span class="w-2 h-2 rounded-full bg-red-500"></span> disconnected';
  document.getElementById('conn-badge').className =
    'inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-red-50 text-red-700';
});

es.addEventListener('message', (ev) => {
  let entry;
  try { entry = JSON.parse(ev.data); } catch { return; }

  if (entry.error) {
    const errDiv = document.createElement('div');
    errDiv.className = 'text-red-400';
    errDiv.textContent = '⚠ ' + entry.error;
    feed.appendChild(errDiv);
    return;
  }

  if (!activeFilters.has(entry.agent)) return;

  const agentColor = AGENT_COLORS[entry.agent] || 'text-gray-400';
  const levelColor = LEVEL_COLORS[entry.level] || '';
  const ts = (entry.time || '').slice(11, 19);

  const row = document.createElement('div');
  row.className = 'flex gap-2 py-0.5 border-b border-gray-900';
  row.dataset.agent = entry.agent;

  const tsSpan = document.createElement('span');
  tsSpan.className = 'text-gray-600 shrink-0';
  tsSpan.textContent = ts;

  const agentSpan = document.createElement('span');
  agentSpan.className = agentColor + ' font-semibold shrink-0 w-16';
  agentSpan.textContent = '[' + entry.agent + ']';

  const msgSpan = document.createElement('span');
  msgSpan.className = levelColor || 'text-gray-300';
  msgSpan.textContent = entry.msg;

  row.appendChild(tsSpan);
  row.appendChild(agentSpan);
  row.appendChild(msgSpan);
  feed.appendChild(row);

  // Prune old lines
  while (feed.children.length > {{ 500 }}) {
    feed.removeChild(feed.firstChild);
  }

  // Auto-scroll
  if (!paused) {
    feed.scrollTop = feed.scrollHeight;
  }

  // Update agent card live
  updateCard(entry);
});

function updateCard(entry) {
  const card = document.querySelector(`.agent-card[data-agent="${entry.agent}"]`);
  if (!card) return;
  const dot = card.querySelector('.status-dot');
  const label = card.querySelector('.status-label');
  const msg = card.querySelector('.last-msg');
  const seen = card.querySelector('.last-seen');
  if (dot) { dot.className = 'status-dot w-2.5 h-2.5 rounded-full bg-green-500'; }
  if (label) { label.textContent = 'active'; }
  if (msg) { msg.textContent = entry.msg.slice(0, 60); msg.title = entry.msg; }
  if (seen) { seen.textContent = (entry.time || '').slice(0, 19).replace('T', ' '); }
}
</script>

<style>
  .agent-filter-btn.active-filter {
    background: #EFF6FF;
    border-color: #93C5FD;
    color: #1D4ED8;
  }
</style>
{% endblock %}
```

- [ ] **Step 2: Verify template renders**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "
import app as a
with a.app.test_request_context():
    from blueprints.admin.agents import _agent_snapshot, KNOWN_AGENTS
    from flask import render_template
    html = render_template('admin_agents.html', snapshot=_agent_snapshot(), known_agents=KNOWN_AGENTS)
    print('template OK, length:', len(html))
"
```
Expected: `template OK, length: <number>`

---

## Task 5: Wire up and smoke test

- [ ] **Step 1: Run the full test suite to check for regressions**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/ --tb=short -q 2>&1 | tail -10
```
Expected: all tests pass (188 passed).

- [ ] **Step 2: Start dev server and verify the route**

```bash
cd /root/montanablotter && source venv/bin/activate && python app.py &
sleep 2
curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/admin/agents
# Should redirect to login (302), not 404/500
```
Expected: `302`

- [ ] **Step 3: Kill dev server**

```bash
kill %1 2>/dev/null; true
```

- [ ] **Step 4: Restart production service**

```bash
systemctl restart montanablotter && sleep 2 && systemctl status montanablotter | grep "active (running)"
```
Expected: line containing `active (running)`.

---

## Self-Review Checklist (completed inline)

- ✅ **Spec coverage**: agent cards ✓, live feed ✓, history tabs ✓, SSE stream ✓, `@login_required` ✓, subprocess cleanup ✓, nginx `X-Accel-Buffering` header ✓, `textContent` not `innerHTML` ✓, openclaw-not-found error event ✓
- ✅ **No placeholders**: all steps have complete code
- ✅ **Type consistency**: `_parse_entry` returns `dict | None`, `_agent_snapshot` returns typed dict — consistent across all tasks
- ✅ **Log format**: updated to use regex `lane=session:agent:(\w+):` matching actual log structure (not `_meta.name` as originally specced)
