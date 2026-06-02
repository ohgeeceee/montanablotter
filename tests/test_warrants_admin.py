"""Tests for warrant photo admin helpers."""

import sqlite3
import unittest

from services.ingestion.warrants.models import ensure_warrant_schema
from services.persons.warrants_admin import (
    facebook_people_search_url,
    update_warrant_photo_fields,
    warrant_admin_context,
)


class WarrantsAdminTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_warrant_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO warrants (
                source_record_id, county, city, person_name, status,
                mugshot_url, source_url, scraped_at, first_seen_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "flathead-warrant:1",
                "Flathead",
                "Kalispell, MT",
                "Jane Doe",
                "active",
                "https://apps.flathead.mt.gov/warrants/image_thumb_script.php?f=1",
                "https://apps.flathead.mt.gov/warrants/warrants_view.php?line=1",
                "2026-05-31 00:00:00",
                "2026-05-31 00:00:00",
                "2026-05-31 00:00:00",
            ),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_facebook_search_url_includes_name_and_city(self):
        url = facebook_people_search_url("Jane Doe", "Kalispell, MT")
        self.assertIn("facebook.com/search/people", url)
        self.assertIn("Jane", url)
        self.assertIn("Montana", url)

    def test_warrant_admin_context_photo_filter(self):
        context = warrant_admin_context(self.conn, photo_filter="with_photo")
        self.assertEqual(len(context["rows"]), 1)
        missing = warrant_admin_context(self.conn, photo_filter="missing_photo")
        self.assertEqual(len(missing["rows"]), 0)

    def test_update_warrant_photo_fields(self):
        record = update_warrant_photo_fields(
            self.conn,
            1,
            photo_url="https://cdn.example/photo.jpg",
            social_profile_url="https://www.facebook.com/example",
            run_ts="2026-05-31 12:00:00",
        )
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["photo_url"], "https://cdn.example/photo.jpg")
        self.assertEqual(record["social_profile_url"], "https://www.facebook.com/example")
        self.assertEqual(record["display_photo_url"], "https://cdn.example/photo.jpg")


if __name__ == "__main__":
    unittest.main()
