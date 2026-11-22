"""Regression coverage for the jail booking_at sanity guard.

A real parse error once stored a Toole County "booking" dated 1001-02-06
(parsed from a non-roster page). The guard clamps timestamps outside the
plausible window to NULL at sync time so they cannot poison date sorting,
digests, or analytics.
"""
import os
import sqlite3
import tempfile
import unittest

from services.ingestion.jail_bookings import _sane_booking_at, _sync_records
from services.ingestion.models import JailBookingRecord


class SaneBookingAtTestCase(unittest.TestCase):
    def test_normal_timestamp_passthrough(self):
        self.assertEqual(
            _sane_booking_at("2026-11-17 14:30:00"),
            "2026-11-17 14:30:00",
        )

    def test_date_only_passthrough(self):
        self.assertEqual(_sane_booking_at("2026-11-17"), "2026-11-17")

    def test_empty_and_garbage_become_none(self):
        self.assertIsNone(_sane_booking_at(""))
        self.assertIsNone(_sane_booking_at(None))
        self.assertIsNone(_sane_booking_at("not a date"))

    def test_impossibly_old_dates_rejected(self):
        self.assertIsNone(_sane_booking_at("1001-02-06 00:00:00"))
        self.assertIsNone(_sane_booking_at("1970-01-01 00:00:00"))

    def test_far_future_dates_rejected(self):
        self.assertIsNone(_sane_booking_at("3026-11-17 00:00:00"))


class SyncRecordsDateGuardTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="mb-sync-", suffix=".db")
        os.close(fd)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE jail_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                county_slug TEXT,
                county_name TEXT,
                facility_name TEXT,
                person_name TEXT,
                age INTEGER,
                booking_number TEXT,
                booking_at TEXT,
                charges_summary TEXT,
                source_url TEXT,
                source_record_id TEXT,
                hash_id TEXT,
                raw_json TEXT,
                name_slug TEXT,
                booking_status TEXT,
                is_current INTEGER,
                release_at TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        self.conn.execute(
            "INSERT INTO jail_bookings DEFAULT VALUES"  # placeholder to keep autoincrement warm
        )
        self.conn.execute("DELETE FROM jail_bookings")
        self.conn.commit()
        self.source = self.conn.execute(
            "SELECT 1 AS id, 'test' AS county_slug, 'Test County' AS county_name, "
            "'Test Facility' AS facility_name, 'https://example.test' AS roster_url"
        ).fetchone()

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _record(self, source_record_id, person_name, booking_at):
        return JailBookingRecord(
            source_record_id=source_record_id,
            person_name=person_name,
            age=None,
            booking_number="",
            booking_at=booking_at,
            charges_summary="test charge",
            source_url=None,
        )

    def _sync(self, records):
        stats = _sync_records(self.conn, self.source, records)
        self.conn.commit()
        return stats

    def test_garbage_date_clamped_to_null(self):
        stats = self._sync([
            self._record("r1", "Shelby, Mt", "1001-02-06 00:00:00"),
        ])
        row = self.conn.execute("SELECT booking_at FROM jail_bookings WHERE source_record_id = 'r1'").fetchone()
        self.assertIsNone(row["booking_at"])
        self.assertEqual(stats.sanitized_date_count, 1)

    def test_valid_date_kept(self):
        stats = self._sync([
            self._record("r2", "DOE, JANE", "2026-11-17 09:00:00"),
        ])
        row = self.conn.execute("SELECT booking_at FROM jail_bookings WHERE source_record_id = 'r2'").fetchone()
        self.assertEqual(row["booking_at"], "2026-11-17 09:00:00")
        self.assertEqual(stats.sanitized_date_count, 0)

    def test_missing_date_stays_null_without_counting(self):
        stats = self._sync([
            self._record("r3", "ROE, RICHARD", None),
        ])
        row = self.conn.execute("SELECT booking_at FROM jail_bookings WHERE source_record_id = 'r3'").fetchone()
        self.assertIsNone(row["booking_at"])
        self.assertEqual(stats.sanitized_date_count, 0)

    def test_update_path_also_sanitized(self):
        self._sync([self._record("r4", "SMITH, BOB", "1001-01-01 00:00:00")])
        row = self.conn.execute("SELECT booking_at FROM jail_bookings WHERE source_record_id = 'r4'").fetchone()
        self.assertIsNone(row["booking_at"])

        stats = self._sync([self._record("r4", "SMITH, BOB", "3026-01-01 00:00:00")])
        row = self.conn.execute("SELECT booking_at FROM jail_bookings WHERE source_record_id = 'r4'").fetchone()
        self.assertIsNone(row["booking_at"])
        self.assertGreaterEqual(stats.sanitized_date_count, 1)


if __name__ == "__main__":
    unittest.main()
