"""Public /court-sources transparency page — renders source state to readers."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

import app as app_module
import config
import init_db
from services.court.tracker import ensure_court_tracker_schema


class CourtSourcesPublicPageTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-courtsrc-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        bootstrap = sqlite3.connect(self.db_path)
        bootstrap.execute(
            '''
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            '''
        )
        bootstrap.commit()
        bootstrap.close()

        init_db.init_database()
        init_db.migrate()

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        ensure_court_tracker_schema(self.conn)
        self._seed_sources()

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_sources(self) -> None:
        recent = (datetime.utcnow() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
        stale = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        self.conn.executescript(
            f'''
            INSERT INTO court_sources
              (slug, name, provider_type, source_url, status,
               last_scraped_at, last_success_at, last_error)
            VALUES
              ('supreme_court_daily',
               'Montana Supreme Court Daily Opinions',
               'document_feed',
               'https://supremecourt.mt.gov/',
               'active',
               '{recent}', '{recent}', NULL),
              ('district_court_calendar',
               'Montana District Court Calendar',
               'court_calendar',
               'https://dcportal.pubcourts.mt.gov/',
               'active',
               '{recent}', '{stale}',
               'ERR_CONNECTION_RESET 522 from dcportal.pubcourts.mt.gov'),
              ('colj_calendar',
               'Montana Courts of Limited Jurisdiction Calendar',
               'court_calendar',
               'https://coljportal.mt.gov/',
               'active',
               '{recent}', '{stale}', NULL);
            '''
        )
        self.conn.commit()

    def test_page_renders_and_lists_all_sources(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/court-sources')
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('Where our court data', html)
        self.assertIn('Montana Supreme Court Daily Opinions', html)
        self.assertIn('Montana District Court Calendar', html)

    def test_page_shows_blocked_badge_for_blocked_source(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/court-sources')
        html = r.get_data(as_text=True)
        self.assertIn('Blocked', html)
        self.assertIn('ERR_CONNECTION_RESET', html)

    def test_page_shows_known_gaps_disclosure(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/court-sources')
        html = r.get_data(as_text=True)
        self.assertIn('Known gaps', html)
        self.assertIn('District Court criminal outcomes', html)
        self.assertIn('appellate-only', html)

    def test_page_section_counts(self) -> None:
        client = app_module.app.test_client()
        r = client.get('/court-sources')
        html = r.get_data(as_text=True)
        self.assertIn('1 working', html)
        self.assertIn('1 stale', html)
        self.assertIn('1 blocked or paused', html)

    def test_empty_state_renders(self) -> None:
        self.conn.execute('DELETE FROM court_sources')
        self.conn.commit()
        client = app_module.app.test_client()
        r = client.get('/court-sources')
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('No court sources registered yet', html)


if __name__ == '__main__':
    unittest.main()
