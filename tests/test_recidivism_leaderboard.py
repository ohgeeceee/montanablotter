"""
Tests for the repeat-booking leaderboard service and /leaderboard route.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from db import connect_db


class RecidivismLeaderboardTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-recidivism-', suffix='.db')
        os.close(fd)

        self._orig_config_db = config.DB_PATH
        self._orig_init_db = init_db.DB_PATH
        self._orig_app_db = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        bootstrap = sqlite3.connect(self.db_path)
        bootstrap.execute(
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
        bootstrap.commit()
        bootstrap.close()

        init_db.init_database()
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self._orig_config_db
        init_db.DB_PATH = self._orig_init_db
        app_module.config.DB_PATH = self._orig_app_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _insert_booking(
        self,
        *,
        person_name: str,
        name_slug: str,
        county_slug: str = 'cascade',
        county_name: str = 'Cascade',
        booking_at: str,
        release_at: str | None = None,
        is_current: int = 0,
        booking_status: str = 'released',
    ) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """
            INSERT INTO jail_bookings (
                county_slug, county_name, facility_name, person_name,
                booking_at, release_at, charges_summary, hash_id,
                name_slug, is_current, booking_status,
                first_seen_at, last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), datetime('now'), datetime('now'))
            """,
            (
                county_slug,
                county_name,
                'County Jail',
                person_name,
                booking_at,
                release_at,
                'Test charge',
                f'hash-{name_slug}-{booking_at}',
                name_slug,
                is_current,
                booking_status,
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    def test_route_returns_200(self) -> None:
        """The leaderboard page renders without error on an empty DB."""
        with app_module.app.test_client() as client:
            resp = client.get('/leaderboard')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Repeat Booking Leaderboard', resp.data)

    def test_only_released_and_rebooked_qualify(self) -> None:
        """A person with two bookings but no release between them does not qualify."""
        self._insert_booking(
            person_name='Doe, John',
            name_slug='doe-john',
            booking_at='2026-01-01',
            release_at=None,
        )
        self._insert_booking(
            person_name='Doe, John',
            name_slug='doe-john',
            booking_at='2026-01-15',
            release_at=None,
        )

        conn = connect_db()
        from services.persons.recidivism import recidivism_leaderboard_context
        context = recidivism_leaderboard_context(conn, limit=10)
        conn.close()

        self.assertEqual(context['leaderboard'], [])
        self.assertEqual(context['total_qualifiers'], 0)

    def test_leaderboard_orders_by_booking_count(self) -> None:
        """The person with the most qualifying bookings ranks #1."""
        # Three-time qualifier — should rank first.
        self._insert_booking(
            person_name='Smith, Jane',
            name_slug='smith-jane',
            county_name='Yellowstone',
            booking_at='2026-01-01',
            release_at='2026-01-05',
        )
        self._insert_booking(
            person_name='Smith, Jane',
            name_slug='smith-jane',
            county_name='Yellowstone',
            booking_at='2026-02-01',
            release_at='2026-02-10',
        )
        self._insert_booking(
            person_name='Smith, Jane',
            name_slug='smith-jane',
            county_name='Cascade',
            booking_at='2026-03-01',
            release_at=None,
        )

        # Two-time qualifier — should rank second.
        self._insert_booking(
            person_name='Brown, Bob',
            name_slug='brown-bob',
            booking_at='2026-01-10',
            release_at='2026-01-12',
        )
        self._insert_booking(
            person_name='Brown, Bob',
            name_slug='brown-bob',
            booking_at='2026-02-15',
            release_at=None,
        )

        conn = connect_db()
        from services.persons.recidivism import recidivism_leaderboard_context
        context = recidivism_leaderboard_context(conn, limit=10)
        conn.close()

        self.assertEqual(len(context['leaderboard']), 2)
        self.assertEqual(context['leaderboard'][0]['name_slug'], 'smith-jane')
        self.assertEqual(context['leaderboard'][0]['booking_count'], 3)
        self.assertEqual(context['leaderboard'][0]['rank'], 1)
        self.assertEqual(context['leaderboard'][1]['name_slug'], 'brown-bob')
        self.assertEqual(context['leaderboard'][1]['booking_count'], 2)
        self.assertEqual(context['total_qualifiers'], 2)

        with app_module.app.test_client() as client:
            resp = client.get('/leaderboard')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Jane Smith', resp.data)
        self.assertIn(b'Bob Brown', resp.data)

    def test_limit_is_respected(self) -> None:
        """Only the top N are returned when there are more qualifiers."""
        for i in range(15):
            self._insert_booking(
                person_name=f'Person, {i}',
                name_slug=f'person-{i}',
                booking_at=f'2026-01-{i + 1:02d}',
                release_at=f'2026-01-{i + 2:02d}',
            )
            self._insert_booking(
                person_name=f'Person, {i}',
                name_slug=f'person-{i}',
                booking_at=f'2026-02-{i + 1:02d}',
                release_at=None,
            )

        conn = connect_db()
        from services.persons.recidivism import recidivism_leaderboard_context
        context = recidivism_leaderboard_context(conn, limit=5)
        conn.close()

        self.assertEqual(len(context['leaderboard']), 5)
        self.assertTrue(all(r['rank'] <= 5 for r in context['leaderboard']))

    def test_county_filter(self) -> None:
        """Filtering by county restricts results to that county."""
        self._insert_booking(
            person_name='Adams, Alice',
            name_slug='adams-alice',
            county_slug='cascade',
            county_name='Cascade',
            booking_at='2026-01-01',
            release_at='2026-01-05',
        )
        self._insert_booking(
            person_name='Adams, Alice',
            name_slug='adams-alice',
            county_slug='cascade',
            county_name='Cascade',
            booking_at='2026-02-01',
            release_at=None,
        )
        self._insert_booking(
            person_name='Baker, Bill',
            name_slug='baker-bill',
            county_slug='yellowstone',
            county_name='Yellowstone',
            booking_at='2026-01-01',
            release_at='2026-01-05',
        )
        self._insert_booking(
            person_name='Baker, Bill',
            name_slug='baker-bill',
            county_slug='yellowstone',
            county_name='Yellowstone',
            booking_at='2026-02-01',
            release_at=None,
        )

        conn = connect_db()
        from services.persons.recidivism import recidivism_leaderboard_context
        context = recidivism_leaderboard_context(conn, limit=10, county_slug='cascade')
        conn.close()

        self.assertEqual(len(context['leaderboard']), 1)
        self.assertEqual(context['leaderboard'][0]['name_slug'], 'adams-alice')
        self.assertEqual(context['total_qualifiers'], 1)

        with app_module.app.test_client() as client:
            resp = client.get('/leaderboard?county=yellowstone')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Bill Baker', resp.data)
        self.assertNotIn(b'Alice Adams', resp.data)


class NameSlugIngestionTests(unittest.TestCase):
    def test_name_slug_helper(self) -> None:
        """The ingestion helper normalizes names consistently with init_db."""
        from services.ingestion.jail_bookings import _name_slug_for
        self.assertEqual(_name_slug_for('Doe, John'), 'doe-john')
        self.assertEqual(_name_slug_for("O'Brien, Tim"), 'obrien-tim')
        self.assertEqual(_name_slug_for('JOHN DOE'), 'john-doe')
        self.assertEqual(_name_slug_for(''), '')


if __name__ == '__main__':
    unittest.main()
