"""County detection for email blotter intake.

The intake pipeline used to recognise only ~12 counties, so a blotter mailed by
any other Montana county was filed under "Unknown" and never reached its
county page. These tests pin the all-county map load and matching behaviour.
"""
import unittest
from pathlib import Path

import email_image_blotter as eib


class CountyDetectionTests(unittest.TestCase):
    def setUp(self):
        self.worker = eib.ImageBlotterWorker()

    def test_extended_maps_load(self):
        # The generated JSON must be found next to this module; a wrong path
        # silently degrades back to the 15-entry default table.
        self.assertGreater(len(self.worker._county_map), 50)
        self.assertGreater(len(self.worker._agency_map), 50)

    def test_all_56_counties_are_reachable_by_name(self):
        expected = {
            "Beaverhead", "Big Horn", "Blaine", "Broadwater", "Carbon", "Carter",
            "Cascade", "Chouteau", "Custer", "Daniels", "Dawson", "Deer Lodge",
            "Fallon", "Fergus", "Flathead", "Gallatin", "Garfield", "Glacier",
            "Granite", "Hill", "Jefferson", "Lake", "Lewis and Clark", "Lincoln",
            "Madison", "McCone", "Meagher", "Mineral", "Missoula", "Musselshell",
            "Park", "Phillips", "Pondera", "Powell", "Prairie", "Ravalli",
            "Richland", "Roosevelt", "Rosebud", "Sanders", "Sheridan",
            "Silver Bow", "Stillwater", "Sweet Grass", "Teton", "Toole",
            "Treasure", "Valley", "Wheatland", "Yellowstone",
        }
        detected = {self.worker._detect_county(f"{name} County") for name in expected}
        missing = expected - detected
        self.assertEqual(missing, set(), f"counties not detectable: {sorted(missing)}")

    def test_seat_city_resolves_to_county(self):
        cases = {
            "Havre": "Hill",
            "Great Falls": "Cascade",
            "Bozeman": "Gallatin",
            "Billings": "Yellowstone",
            "Kalispell": "Flathead",
        }
        for city, county in cases.items():
            self.assertEqual(self.worker._detect_county(city), county)

    def test_longest_key_wins(self):
        # "cascade county" must not be shadowed by a shorter stem.
        self.assertEqual(
            self.worker._detect_county("sheriff@cascadecountymt.gov Cascade County Jail Roster"),
            "Cascade",
        )

    def test_unknown_text_returns_none(self):
        self.assertIsNone(self.worker._detect_county("Subject: Daily report"))
        self.assertIsNone(self.worker._detect_county(""))

    def test_agency_detection(self):
        self.assertEqual(
            self.worker._detect_agency("Sanders County Daily Blotter"),
            "Sanders County Sheriff's Office",
        )
        self.assertEqual(
            self.worker._detect_agency("From: havre police"),
            "Havre Police Department",
        )

    def test_generated_map_file_exists(self):
        path = Path(eib.__file__).resolve().parent / "data" / "county_detection_maps.json"
        self.assertTrue(path.is_file(), f"missing {path}")


if __name__ == "__main__":
    unittest.main()
