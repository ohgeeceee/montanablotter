"""Tests for public /wanted pages and warrant resolution."""

import sqlite3
import unittest
from datetime import datetime, timezone

from app import app
from services.ingestion.warrants.models import ensure_warrant_schema
from services.ingestion.warrants.scraper import resolve_stale_warrants, upsert_warrants
from services.ingestion.warrants.models import WarrantRecord
from services.persons.warrants_public import (
    warrant_city_context,
    warrant_homepage_context,
    warrant_public_context,
    warrant_slug,
)


class WantedPagesTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_warrant_schema(self.conn)
        self.run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self._insert_warrant(
            source_record_id="test-warrant:jane-doe",
            person_name="Jane Doe",
            county="Test",
            city="Example City, MT",
            status="active",
            charges_text="CRIMINAL CONTEMPT",
        )
        app.config["TESTING"] = True
        self._original_get_db = app.view_functions["wanted_index"].__globals__["get_db"]
        app.view_functions["wanted_index"].__globals__["get_db"] = lambda: self.conn
        app.view_functions["wanted_detail"].__globals__["get_db"] = lambda: self.conn
        # Reset the paywall function to the real implementation in case another
        # test file left it patched.
        from services.monetization.paywall import user_has_warrant_access as real_user_has_warrant_access

        app.view_functions["wanted_index"].__globals__[
            "user_has_warrant_access"
        ] = real_user_has_warrant_access
        app.view_functions["wanted_detail"].__globals__[
            "user_has_warrant_access"
        ] = real_user_has_warrant_access
        # The detail route also calls _pick_attorneys_for_county which uses
        # the production DB — mock it to return an empty list so the test
        # doesn't depend on attorney_referrals seed data. Patch the route's
        # own globals to survive tests that reimport the app module.
        self._original_pick_attorneys = app.view_functions[
            "wanted_detail"
        ].__globals__.get("_pick_attorneys_for_county")
        app.view_functions["wanted_detail"].__globals__[
            "_pick_attorneys_for_county"
        ] = lambda *args, **kwargs: []

    def _insert_warrant(self, **kwargs):
        defaults = {
            "source_record_id": "test-warrant:default",
            "county": "Test",
            "city": "",
            "person_name": "Default Person",
            "dob": "",
            "warrant_type": "active",
            "charges_text": "",
            "issued_by": "Test County Sheriff",
            "issue_date": "",
            "bond_amount": "",
            "bond_type": "",
            "status": "active",
            "source_url": "https://example.gov/warrant/1",
            "resolved_at": "",
        }
        defaults.update(kwargs)
        self.conn.execute(
            """
            INSERT INTO warrants (
                source_record_id, county, city, person_name, dob,
                warrant_type, charges_text, issued_by, issue_date,
                bond_amount, bond_type, status, source_url, resolved_at,
                scraped_at, first_seen_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                defaults["source_record_id"],
                defaults["county"],
                defaults["city"],
                defaults["person_name"],
                defaults["dob"],
                defaults["warrant_type"],
                defaults["charges_text"],
                defaults["issued_by"],
                defaults["issue_date"],
                defaults["bond_amount"],
                defaults["bond_type"],
                defaults["status"],
                defaults["source_url"],
                defaults["resolved_at"],
                self.run_ts,
                self.run_ts,
                self.run_ts,
            ),
        )
        self.conn.commit()

    def tearDown(self):
        app.view_functions["wanted_index"].__globals__["get_db"] = self._original_get_db
        app.view_functions["wanted_detail"].__globals__["get_db"] = self._original_get_db
        if self._original_pick_attorneys is None:
            app.view_functions["wanted_detail"].__globals__.pop(
                "_pick_attorneys_for_county", None
            )
        else:
            app.view_functions["wanted_detail"].__globals__[
                "_pick_attorneys_for_county"
            ] = self._original_pick_attorneys
        self.conn.close()

    def _with_warrant_access(self):
        """Context manager that grants warrant access for the current request.

        Patches the function object stored in the route handler globals so the
        patch survives tests that reimport the app module.
        """
        from contextlib import contextmanager

        @contextmanager
        def _grant():
            targets = [
                app.view_functions["wanted_index"].__globals__,
                app.view_functions["wanted_detail"].__globals__,
            ]
            originals = []
            for target in targets:
                original = target.get("user_has_warrant_access")
                originals.append((target, original))
                target["user_has_warrant_access"] = lambda: True
            try:
                yield
            finally:
                for target, original in originals:
                    if original is None:
                        target.pop("user_has_warrant_access", None)
                    else:
                        target["user_has_warrant_access"] = original

        return _grant()

    def test_wanted_index_redirects_without_access(self):
        resp = self.client.get("/wanted?q=Jane")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/wanted/subscribe", resp.headers.get("Location", ""))

    def test_wanted_index_renders_record_with_access(self):
        with self._with_warrant_access():
            resp = self.client.get("/wanted?q=Jane")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Jane Doe", body)
        self.assertIn("WANTED", body)

    def test_wanted_index_renders_photo_when_present(self):
        self.conn.execute(
            "UPDATE warrants SET mugshot_url = ? WHERE source_record_id = ?",
            ("https://example.com/mugshot.jpg", "test-warrant:jane-doe"),
        )
        self.conn.commit()
        with self._with_warrant_access():
            resp = self.client.get("/wanted?q=Jane")
        body = resp.get_data(as_text=True)
        self.assertIn("wanted-poster-card__photo", body)
        self.assertIn("https://example.com/mugshot.jpg", body)
        self.assertNotIn("No photo", body)


    def test_wanted_detail_redirects_without_access(self):
        slug = warrant_slug("test-warrant:jane-doe")
        resp = self.client.get(f"/wanted/{slug}")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/wanted/subscribe", resp.headers.get("Location", ""))

    def test_wanted_detail_renders_record(self):
        slug = warrant_slug("test-warrant:jane-doe")
        with self._with_warrant_access():
            resp = self.client.get(f"/wanted/{slug}")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("Jane Doe", body)
        self.assertIn("CRIMINAL CONTEMPT", body)

    def test_wanted_detail_renders_staff_photo_override(self):
        self.conn.execute(
            """
            UPDATE warrants
               SET mugshot_url = ?, photo_url = ?
             WHERE source_record_id = ?
            """,
            (
                "https://example.com/official.jpg",
                "https://example.com/staff.jpg",
                "test-warrant:jane-doe",
            ),
        )
        self.conn.commit()
        slug = warrant_slug("test-warrant:jane-doe")
        with self._with_warrant_access():
            resp = self.client.get(f"/wanted/{slug}")
        body = resp.get_data(as_text=True)
        self.assertIn("https://example.com/staff.jpg", body)
        self.assertIn("Staff-approved photo", body)

    def test_resolved_warrant_shows_stamp_on_index(self):
        self.conn.execute(
            """
            UPDATE warrants
               SET status = 'resolved', resolved_at = ?
             WHERE source_record_id = ?
            """,
            (self.run_ts, "test-warrant:jane-doe"),
        )
        self.conn.commit()
        with self._with_warrant_access():
            resp = self.client.get("/wanted?status=all")
        body = resp.get_data(as_text=True)
        self.assertIn("RESOLVED", body)
        self.assertIn("wanted-poster-card__stamp", body)

    def test_resolved_warrant_shows_stamp_on_detail(self):
        self.conn.execute(
            """
            UPDATE warrants
               SET status = 'resolved', resolved_at = ?
             WHERE source_record_id = ?
            """,
            (self.run_ts, "test-warrant:jane-doe"),
        )
        self.conn.commit()
        slug = warrant_slug("test-warrant:jane-doe")
        with self._with_warrant_access():
            resp = self.client.get(f"/wanted/{slug}")
        body = resp.get_data(as_text=True)
        self.assertIn("RESOLVED", body)

    def test_warrant_city_context_matches_county(self):
        self._insert_warrant(
            source_record_id="test-warrant:county-only",
            person_name="County Person",
            county="Yellowstone",
            city="",
            status="active",
        )
        context = warrant_city_context(self.conn, "Billings", "Yellowstone", limit=6)
        names = [row["person_name"] for row in context["rows"]]
        self.assertIn("County Person", names)

    def test_warrant_public_context_hides_rows_without_access(self):
        context = warrant_public_context(self.conn, has_access=False)
        self.assertEqual(context["rows"], [])
        self.assertEqual(context["pagination"]["total"], 1)

    def test_warrant_homepage_context_hides_rows_without_access(self):
        context = warrant_homepage_context(self.conn, has_access=False)
        self.assertEqual(context["rows"], [])
        self.assertEqual(context["active_rows"], [])
        self.assertEqual(context["resolved_rows"], [])

    def test_warrant_city_context_hides_rows_without_access(self):
        self._insert_warrant(
            source_record_id="test-warrant:city-only",
            person_name="City Person",
            county="Yellowstone",
            city="Billings",
            status="active",
        )
        context = warrant_city_context(
            self.conn, "Billings", "Yellowstone", limit=6, has_access=False
        )
        self.assertEqual(context["rows"], [])

    def test_resolve_stale_warrants_marks_missing_active_records(self):
        self._insert_warrant(
            source_record_id="test-warrant:stale-person",
            person_name="Stale Person",
            county="Test",
            status="active",
        )
        resolved = resolve_stale_warrants(
            self.conn,
            "Test",
            {"test-warrant:jane-doe"},
            self.run_ts,
        )
        self.assertEqual(resolved, 1)
        row = self.conn.execute(
            "SELECT status, resolved_at FROM warrants WHERE source_record_id = ?",
            ("test-warrant:stale-person",),
        ).fetchone()
        self.assertEqual(row["status"], "resolved")
        self.assertTrue(row["resolved_at"])

    def test_upsert_reactivates_resolved_warrant(self):
        self.conn.execute(
            """
            UPDATE warrants
               SET status = 'resolved', resolved_at = ?
             WHERE source_record_id = ?
            """,
            (self.run_ts, "test-warrant:jane-doe"),
        )
        self.conn.commit()
        record = WarrantRecord(
            source_record_id="test-warrant:jane-doe",
            county="Test",
            person_name="Jane Doe",
            charges_text="CRIMINAL CONTEMPT",
            status="active",
            source_url="https://example.gov/warrant/1",
        )
        upsert_warrants(self.conn, [record], self.run_ts)
        row = self.conn.execute(
            "SELECT status, resolved_at FROM warrants WHERE source_record_id = ?",
            ("test-warrant:jane-doe",),
        ).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["resolved_at"], "")


if __name__ == "__main__":
    unittest.main()
