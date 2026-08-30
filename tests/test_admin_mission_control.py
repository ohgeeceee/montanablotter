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
        fd, self.db_path = tempfile.mkstemp(prefix="mb-admin-mission-control-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH
        self.previous_testing = app_module.app.config.get("TESTING")

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        bootstrap_conn.commit()
        bootstrap_conn.close()

        init_db.init_database()
        init_db.migrate()
        self.admin_user_id = self._create_admin_user()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        app_module.app.config["TESTING"] = self.previous_testing
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_admin_user(self) -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("mission-admin", "not-used-in-tests", "mission@example.com", "ops", 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login(self, client) -> None:
        with client.session_transaction() as session:
            session["_user_id"] = str(self.admin_user_id)
            session["_fresh"] = True
            session["_csrf_token"] = "test-csrf-token"

    def test_mission_control_page_requires_login(self) -> None:
        client = app_module.app.test_client()

        response = client.get("/admin/mission-control")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])

    def test_runbook_page_requires_login(self) -> None:
        client = app_module.app.test_client()

        response = client.get("/admin/mission-control/runbook")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])

    def test_snapshot_endpoint_returns_agents_payload(self) -> None:
        client = app_module.app.test_client()
        self._login(client)
        expected_payload = {
            "captured_at": "2026-04-23T00:00:00+00:00",
            "agents": [
                {
                    "agent_id": "reporter",
                    "display_name": "Reporter",
                    "state": "working",
                }
            ],
        }

        with mock.patch(
            "blueprints.admin.mission_control.build_snapshot",
            return_value=expected_payload,
        ):
            response = client.get("/admin/api/mission-control/snapshot")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload, expected_payload)

    def test_events_endpoint_returns_recent_events(self) -> None:
        client = app_module.app.test_client()
        self._login(client)

        with mock.patch(
            "blueprints.admin.mission_control.recent_events",
            return_value=[
                {
                    "agent_id": "reporter",
                    "event_type": "state_change",
                    "state": "working",
                    "message": "Scanning",
                    "created_at": "2026-04-23T00:00:00+00:00",
                }
            ],
        ):
            response = client.get("/admin/api/mission-control/events")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["events"][0]["agent_id"], "reporter")

    def test_local_heartbeat_endpoint_persists_payload(self) -> None:
        client = app_module.app.test_client()
        heartbeat = {"agent_id": "reporter", "state": "working"}

        with mock.patch("blueprints.admin.mission_control.upsert_agent_heartbeat") as upsert_heartbeat:
            response = client.post(
                "/admin/api/mission-control/heartbeat",
                data=json.dumps(heartbeat),
                content_type="application/json",
                headers={"X-Internal-Mission-Control": "local"},
            )

        self.assertEqual(response.status_code, 202)
        upsert_heartbeat.assert_called_once()
        self.assertEqual(upsert_heartbeat.call_args.args[1], heartbeat)

    def test_mission_control_page_redirects_to_command_center(self) -> None:
        client = app_module.app.test_client()
        self._login(client)

        response = client.get("/admin/mission-control")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/command-center", response.headers["Location"])

    def test_mission_control_runbook_redirects_to_command_center_runbook(self) -> None:
        client = app_module.app.test_client()
        self._login(client)

        response = client.get("/admin/mission-control/runbook")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/command-center/runbook", response.headers["Location"])

    def test_command_center_runbook_renders(self) -> None:
        client = app_module.app.test_client()
        self._login(client)

        response = client.get("/admin/command-center/runbook")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Command Center Runbook", html)
        self.assertIn("Primary Flask App", html)
        self.assertIn("systemctl status montanablotter.service", html)
        self.assertIn("curl -sSI https://montanablotter.com/admin/command-center/runbook", html)
        self.assertIn("Agent Events Sidecar", html)
        self.assertNotIn("Agent Events Service Deployment", html)
        self.assertNotIn("ops/systemd/flask-app.service", html)


if __name__ == "__main__":
    unittest.main()
