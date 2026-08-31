import os
import sqlite3
import tempfile
import unittest
import unittest.mock

import app as app_module
import config
import init_db


class PublicApiTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-public-api-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
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
        bootstrap_conn.commit()
        bootstrap_conn.close()

        init_db.init_database()
        init_db.migrate()
        self.clean_post_id, self.pending_post_id = self._seed_api_content()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_api_content(self) -> tuple[int, int]:
        conn = app_module.get_db()
        blotter_id = conn.execute(
            """
            INSERT INTO blotters (filename, county, status, file_path, source_type, upload_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("api-report.pdf", "Yellowstone", "processed", "/tmp/api-report.pdf", "local_pdf", "2026-04-21"),
        ).lastrowid
        clean_post_id = conn.execute(
            """
            INSERT INTO posts (
                blotter_id, title, summary, city, county, agency_type, agency_name,
                incident_date, incident_type, created_at, audit_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                "Clean Daily Report",
                "Clean summary.",
                "Billings",
                "Yellowstone",
                "police",
                "Billings Police Department",
                "2026-04-21",
                "Daily Digest",
                "2026-04-21 08:00:00",
                "clean",
            ),
        ).lastrowid
        pending_post_id = conn.execute(
            """
            INSERT INTO posts (
                blotter_id, title, summary, city, county, agency_type, agency_name,
                incident_date, incident_type, created_at, audit_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                "Pending Daily Report",
                "Pending summary.",
                "Billings",
                "Yellowstone",
                "police",
                "Billings Police Department",
                "2026-04-21",
                "Daily Digest",
                "2026-04-21 09:00:00",
                "pending",
            ),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO records (
                blotter_id, date, time, incident, incident_type, location, details, county, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                "2026-04-21",
                "08:30",
                "Theft",
                "Theft",
                "100 Main St",
                "Record details.",
                "Yellowstone",
                "2026-04-21 08:35:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO blog_posts (title, slug, body, excerpt, author, published, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("Published Blog", "published-blog", "Body", "Excerpt", "Staff", 1, "2026-04-21 10:00:00"),
        )
        conn.execute(
            """
            INSERT INTO blog_posts (title, slug, body, excerpt, author, published, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("Draft Blog", "draft-blog", "Body", "Excerpt", "Staff", 0, "2026-04-21 10:00:00"),
        )
        conn.commit()
        conn.close()
        return int(clean_post_id), int(pending_post_id)

    def test_posts_api_returns_only_clean_posts(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/api/posts")
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["posts"][0]["id"], self.clean_post_id)
        self.assertEqual(payload["posts"][0]["source_pdf_name"], "api-report.pdf")

    def test_post_detail_api_404s_pending_posts(self) -> None:
        client = app_module.app.test_client()

        clean_response = client.get(f"/api/posts/{self.clean_post_id}")
        pending_response = client.get(f"/api/posts/{self.pending_post_id}")

        self.assertEqual(clean_response.status_code, 200)
        self.assertEqual(clean_response.get_json()["source_pdf_name"], "api-report.pdf")
        self.assertEqual(pending_response.status_code, 404)

    def test_directory_and_stats_api_match_mobile_contract(self) -> None:
        client = app_module.app.test_client()

        counties = client.get("/api/counties").get_json()
        agencies = client.get("/api/agencies").get_json()
        stats = client.get("/api/stats").get_json()

        self.assertEqual(counties["counties"][0]["county"], "Yellowstone")
        self.assertEqual(agencies["agencies"][0]["agency_name"], "Billings Police Department")
        self.assertEqual(stats["total_posts"], 1)
        self.assertEqual(stats["total_records"], 1)

    def test_blog_api_returns_only_published_posts(self) -> None:
        client = app_module.app.test_client()

        listing = client.get("/api/blog").get_json()
        detail = client.get("/api/blog/published-blog").get_json()
        draft_response = client.get("/api/blog/draft-blog")

        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["posts"][0]["slug"], "published-blog")
        self.assertEqual(detail["body"], "Body")
        self.assertEqual(draft_response.status_code, 404)

    def test_subscribe_event_is_recorded_without_blocking_request_path(self) -> None:
        client = app_module.app.test_client()

        response = client.post(
            "/api/subscribe-event",
            json={
                "event_type": "cta_click",
                "source": "homepage_banner",
                "page_path": "/",
            },
        )

        self.assertEqual(response.status_code, 204)

        conn = app_module.get_db()
        row = conn.execute(
            """
            SELECT event_type, source, page_path
            FROM subscribe_events
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["event_type"], "cta_click")
        self.assertEqual(row["source"], "homepage_banner")
        self.assertEqual(row["page_path"], "/")

    @unittest.mock.patch.dict(os.environ, {"MB_REQUIRE_SIGNIN": "true"})
    def test_api_posts_is_exempt_from_signin_wall(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/api/posts")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["posts"][0]["id"], self.clean_post_id)

    def test_api_not_found_returns_json(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/api/posts/999999")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.content_type.startswith("application/json"))
        payload = response.get_json()
        self.assertIn("error", payload)
        self.assertIn("message", payload)

    def test_geo_incidents_date_filter_handles_mmddyy_format(self) -> None:
        # Records are stored in mixed formats (MM/DD/YY and ISO). The geo
        # incidents/heatmap endpoints must normalize against the ISO date
        # filters the Crime Map sends, otherwise last-N-days filters drop
        # every MM/DD/YY record and the map renders empty.
        conn = app_module.get_db()
        blotter_id = conn.execute(
            "INSERT INTO blotters (filename, county, status, file_path, source_type, upload_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("geo-report.pdf", "Gallatin", "processed", "/tmp/geo-report.pdf", "local_pdf", "2026-08-25"),
        ).lastrowid
        record_id = conn.execute(
            "INSERT INTO records (blotter_id, date, time, incident, incident_type, location, county, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (blotter_id, "08/25/26", "12:00", "DUI", "DUI", "Main St", "Gallatin", "2026-08-25 12:00:00"),
        ).lastrowid
        conn.execute(
            "INSERT INTO incident_geocodes (record_id, raw_location, lat, lng, geocode_confidence, county, city, geocoded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, "Main St", 45.6769, -111.0429, "medium", "Gallatin", "Bozeman", "2026-08-25 12:00:00"),
        )
        conn.commit()
        conn.close()

        client = app_module.app.test_client()
        response = client.get("/api/geo/incidents?date_from=2026-08-01&date_to=2026-08-30&limit=10")
        self.assertEqual(response.status_code, 200)
        incidents = response.get_json()["incidents"]
        self.assertTrue(
            any(i["id"] == record_id for i in incidents),
            "MM/DD/YY record should be returned by ISO date filter",
        )

        heat = client.get("/api/geo/heatmap?date_from=2026-08-01&date_to=2026-08-30")
        self.assertEqual(heat.status_code, 200)
        self.assertGreater(len(heat.get_json()["points"]), 0)

    def test_finalize_geocode_coords_scatter_is_deterministic(self) -> None:
        # Place-name-only locations (no street number) must be scattered so the
        # map does not pile thousands of records onto one city-centroid point.
        from services.geo.pipeline import finalize_geocode_coords

        # "Bozeman, MT" has no digit -> should move from the exact centroid.
        lat, lng = finalize_geocode_coords(12345, "Bozeman, MT", 45.676998, -111.042931)
        self.assertNotEqual((lat, lng), (45.676998, -111.042931))

        # Deterministic: same record_id + raw location -> same scattered coords.
        lat2, lng2 = finalize_geocode_coords(12345, "Bozeman, MT", 45.676998, -111.042931)
        self.assertEqual((lat, lng), (lat2, lng2))

        # Street-level location (has a number) keeps exact coordinates.
        slat, slng = finalize_geocode_coords(999, "100 Main St", 45.6700, -111.0400)
        self.assertEqual((slat, slng), (45.6700, -111.0400))


if __name__ == "__main__":
    unittest.main()
