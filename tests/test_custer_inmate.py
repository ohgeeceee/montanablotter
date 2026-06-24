import unittest

from services.ingestion.fetchers.custer_inmate import _parse_roster_text


class CusterInmateParserTests(unittest.TestCase):
    def test_parses_simple_entry(self) -> None:
        text = """CCDF Inmate Daily Roster Printed on June 16, 2026
Last, First Name Booking Date Charges Jacket # Agency Bond
BART, BRYNNA 05/25/26 19:40 45-5-628 - Criminal Child Endangerment; 2602308 Montana Highway Cash/Surety - $50000.00 -
45-5-628 - Criminal Child Endangerment Patrol Judge
"""
        records = _parse_roster_text(text, "https://example.com/roster.pdf")
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.person_name, "Bart, Brynna")
        self.assertEqual(rec.booking_at, "2026-05-25 19:40:00")
        self.assertEqual(rec.booking_number, "2602308")
        self.assertIn("Criminal Child Endangerment", rec.charges_summary)
        self.assertIn("Cash/Surety $50000.00", rec.charges_summary)

    def test_parses_no_bond_entry(self) -> None:
        text = """CCDF Inmate Daily Roster Printed on June 16, 2026
Last, First Name Booking Date Charges Jacket # Agency Bond
BEIERLE, JERMEY 04/23/26 14:23 46-23-1012 - Probation Violation; 46-18-203 - 2100354 Department of No Bond - $0.00
Revocation of Suspended or Deferred Corrections No Bond - $0.00
Sentence; 46-18-203 - Revocation of
Suspended or Deferred Sentence
"""
        records = _parse_roster_text(text, "https://example.com/roster.pdf")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].booking_number, "2100354")
        self.assertIn("No Bond $0.00", records[0].charges_summary)

    def test_skips_header_lines(self) -> None:
        text = """CCDF Inmate Daily Roster Printed on June 16, 2026
Last, First Name Booking Date Charges Jacket # Agency Bond
Total Records: 0
"""
        records = _parse_roster_text(text, "https://example.com/roster.pdf")
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
