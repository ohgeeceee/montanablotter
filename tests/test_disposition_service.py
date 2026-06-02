"""
Tests for the disposition lookup service + the booking→case auto-link watcher.

Covers:
- lookup_disposition() matching strategies (case_number, slug, last+first, last-only)
- booking cross-link enrichment
- watcher.link_recent_bookings() — initial link with outcome snapshot
- watcher.refresh_outcome_data() — change detection, has_outcome, notified_admin_at reset
- watcher.find_pending_notifications() + mark_notified()

All tests use a temp SQLite DB; no network or production data.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from services.disposition import lookup as disposition_lookup
from services.disposition import watcher as disposition_watcher


def _seed_fixtures(conn: sqlite3.Connection) -> dict:
    """Insert one court, two criminal cases, two jail bookings. Returns id map."""
    cur = conn.execute(
        '''
        INSERT INTO courts (slug, name, court_type, county)
        VALUES (?, ?, ?, ?)
        ''',
        ('sanders-county-dc', 'Sanders County District Court', 'district', 'Sanders'),
    )
    court_id = cur.lastrowid

    # Case 1: Wood/Danielle — has an outcome (disposition set)
    cur = conn.execute(
        '''
        INSERT INTO court_cases
            (court_id, slug, case_number, caption, status,
             defendant_name, defendant_slug, defendant_last, defendant_first,
             is_criminal, charges_text, plea, disposition, sentence_text,
             sentence_date, sentencing_judge, outcome_scraped_at, filed_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
        ''',
        (
            court_id,
            'sanders-county-dc-da-21-0260',
            'DA 21-0260',
            'State v. Wood',
            'closed',
            'Danielle Jeanette Wood',
            'wood-danielle-jeanette',
            'wood',
            'danielle',
            'Criminal Possession of Dangerous Drugs',
            'Guilty',
            'Conviction after plea',
            '5 years probation, $500 fine',
            '2022-03-15',
            'Judge Brown',
            '2021-06-01',
        ),
    )
    wood_case_id = cur.lastrowid

    # Case 2: Sandberg/Kevin — no outcome yet
    cur = conn.execute(
        '''
        INSERT INTO court_cases
            (court_id, slug, case_number, caption, status,
             defendant_name, defendant_slug, defendant_last, defendant_first,
             is_criminal, charges_text, filed_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ''',
        (
            court_id,
            'sanders-county-dc-dc-23-101',
            'DC 23-101',
            'State v. Sandberg',
            'open',
            'Kevin James Sandberg',
            'sandberg-kevin-james',
            'sandberg',
            'kevin',
            'Partner Assault',
            '2023-09-12',
        ),
    )
    sandberg_case_id = cur.lastrowid

    # Jail booking: Wood in Sanders County (1 hour ago)
    cur = conn.execute(
        '''
        INSERT INTO jail_bookings
            (person_name, name_slug, age, booking_at,
             county_slug, county_name, facility_name,
             charges_summary, is_current, booking_status)
        VALUES (?, ?, ?, datetime('now', '-1 hour'),
                ?, ?, ?, ?, 0, 'released')
        ''',
        (
            'Wood, Danielle Jeanette',
            'wood-danielle-jeanette',
            34,
            'sanders',
            'Sanders',
            'Sanders County Jail',
            'Drug possession',
        ),
    )
    wood_booking_id = cur.lastrowid

    # Jail booking: Sandberg in Sanders County (30 min ago)
    cur = conn.execute(
        '''
        INSERT INTO jail_bookings
            (person_name, name_slug, age, booking_at,
             county_slug, county_name, facility_name,
             charges_summary, is_current, booking_status)
        VALUES (?, ?, ?, datetime('now', '-30 minutes'),
                ?, ?, ?, ?, 1, 'in_custody')
        ''',
        (
            'Sandberg, Kevin James',
            'sandberg-kevin-james',
            41,
            'sanders',
            'Sanders',
            'Sanders County Jail',
            'Partner assault',
        ),
    )
    sandberg_booking_id = cur.lastrowid

    conn.commit()
    return {
        'court_id': court_id,
        'wood_case_id': wood_case_id,
        'sandberg_case_id': sandberg_case_id,
        'wood_booking_id': wood_booking_id,
        'sandberg_booking_id': sandberg_booking_id,
    }


class DispositionLookupTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-disposition-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.ids = _seed_fixtures(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_case_number_exact_match(self) -> None:
        result = disposition_lookup.lookup_disposition(
            self.conn, case_number='DA 21-0260', include_bookings=False,
        )
        self.assertEqual(result['match_count'], 1)
        m = result['matches'][0]
        self.assertEqual(m['match_type'], 'case_number')
        self.assertEqual(m['confidence'], 1.0)
        self.assertEqual(m['court_cases'][0]['case_number'], 'DA 21-0260')
        self.assertEqual(m['court_cases'][0]['disposition'], 'Conviction after plea')

    def test_slug_exact_match(self) -> None:
        # Input slug is built by lowercasing + space→dash + stripping punct, so
        # to match the stored 'wood-danielle-jeanette' slug the input must put
        # the last name first.
        result = disposition_lookup.lookup_disposition(
            self.conn, name='Wood, Danielle Jeanette', include_bookings=False,
        )
        self.assertEqual(result['match_count'], 1)
        m = result['matches'][0]
        self.assertEqual(m['match_type'], 'exact_slug')
        self.assertEqual(m['confidence'], 1.0)
        self.assertEqual(m['court_cases'][0]['case_number'], 'DA 21-0260')

    def test_last_first_match(self) -> None:
        result = disposition_lookup.lookup_disposition(
            self.conn, name='Kevin Sandberg', include_bookings=False,
        )
        self.assertEqual(result['match_count'], 1)
        m = result['matches'][0]
        self.assertEqual(m['match_type'], 'last_first')
        self.assertEqual(m['confidence'], 0.9)
        self.assertEqual(m['court_cases'][0]['case_number'], 'DC 23-101')

    def test_last_only_match(self) -> None:
        result = disposition_lookup.lookup_disposition(
            self.conn, name='Wood', include_bookings=False,
        )
        self.assertEqual(result['match_count'], 1)
        m = result['matches'][0]
        self.assertEqual(m['match_type'], 'last_only')
        self.assertEqual(m['confidence'], 0.7)

    def test_includes_related_bookings(self) -> None:
        result = disposition_lookup.lookup_disposition(
            self.conn, name='Danielle Wood', include_bookings=True,
        )
        m = result['matches'][0]
        case = m['court_cases'][0]
        self.assertIn('related_jail_bookings', case)
        self.assertGreaterEqual(len(case['related_jail_bookings']), 1)
        self.assertEqual(
            case['related_jail_bookings'][0]['person_name'],
            'Wood, Danielle Jeanette',
        )

    def test_county_filter_matches(self) -> None:
        result = disposition_lookup.lookup_disposition(
            self.conn, name='Wood', county='Sanders', include_bookings=False,
        )
        self.assertEqual(result['match_count'], 1)

    def test_county_filter_misses(self) -> None:
        result = disposition_lookup.lookup_disposition(
            self.conn, name='Wood', county='Yellowstone', include_bookings=False,
        )
        self.assertEqual(result['match_count'], 0)

    def test_no_match_returns_warning(self) -> None:
        result = disposition_lookup.lookup_disposition(
            self.conn, name='NoSuch Person', include_bookings=False,
        )
        self.assertEqual(result['match_count'], 0)
        self.assertTrue(any('No court cases' in w for w in result['warnings']))

    def test_empty_query_returns_warning(self) -> None:
        result = disposition_lookup.lookup_disposition(self.conn)
        self.assertEqual(result['match_count'], 0)
        self.assertTrue(any('Provide' in w for w in result['warnings']))

    def test_data_as_of_populated(self) -> None:
        result = disposition_lookup.lookup_disposition(
            self.conn, name='Danielle Wood', include_bookings=False,
        )
        self.assertIsNotNone(result['data_as_of'])


class DispositionWatcherLinkTests(unittest.TestCase):
    """link_recent_bookings() — initial link + outcome snapshot."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-disposition-watch-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.ids = _seed_fixtures(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_links_both_bookings(self) -> None:
        stats = disposition_watcher.link_recent_bookings(self.conn, since_minutes=120)
        self.assertEqual(stats['scanned'], 2)
        self.assertEqual(stats['linked'], 2)
        self.assertEqual(stats['errors'], 0)
        rows = self.conn.execute('SELECT * FROM booking_case_links').fetchall()
        self.assertEqual(len(rows), 2)

    def test_link_carries_outcome_snapshot(self) -> None:
        disposition_watcher.link_recent_bookings(self.conn, since_minutes=120)
        wood_link = self.conn.execute(
            '''
            SELECT bcl.*, jb.person_name
            FROM booking_case_links bcl
            JOIN jail_bookings jb ON jb.id = bcl.booking_id
            WHERE jb.person_name LIKE 'Wood%'
            '''
        ).fetchone()
        self.assertIsNotNone(wood_link)
        self.assertEqual(wood_link['has_outcome'], 1)
        self.assertIsNotNone(wood_link['last_outcome_snapshot'])
        snap = json.loads(wood_link['last_outcome_snapshot'])
        self.assertEqual(snap['disposition'], 'Conviction after plea')
        self.assertEqual(snap['sentencing_judge'], 'Judge Brown')

    def test_link_without_outcome_marks_zero(self) -> None:
        disposition_watcher.link_recent_bookings(self.conn, since_minutes=120)
        sandberg_link = self.conn.execute(
            '''
            SELECT bcl.*, jb.person_name
            FROM booking_case_links bcl
            JOIN jail_bookings jb ON jb.id = bcl.booking_id
            WHERE jb.person_name LIKE 'Sandberg%'
            '''
        ).fetchone()
        self.assertIsNotNone(sandberg_link)
        self.assertEqual(sandberg_link['has_outcome'], 0)
        self.assertIsNotNone(sandberg_link['last_outcome_snapshot'])

    def test_link_is_idempotent(self) -> None:
        disposition_watcher.link_recent_bookings(self.conn, since_minutes=120)
        stats = disposition_watcher.link_recent_bookings(self.conn, since_minutes=120)
        self.assertEqual(stats['linked'], 0)
        rows = self.conn.execute('SELECT * FROM booking_case_links').fetchall()
        self.assertEqual(len(rows), 2)

    def test_outside_window_scans_nothing(self) -> None:
        # Backdate first_seen_at so the bookings are outside the lookback window.
        self.conn.execute(
            "UPDATE jail_bookings SET first_seen_at = datetime('now', '-2 days')"
        )
        self.conn.commit()
        stats = disposition_watcher.link_recent_bookings(self.conn, since_minutes=60)
        self.assertEqual(stats['scanned'], 0)
        self.assertEqual(stats['linked'], 0)


class DispositionWatcherRefreshTests(unittest.TestCase):
    """refresh_outcome_data() — change detection, has_outcome, notified_admin_at."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-disposition-refresh-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.ids = _seed_fixtures(self.conn)
        # Pre-link both bookings so refresh has something to look at.
        # link_recent_bookings stamps last_checked_at = now, so backdate it
        # to force the refresh to re-scan.
        disposition_watcher.link_recent_bookings(self.conn, since_minutes=120)
        self.conn.execute(
            "UPDATE booking_case_links SET last_checked_at = datetime('now', '-2 days')"
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_initial_refresh_marks_unchanged(self) -> None:
        stats = disposition_watcher.refresh_outcome_data(self.conn)
        self.assertEqual(stats['scanned'], 2)
        self.assertEqual(stats['unchanged'], 2)
        self.assertEqual(stats['outcome_changes'], 0)
        self.assertEqual(stats['new_outcomes'], 0)

    def test_outcome_change_clears_notified_at(self) -> None:
        sandberg_link_id = self.conn.execute(
            '''
            SELECT bcl.id FROM booking_case_links bcl
            JOIN jail_bookings jb ON jb.id = bcl.booking_id
            WHERE jb.person_name LIKE 'Sandberg%'
            '''
        ).fetchone()['id']
        self.conn.execute(
            'UPDATE booking_case_links SET notified_admin_at = datetime("now") WHERE id = ?',
            (sandberg_link_id,),
        )
        self.conn.commit()
        # Simulate a new outcome appearing for Sandberg
        self.conn.execute(
            '''
            UPDATE court_cases SET disposition = ?,
                                   sentence_text = ?,
                                   outcome_scraped_at = datetime('now')
            WHERE defendant_name LIKE 'Kevin%Sandberg%'
            ''',
            ('Plea deal — 2 years deferred', '2 years deferred imposition'),
        )
        self.conn.commit()
        stats = disposition_watcher.refresh_outcome_data(self.conn)
        self.assertEqual(stats['outcome_changes'], 1)
        link_after = self.conn.execute(
            'SELECT notified_admin_at, has_outcome FROM booking_case_links WHERE id = ?',
            (sandberg_link_id,),
        ).fetchone()
        self.assertIsNone(link_after['notified_admin_at'])
        self.assertEqual(link_after['has_outcome'], 1)

    def test_new_outcome_on_unmarked_link(self) -> None:
        # Simulate a legacy link: last_outcome_snapshot is NULL so the watcher's
        # `old_snap is None` branch fires when the outcome first appears.
        self.conn.execute(
            '''
            UPDATE booking_case_links SET last_outcome_snapshot = NULL
            WHERE court_case_id IN (
                SELECT id FROM court_cases WHERE defendant_name LIKE 'Kevin%Sandberg%'
            )
            '''
        )
        # Sandberg had no outcome; now gains one
        self.conn.execute(
            '''
            UPDATE court_cases SET disposition = ?,
                                   sentence_text = ?,
                                   outcome_scraped_at = datetime('now')
            WHERE defendant_name LIKE 'Kevin%Sandberg%'
            ''',
            ('Dismissed', 'nolle prosequi'),
        )
        self.conn.commit()
        stats = disposition_watcher.refresh_outcome_data(self.conn)
        self.assertEqual(stats['new_outcomes'], 1)
        self.assertEqual(stats['outcome_changes'], 0)

    def test_find_pending_notifications(self) -> None:
        # Wood has outcome, Sandberg doesn't yet
        pending = disposition_watcher.find_pending_notifications(self.conn)
        self.assertEqual(len(pending), 1)
        self.assertIn('Wood', pending[0]['person_name'])
        self.assertEqual(pending[0]['disposition'], 'Conviction after plea')

    def test_mark_notified_clears_pending(self) -> None:
        pending = disposition_watcher.find_pending_notifications(self.conn)
        ids = [p['id'] for p in pending]
        n = disposition_watcher.mark_notified(self.conn, ids)
        self.assertEqual(n, 1)
        pending_after = disposition_watcher.find_pending_notifications(self.conn)
        self.assertEqual(len(pending_after), 0)

    def test_mark_notified_empty_list(self) -> None:
        self.assertEqual(disposition_watcher.mark_notified(self.conn, []), 0)

    def test_run_all_combines_stats(self) -> None:
        result = disposition_watcher.run_all(self.conn)
        self.assertIn('link', result)
        self.assertIn('refresh', result)
        # Pre-linked both in setUp; no new bookings in the link window
        self.assertEqual(result['link']['linked'], 0)
        self.assertEqual(result['refresh']['unchanged'], 2)


if __name__ == '__main__':
    unittest.main()
