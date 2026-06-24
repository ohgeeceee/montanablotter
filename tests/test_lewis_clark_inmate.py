import unittest

from services.ingestion.fetchers.lewis_clark_inmate import _parse_roster_text


class LewisClarkInmateParserTests(unittest.TestCase):
    def test_parses_complete_entry(self) -> None:
        text = """Jail Roster Printed on June 16, 2026
Name Age Sex Booking Date & Time Booking # Hold Reasons
ALEXANDER, DARREN 34 Male 04/11/24 09:56 24-00741 Warrant: Unspecified warrant DDC-2024-192
ALLEN, MATTHEW 31 Male 03/28/26 14:12 26-00581 P&P 72 Hour
"""
        records = _parse_roster_text(text, "https://example.com/roster.pdf")
        self.assertEqual(len(records), 2)
        rec = records[0]
        self.assertEqual(rec.person_name, "Alexander, Darren")
        self.assertEqual(rec.age, 34)
        self.assertEqual(rec.booking_at, "2024-04-11 09:56:00")
        self.assertEqual(rec.booking_number, "24-00741")
        self.assertIn("Warrant", rec.charges_summary)

    def test_parses_entry_missing_first_name(self) -> None:
        text = """Jail Roster Printed on June 16, 2026
Name Age Sex Booking Date & Time Booking # Hold Reasons
ANDERSON, 28 Male 06/16/26 09:32 26-01110 Charge: 45-5-206
"""
        records = _parse_roster_text(text, "https://example.com/roster.pdf")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Anderson")
        self.assertEqual(records[0].booking_number, "26-01110")

    def test_skips_header_lines(self) -> None:
        text = """Jail Roster Printed on June 16, 2026
Name Age Sex Booking Date & Time Booking # Hold Reasons
"""
        records = _parse_roster_text(text, "https://example.com/roster.pdf")
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
