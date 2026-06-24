"""Tests for services.ingestion.fetchers.yellowstone_inmate."""

import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, "/root/montanablotter")

from services.ingestion.fetchers.yellowstone_inmate import (
    YELLOWSTONE_CHARGE_LOOKBACK_DAYS,
    _parse_charges,
    _parse_full_roster,
    _parse_search_result,
    _should_fetch_yellowstone_charge_detail,
    _solve_prompt,
    fetch_bookings,
)


class YellowstonePromptSolverTests(unittest.TestCase):
    def test_solve_prompt_addition_digits(self) -> None:
        html = '<label for="Answer">3 + 4 = </label>'
        self.assertEqual(_solve_prompt(html), "7")

    def test_solve_prompt_subtraction_words(self) -> None:
        html = '<label for="Answer">Nine - Two = </label>'
        self.assertEqual(_solve_prompt(html), "7")

    def test_solve_prompt_mixed(self) -> None:
        html = '<label for="Answer">1 + One = </label>'
        self.assertEqual(_solve_prompt(html), "2")

    def test_solve_prompt_missing_label(self) -> None:
        with self.assertRaises(RuntimeError):
            _solve_prompt("<html><body>No prompt here</body></html>")


class YellowstoneRecencyGateTests(unittest.TestCase):
    def test_should_fetch_recent_booking(self) -> None:
        now = datetime.now()
        recent = now - timedelta(days=2)
        self.assertTrue(
            _should_fetch_yellowstone_charge_detail(
                recent.strftime("%Y-%m-%d %H:%M:%S")
            )
        )

    def test_should_fetch_exactly_at_cutoff(self) -> None:
        now = datetime.now()
        cutoff = now - timedelta(days=YELLOWSTONE_CHARGE_LOOKBACK_DAYS)
        self.assertTrue(
            _should_fetch_yellowstone_charge_detail(
                cutoff.strftime("%Y-%m-%d %H:%M:%S")
            )
        )

    def test_should_not_fetch_old_booking(self) -> None:
        now = datetime.now()
        old = now - timedelta(days=30)
        self.assertFalse(
            _should_fetch_yellowstone_charge_detail(
                old.strftime("%Y-%m-%d %H:%M:%S")
            )
        )

    def test_should_fetch_when_no_date(self) -> None:
        self.assertTrue(_should_fetch_yellowstone_charge_detail(None))

    def test_should_fetch_when_invalid_date(self) -> None:
        self.assertTrue(_should_fetch_yellowstone_charge_detail("not-a-date"))


class YellowstoneRosterParserTests(unittest.TestCase):
    def test_parse_full_roster_basic(self) -> None:
        html = (
            '<table class="table table-striped _table-sm caption-top data-table">'
            "<tr>"
            "<td>DOE</td><td>JOHN</td><td>ALAN</td>"
            "<td>12345</td><td>Bkg-HC01</td><td>$500.00</td>"
            "<td>05/20/2026</td><td>01/15/1990</td>"
            "</tr>"
            "<tr>"
            "<td>SMITH</td><td>JANE</td><td></td>"
            "<td>67890</td><td>Bkg-HC02</td><td>$0.00</td>"
            "<td>05/19/2026</td><td>03/22/1985</td>"
            "</tr>"
            "</table>"
        )
        rows = _parse_full_roster(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["last_name"], "DOE")
        self.assertEqual(rows[0]["first_name"], "JOHN")
        self.assertEqual(rows[0]["middle_name"], "ALAN")
        self.assertEqual(rows[0]["jacket_number"], "12345")
        self.assertEqual(rows[0]["housing_unit"], "Bkg-HC01")
        self.assertEqual(rows[0]["total_bond"], "$500.00")
        self.assertEqual(rows[0]["booking_date"], "05/20/2026")
        self.assertEqual(rows[0]["date_of_birth"], "01/15/1990")
        self.assertEqual(rows[1]["last_name"], "SMITH")
        self.assertEqual(rows[1]["middle_name"], "")

    def test_parse_full_roster_no_table(self) -> None:
        rows = _parse_full_roster("<html><body>No table</body></html>")
        self.assertEqual(rows, [])

    def test_parse_search_result_with_detail_links(self) -> None:
        html = (
            '<table class="table table-striped table-sm caption-top data-table">'
            "<tr>"
            '<td><a href="inmatedet.asp?Booknum=12345">DOE, JOHN ALAN</a></td>'
            "<td>12345</td><td>Bkg-HC01</td><td>M</td>"
            "<td>$500.00</td><td>05/20/2026</td><td>01/15/1990</td>"
            "</tr>"
            "</table>"
        )
        rows = _parse_search_result(html, "https://example.com/sheriff/detection/")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["person_name"], "DOE, JOHN ALAN")
        self.assertEqual(rows[0]["jacket_number"], "12345")
        self.assertEqual(
            rows[0]["detail_url"],
            "https://example.com/sheriff/detection/inmatedet.asp?Booknum=12345",
        )

    def test_parse_search_result_no_links(self) -> None:
        html = (
            '<table class="table table-striped table-sm caption-top data-table">'
            "<tr>"
            "<td>DOE, JOHN ALAN</td>"
            "<td>12345</td><td>Bkg-HC01</td><td>M</td>"
            "<td>$500.00</td><td>05/20/2026</td><td>01/15/1990</td>"
            "</tr>"
            "</table>"
        )
        rows = _parse_search_result(html, "https://example.com/")
        self.assertEqual(rows[0]["detail_url"], None)


class YellowstoneChargesParserTests(unittest.TestCase):
    def test_parse_charges_with_bond(self) -> None:
        html = (
            '<table class="table table-striped text-center data-table">'
            "<tr>"
            "<td>1</td><td>County</td><td>Felony</td>"
            "<td>Criminal Endangerment</td><td>$100,000.00</td>"
            "</tr>"
            "<tr>"
            "<td>2</td><td>County</td><td>Misdemeanor</td>"
            "<td>Disorderly Conduct</td><td>$0.00</td>"
            "</tr>"
            "</table>"
        )
        summary = _parse_charges(html)
        self.assertIn("Criminal Endangerment", summary)
        self.assertIn("Felony", summary)
        self.assertIn("Bond $100,000.00", summary)
        self.assertIn("Disorderly Conduct", summary)

    def test_parse_charges_no_table(self) -> None:
        summary = _parse_charges("<html><body>No charges</body></html>")
        self.assertEqual(
            summary,
            "Charge details available on the official Yellowstone County inmate page.",
        )

    def test_parse_charges_empty_rows(self) -> None:
        html = (
            '<table class="table table-striped text-center data-table">'
            "<tr><td>Header</td></tr>"
            "</table>"
        )
        summary = _parse_charges(html)
        self.assertEqual(
            summary,
            "Charge details available on the official Yellowstone County inmate page.",
        )


class YellowstoneFetcherTests(unittest.TestCase):
    def test_fetch_bookings_without_charges(self) -> None:
        prompt_html = '<label for="Answer">1 + One = </label>'
        roster_html = (
            '<table class="table table-striped _table-sm caption-top data-table">'
            "<tr>"
            "<td>DOE</td><td>JOHN</td><td>ALAN</td>"
            "<td>12345</td><td>Bkg-HC01</td><td>$500.00</td>"
            "<td>05/20/2026</td><td>01/15/1990</td>"
            "</tr>"
            "</table>"
        )
        with mock.patch(
            "services.ingestion.fetchers.yellowstone_inmate.requests.Session"
        ) as MockSession:
            mock_get = mock.Mock()
            mock_get.text = prompt_html
            mock_get.raise_for_status = mock.Mock()

            mock_post = mock.Mock()
            mock_post.text = roster_html
            mock_post.raise_for_status = mock.Mock()

            session = MockSession.return_value
            session.get.return_value = mock_get
            session.post.return_value = mock_post

            records = fetch_bookings(
                "https://example.com/roster",
                fetch_charges=False,
                max_charge_lookups=0,
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Doe, John Alan")
        self.assertEqual(records[0].booking_number, "12345")
        self.assertEqual(records[0].booking_at, "2026-05-20 00:00:00")
        self.assertEqual(
            records[0].charges_summary,
            "Charge details available on the official Yellowstone County inmate page.",
        )

    @mock.patch(
        "services.ingestion.fetchers.yellowstone_inmate._should_fetch_yellowstone_charge_detail",
        return_value=True,
    )
    def test_fetch_bookings_with_charges(self, _mock_should_fetch) -> None:
        prompt_html = '<label for="Answer">1 + One = </label>'
        roster_html = (
            '<table class="table table-striped _table-sm caption-top data-table">'
            "<tr>"
            "<td>DOE</td><td>JOHN</td><td>ALAN</td>"
            "<td>12345</td><td>Bkg-HC01</td><td>$500.00</td>"
            "<td>05/20/2026</td><td>01/15/1990</td>"
            "</tr>"
            "</table>"
        )
        search_html = (
            '<table class="table table-striped table-sm caption-top data-table">'
            "<tr>"
            '<td><a href=\"inmatedet.asp?Booknum=12345\">DOE, JOHN ALAN</a></td>'
            "<td>12345</td><td>Bkg-HC01</td><td>M</td>"
            "<td>$500.00</td><td>05/20/2026</td><td>01/15/1990</td>"
            "</tr>"
            "</table>"
        )
        charge_html = (
            '<table class="table table-striped text-center data-table">'
            "<tr>"
            "<td>1</td><td>County</td><td>Felony</td>"
            "<td>Criminal Endangerment</td><td>$100,000.00</td>"
            "</tr>"
            "</table>"
        )

        with mock.patch(
            "services.ingestion.fetchers.yellowstone_inmate.requests.Session"
        ) as MockSession:
            mock_get_prompt = mock.Mock()
            mock_get_prompt.text = prompt_html
            mock_get_prompt.raise_for_status = mock.Mock()

            mock_post_roster = mock.Mock()
            mock_post_roster.text = roster_html
            mock_post_roster.raise_for_status = mock.Mock()

            mock_post_search = mock.Mock()
            mock_post_search.text = search_html
            mock_post_search.raise_for_status = mock.Mock()

            mock_get_detail = mock.Mock()
            mock_get_detail.text = charge_html
            mock_get_detail.raise_for_status = mock.Mock()
            mock_get_detail.url = "https://example.com/inmatedet.asp?Booknum=12345"

            session = MockSession.return_value
            session.get.side_effect = [mock_get_prompt, mock_get_detail]
            session.post.side_effect = [mock_post_roster, mock_post_search]

            records = fetch_bookings(
                "https://example.com/roster",
                fetch_charges=True,
                max_charge_lookups=1,
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Doe, John Alan")
        self.assertIn("Criminal Endangerment", records[0].charges_summary)
        self.assertEqual(records[0].source_url, "https://example.com/inmatedet.asp?Booknum=12345")

    def test_fetch_bookings_old_booking_skips_charge_lookup(self) -> None:
        old_date = (datetime.now() - timedelta(days=30)).strftime("%m/%d/%Y")
        prompt_html = '<label for="Answer">1 + One = </label>'
        roster_html = (
            '<table class="table table-striped _table-sm caption-top data-table">'
            "<tr>"
            "<td>DOE</td><td>JOHN</td><td>ALAN</td>"
            "<td>12345</td><td>Bkg-HC01</td><td>$500.00</td>"
            f"<td>{old_date}</td><td>01/15/1990</td>"
            "</tr>"
            "</table>"
        )
        with mock.patch(
            "services.ingestion.fetchers.yellowstone_inmate.requests.Session"
        ) as MockSession:
            mock_get = mock.Mock()
            mock_get.text = prompt_html
            mock_get.raise_for_status = mock.Mock()

            mock_post = mock.Mock()
            mock_post.text = roster_html
            mock_post.raise_for_status = mock.Mock()

            session = MockSession.return_value
            session.get.return_value = mock_get
            session.post.return_value = mock_post

            records = fetch_bookings(
                "https://example.com/roster",
                fetch_charges=True,
                max_charge_lookups=1,
            )

        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0].charges_summary,
            "Charge details available on the official Yellowstone County inmate page.",
        )
        # Ensure no search POST was made for charge lookup
        self.assertEqual(session.post.call_count, 1)  # Only the roster POST


if __name__ == "__main__":
    unittest.main()
