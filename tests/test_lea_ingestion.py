"""Tests for LEA ingestion workers (Phase 5)."""
import json
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from services.lea_auth.user_auth import hash_password
from services.ingestion.poll_lea_panel import fetch_approved_drafts, fetch_staged_rosters
from services.ingestion.normalize_lea_records import normalize_and_publish, process_all_approved_drafts
from services.ingestion.ingest_lea_rosters import ingest_roster, process_all_staged_rosters


class TestLEAIngestionWorkers(unittest.TestCase):
    """Tests for LEA ingestion workers (poll, normalize, roster)."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-ingest-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.init_database()
        init_db.migrate()
        cursor = conn.cursor()

        # Create test agency
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        self.agency_id = cursor.lastrowid

        # Create test user
        pw_hash = hash_password("pass123")
        cursor.execute(
            "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "officer1", "officer@test.gov", "Officer", pw_hash, "pio")
        )
        self.user_id = cursor.lastrowid
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        return conn

    def test_fetch_approved_drafts_empty(self) -> None:
        """fetch_approved_drafts returns empty list when no approved drafts."""
        conn = self._conn()
        drafts = fetch_approved_drafts(conn=conn)
        self.assertEqual(drafts, [])
        conn.close()

    def test_normalize_and_publish_no_approved_drafts(self) -> None:
        """process_all_approved_drafts returns empty when no drafts exist."""
        conn = self._conn()
        results = process_all_approved_drafts(conn=conn)
        self.assertEqual(results, [])
        conn.close()

    def test_normalize_and_publish_single_draft(self) -> None:
        """Approved draft should be published to records table."""
        conn = self._conn()

        # Create an approved draft
        conn.execute(
            "INSERT INTO lea_blotter_drafts (agency_id, submitted_by_user_id, incident_date, incident_time, "
            "cad_number, primary_offense_mca, incident_location_block, public_narrative, "
            "responding_officer, submission_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.agency_id, self.user_id, "2026-08-02", "14:30:00",
             "CAD-001", "45-5-202", "300 BLK CENTRAL AVE", "Subject was observed...",
             "Smith", "approved")
        )
        draft_id = cursor.lastrowid if hasattr(self, 'conn') else None
        conn.commit()

        # Use the function directly
        drafts = fetch_approved_drafts(conn=conn)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]['cad_number'], "CAD-001")

        result = normalize_and_publish(drafts[0], conn=conn)
        self.assertTrue(result['success'])

        # Verify record in public table
        records = conn.execute("SELECT * FROM records WHERE cfs_number = ?", ("CAD-001",)).fetchall()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['county'], "test")
        self.assertEqual(records[0]['incident_type'], "45-5-202")

        # Verify draft status updated
        draft = conn.execute("SELECT submission_status, published_at FROM lea_blotter_drafts WHERE id = ?",
                             (drafts[0]['id'],)).fetchone()
        self.assertEqual(draft['submission_status'], "published")
        self.assertIsNotNone(draft['published_at'])

        # Verify audit log
        audit = conn.execute("SELECT * FROM lea_audit_log WHERE resource_id = ?",
                             (str(drafts[0]['id']),)).fetchall()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['action'], "blotter.publish")
        conn.close()

    def test_fetch_staged_rosters_empty(self) -> None:
        """fetch_staged_rosters returns empty list when no staged rosters."""
        conn = self._conn()
        rosters = fetch_staged_rosters(conn=conn)
        self.assertEqual(rosters, [])
        conn.close()

    def test_ingest_roster_empty(self) -> None:
        """process_all_staged_rosters returns empty when none staged."""
        conn = self._conn()
        results = process_all_staged_rosters(conn=conn)
        self.assertEqual(results, [])
        conn.close()

    def test_ingest_roster_with_inmates(self) -> None:
        """Staged roster with inmates should insert into jail_bookings."""
        conn = self._conn()

        inmates_data = [
            {"name": "John Doe", "booking_date": "2026-08-01", "agency": "Test Detention"},
            {"name": "Jane Smith", "booking_date": "2026-08-01", "agency": "Test Detention"}
        ]

        conn.execute(
            "INSERT INTO lea_roster_snapshots (agency_id, snapshot_date, roster_json, total_inmates, hash_checksum, ingestion_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "2026-08-02", json.dumps(inmates_data), 2, "testhash1", "staged")
        )
        snapshot_id = cursor.lastrowid if hasattr(self, 'conn') else None
        conn.commit()

        snapshots = fetch_staged_rosters(conn=conn)
        self.assertEqual(len(snapshots), 1)

        result = ingest_roster(snapshots[0], conn=conn)
        self.assertTrue(result['success'])
        self.assertEqual(result['inserted'], 2)

        # Verify jail_bookings entries
        bookings = conn.execute("SELECT * FROM jail_bookings ORDER BY id").fetchall()
        self.assertEqual(len(bookings), 2)
        self.assertEqual(bookings[0]['person_name'], "John Doe")

        # Verify snapshot status updated
        snapshot = conn.execute("SELECT ingestion_status FROM lea_roster_snapshots WHERE id = ?",
                                (snapshots[0]['id'],)).fetchone()
        self.assertEqual(snapshot['ingestion_status'], "published")

        # Verify audit log
        audit = conn.execute("SELECT * FROM lea_audit_log WHERE action = 'roster.publish'").fetchall()
        self.assertEqual(len(audit), 1)
        conn.close()

    def test_ingest_roster_dedup(self) -> None:
        """Same inmate twice should only insert once (dedup by hash)."""
        conn = self._conn()

        inmates_data = [
            {"name": "John Doe", "booking_date": "2026-08-01", "agency": "Test Detention"},
            {"name": "John Doe", "booking_date": "2026-08-01", "agency": "Test Detention"}
        ]

        conn.execute(
            "INSERT INTO lea_roster_snapshots (agency_id, snapshot_date, roster_json, total_inmates, hash_checksum, ingestion_status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "2026-08-02", json.dumps(inmates_data), 2, "testhash2", "staged")
        )
        conn.commit()

        snapshots = fetch_staged_rosters(conn=conn)
        result = ingest_roster(snapshots[0], conn=conn)
        self.assertTrue(result['success'])
        self.assertEqual(result['inserted'], 1, "Only 1 unique inmate should be inserted")

        bookings = conn.execute("SELECT COUNT(*) FROM jail_bookings").fetchone()[0]
        self.assertEqual(bookings, 1)
        conn.close()
