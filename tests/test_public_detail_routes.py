import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class PublicDetailRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-public-detail-', suffix='.db')
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
        conn = sqlite3.connect(self.db_path)
        for statement in [
            "ALTER TABLE bail_ad_orders ADD COLUMN simulator_logo_path TEXT",
            "ALTER TABLE bail_ad_orders ADD COLUMN simulator_target_url TEXT",
        ]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_public_report(self, audit_status: str = 'clean') -> tuple[int, int]:
        conn = app_module.get_db()
        blotter_id = conn.execute(
            """
            INSERT INTO blotters (
                filename, county, status, file_path, source_type
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                'yellowstone-daily-report.pdf',
                'Yellowstone',
                'processed',
                '/root/montanablotter/uploads/yellowstone-daily-report.pdf',
                'local_pdf',
            ),
        ).lastrowid
        post_id = conn.execute(
            """
            INSERT INTO posts (
                record_id, blotter_id, title, summary, city, county, agency_type,
                agency_name, incident_date, incident_type, created_at, audit_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                blotter_id,
                'Daily Activity Report - Yellowstone',
                'Lead summary line.\nSecond summary line.',
                'Billings',
                'Yellowstone',
                'police',
                'Billings Police Department',
                '2026-04-10',
                'Theft',
                '2026-04-10 08:00:00',
                audit_status,
            ),
        ).lastrowid
        record_id = conn.execute(
            """
            INSERT INTO records (
                blotter_id, cfs_number, date, time, incident, incident_type, location,
                details, county, officer, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                'CFS26-1001',
                '2026-04-10',
                '09:15',
                'Theft',
                'Theft',
                '123 Main St',
                'Caller reported a theft from a parked vehicle.',
                'Yellowstone',
                'D12',
                '2026-04-10 09:20:00',
            ),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO command_logs (record_id, timestamp, officer, entry)
            VALUES (?, ?, ?, ?)
            """,
            (
                record_id,
                '2026-04-10 09:30:00',
                'D12',
                'Officer documented the scene and collected witness information.',
            ),
        )
        conn.execute(
            """
            INSERT INTO charge_explainers (
                incident_type, slug, title, body, excerpt, charge_category, published
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'Theft',
                'theft',
                'Theft in Montana',
                'Reference body',
                'Reference excerpt',
                'Property',
                1,
            ),
        )
        conn.commit()
        conn.close()
        return int(post_id), int(record_id)

    def test_post_detail_route_renders_existing_post(self) -> None:
        post_id, _ = self._seed_public_report()

        client = app_module.app.test_client()
        response = client.get(f'/post/{post_id}')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Daily Activity Report - Yellowstone', html)
        self.assertIn('Incident Log Behind This Report', html)
        self.assertIn('/record/', html)
        self.assertIn('/laws/charge/theft', html)
        self.assertIn('Open Original PDF', html)
        self.assertIn('/uploads/yellowstone-daily-report.pdf', html)
        self.assertNotIn('123 Main St', html)
        self.assertIn('[redacted home address]', html)

    def test_record_detail_route_renders_existing_record(self) -> None:
        post_id, record_id = self._seed_public_report()

        client = app_module.app.test_client()
        response = client.get(f'/record/{record_id}')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Incident Record', html)
        self.assertIn('Command Log', html)
        self.assertIn('/post/' + str(post_id), html)
        self.assertIn('Officer documented the scene', html)
        self.assertIn('Open Source PDF', html)
        self.assertIn('/uploads/yellowstone-daily-report.pdf', html)
        self.assertNotIn('123 Main St', html)
        self.assertIn('[redacted home address]', html)

    def test_pending_post_and_records_are_not_public(self) -> None:
        post_id, record_id = self._seed_public_report(audit_status='pending')

        client = app_module.app.test_client()
        post_response = client.get(f'/post/{post_id}')
        record_response = client.get(f'/record/{record_id}')

        self.assertEqual(post_response.status_code, 404)
        self.assertEqual(record_response.status_code, 404)

    def test_missing_route_uses_public_theme_shell(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/this-route-does-not-exist')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 404)
        self.assertIn('public-redesign.css', html)
        self.assertIn('public-error-card', html)
        self.assertIn('Record Not Found', html)


if __name__ == '__main__':
    unittest.main()
