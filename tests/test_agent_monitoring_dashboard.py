import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class AgentMonitoringDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-agent-dashboard-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

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

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_agent_monitoring_dashboard_renders_full_page(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/monitoring/agents')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Hermes Agent Monitoring Dashboard', html)
        self.assertIn('Live Thought Stream', html)
        self.assertIn('Data Pipeline', html)
        self.assertIn('Manual Intervention', html)
        self.assertIn('agent-dashboard-root', html)
        self.assertIn('/api/monitoring/agents/bootstrap', html)
        self.assertIn('/ws/agents', html)


if __name__ == '__main__':
    unittest.main()
