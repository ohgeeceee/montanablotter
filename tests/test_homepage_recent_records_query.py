import sqlite3
import unittest
from unittest.mock import patch

import app as app_module


class HomepageRecentRecordsQueryTests(unittest.TestCase):
    def test_latest_clean_post_and_agency_filter(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.executescript('''
            CREATE TABLE posts (id INTEGER PRIMARY KEY, blotter_id INTEGER,
                title TEXT, summary TEXT, agency_name TEXT, city TEXT,
                agency_type TEXT, case_status TEXT, audit_status TEXT);
            CREATE TABLE records (id INTEGER PRIMARY KEY, blotter_id INTEGER,
                date TEXT, time TEXT, county TEXT, incident_type TEXT,
                incident TEXT, location TEXT, details TEXT);
            INSERT INTO posts VALUES
                (1, 1, 'Older clean', '', 'Test Police', '', 'police', 'active', 'clean'),
                (2, 1, 'Newest clean', '', 'Test Police', '', 'police', 'active', 'clean'),
                (3, 1, 'Unapproved', '', 'Test Police', '', 'police', 'active', 'pending'),
                (4, 2, 'Unapproved only', '', '', '', 'police', 'active', NULL);
            INSERT INTO records VALUES
                (1, 1, '09/06/26', '12:00', 'Test', 'Call', '', '', ''),
                (2, 2, '09/06/26', '12:00', 'Test', 'Call', '', '', ''),
                (3, 3, '09/06/26', '12:00', 'Test', 'Call', '', '', '');
        ''')
        with patch.object(app_module, '_homepage_recent_records_dedupe',
                          side_effect=lambda conn, rows, **kwargs: rows):
            rows = app_module._homepage_recent_records(conn, agency_type='police')
            self.assertEqual([(r['id'], r['post_id']) for r in rows], [(1, 2)])
            self.assertEqual(app_module._homepage_recent_records(
                conn, agency_type='sheriff'), [])
