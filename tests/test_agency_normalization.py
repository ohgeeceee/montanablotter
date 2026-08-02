import sqlite3
import unittest

from core.agency_normalization import (
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

    def test_does_not_duplicate_agency_name_in_title(self) -> None:
        # Regression for montanablotter.com/post/765 -- a looping re.sub in
        # normalize_post_title was stacking "Department" ~150x onto titles
        # whose raw agency name was already the canonical form.
        title = normalize_post_title(
            "Daily Police Activity Report - Missoula Police Department",
            "Missoula Police Department",
            "Missoula Police Department",
            county="Missoula",
            city="Missoula",
            agency_type="police",
        )
        self.assertEqual(
            title,
            "Daily Police Activity Report - Missoula Police Department",
        )

    def test_does_not_loop_variant_phrase_into_canonical_form(self) -> None:
        # Same bug class, raw agency != normalized output: word boundaries
        # must keep the variant phrase from doubling inside the canonical form
        # (e.g. "Hill Police" is a prefix of "Hill Police Department").
        title = normalize_post_title(
            "Daily Police Activity Report - Hill Police Department",
            "Hill Police Department",
            "Hill County Sheriff's Office",
            county="Hill",
            city="Havre",
            agency_type="police",
        )
        self.assertEqual(
            title,
            "Daily Police Activity Report - Hill County Sheriff's Office",
        )

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
