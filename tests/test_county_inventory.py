"""Regression coverage for the county inventory builder.

The inventory drives coverage prioritization across all 56 counties, so
membership and classification bugs have outsized impact. These tests pin:
- The county list is exactly Montana's 56 counties (Granite/Wibaux were
  missing and non-counties "Brown"/"Pendroy" were present until 2026-11).
- Fetcher discovery is token-based ("hill" must not match phillips_inmate).
- Status classification uses jail booking volume and source health, not
  just blotter PDF counts.
"""
import os
import sqlite3
import tempfile
import unittest

import county_inventory


CANONICAL_MT_COUNTIES = {
    "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Carbon", "Carter",
    "Cascade", "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge",
    "Fallon", "Fergus", "Flathead", "Gallatin", "Garfield", "Glacier",
    "Golden Valley", "Granite", "Hill", "Jefferson", "Judith Basin", "Lake",
    "Lewis and Clark", "Liberty", "Lincoln", "Madison", "McCone", "Meagher",
    "Mineral", "Missoula", "Musselshell", "Park", "Petroleum", "Phillips",
    "Pondera", "Powder River", "Powell", "Prairie", "Ravalli", "Richland",
    "Roosevelt", "Rosebud", "Sanders", "Sheridan", "Silver Bow",
    "Stillwater", "Sweet Grass", "Teton", "Toole", "Treasure", "Valley",
    "Wheatland", "Wibaux", "Yellowstone",
}


class CountyListIntegrityTestCase(unittest.TestCase):
    def test_exactly_montanas_56_counties(self):
        listed = set(county_inventory.ALL_COUNTIES)
        self.assertEqual(len(county_inventory.ALL_COUNTIES), 56)
        self.assertEqual(len(listed), 56, "duplicate county names in ALL_COUNTIES")
        self.assertEqual(listed, CANONICAL_MT_COUNTIES)

    def test_population_rank_covers_same_counties(self):
        self.assertEqual(set(county_inventory.POPULATION_RANK), CANONICAL_MT_COUNTIES)
        self.assertEqual(len(county_inventory.POPULATION_RANK), 56)

    def test_known_non_counties_removed(self):
        for fake in ("Brown", "Pendroy"):
            self.assertNotIn(fake, county_inventory.ALL_COUNTIES)
            self.assertNotIn(fake, county_inventory.POPULATION_RANK)


class FetcherDiscoveryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="mb-fetchers-")
        self._previous = county_inventory.FETCHERS_DIR
        county_inventory.FETCHERS_DIR = self._tmp

    def tearDown(self):
        county_inventory.FETCHERS_DIR = self._previous

    def _seed(self, *filenames):
        for fname in filenames:
            with open(os.path.join(self._tmp, fname), "w") as fh:
                fh.write("# fetcher stub\n")

    def test_exact_and_multiword_counties_match(self):
        self._seed("hill_inmate.py", "lewis_clark_inmate.py", "silver_bow_inmate.py")
        self.assertEqual(county_inventory.get_fetcher_for_county("Hill"), "hill_inmate.py")
        self.assertEqual(county_inventory.get_fetcher_for_county("Lewis and Clark"), "lewis_clark_inmate.py")
        self.assertEqual(county_inventory.get_fetcher_for_county("Silver Bow"), "silver_bow_inmate.py")

    def test_substring_false_positive_rejected(self):
        # "hill" is a substring of "phillips" — must not match.
        self._seed("phillips_inmate.py")
        self.assertEqual(county_inventory.get_fetcher_for_county("Hill"), "")

    def test_lookalike_county_names_stay_distinct(self):
        self._seed("carbon_inmate.py", "carter_inmate.py", "cascade_roster.py")
        self.assertEqual(county_inventory.get_fetcher_for_county("Carter"), "carter_inmate.py")
        self.assertEqual(county_inventory.get_fetcher_for_county("Carbon"), "carbon_inmate.py")
        self.assertEqual(county_inventory.get_fetcher_for_county("Cascade"), "cascade_roster.py")

    def test_missing_dir_returns_empty(self):
        county_inventory.FETCHERS_DIR = os.path.join(self._tmp, "does-not-exist")
        self.assertEqual(county_inventory.get_fetcher_for_county("Hill"), "")


class InventoryClassificationTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="mb-inventory-", suffix=".db")
        os.close(fd)
        self._prev_db = county_inventory.DB_PATH
        county_inventory.DB_PATH = self.db_path
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE jail_booking_sources (
                id INTEGER PRIMARY KEY,
                county_slug TEXT,
                county_name TEXT,
                facility_name TEXT,
                roster_url TEXT,
                last_success_at TEXT,
                latest_error TEXT
            );
            CREATE TABLE jail_bookings (
                id INTEGER PRIMARY KEY,
                county_name TEXT,
                first_seen_at TEXT
            );
            CREATE TABLE blotters (
                id INTEGER PRIMARY KEY,
                county TEXT,
                upload_date TEXT
            );
            CREATE TABLE records (
                id INTEGER PRIMARY KEY,
                county TEXT
            );
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        county_inventory.DB_PATH = self._prev_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_county(self, county, *, bookings=0, success_at=None, error="", records=0):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO jail_booking_sources (county_name, roster_url, last_success_at, latest_error) VALUES (?, 'https://example.test', ?, ?)",
            (county, success_at, error),
        )
        for _ in range(bookings):
            conn.execute(
                "INSERT INTO jail_bookings (county_name, first_seen_at) VALUES (?, datetime('now'))",
                (county,),
            )
        for _ in range(records):
            conn.execute("INSERT INTO records (county) VALUES (?)", (county,))
        conn.commit()
        conn.close()

    def _status_for(self, county):
        inventory = county_inventory.build_inventory()
        by_county = {item["county"]: item for item in inventory}
        self.assertIn(county, by_county, "county missing from inventory")
        return by_county[county]

    def test_active_when_recent_jail_volume(self):
        self._seed_county("Gallatin", bookings=15, success_at="2026-11-18 00:00:00")
        self.assertEqual(self._status_for("Gallatin")["status"], "active")

    def test_partial_with_any_data(self):
        self._seed_county("Lake", bookings=1, success_at="2026-11-18 00:00:00")
        self.assertEqual(self._status_for("Lake")["status"], "partial")

    def test_partial_with_records_but_no_bookings(self):
        self._seed_county("Mineral", records=3, success_at="2026-11-18 00:00:00")
        self.assertEqual(self._status_for("Mineral")["status"], "partial")

    def test_failing_when_error_and_no_recent_success(self):
        self._seed_county("Broadwater", error="Site times out", success_at=None)
        item = self._status_for("Broadwater")
        self.assertEqual(item["status"], "failing")
        self.assertIn("times out", item["latest_error"])

    def test_configured_when_source_but_no_data(self):
        self._seed_county("Daniels", success_at="2026-11-18 00:00:00")
        self.assertEqual(self._status_for("Daniels")["status"], "configured")

    def test_not_covered_when_nothing(self):
        self.assertEqual(self._status_for("Wibaux")["status"], "not_covered")

    def test_every_county_present_in_output(self):
        self._seed_county("Gallatin", bookings=15)
        inventory = county_inventory.build_inventory()
        self.assertEqual(len(inventory), 56)
        self.assertEqual(
            {item["county"] for item in inventory},
            set(county_inventory.ALL_COUNTIES),
        )


if __name__ == "__main__":
    unittest.main()
