import sqlite3
import unittest

from agency_normalization import (
    normalize_agency_identity,
    normalize_existing_post_agencies,
    normalize_post_title,
)


class AgencyNormalizationTests(unittest.TestCase):
    def test_normalizes_uppercase_police_department(self) -> None:
        name, agency_type = normalize_agency_identity(
            "HAVRE POLICE DEPARTMENT",
            "police",
            county="Hill",
        )

        self.assertEqual(name, "Havre Police Department")
        self.assertEqual(agency_type, "police")

    def test_maps_county_police_to_city_police_when_city_known(self) -> None:
        name, agency_type = normalize_agency_identity(
            "Yellowstone County Police",
            "other",
            county="Yellowstone",
            city="Billings",
        )

        self.assertEqual(name, "Billings Police Department")
        self.assertEqual(agency_type, "police")

    def test_maps_county_police_to_sheriff_when_city_missing(self) -> None:
        name, agency_type = normalize_agency_identity(
            "Flathead Police Department",
            "police",
            county="Flathead",
        )

        self.assertEqual(name, "Flathead County Sheriff's Office")
        self.assertEqual(agency_type, "sheriff")

    def test_extracts_real_agency_from_junk_phrase(self) -> None:
        name, agency_type = normalize_agency_identity(
            "NEEDED THE PHONE NUMBER FOR GREAT FALLS POLICE DEPARTMENT",
            "police",
            county="Hill",
        )

        self.assertEqual(name, "Great Falls Police Department")
        self.assertEqual(agency_type, "police")

    def test_normalizes_default_activity_title(self) -> None:
        title = normalize_post_title(
            "Daily Police Activity Report - Yellowstone County Police",
            "Yellowstone County Police",
            "Billings Police Department",
            county="Yellowstone",
            city="Billings",
            agency_type="police",
        )

        self.assertEqual(title, "Daily Police Activity Report - Billings Police Department")

    def test_backfills_existing_posts(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                agency_name TEXT,
                agency_type TEXT,
                county TEXT,
                city TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO posts (title, agency_name, agency_type, county, city)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Daily Police Activity Report - Yellowstone County Police",
                "Yellowstone County Police",
                "other",
                "Yellowstone",
                "Billings",
            ),
        )

        changed = normalize_existing_post_agencies(conn)
        row = conn.execute(
            "SELECT title, agency_name, agency_type FROM posts"
        ).fetchone()

        self.assertEqual(changed, 1)
        self.assertEqual(row["agency_name"], "Billings Police Department")
        self.assertEqual(row["agency_type"], "police")
        self.assertEqual(
            row["title"],
            "Daily Police Activity Report - Billings Police Department",
        )


if __name__ == "__main__":
    unittest.main()
