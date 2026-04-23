"""
Test suite for pdf_parser.py text blotter parsing functionality.
Covers date normalization, parser robustness, and edge cases.
"""

import textwrap
import unittest
from pdf_parser import (
    normalize_date,
    parse_text_blotter,
    BlotterParser,
)


class TestNormalizeDate(unittest.TestCase):
    """Test the unified date normalization function."""

    # Standard formats (already worked)
    def test_slash_2digit_year(self):
        self.assertEqual(normalize_date("03/14/26"), "03/14/26")
        self.assertEqual(normalize_date("12/01/25"), "12/01/25")

    # 4-digit year variants (the main fix)
    def test_slash_4digit_year(self):
        self.assertEqual(normalize_date("03/14/2026"), "03/14/26")
        self.assertEqual(normalize_date("01/01/2025"), "01/01/25")

    # Dash-separated formats (new support)
    def test_dash_2digit_year(self):
        self.assertEqual(normalize_date("03-14-26"), "03/14/26")
        self.assertEqual(normalize_date("12-01-25"), "12/01/25")

    def test_dash_4digit_year(self):
        self.assertEqual(normalize_date("03-14-2026"), "03/14/26")
        self.assertEqual(normalize_date("01-01-2025"), "01/01/25")

    # ISO format (new support)
    def test_iso_format(self):
        self.assertEqual(normalize_date("2026-03-14"), "03/14/26")
        self.assertEqual(normalize_date("2025-12-01"), "12/01/25")

    # Long month formats (Helena email style)
    def test_long_month_with_comma(self):
        self.assertEqual(normalize_date("March 14, 2026"), "03/14/26")
        self.assertEqual(normalize_date("January 1, 2025"), "01/01/25")

    def test_long_month_without_comma(self):
        self.assertEqual(normalize_date("March 14 2026"), "03/14/26")
        self.assertEqual(normalize_date("December 31 2025"), "12/31/25")

    # Short month formats
    def test_short_month_with_comma(self):
        self.assertEqual(normalize_date("Mar 14, 2026"), "03/14/26")
        self.assertEqual(normalize_date("Jan 1, 2025"), "01/01/25")

    def test_short_month_without_comma(self):
        self.assertEqual(normalize_date("Mar 14 2026"), "03/14/26")
        self.assertEqual(normalize_date("Dec 31 2025"), "12/31/25")

    # Edge cases
    def test_empty_string(self):
        self.assertIsNone(normalize_date(""))
        self.assertIsNone(normalize_date("   "))

    def test_none_input(self):
        self.assertIsNone(normalize_date(None))

    def test_invalid_date(self):
        self.assertIsNone(normalize_date("not-a-date"))
        self.assertIsNone(normalize_date("13/32/2026"))  # Invalid day
        self.assertIsNone(normalize_date("25/01/2026"))   # Invalid month

    def test_with_whitespace(self):
        self.assertEqual(normalize_date("  03/14/26  "), "03/14/26")
        self.assertEqual(normalize_date("\t03/14/2026\t"), "03/14/26")


class TestGCSOParser(unittest.TestCase):
    """Test GCSO format parsing with various date formats."""

    def test_gcso_2digit_year(self):
        """Original GCSO format with 2-digit year - should work."""
        text = textwrap.dedent("""\
            GALLATIN COUNTY SHERIFFS OFFICE
            CFS Date/Time    CFS Number    Location    Code
            03/14/26 08:12:01 CFS26-1001 123 Main St Welfare Check
            03/14/26 09:15:22 CFS26-1002 456 Oak Ave Traffic Stop
            """)
        result = parse_text_blotter(text)
        self.assertIn(result["county"].lower(), ["gallatin", "gcs0", "gcs o"])
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_gcso_4digit_year(self):
        """GCSO format with 4-digit year - the main bug fix."""
        text = textwrap.dedent("""\
            GALLATIN COUNTY SHERIFFS OFFICE
            CFS Date/Time    CFS Number    Location    Code
            03/14/2026 08:12:01 CFS26-1001 123 Main St Welfare Check
            03/14/2026 09:15:22 CFS26-1002 456 Oak Ave Traffic Stop
            """)
        result = parse_text_blotter(text)
        self.assertIn(result["county"].lower(), ["gallatin", "gcs0", "gcs o"])
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_gcso_mixed_years(self):
        """GCSO with mixed 2-digit and 4-digit years."""
        text = textwrap.dedent("""\
            GALLATIN COUNTY SHERIFFS OFFICE
            CFS Date/Time    CFS Number    Location    Code
            03/14/26 08:12:01 CFS26-1001 123 Main St Welfare Check
            03/15/2026 09:15:22 CFS26-1002 456 Oak Ave Traffic Stop
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")
        self.assertEqual(result["incidents"][1]["date"], "03/15/26")

    def test_gcso_dash_separator(self):
        """GCSO with dash-separated dates."""
        text = textwrap.dedent("""\
            GALLATIN COUNTY SHERIFFS OFFICE
            CFS Date/Time    CFS Number    Location    Code
            03-14-26 08:12:01 CFS26-1001 123 Main St Welfare Check
            03-15-2026 09:15:22 CFS26-1002 456 Oak Ave Traffic Stop
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")
        self.assertEqual(result["incidents"][1]["date"], "03/15/26")


class TestHelenaParser(unittest.TestCase):
    """Test Helena Police format parsing."""

    def test_helena_long_month_date(self):
        """Helena with long month format."""
        text = textwrap.dedent("""\
            Helena Police Department
            March 14, 2026

            8:20 AM \u2013 A theft was reported near the 3100 block of Euclid Ave.
            9:15 AM \u2013 Officers responded to a domestic disturbance at 500 N.
            """)
        result = parse_text_blotter(text)
        self.assertIn(result["county"].lower(), ["lewis and clark", "lewis & clark"])
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_helena_slash_date(self):
        """Helena with slash-separated date."""
        text = textwrap.dedent("""\
            Helena Police Department
            03/14/2026

            8:20 AM \u2013 A theft was reported near the 3100 block of Euclid Ave.
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_helena_iso_date(self):
        """Helena with ISO format date."""
        text = textwrap.dedent("""\
            Helena Police Department
            2026-03-14

            8:20 AM \u2013 A theft was reported near the 3100 block of Euclid Ave.
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")


class TestGenericParser(unittest.TestCase):
    """Test generic fallback parser."""

    def test_generic_slash_2digit(self):
        """Generic parser with slash 2-digit year."""
        text = textwrap.dedent("""\
            Daily Activity Report
            03/14/26 - Welfare Check - Officer dispatched to 123 Main St
            03/14/26 - Traffic Stop - 456 Oak Ave
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_generic_slash_4digit(self):
        """Generic parser with slash 4-digit year."""
        text = textwrap.dedent("""\
            Daily Activity Report
            03/14/2026 - Welfare Check - Officer dispatched to 123 Main St
            03/14/2026 - Traffic Stop - 456 Oak Ave
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_generic_dash_date(self):
        """Generic parser with dash-separated date."""
        text = textwrap.dedent("""\
            Daily Activity Report
            03-14-2026 - Welfare Check - Officer dispatched to 123 Main St
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_generic_iso_date(self):
        """Generic parser with ISO format date."""
        text = textwrap.dedent("""\
            Daily Activity Report
            2026-03-14 - Welfare Check - Officer dispatched to 123 Main St
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_generic_windows_line_endings(self):
        """Generic parser handles Windows line endings."""
        text = "03/14/26 - Welfare Check\r\n03/15/26 - Traffic Stop\r\n"
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 2)

    def test_generic_mac_line_endings(self):
        """Generic parser handles old Mac line endings."""
        text = "03/14/26 - Welfare Check\r03/15/26 - Traffic Stop\r"
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 2)


class TestHavreParser(unittest.TestCase):
    """Test Havre Police format parsing."""

    def test_havre_standard_date(self):
        """Havre with standard For Date: format."""
        text = textwrap.dedent("""\
            HAVRE POLICE DEPARTMENT
            For Date: 03/14/2026

            26-2080 0737 COMPLAINT
            Location/Address: 123 Main St
            Narrative:
            Complaint received regarding noise disturbance.
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["county"], "Hill")
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_havre_2digit_year(self):
        """Havre with 2-digit year."""
        text = textwrap.dedent("""\
            HAVRE POLICE DEPARTMENT
            For Date: 03/14/26

            26-2080 0737 COMPLAINT
            Location/Address: 123 Main St
            Narrative:
            Complaint received.
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")

    def test_havre_dash_date(self):
        """Havre with dash-separated date."""
        text = textwrap.dedent("""\
            HAVRE POLICE DEPARTMENT
            For Date: 03-14-2026

            26-2080 0737 COMPLAINT
            Location/Address: 123 Main St
            Narrative:
            Complaint received.
            """)
        result = parse_text_blotter(text)
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["incidents"][0]["date"], "03/14/26")


class TestCountyDetection(unittest.TestCase):
    """Test county detection normalization."""

    def test_flathead_variations(self):
        """Flathead County name variations."""
        text = "Daily Incidents\nWF\n123 Main St - Some incident"
        parser = BlotterParser.__new__(BlotterParser)
        parser.pdf_path = None
        parser.county = None
        parser.incidents = []
        parser.parser_slug = "generic"
        county = parser._detect_county(text)
        self.assertEqual(county, "Flathead")

    def test_gcso_gallatin(self):
        """GCSO detected as Gallatin."""
        text = "GCSO\nCFS26-1001"
        parser = BlotterParser.__new__(BlotterParser)
        parser.pdf_path = None
        parser.county = None
        parser.incidents = []
        parser.parser_slug = "generic"
        county = parser._detect_county(text)
        self.assertEqual(county, "Gallatin")


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def test_empty_text(self):
        """Empty text returns empty incidents."""
        result = parse_text_blotter("")
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["incidents"], [])

    def test_whitespace_only(self):
        """Whitespace-only text returns empty incidents."""
        result = parse_text_blotter("   \n\n   \t\t   ")
        self.assertEqual(result["total_count"], 0)

    def test_no_dates(self):
        """Text without dates still parses but with fallback."""
        text = "Some random text without any dates"
        result = parse_text_blotter(text)
        # Should not crash, may return 0 or use fallback
        self.assertIn("incidents", result)

    def test_duplicate_incidents(self):
        """Same incident appearing twice is not deduplicated (known limitation)."""
        text = textwrap.dedent("""\
            GCSO
            03/14/26 08:12:01 CFS26-1001 123 Main St Welfare Check
            03/14/26 08:12:01 CFS26-1001 123 Main St Welfare Check
            """)
        result = parse_text_blotter(text)
        # Currently NOT deduplicated - this is a known issue
        self.assertEqual(result["total_count"], 2)


if __name__ == "__main__":
    unittest.main()
