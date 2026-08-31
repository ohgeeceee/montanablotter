"""Tests for the generic roster scrapers (services/ingestion/roster_generic.py).

Covers:
- HTML table parser (parse_html_tables, find_roster_table, index_by_header)
- Park County HTML table adapter
- Beaverhead PDF text adapter (using canned text, not the real PDF)
"""
from __future__ import annotations

import re
import sys
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

sys.path.insert(0, "/root/montanablotter")

from services.ingestion.roster_generic import (
    parse_html_tables,
    find_roster_table,
    index_by_header,
    fetch_park_bookings,
    fetch_beaverhead_bookings,
    fetch_chouteau_bookings,
    _parse_beaverhead_text,
    _parse_chouteau_text,
    _normalize_beaverhead_name,
    _normalize_beaverhead_date,
)


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------


class ParseHtmlTablesTests(unittest.TestCase):
    def test_simple_table(self):
        html = """
        <table>
          <tr><th>Name</th><th>Age</th></tr>
          <tr><td>Alice</td><td>30</td></tr>
          <tr><td>Bob</td><td>25</td></tr>
        </table>
        """
        rows, hrefs = parse_html_tables(html)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], ["Name", "Age"])
        self.assertEqual(rows[1], ["Alice", "30"])
        self.assertEqual(rows[2], ["Bob", "25"])
        # Hrefs mirrors rows; no anchors so all empty
        self.assertEqual(hrefs[1], ["", ""])

    def test_anchors_capture_first_href(self):
        html = """
        <table>
          <tr><th>Name</th></tr>
          <tr><td><a href="/jail/alice">Alice</a></td></tr>
        </table>
        """
        rows, hrefs = parse_html_tables(html)
        self.assertEqual(rows[1], ["Alice"])
        self.assertEqual(hrefs[1], ["/jail/alice"])

    def test_nested_table_ignored(self):
        html = """
        <table>
          <tr><th>Outer</th></tr>
          <tr><td>
            <table><tr><td>Inner</td></tr></table>
          </td></tr>
        </table>
        """
        rows, _ = parse_html_tables(html)
        # Only the outer table's rows; inner is skipped
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ["Outer"])

    def test_whitespace_normalized(self):
        html = "<table><tr><th>  Name  </th></tr><tr><td>\n  Alice  \n</td></tr></table>"
        rows, _ = parse_html_tables(html)
        self.assertEqual(rows[0], ["Name"])
        self.assertEqual(rows[1], ["Alice"])


class FindRosterTableTests(unittest.TestCase):
    def test_finds_table_by_header_keyword(self):
        html = """
        <table>
          <tr><th>Officer</th><th>Phone</th></tr>
          <tr><td>Smith</td><td>555-1212</td></tr>
        </table>
        <table>
          <tr><th>Inmate</th><th>Charge</th></tr>
          <tr><td>Doe</td><td>DUI</td></tr>
          <tr><td>Smith</td><td>Assault</td></tr>
        </table>
        """
        found = find_roster_table(html, header_keywords=("inmate",), min_data_rows=1)
        self.assertIsNotNone(found)
        headers, data_rows, _ = found
        self.assertEqual(headers, ["Inmate", "Charge"])
        self.assertEqual(len(data_rows), 2)
        self.assertEqual(data_rows[0], ["Doe", "DUI"])

    def test_no_match_returns_none(self):
        html = "<table><tr><th>Officer</th></tr><tr><td>Smith</td></tr></table>"
        self.assertIsNone(find_roster_table(html, header_keywords=("inmate",)))

    def test_min_data_rows_filter(self):
        html = """
        <table>
          <tr><th>Inmate</th></tr>
          <tr><td>Only</td></tr>
        </table>
        """
        # 1 data row, default min_data_rows=1 -> match
        self.assertIsNotNone(find_roster_table(html, min_data_rows=1))
        # min_data_rows=2 -> no match
        self.assertIsNone(find_roster_table(html, min_data_rows=2))


class IndexByHeaderTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(index_by_header(["Inmate", "Charge"], "inmate"), 0)
        self.assertEqual(index_by_header(["Inmate", "Charge"], "charge"), 1)

    def test_substring_match(self):
        self.assertEqual(index_by_header(["Booking Date", "Charge"], "booking"), 0)
        self.assertEqual(index_by_header(["Name (Last, First)", "Charge"], "name"), 0)

    def test_no_match(self):
        self.assertIsNone(index_by_header(["Foo", "Bar"], "inmate"))


# ---------------------------------------------------------------------------
# Park County (HTML)
# ---------------------------------------------------------------------------


PARK_HTML = """
<html><body>
  <table>
    <tr><th>Officer</th><th>Phone</th></tr>
    <tr><td>Smith</td><td>555-1212</td></tr>
  </table>
  <table>
    <tr><th>INMATE</th><th>CHARGE/BOND</th><th>HOLDING AGENCY/ ARREST DATE</th></tr>
    <tr>
      <td>Baier, Tyler</td>
      <td>Felony Probation Violation $50,000 Bond (Justice Court)</td>
      <td>SO / 5-28-2026</td>
    </tr>
    <tr>
      <td>Benjamin, Shane</td>
      <td>Felony Possession of Dangerous Drugs</td>
      <td>SO / 5-30-2026</td>
    </tr>
  </table>
</body></html>
"""


class FetchParkBookingsTests(unittest.TestCase):
    def test_parses_inmate_rows(self):
        fake_response = mock.Mock(text=PARK_HTML, status_code=200)
        with mock.patch(
            "services.ingestion.roster_generic.fetch_url", return_value=fake_response
        ):
            records = fetch_park_bookings("https://www.parkcounty.org/.../Inmates-Housed/")

        self.assertEqual(len(records), 2)
        # First record
        self.assertEqual(records[0].person_name, "Tyler Baier")
        self.assertIn("Probation", records[0].charges_summary)
        self.assertTrue(records[0].source_url.startswith("https://www.parkcounty.org"))
        # Second record
        self.assertEqual(records[1].person_name, "Shane Benjamin")
        # Source record IDs must be unique
        ids = [r.source_record_id for r in records]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_table_returns_empty(self):
        # Page exists but no roster table
        fake_response = mock.Mock(text="<html><body>nothing</body></html>", status_code=200)
        with mock.patch(
            "services.ingestion.roster_generic.fetch_url", return_value=fake_response
        ):
            records = fetch_park_bookings("https://www.parkcounty.org/.../")
        self.assertEqual(records, [])

    def test_synthetic_staff_table_ignored(self):
        """A page that has only a non-roster table must produce zero records."""
        html = """
        <html><body>
          <table>
            <tr><th>Officer</th><th>Phone</th></tr>
            <tr><td>Smith</td><td>555-1212</td></tr>
          </table>
        </body></html>
        """
        fake_response = mock.Mock(text=html, status_code=200)
        with mock.patch(
            "services.ingestion.roster_generic.fetch_url", return_value=fake_response
        ):
            records = fetch_park_bookings("https://www.parkcounty.org/.../")
        self.assertEqual(records, [])


# ---------------------------------------------------------------------------
# Beaverhead (PDF) — name + date helpers + text parser
# ---------------------------------------------------------------------------


class NormalizeNameTests(unittest.TestCase):
    def test_last_comma_first_unchanged(self):
        self.assertEqual(_normalize_beaverhead_name("SMITH, JOHN"), "John Smith")

    def test_space_stripped_first_runs_into_middle_initial(self):
        # "NICHOLASC" -> "Nicholas C."
        self.assertEqual(
            _normalize_beaverhead_name("AMOS, NICHOLASC"),
            "Nicholas C. Amos",
        )

    def test_no_comma_falls_back_to_title(self):
        self.assertEqual(_normalize_beaverhead_name("BOB SMITH"), "Bob Smith")


class NormalizeDateTests(unittest.TestCase):
    def test_two_digit_year_low(self):
        self.assertEqual(_normalize_beaverhead_date("03/09/26"), "2026-03-09")

    def test_two_digit_year_high(self):
        self.assertEqual(_normalize_beaverhead_date("01/15/85"), "1985-01-15")

    def test_four_digit_year(self):
        self.assertEqual(_normalize_beaverhead_date("12/31/2024"), "2024-12-31")

    def test_invalid_returns_none(self):
        self.assertIsNone(_normalize_beaverhead_date("not a date"))


# A realistic slice of text from a Beaverhead PDF (based on the live
# extraction we observed in 2026-06-11).
BEAVERHEAD_TEXT_SAMPLE = """
Beaverhead County Sheriff's Office
Inmate Offense List, with Statute
Book#: 26BK00095 Name: AMOS,NICHOLASC NameID: 21693 Rel.Dt: **/**/**
Statute# Statute BillAgncy JudDisp Reference# Booking Num
Date Days
45-5-215(2)(a)[1st] StrangulationofPartnerorFamilyBCSO DC-1-2026- 03/09/26 91
Member-1stOffense 4358
45-5-508 AggravatedSexualIntercourse BCSO DC-1-2026- 03/09/26 91
WithoutConsent 4358
Book#: 26BK00055 Name: BLUME,CHRISTOPHERT NameID: 8127 Rel.Dt: **/**/**
45-7-302(1) ObstructingAPeaceOfficer BCSO Warrant 02/15/26 105
Book#: 26BK00094 Name: FREEMAN,NICHOLASF NameID: 7989 Rel.Dt: **/**/**
45-5-401 Robbery DPD Warrant 03/08/26 92
45-5-201(1)(c) Assault,PurposelyOrKnowingly DPD C18A-19885 03/08/26 92
"""


class ParseBeaverheadTextTests(unittest.TestCase):
    def test_parses_three_inmates(self):
        records = _parse_beaverhead_text(BEAVERHEAD_TEXT_SAMPLE, source_url="x")
        self.assertEqual(len(records), 3)
        # First inmate
        self.assertEqual(records[0].person_name, "Nicholas C. Amos")
        self.assertEqual(records[0].booking_number, "26BK00095")
        self.assertEqual(records[0].booking_at, "2026-03-09")
        # Second inmate
        self.assertEqual(records[1].person_name, "Christopher T. Blume")
        self.assertEqual(records[1].booking_number, "26BK00055")
        self.assertEqual(records[1].booking_at, "2026-02-15")
        # Third inmate
        self.assertEqual(records[2].person_name, "Nicholas F. Freeman")
        self.assertEqual(records[2].booking_at, "2026-03-08")

    def test_charges_summary_present(self):
        records = _parse_beaverhead_text(BEAVERHEAD_TEXT_SAMPLE, source_url="x")
        for r in records:
            self.assertTrue(r.charges_summary, f"empty charges for {r.person_name}")
            self.assertLessEqual(len(r.charges_summary), 240)

    def test_unique_source_record_ids(self):
        records = _parse_beaverhead_text(BEAVERHEAD_TEXT_SAMPLE, source_url="x")
        ids = [r.source_record_id for r in records]
        self.assertEqual(len(ids), len(set(ids)))

    def test_empty_text_returns_empty(self):
        self.assertEqual(_parse_beaverhead_text("", source_url="x"), [])


class FetchBeaverheadBookingsTests(unittest.TestCase):
    """The PDF fetcher wraps the text parser.

    Two tests:
    - The wrapper must pass a BytesIO to pdfplumber.open, not
      response.raw (which doesn't support seek).
    - When given real PDF bytes, the wrapper extracts text and
      delegates to the text parser.
    """

    def test_passes_bytesio_to_pdfplumber(self):
        import io

        fake_response = mock.Mock(
            content=b"%PDF-fake-content-here",
            headers={"Content-Type": "application/pdf"},
        )
        # response.raw exists but should NOT be passed to pdfplumber
        fake_response.raw = mock.Mock(spec=["read"])

        opened_with: list[object] = []
        def fake_open(arg, *args, **kwargs):
            opened_with.append(arg)
            cm = mock.MagicMock()
            cm.__enter__.return_value.pages = []
            return cm

        # Patch pdfplumber at the import location used by the module
        with mock.patch(
            "services.ingestion.roster_generic.fetch_url",
            return_value=fake_response,
        ), mock.patch.dict(
            "sys.modules",
            {"pdfplumber": mock.MagicMock(open=mock.Mock(side_effect=fake_open))},
        ):
            try:
                fetch_beaverhead_bookings("https://beaverheadcountymt.gov/...pdf")
            except Exception:
                pass  # the mocked pdfplumber returns empty pages, so parser may no-op

        self.assertEqual(len(opened_with), 1)
        self.assertIsInstance(opened_with[0], io.BytesIO)
        # And the BytesIO must contain response.content
        self.assertEqual(opened_with[0].getvalue(), b"%PDF-fake-content-here")

    def test_real_pdf_extracts_text(self):
        """If a real PDF is downloaded, the wrapper extracts text and
        hands it to the parser. We use a canned text string to keep
        the test hermetic."""
        from services.ingestion.roster_generic import _parse_beaverhead_text

        # The fetcher does `import pdfplumber` inside the function, so
        # we have to stub sys.modules['pdfplumber'] (not patch the
        # attribute, which won't exist yet).
        cm = mock.MagicMock()
        cm.__enter__.return_value.pages = [
            mock.Mock(extract_text=lambda: BEAVERHEAD_TEXT_SAMPLE)
        ]
        fake_pdfplumber = mock.MagicMock()
        fake_pdfplumber.open.return_value = cm

        with mock.patch(
            "services.ingestion.roster_generic.fetch_url",
            return_value=mock.Mock(content=b"%PDF-placeholder"),
        ), mock.patch.dict(
            "sys.modules", {"pdfplumber": fake_pdfplumber},
        ):
            records = fetch_beaverhead_bookings("https://beaverheadcountymt.gov/...pdf")

        # The parser is exercised end-to-end through the wrapper
        self.assertGreater(len(records), 0)
        self.assertEqual(records[0].person_name, "Nicholas C. Amos")


# ---------------------------------------------------------------------------
# Chouteau County (Wix-hosted PDF, link discovered from landing page)
# ---------------------------------------------------------------------------

CHOUTEAU_TEXT_SAMPLE = """Inmate Population Printed on June 29, 2026

Booking Date/Time Last, First Name Age Hold Reasons
06/27/26 23:45 Foust, Charles 51 Charge: 61-8-1002(1)(a)[1st] - Driving Under The Influence Of Alcohol and or Drugs - 1st Offense; Charge: 61-6-301(2)[3rd] - Operating Without Liability Insurance In Effect - 3rd Offense
06/12/26 19:18 Knecht, Onie 39 Warrant: Unspecified warrant ; Warrant: Unspecified warrant
06/11/26 15:23 Spotted Eagle, 52 Sentenced: Serving 0 days - Concurrent
Stephanie
06/11/26 15:23 Naranjo, Tisha 47 DOC Hold for Department of Corrections
06/11/26 15:23 Lawrence, Troy 42 Charge: 46-6-210 - Arrest On A Warrant By Peace Officer
06/11/26 15:23 Bullshoe, Galen Jr 50 DOC Hold for Department of Corrections
05/15/26 15:31 Waddell, Marcus Jr 25 Warrant Charge: Unspecified warrant (45-5-625(2)(b) - Sexual Abuse of Children Under 16)
"""


class ParseChouteauTextTests(unittest.TestCase):
    def test_parses_inmates(self):
        records = _parse_chouteau_text(CHOUTEAU_TEXT_SAMPLE, source_url="x")
        self.assertEqual(len(records), 7)
        self.assertEqual(records[0].person_name, "Charles Foust")
        self.assertEqual(records[0].booking_at, "2026-06-27")
        self.assertEqual(records[0].age, 51)
        self.assertIn("Driving Under The Influence", records[0].charges_summary)

    def test_wrapped_first_name_joins(self):
        records = _parse_chouteau_text(CHOUTEAU_TEXT_SAMPLE, source_url="x")
        spotted = next(r for r in records if r.person_name == "Stephanie Spotted Eagle")
        self.assertEqual(spotted.person_name, "Stephanie Spotted Eagle")
        self.assertEqual(spotted.age, 52)

    def test_jr_suffix_stays_in_last_name(self):
        records = _parse_chouteau_text(CHOUTEAU_TEXT_SAMPLE, source_url="x")
        bullshoe = next(r for r in records if r.person_name == "Galen Bullshoe Jr")
        self.assertEqual(bullshoe.person_name, "Galen Bullshoe Jr")
        waddell = next(r for r in records if r.person_name == "Marcus Waddell Jr")
        self.assertEqual(waddell.person_name, "Marcus Waddell Jr")

    def test_unique_source_record_ids(self):
        records = _parse_chouteau_text(CHOUTEAU_TEXT_SAMPLE, source_url="x")
        ids = [r.source_record_id for r in records]
        self.assertEqual(len(ids), len(set(ids)))

    def test_empty_text_returns_empty(self):
        self.assertEqual(_parse_chouteau_text("", source_url="x"), [])


class FetchChouteauBookingsTests(unittest.TestCase):
    def test_resolves_pdf_from_landing_page(self):
        """The landing page links the rotating PDF; the fetcher must
        extract that link and hand PDF bytes to pdfplumber."""
        import io

        landing = (
            '<a data-aid="DOWNLOAD_DOCUMENT_LINK_WRAPPER_RENDERED" '
            'href="//img1.wsimg.com/blobby/go/84a621ca/downloads/483fea65/'
            'Inmate_Population_2026-06-29_06.14.52.pdf?ver=1784758477993">'
            "Inmate Population</a>"
        )
        pdf_url = "https://img1.wsimg.com/blobby/go/84a621ca/downloads/483fea65/Inmate_Population_2026-06-29_06.14.52.pdf"

        responses = {
            "https://chouteaucountysheriff.com/": mock.Mock(
                text=landing, content=b"<html/>", headers={}
            ),
            "https://img1.wsimg.com/blobby/go/84a621ca/downloads/483fea65/Inmate_Population_2026-06-29_06.14.52.pdf?ver=1784758477993": mock.Mock(
                content=b"%PDF-1.4",
                headers={"Content-Type": "application/pdf"},
                url="https://img1.wsimg.com/blobby/go/84a621ca/downloads/483fea65/Inmate_Population_2026-06-29_06.14.52.pdf?ver=1784758477993",
            ),
        }

        def fake_fetch(url, *a, **k):
            return responses[url]

        fake_pdfplumber = mock.MagicMock()
        fake_pdfplumber.open.return_value.__enter__.return_value.pages = [
            mock.Mock(extract_text=lambda: CHOUTEAU_TEXT_SAMPLE)
        ]

        opened_with: list[object] = []

        def fake_open(arg, *args, **kwargs):
            opened_with.append(arg)
            return fake_pdfplumber.open.return_value

        with mock.patch(
            "services.ingestion.roster_generic.fetch_url",
            side_effect=fake_fetch,
        ), mock.patch.dict(
            "sys.modules",
            {"pdfplumber": mock.MagicMock(open=mock.Mock(side_effect=fake_open))},
        ):
            records = fetch_chouteau_bookings("https://chouteaucountysheriff.com/")

        self.assertEqual(len(records), 7)
        self.assertIsInstance(opened_with[0], io.BytesIO)


if __name__ == "__main__":
    unittest.main()
