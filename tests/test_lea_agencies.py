"""Tests for LEA Panel database schema (Phase 1)."""
import os
import sqlite3
import tempfile
import unittest
import secrets

import app as app_module
import config
import init_db


class TestLEAAgenciesSchema(unittest.TestCase):
    """Tests for lea_agencies table."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)
        self.conn = conn

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_agencies_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_agencies'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_agencies_has_required_columns(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(lea_agencies)")
        columns = {row[1] for row in cursor.fetchall()}
        required = {'id', 'org_name', 'agency_type', 'county_slug', 'ori_number', 'verification_status'}
        self.assertTrue(required.issubset(columns))

    def test_lea_agencies_org_name_unique(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Great Falls PD", "police", "cascade", "Cascade", "officer@gfpd.gov")
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
                ("Great Falls PD", "police", "cascade", "Cascade", "other@gfpd.gov")
            )

    def test_lea_agencies_insert_and_query(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email, ori_number) VALUES (?, ?, ?, ?, ?, ?)",
            ("Cascade SO", "sheriff", "cascade", "Cascade", "sheriff@cascadecountymt.gov", "MT0120100")
        )
        self.conn.commit()
        row = cursor.execute("SELECT * FROM lea_agencies WHERE ori_number = ?", ("MT0120100",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['org_name'], "Cascade SO")
        self.assertEqual(row['verification_status'], "pending")


class TestLEAUsersSchema(unittest.TestCase):
    """Tests for lea_users table."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)
        # Create a test agency
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        conn.commit()
        self.conn = conn
        self.agency_id = cursor.lastrowid

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_users_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_users'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_users_has_required_columns(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(lea_users)")
        columns = {row[1] for row in cursor.fetchall()}
        required = {'id', 'agency_id', 'username', 'email', 'full_name', 'password_hash', 'role'}
        self.assertTrue(required.issubset(columns))

    def test_lea_users_role_enforcement(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "admin1", "admin@test.gov", "Admin User", "hashed_pwd", "admin")
        )
        self.conn.commit()
        row = cursor.execute("SELECT role FROM lea_users WHERE username = ?", ("admin1",)).fetchone()
        self.assertEqual(row[0], "admin")

    def test_lea_users_unique_email_per_agency(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, "user1", "same@email.gov", "User One", "hash1", "pio")
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
                (self.agency_id, "user2", "same@email.gov", "User Two", "hash2", "records_officer")
            )

    def test_lea_users_foreign_key_agency(self) -> None:
        cursor = self.conn.cursor()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
                (99999, "orphan", "orphan@test.gov", "Orphan", "hash", "records_officer")
            )


class TestLEAInvitationsSchema(unittest.TestCase):
    """Tests for lea_invitations table."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        conn.commit()
        self.conn = conn
        self.agency_id = cursor.lastrowid

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_invitations_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_invitations'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_invitations_unique_token(self) -> None:
        cursor = self.conn.cursor()
        token1 = secrets.token_urlsafe(32)
        token2 = secrets.token_urlsafe(32)
        cursor.execute(
            "INSERT INTO lea_invitations (agency_id, email, role, token, expires_at) VALUES (?, ?, ?, ?, ?)",
            (self.agency_id, "newuser@test.gov", "records_officer", token1, "2026-09-02T00:00:00Z")
        )
        self.conn.commit()
        cursor.execute(
            "INSERT INTO lea_invitations (agency_id, email, role, token, expires_at) VALUES (?, ?, ?, ?, ?)",
            (self.agency_id, "newuser@test.gov", "pio", token2, "2026-09-02T00:00:00Z")
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_invitations (agency_id, email, role, token, expires_at) VALUES (?, ?, ?, ?, ?)",
                (self.agency_id, "another@test.gov", "records_officer", token1, "2026-09-02T00:00:00Z")
            )

    def test_lea_invitations_foreign_key_agency(self) -> None:
        cursor = self.conn.cursor()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_invitations (agency_id, email, role, token, expires_at) VALUES (?, ?, ?, ?, ?)",
                (99999, "orphan@test.gov", "records_officer", secrets.token_urlsafe(32), "2026-09-02T00:00:00Z")
            )


class TestLEABlotterDraftsSchema(unittest.TestCase):
    """Tests for lea_blotter_drafts table."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        agency_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO lea_users (agency_id, username, email, full_name, password_hash, role) VALUES (?, ?, ?, ?, ?, ?)",
            (agency_id, "officer1", "officer@test.gov", "Officer One", "hash", "pio")
        )
        conn.commit()
        self.conn = conn
        self.agency_id = agency_id
        self.user_id = cursor.lastrowid

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_blotter_drafts_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_blotter_drafts'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_blotter_drafts_has_required_columns(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(lea_blotter_drafts)")
        columns = {row[1] for row in cursor.fetchall()}
        required = {'id', 'agency_id', 'submitted_by_user_id', 'incident_date', 'submission_status'}
        self.assertTrue(required.issubset(columns))

    def test_lea_blotter_drafts_insert_and_status_default(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_blotter_drafts (agency_id, submitted_by_user_id, incident_date) VALUES (?, ?, ?)",
            (self.agency_id, self.user_id, "2026-08-02")
        )
        self.conn.commit()
        row = cursor.execute("SELECT submission_status FROM lea_blotter_drafts WHERE id = ?", (cursor.lastrowid,)).fetchone()
        self.assertEqual(row[0], "draft")

    def test_lea_blotter_drafts_workflow_status(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_blotter_drafts (agency_id, submitted_by_user_id, incident_date, cad_number, primary_offense_mca, submission_status) VALUES (?, ?, ?, ?, ?, ?)",
            (self.agency_id, self.user_id, "2026-08-02", "CAD-123", "45-5-202", "submitted")
        )
        self.conn.commit()
        row = cursor.execute("SELECT cad_number, primary_offense_mca, submission_status FROM lea_blotter_drafts WHERE id = ?", (cursor.lastrowid,)).fetchone()
        self.assertEqual(row['cad_number'], "CAD-123")
        self.assertEqual(row['primary_offense_mca'], "45-5-202")
        self.assertEqual(row['submission_status'], "submitted")


class TestLEARosterSnapshotsSchema(unittest.TestCase):
    """Tests for lea_roster_snapshots table."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test SO", "sheriff", "test", "Test", "sheriff@test.gov")
        )
        conn.commit()
        self.conn = conn
        self.agency_id = cursor.lastrowid

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_roster_snapshots_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_roster_snapshots'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_roster_snapshots_insert_and_query(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_roster_snapshots (agency_id, snapshot_date, roster_json, total_inmates, hash_checksum) VALUES (?, ?, ?, ?, ?)",
            (self.agency_id, "2026-08-02", '[{"name": "Inmate A"}]', 1, "abc123")
        )
        self.conn.commit()
        row = cursor.execute("SELECT * FROM lea_roster_snapshots WHERE hash_checksum = ?", ("abc123",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['total_inmates'], 1)
        self.assertEqual(row['ingestion_status'], "staged")


class TestLEAAPITokensSchema(unittest.TestCase):
    """Tests for lea_api_tokens table."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        conn.commit()
        self.conn = conn
        self.agency_id = cursor.lastrowid

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_api_tokens_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_api_tokens'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_api_tokens_unique_hash(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_api_tokens (agency_id, token_name, token_hash, scopes) VALUES (?, ?, ?, ?)",
            (self.agency_id, "CAD Sync", "hash123", '["blotter.publish"]')
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_api_tokens (agency_id, token_name, token_hash, scopes) VALUES (?, ?, ?, ?)",
                (self.agency_id, "RMS Export", "hash123", '["roster.read"]')
            )


class TestLEAAuditLogSchema(unittest.TestCase):
    """Tests for lea_audit_log table (immutable CJIS-compliant audit trail)."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        conn.commit()
        self.conn = conn
        self.agency_id = cursor.lastrowid

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_audit_log_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_audit_log'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_audit_log_insert_and_query(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_audit_log (agency_id, action, resource_type, resource_id, change_summary) VALUES (?, ?, ?, ?, ?)",
            (self.agency_id, "blotter.submit", "blotter", "42", "Submitted incident CAD-123")
        )
        self.conn.commit()
        row = cursor.execute("SELECT * FROM lea_audit_log WHERE resource_id = ?", ("42",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['action'], "blotter.submit")

    def test_lea_audit_log_not_null_agency_id(self) -> None:
        cursor = self.conn.cursor()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_audit_log (agency_id, action) VALUES (?, ?)",
                (None, "test.action")
            )


class TestLEAAgencyCoveragesSchema(unittest.TestCase):
    """Tests for lea_agency_coverages table."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row
        init_db.ensure_lea_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agencies (org_name, agency_type, county_slug, county_name, primary_contact_email) VALUES (?, ?, ?, ?, ?)",
            ("Test PD", "police", "test", "Test", "admin@test.gov")
        )
        conn.commit()
        self.conn = conn
        self.agency_id = cursor.lastrowid

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_lea_agency_coverages_table_exists(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lea_agency_coverages'")
        self.assertIsNotNone(cursor.fetchone())

    def test_lea_agency_coverages_unique_agency(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agency_coverages (agency_id) VALUES (?)",
            (self.agency_id,)
        )
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                "INSERT INTO lea_agency_coverages (agency_id) VALUES (?)",
                (self.agency_id,)
            )

    def test_lea_agency_coverages_defaults(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO lea_agency_coverages (agency_id) VALUES (?)",
            (self.agency_id,)
        )
        self.conn.commit()
        row = cursor.execute("SELECT * FROM lea_agency_coverages WHERE agency_id = ?", (self.agency_id,)).fetchone()
        self.assertEqual(row['blotter_coverage_tier'], "standard")
        self.assertEqual(row['roster_coverage_tier'], "off")


class TestLEASchemaIntegration(unittest.TestCase):
    """Integration tests — verify migrate() creates all LEA tables."""

    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lea-int-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_migrate_calls_ensure_lea_schema(self) -> None:
        init_db.init_database()
        init_db.migrate()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        table_names = {row[0] for row in cursor.fetchall()}
        expected_lea_tables = {
            'lea_agencies', 'lea_users', 'lea_invitations', 'lea_blotter_drafts',
            'lea_roster_snapshots', 'lea_api_tokens', 'lea_audit_log', 'lea_agency_coverages'
        }
        self.assertTrue(expected_lea_tables.issubset(table_names))
        conn.close()
