import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

import services.agents.mission_control as mission


class MissionControlServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-mission-control-", suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        mission.ensure_agent_mission_control_schema(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        os.unlink(self.db_path)

    def test_upsert_agent_heartbeat_persists_live_state_with_heartbeat_confidence(self) -> None:
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
            """
            SELECT agent_id, state, current_task, confidence
            FROM agent_runtime_state
            WHERE agent_id = ?
            """,
            ("reporter",),
        ).fetchone()

        self.assertEqual(row[0], "reporter")
        self.assertEqual(row[1], "working")
        self.assertEqual(row[2], "Scanning Gallatin feed")
        self.assertEqual(row[3], "heartbeat")

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
        mission.upsert_agent_heartbeat(
            self.conn,
            {
                "agent_id": "scout",
                "display_name": "Scout",
                "runtime": "openclaw",
                "state": "tool_run",
                "current_task": "Reading county docket",
                "source_kind": "heartbeat",
            },
        )

        events = self.conn.execute(
            """
            SELECT event_type, state, source_kind
            FROM agent_runtime_events
            WHERE agent_id = ?
            ORDER BY id ASC
            """,
            ("scout",),
        ).fetchall()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "state_change")
        self.assertEqual(events[0]["state"], "tool_run")
        self.assertEqual(events[0]["source_kind"], "heartbeat")

    def test_upsert_agent_heartbeat_same_state_does_not_write_extra_state_change_event(self) -> None:
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
        mission.upsert_agent_heartbeat(
            self.conn,
            {
                "agent_id": "scout",
                "display_name": "Scout",
                "runtime": "openclaw",
                "state": "working",
                "current_task": "Still reading county docket",
                "source_kind": "heartbeat",
            },
        )

        event_count = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM agent_runtime_events
            WHERE agent_id = ?
            """,
            ("scout",),
        ).fetchone()[0]

        self.assertEqual(event_count, 0)

    def test_build_snapshot_marks_stale_but_keeps_state_before_offline_threshold(self) -> None:
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

        snapshot = mission.build_snapshot(
            self.conn,
            now=now,
            stale_after_seconds=5,
            offline_after_seconds=20,
        )

        self.assertEqual(len(snapshot["agents"]), 1)
        self.assertTrue(snapshot["agents"][0]["stale"])
        self.assertEqual(snapshot["agents"][0]["state"], "working")

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

        self.assertEqual(len(snapshot["agents"]), 1)
        self.assertEqual(snapshot["agents"][0]["confidence"], "observed-only")
        self.assertEqual(snapshot["agents"][0]["state"], "working")
        self.assertIsNone(snapshot["agents"][0]["last_heartbeat_at"])

    def test_build_snapshot_marks_observer_only_agents_stale_and_offline_by_updated_age(self) -> None:
        now = datetime.now(UTC)
        self.conn.execute(
            """
            INSERT INTO agent_runtime_state (
                agent_id, display_name, runtime, state, current_task, source_kind, confidence,
                last_heartbeat_at, state_started_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "spotter",
                "Spotter",
                "codex",
                "working",
                "Watching output",
                "process",
                "observed-only",
                None,
                (now - timedelta(seconds=40)).isoformat(),
                (now - timedelta(seconds=25)).isoformat(),
            ),
        )
        self.conn.commit()

        snapshot = mission.build_snapshot(
            self.conn,
            now=now,
            stale_after_seconds=5,
            offline_after_seconds=20,
        )

        self.assertEqual(len(snapshot["agents"]), 1)
        self.assertTrue(snapshot["agents"][0]["stale"])
        self.assertEqual(snapshot["agents"][0]["state"], "offline")
        self.assertEqual(snapshot["agents"][0]["confidence"], "observed-only")

    def test_upsert_observed_agent_clears_inherited_heartbeat_timestamp(self) -> None:
        old_heartbeat = datetime.now(UTC) - timedelta(seconds=30)
        mission.upsert_agent_heartbeat(
            self.conn,
            {
                "agent_id": "watcher",
                "display_name": "Watcher",
                "runtime": "openclaw",
                "state": "blocked",
                "current_task": "Waiting on heartbeat",
                "source_kind": "heartbeat",
            },
        )
        self.conn.execute(
            """
            UPDATE agent_runtime_state
            SET last_heartbeat_at = ?, state_started_at = ?, updated_at = ?
            WHERE agent_id = ?
            """,
            (
                old_heartbeat.isoformat(),
                old_heartbeat.isoformat(),
                old_heartbeat.isoformat(),
                "watcher",
            ),
        )
        self.conn.commit()

        mission.upsert_observed_agent(
            self.conn,
            {
                "agent_id": "watcher",
                "display_name": "Watcher",
                "runtime": "codex",
                "state": "working",
                "current_task": "Observed in shell",
                "source_kind": "process",
            },
        )

        row = self.conn.execute(
            """
            SELECT confidence, last_heartbeat_at
            FROM agent_runtime_state
            WHERE agent_id = ?
            """,
            ("watcher",),
        ).fetchone()
        snapshot = mission.build_snapshot(
            self.conn,
            now=datetime.now(UTC),
            stale_after_seconds=5,
            offline_after_seconds=20,
        )

        self.assertEqual(row["confidence"], "heartbeat")
        self.assertIsNotNone(row["last_heartbeat_at"])
        self.assertEqual(snapshot["agents"][0]["state"], "offline")
        self.assertTrue(snapshot["agents"][0]["stale"])
        self.assertGreater(snapshot["agents"][0]["heartbeat_age_seconds"], 20)

    def test_build_snapshot_overrides_state_to_offline_after_offline_threshold(self) -> None:
        now = datetime.now(UTC)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            INSERT INTO agent_runtime_state (
                agent_id, display_name, runtime, state, source_kind, confidence,
                last_heartbeat_at, state_started_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "observer",
                "Observer",
                "codex",
                "blocked",
                "heartbeat",
                "heartbeat",
                (now - timedelta(seconds=25)).isoformat(),
                (now - timedelta(minutes=2)).isoformat(),
                now.isoformat(),
            ),
        )
        self.conn.commit()

        snapshot = mission.build_snapshot(
            self.conn,
            now=now,
            stale_after_seconds=5,
            offline_after_seconds=20,
        )

        self.assertEqual(len(snapshot["agents"]), 1)
        self.assertTrue(snapshot["agents"][0]["stale"])
        self.assertEqual(snapshot["agents"][0]["state"], "offline")

    def test_build_snapshot_backfills_observed_agents_when_no_heartbeat_rows_exist(self) -> None:
        now = datetime.now(UTC)
        with mock.patch(
            "agent_mission_control._observed_agent_snapshot",
            return_value={
                "agents": {
                    "reporter": {
                        "status": "active",
                        "last_seen": (now - timedelta(seconds=10)).isoformat(),
                        "last_msg": "Reporter finished county scan",
                    }
                }
            },
        ):
            snapshot = mission.build_snapshot(self.conn, now=now)

        self.assertEqual(len(snapshot["agents"]), 1)
        self.assertEqual(snapshot["agents"][0]["agent_id"], "reporter")
        self.assertEqual(snapshot["agents"][0]["confidence"], "observed-only")
        self.assertEqual(snapshot["agents"][0]["state"], "working")
        self.assertEqual(snapshot["agents"][0]["current_task"], "Reporter finished county scan")

    def test_recent_events_returns_latest_first_with_payload_shape(self) -> None:
        mission.upsert_agent_heartbeat(
            self.conn,
            {
                "agent_id": "runner",
                "display_name": "Runner",
                "runtime": "openclaw",
                "state": "working",
                "current_task": "Initial task",
                "source_kind": "heartbeat",
            },
        )
        mission.upsert_agent_heartbeat(
            self.conn,
            {
                "agent_id": "runner",
                "display_name": "Runner",
                "runtime": "openclaw",
                "state": "done",
                "current_task": "Finished task",
                "source_kind": "heartbeat",
                "problem_id": "case-9",
                "last_tool": "curl",
                "detail_text": "All finished",
            },
        )

        events = mission.recent_events(self.conn, limit=10)

        self.assertEqual(len(events), 1)
        self.assertEqual(
            set(events[0].keys()),
            {
                "id",
                "agent_id",
                "event_type",
                "state",
                "message",
                "problem_id",
                "tool_name",
                "source_kind",
                "raw_excerpt",
                "created_at",
            },
        )
        self.assertEqual(events[0]["agent_id"], "runner")
        self.assertEqual(events[0]["event_type"], "state_change")
        self.assertEqual(events[0]["state"], "done")
        self.assertEqual(events[0]["message"], "Finished task")
        self.assertEqual(events[0]["problem_id"], "case-9")
        self.assertEqual(events[0]["tool_name"], "curl")
        self.assertEqual(events[0]["source_kind"], "heartbeat")
        self.assertEqual(events[0]["raw_excerpt"], "All finished")

    def test_recent_events_orders_descending_and_applies_limit(self) -> None:
        for state in ("working", "tool_run", "done"):
            mission.upsert_agent_heartbeat(
                self.conn,
                {
                    "agent_id": "runner",
                    "display_name": "Runner",
                    "runtime": "openclaw",
                    "state": state,
                    "current_task": f"State {state}",
                    "source_kind": "heartbeat",
                },
            )

        events = mission.recent_events(self.conn, limit=2)

        self.assertEqual(len(events), 2)
        self.assertEqual([event["state"] for event in events], ["done", "tool_run"])


if __name__ == "__main__":
    unittest.main()
