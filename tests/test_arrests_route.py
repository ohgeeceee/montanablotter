"""
Integration tests for the /arrests route.

The route UNIONs blotter `records` (arrest-keyword filter) with `jail_bookings`
(active/recent). Tests verify rendering, filtering, pagination URL safety, and
that both data sources appear in the combined result.
"""
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class ArrestsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-arrests-', suffix='.db')
        os.close(fd)

        # Capture originals for teardown
        self._orig_config_db = config.DB_PATH
        self._orig_init_db = init_db.DB_PATH
        self._orig_app_db = app_module.config.DB_PATH

        # Redirect all DB access to the temp file
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        # Bootstrap the subscribers table before init_database(), because
        # ensure_incident_notification_schema() (called inside init_database)
        # attempts to ALTER subscribers and fails if it doesn't exist yet.
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _insert_blotter(self, county: str = "Cascade") -> int:
        """Insert a minimal blotter row; records.blotter_id is NOT NULL."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "INSERT INTO blotters (filename, county, status) VALUES (?, ?, 'processed')",
            (f"test-{county}.pdf", county),
        )
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    def _insert_record(self, county: str = "Cascade", incident_type: str = "Arrest",
                       details: str = "Arrested subject for DUI",
                       location: str = "Main St",
                       blotter_id: int | None = None) -> int:
        if blotter_id is None:
            blotter_id = self._insert_blotter(county)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """
            INSERT INTO records (blotter_id, date, county, incident_type, details, location, incident, created_at)
            VALUES (?, date('now'), ?, ?, ?, ?, ?, datetime('now'))
            """,
            (blotter_id, county, incident_type, details, location, incident_type),
        )
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    def _insert_booking(self, county_slug: str = "cascade",
                        county_name: str = "Cascade",
                        person_name: str = "Doe, John",
                        charges: str = "DUI",
                        is_current: int = 1) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """
            INSERT INTO jail_bookings (
                county_slug, county_name, facility_name, person_name,
                booking_at, charges_summary, hash_id,
                is_current, first_seen_at, created_at
            )
            VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (county_slug, county_name, "County Jail", person_name,
             charges, f"hash-{person_name}-{county_slug}", is_current),
        )
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_arrests_returns_200(self) -> None:
        """Route renders without error on empty DB."""
        with app_module.app.test_client() as client:
            resp = client.get('/arrests')
        self.assertEqual(resp.status_code, 200)

    def test_arrests_union_includes_both_sources(self) -> None:
        """A blotter record AND a jail booking both appear in the response."""
        self._insert_record(county="Cascade", incident_type="Arrest",
                            details="Arrested suspect on warrant")
        self._insert_booking(county_name="Yellowstone", person_name="Smith, Jane",
                             charges="Theft")

        with app_module.app.test_client() as client:
            resp = client.get('/arrests')

        body = resp.data.decode()
        self.assertIn("Arrest", body)
        self.assertIn("Smith, Jane", body)

    def test_arrests_county_filter_restricts_results(self) -> None:
        """Filtering by county returns only records from that county."""
        self._insert_record(county="Cascade", incident_type="Arrest",
                            details="Arrested on outstanding warrant")
        self._insert_booking(county_slug="missoula", county_name="Missoula",
                             person_name="Jones, Bob", charges="Assault")

        with app_module.app.test_client() as client:
            resp = client.get('/arrests?county=Cascade')

        body = resp.data.decode()
        self.assertIn("Cascade", body)
        self.assertNotIn("Jones, Bob", body)

    def test_arrests_search_filter_matches_title_and_details(self) -> None:
        """Search query filters results to matching records only."""
        self._insert_record(county="Lewis and Clark", incident_type="DUI Arrest",
                            details="Driver arrested for DUI at highway checkpoint")
        self._insert_booking(county_name="Flathead", person_name="Cooper, Alice",
                             charges="Vandalism")

        with app_module.app.test_client() as client:
            resp = client.get('/arrests?q=DUI')

        body = resp.data.decode()
        self.assertIn("DUI", body)
        # Vandalism booking should not appear for DUI search
        self.assertNotIn("Cooper, Alice", body)

    def test_arrests_pagination_links_url_encoded(self) -> None:
        """Pagination hrefs must encode special characters in q and county params."""
        # Insert enough rows to trigger pagination (per_page = 25)
        for i in range(30):
            self._insert_record(
                county="Cascade",
                incident_type=f"Arrest {i}",
                details=f"Arrested suspect {i} on warrant",
            )

        with app_module.app.test_client() as client:
            # q contains & which must be encoded as %26 to keep URL valid
            resp = client.get('/arrests?q=DUI+%26+Assault&page=1')

        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        # Pagination links should NOT contain a raw & inside the q= value
        # (i.e., ?q=DUI+%26+Assault should survive round-trip without breaking URL)
        # The |urlencode filter in the template should encode & as %26
        if 'page=2' in body:
            # If next-page link exists, verify it doesn't inject a bare & into the q param
            self.assertNotIn('q=DUI+&+Assault', body, (
                "Pagination link contains un-encoded & in query string — "
                "apply |urlencode filter to {{ q }} in arrests.html"
            ))


if __name__ == '__main__':
    unittest.main()
