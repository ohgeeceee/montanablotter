import unittest

from services.ingestion.fetchers.lincoln_inmate import _parse_roster_text, _parse_name


class LincolnInmateParserTests(unittest.TestCase):
    def test_parse_name_with_middle_initial(self) -> None:
        self.assertEqual(_parse_name("BAKER,DAISYR"), "Baker, Daisy R")
        self.assertEqual(_parse_name("BERNHARD,DAWNM"), "Bernhard, Dawn M")

    def test_parses_inmate_blocks(self) -> None:
        text = """Lincoln County Sheriff's Office
Current Inmate Offense List, by Name
Booking#: 932 Name: BAKER,DAISYR NameNumber: 340
Statute Offense Court Offense Class
45-5-626(3)[1st] AllOtherOffenses ECC 90Z
46-9-503 AllOtherOffenses LCDC 90Z M
Booking#: 26BK00243 Name: BERNHARD,DAWNM NameNumber: 7052
Statute Offense Court Offense Class
45-5-212 AggravatedAssault LCJC 13A F
"""
        records = _parse_roster_text(text, "https://example.com/roster.pdf")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].person_name, "Baker, Daisy R")
        self.assertEqual(records[0].booking_number, "932")
        self.assertEqual(records[1].person_name, "Bernhard, Dawn M")
        self.assertIn("AggravatedAssault", records[1].charges_summary)


if __name__ == "__main__":
    unittest.main()
