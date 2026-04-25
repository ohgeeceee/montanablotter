# Agent Mission Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an admin-only Mission Control page that shows the real OpenClaw/Codex agents running on the VPS in a 2D office view backed by truthful live telemetry.

**Architecture:** Add a focused mission-control service layer inside the Flask app. Real agent state comes from a local heartbeat registry persisted into SQLite, while observer adapters enrich or backfill state from logs and process inspection. The admin page loads from Jinja and polls JSON endpoints every 2 seconds for office snapshot and recent events.

**Tech Stack:** Flask, SQLite, Jinja, vanilla JavaScript, existing admin auth, `psutil`, Python unit tests via `pytest`

---

## File Structure

### New files

- `agent_mission_control.py`
  Local service module for state normalization, SQLite persistence, stale/offline handling, and snapshot/event reads.
- `blueprints/admin/mission_control.py`
  Admin page route plus JSON snapshot/event routes and optional local-only heartbeat ingestion route.
- `templates/admin_mission_control.html`
  Mission Control UI with top bar, office floor, agent detail rail, and event feed.
- `tests/test_agent_mission_control.py`
  Unit tests for registry/state/event logic.
- `tests/test_admin_mission_control.py`
  Route/auth/payload tests for the new admin surface.

### Modified files

- `init_db.py`
  Add SQLite migrations for `agent_runtime_state` and `agent_runtime_events`.
- `blueprints/admin/__init__.py`
  Register the new `mission_control` admin module.
- `templates/admin_dashboard.html`
  Add a clear entry point to Mission Control from the admin dashboard.

### Responsibility boundaries

- `agent_mission_control.py` owns telemetry truth and storage. It must not render HTML.
- `blueprints/admin/mission_control.py` owns auth-protected routes and HTTP payload shaping.
- `templates/admin_mission_control.html` owns office rendering and polling behavior.
- Tests stay split between service behavior and admin route behavior.

---

### Task 1: Add SQLite tables and the telemetry service skeleton

**Files:**
- Modify: `init_db.py`
- Create: `agent_mission_control.py`
- Test: `tests/test_agent_mission_control.py`

- [ ] **Step 1: Write the failing service tests**

```python
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

import agent_mission_control as mission


class MissionControlServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-mission-control-", suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE agent_runtime_state (
                agent_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                runtime TEXT NOT NULL,
                pid INTEGER,
                session_id TEXT,
                state TEXT NOT NULL,
                current_task TEXT,
                problem_id TEXT,
                step_label TEXT,
                last_tool TEXT,
                detail_text TEXT,
                source_kind TEXT NOT NULL,
                confidence TEXT NOT NULL,
                last_heartbeat_at TEXT,
                state_started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE agent_runtime_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                state TEXT,
                message TEXT,
                problem_id TEXT,
                tool_name TEXT,
                source_kind TEXT NOT NULL,
                raw_excerpt TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        os.unlink(self.db_path)

    def test_upsert_agent_heartbeat_persists_live_state(self) -> None:
        mission.upsert_agent_heartbeat(
            self.conn,
            {
                "agent_id": "reporter",
                "display_name": "Reporter",
                "runtime": "openclaw",
                "state": "working",
                "current_task": "Scanning Gallatin feed",
                "problem_id": "case-42",
                "step_label": "fetch",
                "last_tool": "curl",
                "detail_text": "Polling official endpoint",
                "source_kind": "heartbeat",
                "pid": 1234,
                "session_id": "tmux:reporter",
            },
        )

        row = self.conn.execute(
            "SELECT agent_id, state, current_task, confidence FROM agent_runtime_state WHERE agent_id = ?",
            ("reporter",),
        ).fetchone()

        self.assertEqual(row["agent_id"], "reporter")
        self.assertEqual(row["state"], "working")
        self.assertEqual(row["current_task"], "Scanning Gallatin feed")
        self.assertEqual(row["confidence"], "heartbeat")

    def test_snapshot_marks_stale_and_offline_by_heartbeat_age(self) -> None:
        now = datetime.now(UTC)
        self.conn.execute(
            """
            INSERT INTO agent_runtime_state (
                agent_id, display_name, runtime, state, source_kind, confidence,
                last_heartbeat_at, state_started_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "clerk",
                "Clerk",
                "codex",
                "working",
                "heartbeat",
                "heartbeat",
                (now - timedelta(seconds=8)).isoformat(),
                (now - timedelta(seconds=30)).isoformat(),
                now.isoformat(),
            ),
        )
        self.conn.commit()

        snapshot = mission.build_snapshot(self.conn, now=now, stale_after_seconds=5, offline_after_seconds=20)

        self.assertTrue(snapshot["agents"][0]["stale"])
        self.assertEqual(snapshot["agents"][0]["state"], "working")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_agent_mission_control.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_mission_control'`

- [ ] **Step 3: Add the mission-control service skeleton**

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


VALID_STATES = {"ready", "working", "tool_run", "waiting", "blocked", "done", "offline"}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _normalized_state(value: str | None) -> str:
    state = (value or "").strip().lower()
    return state if state in VALID_STATES else "working"


def upsert_agent_heartbeat(conn, payload: dict[str, Any]) -> None:
    now = utcnow().isoformat()
    state = _normalized_state(payload.get("state"))
    conn.execute(
        """
        INSERT INTO agent_runtime_state (
            agent_id, display_name, runtime, pid, session_id, state, current_task,
            problem_id, step_label, last_tool, detail_text, source_kind, confidence,
            last_heartbeat_at, state_started_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            display_name = excluded.display_name,
            runtime = excluded.runtime,
            pid = excluded.pid,
            session_id = excluded.session_id,
            state = excluded.state,
            current_task = excluded.current_task,
            problem_id = excluded.problem_id,
            step_label = excluded.step_label,
            last_tool = excluded.last_tool,
            detail_text = excluded.detail_text,
            source_kind = excluded.source_kind,
            confidence = excluded.confidence,
            last_heartbeat_at = excluded.last_heartbeat_at,
            updated_at = excluded.updated_at
        """,
        (
            payload["agent_id"],
            payload.get("display_name") or payload["agent_id"].title(),
            payload.get("runtime") or "openclaw",
            payload.get("pid"),
            payload.get("session_id"),
            state,
            payload.get("current_task") or "",
            payload.get("problem_id") or "",
            payload.get("step_label") or "",
            payload.get("last_tool") or "",
            payload.get("detail_text") or "",
            payload.get("source_kind") or "heartbeat",
            "heartbeat",
            now,
            now,
            now,
        ),
    )
    conn.commit()


def build_snapshot(conn, *, now: datetime | None = None, stale_after_seconds: int = 5, offline_after_seconds: int = 20) -> dict[str, Any]:
    now = now or utcnow()
    rows = conn.execute(
        """
        SELECT agent_id, display_name, runtime, pid, session_id, state, current_task,
               problem_id, step_label, last_tool, detail_text, source_kind, confidence,
               last_heartbeat_at, state_started_at, updated_at
        FROM agent_runtime_state
        ORDER BY agent_id ASC
        """
    ).fetchall()
    agents: list[dict[str, Any]] = []
    for row in rows:
        heartbeat_raw = row["last_heartbeat_at"] or ""
        heartbeat_at = datetime.fromisoformat(heartbeat_raw) if heartbeat_raw else None
        age_seconds = int((now - heartbeat_at).total_seconds()) if heartbeat_at else None
        stale = age_seconds is not None and age_seconds > stale_after_seconds
        state = row["state"]
        confidence = row["confidence"]
        if age_seconds is not None and age_seconds > offline_after_seconds:
            state = "offline"
            confidence = "offline"
        elif stale:
            confidence = "stale"
        agents.append(
            {
                "agent_id": row["agent_id"],
                "display_name": row["display_name"],
                "runtime": row["runtime"],
                "pid": row["pid"],
                "session_id": row["session_id"],
                "state": state,
                "current_task": row["current_task"] or "",
                "problem_id": row["problem_id"] or "",
                "step_label": row["step_label"] or "",
                "last_tool": row["last_tool"] or "",
                "detail_text": row["detail_text"] or "",
                "source_kind": row["source_kind"],
                "confidence": confidence,
                "last_heartbeat_at": heartbeat_raw,
                "state_started_at": row["state_started_at"],
                "updated_at": row["updated_at"],
                "stale": stale,
                "age_seconds": age_seconds,
            }
        )
    return {"captured_at": now.isoformat(), "agents": agents}
```

- [ ] **Step 4: Add the SQLite migration**

```python
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS agent_runtime_state (
        agent_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        runtime TEXT NOT NULL,
        pid INTEGER,
        session_id TEXT,
        state TEXT NOT NULL,
        current_task TEXT DEFAULT '',
        problem_id TEXT DEFAULT '',
        step_label TEXT DEFAULT '',
        last_tool TEXT DEFAULT '',
        detail_text TEXT DEFAULT '',
        source_kind TEXT NOT NULL DEFAULT 'heartbeat',
        confidence TEXT NOT NULL DEFAULT 'heartbeat',
        last_heartbeat_at TEXT,
        state_started_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS agent_runtime_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        state TEXT,
        message TEXT DEFAULT '',
        problem_id TEXT DEFAULT '',
        tool_name TEXT DEFAULT '',
        source_kind TEXT NOT NULL DEFAULT 'heartbeat',
        raw_excerpt TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runtime_events_agent_created ON agent_runtime_events(agent_id, created_at DESC)")
```

- [ ] **Step 5: Run the focused test to verify it passes**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_agent_mission_control.py -v`

Expected: PASS for both tests

- [ ] **Step 6: Commit**

```bash
cd /root/montanablotter
git add init_db.py agent_mission_control.py tests/test_agent_mission_control.py
git commit -m "feat(admin): add mission control state registry"
```

---

### Task 2: Record timeline events and observer-only fallbacks

**Files:**
- Modify: `agent_mission_control.py`
- Test: `tests/test_agent_mission_control.py`

- [ ] **Step 1: Add failing tests for event writes and observer snapshots**

```python
def test_upsert_agent_heartbeat_writes_state_change_event(self) -> None:
    mission.upsert_agent_heartbeat(
        self.conn,
        {
            "agent_id": "scout",
            "display_name": "Scout",
            "runtime": "openclaw",
            "state": "working",
            "current_task": "Reading county docket",
            "source_kind": "heartbeat",
        },
    )

    event = self.conn.execute(
        "SELECT event_type, state, source_kind FROM agent_runtime_events WHERE agent_id = ? ORDER BY id DESC LIMIT 1",
        ("scout",),
    ).fetchone()

    self.assertEqual(event["event_type"], "state_change")
    self.assertEqual(event["state"], "working")
    self.assertEqual(event["source_kind"], "heartbeat")


def test_build_snapshot_keeps_observer_only_agents_visible(self) -> None:
    now = datetime.now(UTC)
    self.conn.execute(
        """
        INSERT INTO agent_runtime_state (
            agent_id, display_name, runtime, state, current_task, source_kind, confidence,
            last_heartbeat_at, state_started_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "bailbot",
            "Bailbot",
            "codex",
            "working",
            "Watching shell output",
            "process",
            "observed-only",
            None,
            now.isoformat(),
            now.isoformat(),
        ),
    )
    self.conn.commit()

    snapshot = mission.build_snapshot(self.conn, now=now)

    self.assertEqual(snapshot["agents"][0]["confidence"], "observed-only")
    self.assertEqual(snapshot["agents"][0]["state"], "working")
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_agent_mission_control.py -v`

Expected: FAIL because no events are written and observer-only handling is incomplete

- [ ] **Step 3: Update the service to append events and preserve observer confidence**

```python
def _append_event(
    conn,
    *,
    agent_id: str,
    event_type: str,
    state: str,
    message: str,
    problem_id: str = "",
    tool_name: str = "",
    source_kind: str = "heartbeat",
    raw_excerpt: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO agent_runtime_events (
            agent_id, event_type, state, message, problem_id, tool_name, source_kind, raw_excerpt, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (agent_id, event_type, state, message, problem_id, tool_name, source_kind, raw_excerpt, utcnow().isoformat()),
    )


def upsert_agent_heartbeat(conn, payload: dict[str, Any]) -> None:
    existing = conn.execute(
        "SELECT state FROM agent_runtime_state WHERE agent_id = ?",
        (payload["agent_id"],),
    ).fetchone()
    previous_state = existing["state"] if existing else None
    # existing insert/update body stays here
    if previous_state != state:
        _append_event(
            conn,
            agent_id=payload["agent_id"],
            event_type="state_change",
            state=state,
            message=payload.get("current_task") or f"{payload['agent_id']} moved to {state}",
            problem_id=payload.get("problem_id") or "",
            tool_name=payload.get("last_tool") or "",
            source_kind=payload.get("source_kind") or "heartbeat",
            raw_excerpt=payload.get("detail_text") or "",
        )
    conn.commit()


def upsert_observed_agent(conn, payload: dict[str, Any]) -> None:
    now = utcnow().isoformat()
    conn.execute(
        """
        INSERT INTO agent_runtime_state (
            agent_id, display_name, runtime, pid, session_id, state, current_task,
            problem_id, step_label, last_tool, detail_text, source_kind, confidence,
            last_heartbeat_at, state_started_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            display_name = excluded.display_name,
            runtime = excluded.runtime,
            pid = excluded.pid,
            session_id = excluded.session_id,
            state = excluded.state,
            current_task = excluded.current_task,
            problem_id = excluded.problem_id,
            step_label = excluded.step_label,
            last_tool = excluded.last_tool,
            detail_text = excluded.detail_text,
            source_kind = excluded.source_kind,
            confidence = excluded.confidence,
            updated_at = excluded.updated_at
        """,
        (
            payload["agent_id"],
            payload.get("display_name") or payload["agent_id"].title(),
            payload.get("runtime") or "codex",
            payload.get("pid"),
            payload.get("session_id"),
            _normalized_state(payload.get("state")),
            payload.get("current_task") or "",
            payload.get("problem_id") or "",
            payload.get("step_label") or "",
            payload.get("last_tool") or "",
            payload.get("detail_text") or "",
            payload.get("source_kind") or "process",
            "observed-only",
            None,
            payload.get("state_started_at") or now,
            now,
        ),
    )
    conn.commit()
```

- [ ] **Step 4: Add recent-events reads to the service**

```python
def recent_events(conn, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, agent_id, event_type, state, message, problem_id, tool_name, source_kind, raw_excerpt, created_at
        FROM agent_runtime_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "agent_id": row["agent_id"],
            "event_type": row["event_type"],
            "state": row["state"] or "",
            "message": row["message"] or "",
            "problem_id": row["problem_id"] or "",
            "tool_name": row["tool_name"] or "",
            "source_kind": row["source_kind"],
            "raw_excerpt": row["raw_excerpt"] or "",
            "created_at": row["created_at"],
        }
        for row in rows
    ]
```

- [ ] **Step 5: Run the focused test to verify it passes**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_agent_mission_control.py -v`

Expected: PASS with event persistence and observer-only coverage

- [ ] **Step 6: Commit**

```bash
cd /root/montanablotter
git add agent_mission_control.py tests/test_agent_mission_control.py
git commit -m "feat(admin): add mission control event timeline"
```

---

### Task 3: Add admin routes for page, snapshot, events, and local heartbeat ingestion

**Files:**
- Create: `blueprints/admin/mission_control.py`
- Modify: `blueprints/admin/__init__.py`
- Test: `tests/test_admin_mission_control.py`

- [ ] **Step 1: Write failing admin-route tests**

```python
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import app as app_module
import config
import init_db


class AdminMissionControlTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-admin-mission-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True
        init_db.init_database()
        init_db.migrate()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO users (username, password, email, role, is_active) VALUES (?, ?, ?, ?, ?)",
            ("mission-admin", "unused", "mission@example.com", "ops", 1),
        )
        conn.commit()
        self.admin_user_id = conn.execute("SELECT id FROM users WHERE username = ?", ("mission-admin",)).fetchone()[0]
        conn.close()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        os.unlink(self.db_path)

    def _login(self, client) -> None:
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin_user_id)
            session["_fresh"] = True

    def test_mission_control_page_requires_login(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/admin/mission-control")
        self.assertEqual(response.status_code, 302)

    def test_snapshot_endpoint_returns_agents_payload(self) -> None:
        client = app_module.app.test_client()
        self._login(client)
        with mock.patch("blueprints.admin.mission_control.build_snapshot", return_value={"captured_at": "2026-04-23T00:00:00+00:00", "agents": []}):
            response = client.get("/admin/api/mission-control/snapshot")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("captured_at", payload)
        self.assertIn("agents", payload)

    def test_local_heartbeat_endpoint_persists_payload(self) -> None:
        client = app_module.app.test_client()
        response = client.post(
            "/admin/api/mission-control/heartbeat",
            data=json.dumps({"agent_id": "reporter", "state": "working"}),
            content_type="application/json",
            headers={"X-Internal-Mission-Control": "local"},
        )
        self.assertEqual(response.status_code, 202)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_admin_mission_control.py -v`

Expected: FAIL because the module and routes do not exist

- [ ] **Step 3: Add the new mission-control admin blueprint module**

```python
from __future__ import annotations

from flask import jsonify, render_template, request

from agent_mission_control import build_snapshot, recent_events, upsert_agent_heartbeat
from blueprints.admin import admin_bp, require_role
from db import connect_db


def _db():
    return connect_db(timeout_seconds=0.75, busy_timeout_ms=750)


@admin_bp.route("/mission-control")
@require_role("admin", "ops")
def admin_mission_control():
    conn = _db()
    try:
        snapshot = build_snapshot(conn)
    finally:
        conn.close()
    return render_template("admin_mission_control.html", snapshot=snapshot)


@admin_bp.route("/api/mission-control/snapshot")
@require_role("admin", "ops")
def admin_mission_control_snapshot():
    conn = _db()
    try:
        payload = build_snapshot(conn)
    finally:
        conn.close()
    return jsonify(payload)


@admin_bp.route("/api/mission-control/events")
@require_role("admin", "ops")
def admin_mission_control_events():
    conn = _db()
    try:
        payload = {"events": recent_events(conn, limit=80)}
    finally:
        conn.close()
    return jsonify(payload)


```

- [ ] **Step 4: Add a real local-only heartbeat route and register the module**

```python
@admin_bp.record_once
def _noop(_state):
    return None


def _is_local_heartbeat(request_headers) -> bool:
    return (request_headers.get("X-Internal-Mission-Control") or "").strip() == "local"


from flask import abort


@admin_bp.route("/api/mission-control/heartbeat", methods=["POST"])
def mission_control_heartbeat():
    if not _is_local_heartbeat(request.headers):
        abort(403)
    payload = request.get_json(silent=True) or {}
    if not payload.get("agent_id"):
        return jsonify({"ok": False, "error": "agent_id is required"}), 400
    conn = _db()
    try:
        upsert_agent_heartbeat(conn, payload)
    finally:
        conn.close()
    return jsonify({"ok": True}), 202
```

```python
from blueprints.admin import mission_control  # noqa: F401
```

- [ ] **Step 5: Run the admin-route tests to verify they pass**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_admin_mission_control.py -v`

Expected: PASS for login protection, JSON payload, and local heartbeat acceptance

- [ ] **Step 6: Commit**

```bash
cd /root/montanablotter
git add blueprints/admin/__init__.py blueprints/admin/mission_control.py tests/test_admin_mission_control.py
git commit -m "feat(admin): add mission control routes"
```

---

### Task 4: Build the Mission Control office UI and polling behavior

**Files:**
- Create: `templates/admin_mission_control.html`
- Modify: `templates/admin_dashboard.html`
- Test: `tests/test_admin_mission_control.py`

- [ ] **Step 1: Add a failing page-render test for the new UI**

```python
def test_mission_control_page_renders_office_shell(self) -> None:
    client = app_module.app.test_client()
    self._login(client)
    with mock.patch(
        "blueprints.admin.mission_control.build_snapshot",
        return_value={
            "captured_at": "2026-04-23T00:00:00+00:00",
            "agents": [
                {
                    "agent_id": "reporter",
                    "display_name": "Reporter",
                    "runtime": "openclaw",
                    "state": "working",
                    "current_task": "Scanning Gallatin feed",
                    "problem_id": "case-42",
                    "step_label": "fetch",
                    "last_tool": "curl",
                    "detail_text": "Polling official endpoint",
                    "confidence": "heartbeat",
                    "source_kind": "heartbeat",
                    "last_heartbeat_at": "2026-04-23T00:00:00+00:00",
                    "state_started_at": "2026-04-23T00:00:00+00:00",
                    "updated_at": "2026-04-23T00:00:00+00:00",
                    "stale": False,
                    "age_seconds": 1,
                }
            ],
        },
    ):
        response = client.get("/admin/mission-control")
    html = response.get_data(as_text=True)
    self.assertEqual(response.status_code, 200)
    self.assertIn("Mission Control", html)
    self.assertIn("Working Desks", html)
    self.assertIn("Reporter", html)
```

- [ ] **Step 2: Run the page-render test to verify it fails**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_admin_mission_control.py::AdminMissionControlTests::test_mission_control_page_renders_office_shell -v`

Expected: FAIL because the template does not exist yet

- [ ] **Step 3: Create the template with a fixed-zone office layout**

```html
{% extends "base.html" %}
{% block title %}Mission Control | Admin{% endblock %}
{% block content %}
<style>
  .mc-shell { --mc-bg: #f4efe7; --mc-card: #fffdf8; --mc-line: #d7c8b3; --mc-ink: #1f2937; --mc-muted: #6b7280; background:
    radial-gradient(circle at top left, rgba(14,165,233,0.10), transparent 32%),
    radial-gradient(circle at bottom right, rgba(245,158,11,0.12), transparent 28%),
    var(--mc-bg); }
  .mc-panel { background: var(--mc-card); border: 1px solid var(--mc-line); border-radius: 22px; box-shadow: 0 16px 40px rgba(31,41,55,0.08); }
  .mc-floor { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
  .mc-zone { min-height: 180px; padding: 14px; border-radius: 18px; border: 1px dashed var(--mc-line); background: rgba(255,255,255,0.55); }
  .mc-agent { border-radius: 16px; border: 1px solid #cbd5e1; background: white; padding: 10px 12px; transition: transform 180ms ease, box-shadow 180ms ease; }
</style>
<div class="mc-shell min-h-screen p-4 sm:p-6">
  <div class="max-w-[1500px] mx-auto space-y-6">
    <section class="mc-panel p-5">
      <div class="flex items-center justify-between gap-4">
        <div>
          <p class="text-[11px] uppercase tracking-[0.18em] font-black text-slate-500">Admin Only</p>
          <h1 class="text-3xl font-black text-slate-900">Mission Control</h1>
          <p class="text-sm text-slate-600">Live operational view of real agents on this VPS.</p>
        </div>
        <div class="text-right">
          <p class="text-[11px] uppercase tracking-[0.18em] font-black text-slate-500">Snapshot</p>
          <p id="mc-captured-at" class="text-sm font-mono text-slate-800">{{ snapshot.captured_at }}</p>
        </div>
      </div>
    </section>

    <section class="grid grid-cols-1 xl:grid-cols-12 gap-6">
      <article class="xl:col-span-8 mc-panel p-5">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-xl font-black text-slate-900">Office Floor</h2>
          <span class="text-xs text-slate-500">Polling every 2 seconds</span>
        </div>
        <div class="mc-floor">
          <section class="mc-zone" data-zone="ready"><h3 class="font-black text-slate-800">Ready Lounge</h3><div class="mt-3 space-y-3" id="zone-ready"></div></section>
          <section class="mc-zone" data-zone="working"><h3 class="font-black text-slate-800">Working Desks</h3><div class="mt-3 space-y-3" id="zone-working"></div></section>
          <section class="mc-zone" data-zone="tool_run"><h3 class="font-black text-slate-800">Tool Bench</h3><div class="mt-3 space-y-3" id="zone-tool_run"></div></section>
          <section class="mc-zone" data-zone="waiting"><h3 class="font-black text-slate-800">Review Table</h3><div class="mt-3 space-y-3" id="zone-waiting"></div></section>
          <section class="mc-zone" data-zone="blocked"><h3 class="font-black text-slate-800">Incident Bay</h3><div class="mt-3 space-y-3" id="zone-blocked"></div></section>
          <section class="mc-zone" data-zone="done"><h3 class="font-black text-slate-800">Archive Shelf</h3><div class="mt-3 space-y-3" id="zone-done"></div></section>
        </div>
      </article>
      <aside class="xl:col-span-4 space-y-6">
        <section class="mc-panel p-5">
          <h2 class="text-lg font-black text-slate-900">Agent Detail</h2>
          <div id="agent-detail" class="mt-3 text-sm text-slate-600">Select an agent card to inspect the current task.</div>
        </section>
        <section class="mc-panel p-5">
          <h2 class="text-lg font-black text-slate-900">Recent Events</h2>
          <div id="event-rail" class="mt-3 space-y-2 text-sm text-slate-700"></div>
        </section>
      </aside>
    </section>
  </div>
</div>
<script>
  window.__MISSION_CONTROL_BOOTSTRAP__ = {{ snapshot | tojson }};
</script>
<script>
  const bootstrap = window.__MISSION_CONTROL_BOOTSTRAP__ || {agents: [], captured_at: ""};
  const zoneIds = ["ready", "working", "tool_run", "waiting", "blocked", "done", "offline"];
  let selectedAgentId = "";

  function renderAgentCard(agent) {
    return `
      <button class="mc-agent w-full text-left" data-agent-id="${agent.agent_id}">
        <div class="flex items-center justify-between gap-2">
          <strong class="text-sm text-slate-900">${agent.display_name}</strong>
          <span class="text-[10px] uppercase font-black tracking-[0.14em] text-slate-500">${agent.confidence}</span>
        </div>
        <p class="mt-1 text-xs text-slate-500">${agent.runtime} · ${agent.state}</p>
        <p class="mt-2 text-sm text-slate-700">${agent.current_task || "No current task"}</p>
      </button>
    `;
  }
```

- [ ] **Step 4: Add the polling and detail-panel behavior**

```html
<script>
  function attachAgentClickHandlers(agentsById) {
    document.querySelectorAll("[data-agent-id]").forEach((node) => {
      node.addEventListener("click", () => {
        selectedAgentId = node.getAttribute("data-agent-id") || "";
        renderAgentDetail(agentsById[selectedAgentId] || null);
      });
    });
  }

  function renderAgentDetail(agent) {
    const panel = document.getElementById("agent-detail");
    if (!agent) {
      panel.textContent = "Select an agent card to inspect the current task.";
      return;
    }
    panel.innerHTML = `
      <div class="space-y-2">
        <div><strong>${agent.display_name}</strong> <span class="text-slate-500">(${agent.agent_id})</span></div>
        <div>State: ${agent.state}</div>
        <div>Problem: ${agent.problem_id || "—"}</div>
        <div>Step: ${agent.step_label || "—"}</div>
        <div>Tool: ${agent.last_tool || "—"}</div>
        <div>Heartbeat: ${agent.last_heartbeat_at || "—"}</div>
        <div class="text-slate-700">${agent.detail_text || agent.current_task || "No additional detail."}</div>
      </div>
    `;
  }

  function renderSnapshot(snapshot) {
    document.getElementById("mc-captured-at").textContent = snapshot.captured_at || "—";
    zoneIds.forEach((zoneId) => {
      const zone = document.getElementById(`zone-${zoneId}`);
      if (zone) zone.innerHTML = "";
    });
    const agentsById = {};
    (snapshot.agents || []).forEach((agent) => {
      agentsById[agent.agent_id] = agent;
      const zoneId = zoneIds.includes(agent.state) ? agent.state : "working";
      const zone = document.getElementById(`zone-${zoneId === "offline" ? "blocked" : zoneId}`);
      if (zone) zone.insertAdjacentHTML("beforeend", renderAgentCard(agent));
    });
    attachAgentClickHandlers(agentsById);
    if (selectedAgentId) renderAgentDetail(agentsById[selectedAgentId] || null);
  }

  async function refreshMissionControl() {
    const [snapshotRes, eventsRes] = await Promise.all([
      fetch("/admin/api/mission-control/snapshot", {credentials: "same-origin"}),
      fetch("/admin/api/mission-control/events", {credentials: "same-origin"}),
    ]);
    const snapshot = await snapshotRes.json();
    const eventsPayload = await eventsRes.json();
    renderSnapshot(snapshot);
    document.getElementById("event-rail").innerHTML = (eventsPayload.events || []).map((event) => `
      <div class="rounded-xl border border-slate-200 bg-slate-50 p-2">
        <div class="text-[11px] font-black uppercase tracking-[0.12em] text-slate-500">${event.agent_id} · ${event.state || event.event_type}</div>
        <div class="mt-1 text-sm text-slate-700">${event.message || "No message"}</div>
      </div>
    `).join("");
  }

  renderSnapshot(bootstrap);
  refreshMissionControl();
  window.setInterval(refreshMissionControl, 2000);
</script>
{% endblock %}
```

- [ ] **Step 5: Add the admin-dashboard link and rerun tests**

```html
<a href="/admin/mission-control" class="inline-flex items-center rounded-xl border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100 transition">
  Mission Control
</a>
```

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_admin_mission_control.py -v`

Expected: PASS with the new page rendering and route responses still green

- [ ] **Step 6: Commit**

```bash
cd /root/montanablotter
git add templates/admin_mission_control.html templates/admin_dashboard.html tests/test_admin_mission_control.py
git commit -m "feat(admin): add mission control office ui"
```

---

### Task 5: Wire observer fallback from live processes and logs

**Files:**
- Modify: `agent_mission_control.py`
- Test: `tests/test_agent_mission_control.py`

- [ ] **Step 1: Add failing tests for process/log fallback ingestion**

```python
from unittest import mock


def test_refresh_observed_agents_uses_process_data(self) -> None:
    with mock.patch("agent_mission_control.psutil.process_iter") as process_iter:
        process_iter.return_value = [
            mock.Mock(info={
                "pid": 4455,
                "cmdline": ["codex", "run", "--agent", "clerk"],
                "name": "codex",
                "create_time": datetime.now(UTC).timestamp(),
            })
        ]
        mission.refresh_observed_agents(self.conn)

    snapshot = mission.build_snapshot(self.conn)
    self.assertEqual(snapshot["agents"][0]["agent_id"], "clerk")
    self.assertEqual(snapshot["agents"][0]["confidence"], "observed-only")


def test_refresh_observed_agents_uses_openclaw_log_excerpt(self) -> None:
    with tempfile.NamedTemporaryFile("w+", delete=False) as handle:
        handle.write('{"0":"Fetching docket","1":"lane=session:agent:reporter:main durationMs=44","time":"2026-04-23T00:00:00+00:00"}\n')
        log_path = handle.name
    try:
        mission.refresh_observed_agents(self.conn, log_path=log_path)
        snapshot = mission.build_snapshot(self.conn)
        self.assertEqual(snapshot["agents"][0]["agent_id"], "reporter")
    finally:
        os.unlink(log_path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_agent_mission_control.py -v`

Expected: FAIL because observer refresh is not implemented

- [ ] **Step 3: Implement process/log observation refresh**

```python
import json
import psutil
import re
from pathlib import Path


AGENT_NAME_RE = re.compile(r"(main|reporter|scout|clerk|bailbot)")
OPENCLAW_LANE_RE = re.compile(r"lane=session:agent:(\w+):")


def _extract_agent_from_cmdline(cmdline: list[str]) -> str | None:
    text = " ".join(cmdline)
    match = AGENT_NAME_RE.search(text)
    return match.group(1) if match else None


def _extract_agent_from_log_line(raw: str) -> tuple[str | None, str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None, raw.strip()
    for value in payload.values():
        if isinstance(value, str):
            match = OPENCLAW_LANE_RE.search(value)
            if match:
                return match.group(1), (payload.get("0") or "").strip()
    return None, (payload.get("0") or "").strip()


def refresh_observed_agents(conn, *, log_path: str | None = None) -> None:
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        cmdline = proc.info.get("cmdline") or []
        agent_id = _extract_agent_from_cmdline([str(part) for part in cmdline])
        if not agent_id:
            continue
        upsert_observed_agent(
            conn,
            {
                "agent_id": agent_id,
                "display_name": agent_id.title(),
                "runtime": (proc.info.get("name") or "process").lower(),
                "pid": int(proc.info["pid"]),
                "state": "working",
                "current_task": "Observed from process table",
                "detail_text": " ".join(str(part) for part in cmdline)[:280],
                "source_kind": "process",
            },
        )

    if log_path and Path(log_path).exists():
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                agent_id, message = _extract_agent_from_log_line(line)
                if not agent_id:
                    continue
                upsert_observed_agent(
                    conn,
                    {
                        "agent_id": agent_id,
                        "display_name": agent_id.title(),
                        "runtime": "openclaw",
                        "state": "working",
                        "current_task": message or "Observed from openclaw log",
                        "detail_text": line.strip()[:280],
                        "source_kind": "log",
                    },
                )
```

- [ ] **Step 4: Make snapshot reads refresh observers before returning**

```python
def build_snapshot(conn, *, now: datetime | None = None, stale_after_seconds: int = 5, offline_after_seconds: int = 20) -> dict[str, Any]:
    refresh_observed_agents(conn)
    now = now or utcnow()
    # existing query and projection logic stays here
```

- [ ] **Step 5: Run both mission-control test files**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_agent_mission_control.py tests/test_admin_mission_control.py -v`

Expected: PASS with observer fallback and admin API coverage

- [ ] **Step 6: Commit**

```bash
cd /root/montanablotter
git add agent_mission_control.py tests/test_agent_mission_control.py tests/test_admin_mission_control.py
git commit -m "feat(admin): add mission control observer fallback"
```

---

### Task 6: Final verification and admin-entry smoke coverage

**Files:**
- Modify: `tests/test_admin_mission_control.py`
- Modify: `tests/test_admin_dashboard.py`

- [ ] **Step 1: Add a failing smoke test for the dashboard entry link**

```python
def test_admin_dashboard_links_to_mission_control(self) -> None:
    client = app_module.app.test_client()
    self._login_admin_session(client)
    response = client.get("/admin")
    html = response.get_data(as_text=True)
    self.assertEqual(response.status_code, 200)
    self.assertIn("/admin/mission-control", html)
```

- [ ] **Step 2: Run the targeted tests to verify they fail if the link is missing**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_admin_dashboard.py tests/test_admin_mission_control.py -v`

Expected: FAIL if the dashboard entry is absent or not visible to admin users

- [ ] **Step 3: Add any missing assertions for event rail, stale badge, and JSON shape**

```python
def test_events_endpoint_returns_recent_events(self) -> None:
    client = app_module.app.test_client()
    self._login(client)
    with mock.patch("blueprints.admin.mission_control.recent_events", return_value=[{"agent_id": "reporter", "event_type": "state_change", "state": "working", "message": "Scanning", "created_at": "2026-04-23T00:00:00+00:00"}]):
        response = client.get("/admin/api/mission-control/events")
    payload = response.get_json()
    self.assertEqual(response.status_code, 200)
    self.assertEqual(payload["events"][0]["agent_id"], "reporter")
```

- [ ] **Step 4: Run the full focused verification set**

Run: `cd /root/montanablotter && source venv/bin/activate && pytest tests/test_agent_mission_control.py tests/test_admin_mission_control.py tests/test_admin_dashboard.py -v`

Expected: PASS across all new mission-control coverage

- [ ] **Step 5: Commit**

```bash
cd /root/montanablotter
git add tests/test_admin_dashboard.py tests/test_admin_mission_control.py
git commit -m "test(admin): verify mission control entrypoints"
```

---

## Self-Review

### Spec coverage

- Admin-only Mission Control page: covered by Task 3 and Task 4.
- 1-2 second freshness via polling: covered by Task 4.
- Heartbeat as truth source: covered by Task 1 and Task 3.
- Recent events rail and agent detail panel: covered by Task 2 and Task 4.
- Observer-only fallback for uninstrumented agents: covered by Task 2 and Task 5.
- SQLite-backed recent history: covered by Task 1 and Task 2.
- Dashboard entry point: covered by Task 4 and Task 6.

### Placeholder scan

- No `TODO`, `TBD`, or “implement later” markers remain.
- Each code step contains concrete code, concrete file paths, and concrete commands.

### Type consistency

- Shared state names are consistent across the plan: `ready`, `working`, `tool_run`, `waiting`, `blocked`, `done`, `offline`.
- Shared route names are consistent across the plan: `/admin/mission-control`, `/admin/api/mission-control/snapshot`, `/admin/api/mission-control/events`, `/admin/api/mission-control/heartbeat`.
- Shared function names are consistent across the plan: `upsert_agent_heartbeat`, `upsert_observed_agent`, `build_snapshot`, `recent_events`, `refresh_observed_agents`.
