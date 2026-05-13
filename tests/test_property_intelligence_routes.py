import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class PropertyIntelligenceRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-property-intel-', suffix='.db')
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

    def test_eviction_records_route_renders_seeded_filing(self):
        conn = app_module.get_db()
        source_id = conn.execute(
            "INSERT INTO civil_filing_sources (source_key, display_name, adapter_type) VALUES (?, ?, ?)",
            ('yellowstone-import', 'Yellowstone Import', 'import_json'),
        ).lastrowid
        conn.execute(
            '''
            INSERT INTO civil_filings (
                source_id, county, city, case_number, case_type_code, filing_class, caption,
                plaintiff_name, defendant_name, raw_address, filing_date, case_status, hash_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                source_id, 'Yellowstone', 'Billings', 'UD-26-1234', 'UD', 'eviction',
                'ABC Properties LLC v. Jane Doe', 'ABC Properties LLC', 'Jane Doe',
                '123 Main St, Billings, MT 59101', '2026-05-11', 'Open', 'hash-1',
            ),
        )
        conn.commit()
        conn.close()

        response = app_module.app.test_client().get('/eviction-records')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Eviction Records', html)
        self.assertIn('UD-26-1234', html)
        self.assertIn('ABC Properties LLC', html)

    def test_property_detail_shows_civil_filings_and_rollup(self):
        conn = app_module.get_db()
        property_id = conn.execute(
            "INSERT INTO property_addresses (address_slug, street, city, state, zip, county) VALUES (?, ?, ?, ?, ?, ?)",
            ('123-main-st-billings-mt-59101', '123 Main St', 'Billings', 'MT', '59101', 'Yellowstone'),
        ).lastrowid
        source_id = conn.execute(
            "INSERT INTO civil_filing_sources (source_key, display_name, adapter_type) VALUES (?, ?, ?)",
            ('yellowstone-import', 'Yellowstone Import', 'import_json'),
        ).lastrowid
        conn.execute(
            '''
            INSERT INTO civil_filings (
                source_id, property_address_id, county, city, case_number, filing_class,
                plaintiff_name, defendant_name, raw_address, filing_date, case_status, hash_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                source_id, property_id, 'Yellowstone', 'Billings', 'UD-26-1234', 'eviction',
                'ABC Properties LLC', 'Jane Doe', '123 Main St, Billings, MT 59101',
                '2026-05-11', 'Open', 'civil-hash-1',
            ),
        )
        conn.commit()
        conn.close()

        response = app_module.app.test_client().get('/property/123-main-st-billings-mt-59101')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Civil Filings', html)
        self.assertIn('ABC Properties LLC', html)
        self.assertIn('Property Summary', html)
        self.assertIn('Civil filings', html)
        self.assertIn('This address has 1 eviction filings and 0 arrest-linked records.', html)


if __name__ == '__main__':
    unittest.main()
