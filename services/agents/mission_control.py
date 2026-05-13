from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

import psutil


VALID_STATES = {"ready", "working", "tool_run", "waiting", "blocked", "done", "offline"}
OBSERVED_STATE_MAP = {
    "active": "working",
    "idle": "waiting",
    "seen": "ready",
    "unknown": "ready",
}
_SNAPSHOT_COLUMNS = (
    "agent_id",
    "display_name",
    "runtime",
    "pid",
    "session_id",
    "state",
    "current_task",
    "problem_id",
    "step_label",
    "last_tool",
    "detail_text",
    "source_kind",
    "confidence",
    "last_heartbeat_at",
    "state_started_at",
    "updated_at",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _normalized_state(value: str | None) -> str:
    state = (value or "").strip().lower()
    return state if state in VALID_STATES else "working"


def _row_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key]
    return row[index]


def _observed_agent_snapshot() -> dict[str, Any]:
    from blueprints.admin import agents as admin_agents

    return admin_agents._agent_snapshot()


def ensure_agent_mission_control_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS agent_runtime_state (
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
        )
        '''
    )
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS agent_runtime_events (
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
        )
        '''
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_agent_runtime_events_agent_created '
        'ON agent_runtime_events(agent_id, created_at DESC)'
    )


def _append_event(
    conn: sqlite3.Connection,
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
        (
            agent_id,
            event_type,
            state,
            message,
            problem_id,
            tool_name,
            source_kind,
            raw_excerpt,
            utcnow().isoformat(),
        ),
    )


def upsert_agent_heartbeat(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    now = utcnow().isoformat()
    state = _normalized_state(payload.get("state"))
    existing = conn.execute(
        """
        SELECT state
        FROM agent_runtime_state
        WHERE agent_id = ?
        """,
        (payload["agent_id"],),
    ).fetchone()
    previous_state = _row_value(existing, "state", 0) if existing is not None else None
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
            state_started_at = CASE
                WHEN agent_runtime_state.state = excluded.state THEN agent_runtime_state.state_started_at
                ELSE excluded.state_started_at
            END,
            updated_at = excluded.updated_at
        """,
        (
            payload["agent_id"],
            payload.get("display_name") or payload["agent_id"].replace("_", " ").title(),
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
    if previous_state is not None and previous_state != state:
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


def upsert_observed_agent(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    now = utcnow().isoformat()
    state = _normalized_state(payload.get("state"))
    existing = conn.execute(
        """
        SELECT confidence
        FROM agent_runtime_state
        WHERE agent_id = ?
        """,
        (payload["agent_id"],),
    ).fetchone()
    if existing is not None and _row_value(existing, "confidence", 0) == "heartbeat":
        return
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
            state_started_at = CASE
                WHEN agent_runtime_state.state = excluded.state THEN agent_runtime_state.state_started_at
                ELSE excluded.state_started_at
            END,
            updated_at = excluded.updated_at
        """,
        (
            payload["agent_id"],
            payload.get("display_name") or payload["agent_id"].replace("_", " ").title(),
            payload.get("runtime") or "codex",
            payload.get("pid"),
            payload.get("session_id"),
            state,
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


def refresh_observed_agents(
    conn: sqlite3.Connection,
    snapshot: dict[str, Any] | None = None,
    *,
    include_processes: bool = True,
) -> int:
    observed = snapshot or _observed_agent_snapshot()
    agents = observed.get("agents") or {}
    count = 0
    for agent_id, info in agents.items():
        status = (info.get("status") or "").strip().lower()
        last_seen = (info.get("last_seen") or "").strip()
        last_msg = (info.get("last_msg") or "").strip()
        if status == "unknown" and not last_seen and not last_msg:
            continue
        state = OBSERVED_STATE_MAP.get(status, "ready")
        upsert_observed_agent(
            conn,
            {
                "agent_id": agent_id,
                "display_name": agent_id.replace("_", " ").title(),
                "runtime": "openclaw",
                "state": state,
                "current_task": last_msg or f"Observed via {status or 'observer'} snapshot",
                "detail_text": last_msg or "",
                "source_kind": "process",
                "state_started_at": last_seen or utcnow().isoformat(),
            },
        )
        count += 1

    if not include_processes:
        return count

    now = utcnow()
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue
            cmd_text = " ".join(str(part) for part in cmdline).lower()
            name = (proc.info.get("name") or "").lower()
            if (name == "codex" or "codex/codex" in cmd_text) and "grep" not in cmd_text:
                pid = int(proc.info["pid"])
                started_at = datetime.fromtimestamp(float(proc.info.get("create_time") or 0), tz=UTC)
                upsert_observed_agent(
                    conn,
                    {
                        "agent_id": f"codex_{pid}",
                        "display_name": f"Codex Worker {pid}",
                        "runtime": "codex",
                        "pid": pid,
                        "state": "working",
                        "current_task": "Observed codex process running",
                        "detail_text": " ".join(str(part) for part in cmdline)[:200],
                        "source_kind": "process",
                        "state_started_at": started_at.isoformat(),
                    },
                )
                count += 1
            elif "hermes_cli.main" in cmd_text and "grep" not in cmd_text:
                pid = int(proc.info["pid"])
                started_at = datetime.fromtimestamp(float(proc.info.get("create_time") or 0), tz=UTC)
                upsert_observed_agent(
                    conn,
                    {
                        "agent_id": f"hermes_{pid}",
                        "display_name": f"Hermes Agent {pid}",
                        "runtime": "hermes",
                        "pid": pid,
                        "state": "working",
                        "current_task": "Observed hermes process running",
                        "detail_text": " ".join(str(part) for part in cmdline)[:200],
                        "source_kind": "process",
                        "state_started_at": started_at.isoformat(),
                    },
                )
                count += 1
        except Exception:
            continue

    return count


def recent_events(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            id,
            agent_id,
            event_type,
            state,
            message,
            problem_id,
            tool_name,
            source_kind,
            raw_excerpt,
            created_at
        FROM agent_runtime_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [
        {
            "id": _row_value(row, "id", 0),
            "agent_id": _row_value(row, "agent_id", 1),
            "event_type": _row_value(row, "event_type", 2),
            "state": _row_value(row, "state", 3) or "",
            "message": _row_value(row, "message", 4) or "",
            "problem_id": _row_value(row, "problem_id", 5) or "",
            "tool_name": _row_value(row, "tool_name", 6) or "",
            "source_kind": _row_value(row, "source_kind", 7),
            "raw_excerpt": _row_value(row, "raw_excerpt", 8) or "",
            "created_at": _row_value(row, "created_at", 9),
        }
        for row in rows
    ]


def build_snapshot(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    stale_after_seconds: int = 5,
    offline_after_seconds: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    current_time = now or utcnow()
    existing_count = conn.execute("SELECT COUNT(*) FROM agent_runtime_state").fetchone()[0]
    if not existing_count:
        refresh_observed_agents(conn, include_processes=False)
    rows = conn.execute(
        """
        SELECT
            agent_id,
            display_name,
            runtime,
            pid,
            session_id,
            state,
            current_task,
            problem_id,
            step_label,
            last_tool,
            detail_text,
            source_kind,
            confidence,
            last_heartbeat_at,
            state_started_at,
            updated_at
        FROM agent_runtime_state
        ORDER BY agent_id
        """
    ).fetchall()

    agents: list[dict[str, Any]] = []
    for row in rows:
        record = {
            column: _row_value(row, column, index)
            for index, column in enumerate(_SNAPSHOT_COLUMNS)
        }
        activity_age_seconds = None
        last_heartbeat_at = record["last_heartbeat_at"]
        heartbeat_age_seconds = None
        if last_heartbeat_at:
            heartbeat_age_seconds = max(
                0.0,
                (current_time - datetime.fromisoformat(last_heartbeat_at)).total_seconds(),
            )
            activity_age_seconds = heartbeat_age_seconds
        elif record["confidence"] == "observed-only" and record["updated_at"]:
            activity_age_seconds = max(
                0.0,
                (current_time - datetime.fromisoformat(record["updated_at"])).total_seconds(),
            )

        stale = activity_age_seconds is not None and activity_age_seconds > stale_after_seconds
        state = record["state"]
        if activity_age_seconds is not None and activity_age_seconds > offline_after_seconds:
            state = "offline"

        agents.append(
            {
                "agent_id": record["agent_id"],
                "display_name": record["display_name"],
                "runtime": record["runtime"],
                "pid": record["pid"],
                "session_id": record["session_id"],
                "state": state,
                "current_task": record["current_task"],
                "problem_id": record["problem_id"],
                "step_label": record["step_label"],
                "last_tool": record["last_tool"],
                "detail_text": record["detail_text"],
                "source_kind": record["source_kind"],
                "confidence": record["confidence"],
                "last_heartbeat_at": record["last_heartbeat_at"],
                "state_started_at": record["state_started_at"],
                "updated_at": record["updated_at"],
                "heartbeat_age_seconds": heartbeat_age_seconds,
                "stale": stale,
            }
        )

    return {"agents": agents}
