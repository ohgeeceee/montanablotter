"""Tests for services.ingestion.fetchers.missoula_inmate."""

import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, "/root/montanablotter")

from services.ingestion.fetchers.missoula_inmate import (
    _extract_missoula_charge_targets,
    _parse_missoula_charges,
    _parse_missoula_lines,
    _should_fetch_missoula_charge_detail,
    fetch_missoula_bookings,
)
from services.ingestion.jail_bookings import JailBookingRecord


class MissoulaInmateParserTests(unittest.TestCase):
    def test_extract_missoula_charge_targets(self) -> None:
        html = (
            '<a href="javascript:__doPostBack(&#39;ctl00$MainContent$ParentRepeater$ctl01$lnkCharges&#39;,&#39;&#39;)">Charges</a>'
            '<a href="javascript:__doPostBack(&#39;ctl00$MainContent$ParentRepeater$ctl02$lnkCharges&#39;,&#39;&#39;)">Charges</a>'
            '<a href="javascript:__doPostBack(&#39;ctl00$MainContent$ParentRepeater$ctl01$lnkCharges&#39;,&#39;&#39;)">Charges</a>'
        )
        targets = _extract_missoula_charge_targets(html)
        self.assertEqual(targets, [
            "ctl00$MainContent$ParentRepeater$ctl01$lnkCharges",
            "ctl00$MainContent$ParentRepeater$ctl02$lnkCharges",
        ])

    def test_parse_missoula_lines_standard_layout(self) -> None:
        lines = [
            "Current Inmate List for Today: Thursday, May 21, 2026",
            "Name Age Booking ID Global/Jacket No Booking Date Charge Details",
            "DOE, JOHN ALAN",
            "35",
            "2026-00000123",
            "123456",
            "1/15/2026 9:30:00 AM",
            "Charges",
            "SMITH, JANE",
            "28",
            "2026-00000456",
            "789012",
            "3/20/2026 2:15:00 PM",
            "Charges",
            "\u00a9 2026 Missoula County",
        ]
        records = _parse_missoula_lines(lines, "https://example.com/roster")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].person_name, "Doe, John Alan")
        self.assertEqual(records[0].age, 35)
        self.assertEqual(records[0].booking_number, "2026-00000123")
        self.assertEqual(records[0].booking_at, "2026-01-15 09:30:00")
        self.assertEqual(records[1].person_name, "Smith, Jane")
        self.assertEqual(records[1].booking_at, "2026-03-20 14:15:00")

    def test_parse_missoula_lines_compact_layout(self) -> None:
        lines = [
            "Current Inmate List for Today:",
            "DOE, JOHN ALAN",
            "35 2026-00000123 123456 1/15/2026 9:30:00 AM Charges",
            "\u00a9 2026 Missoula County",
        ]
        records = _parse_missoula_lines(lines, "https://example.com/roster")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Doe, John Alan")
        self.assertEqual(records[0].age, 35)
        self.assertEqual(records[0].booking_number, "2026-00000123")
        self.assertEqual(records[0].booking_at, "2026-01-15 09:30:00")

    def test_parse_missoula_lines_skips_non_names(self) -> None:
        lines = [
            "Current Inmate List for Today:",
            "This is not a name",
            "DOE, JOHN ALAN",
            "35",
            "2026-00000123",
            "123456",
            "1/15/2026 9:30:00 AM",
            "Charges",
        ]
        records = _parse_missoula_lines(lines, "https://example.com/roster")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Doe, John Alan")

    def test_parse_missoula_charges_with_bond(self) -> None:
        html = (
            '<table class="table table-bordered table-striped">'
            '<tr class="ChargeRecordHeaderTopRow">'
            '<th>Charge(s)</th><th>Crime Type</th><th>Arresting Agency/Officer</th>'
            '<th>Bond/CFS</th><th>Cash/Surety</th><th>Posted By</th>'
            '</tr>'
            '<tr>'
            '<td>1. Criminal Endangerment</td><td>Felony</td>'
            '<td>MCSO /Wagner, Nicholas </td><td>$100,000.00</td><td>Cash/Surety</td><td> </td>'
            '</tr>'
            '<tr>'
            '<td>2. Disorderly Conduct</td><td>Misdemeanor</td>'
            '<td>MCSO /Wagner, Nicholas </td><td>$0.00</td><td> </td><td> </td>'
            '</tr>'
            '</table>'
        )
        summary = _parse_missoula_charges(html)
        self.assertIn("Criminal Endangerment", summary)
        self.assertIn("Bond $100,000.00", summary)
        self.assertIn("Cash/Surety", summary)

    def test_parse_missoula_charges_no_table(self) -> None:
        summary = _parse_missoula_charges("<html><body>No charges here</body></html>")
        self.assertEqual(summary, "Charge details available on the official Missoula County inmate portal.")

    def test_should_fetch_charge_detail_recent(self) -> None:
        now = datetime.now()
        recent = now - timedelta(days=5)
        record = JailBookingRecord(
            source_record_id="2026-00000123",
            person_name="Doe, John",
            age=30,
            booking_number="2026-00000123",
            booking_at=recent.strftime("%Y-%m-%d %H:%M:%S"),
            charges_summary="",
        )
        self.assertTrue(_should_fetch_missoula_charge_detail(record))

    def test_should_fetch_charge_detail_old(self) -> None:
        now = datetime.now()
        old = now - timedelta(days=90)
        record = JailBookingRecord(
            source_record_id="2026-00000123",
            person_name="Doe, John",
            age=30,
            booking_number="2026-00000123",
            booking_at=old.strftime("%Y-%m-%d %H:%M:%S"),
            charges_summary="",
        )
        self.assertFalse(_should_fetch_missoula_charge_detail(record))

    def test_should_fetch_charge_detail_no_date(self) -> None:
        record = JailBookingRecord(
            source_record_id="2026-00000123",
            person_name="Doe, John",
            age=30,
            booking_number="2026-00000123",
            booking_at=None,
            charges_summary="",
        )
        self.assertTrue(_should_fetch_missoula_charge_detail(record))


class MissoulaInmateFetcherTests(unittest.TestCase):
    def test_fetch_missoula_bookings_no_viewstate(self) -> None:
        page_html = (
            "<html><body>"
            "Current Inmate List for Today: Thursday, May 21, 2026<br/>"
            "DOE, JOHN ALAN<br/>35<br/>2026-00000123<br/>123456<br/>1/15/2026 9:30:00 AM<br/>Charges<br/>"
            "</body></html>"
        )
        with mock.patch("services.ingestion.fetchers.missoula_inmate.requests.Session") as MockSession:
            mock_response = mock.Mock()
            mock_response.text = page_html
            mock_response.raise_for_status = mock.Mock()
            MockSession.return_value.get.return_value = mock_response

            records = fetch_missoula_bookings("https://example.com/roster")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Doe, John Alan")
        self.assertEqual(records[0].booking_number, "2026-00000123")

    def test_fetch_missoula_bookings_with_viewstate_and_enrichment(self) -> None:
        roster_html = (
            '<html><body><form>'
            '<input type="hidden" name="__VIEWSTATE" value="abc"/>'
            '<input type="hidden" name="__EVENTVALIDATION" value="def"/>'
            "Current Inmate List for Today:<br/>"
            "DOE, JOHN ALAN<br/>35<br/>2026-00000123<br/>123456<br/>5/20/2026 9:30:00 AM<br/>Charges<br/>"
            '<a href="javascript:__doPostBack(&#39;ctl00$MainContent$ParentRepeater$ctl01$lnkCharges&#39;,&#39;&#39;)">Charges</a>'
            "</form></body></html>"
        )
        charge_html = (
            '<table class="table table-bordered table-striped">'
            '<tr class="ChargeRecordHeaderTopRow">'
            '<th>Charge(s)</th><th>Crime Type</th><th>Arresting Agency/Officer</th>'
            '<th>Bond/CFS</th><th>Cash/Surety</th><th>Posted By</th>'
            '</tr>'
            '<tr>'
            '<td>1. DUI</td><td>Misdemeanor</td>'
            '<td>MCSO /Officer</td><td>$5,000.00</td><td>Cash</td><td> </td>'
            '</tr>'
            '</table>'
        )

        with mock.patch("services.ingestion.fetchers.missoula_inmate.requests.Session") as MockSession:
            mock_get = mock.Mock()
            mock_get.text = roster_html
            mock_get.raise_for_status = mock.Mock()

            mock_post_all = mock.Mock()
            mock_post_all.text = roster_html
            mock_post_all.raise_for_status = mock.Mock()

            mock_post_detail = mock.Mock()
            mock_post_detail.text = charge_html
            mock_post_detail.raise_for_status = mock.Mock()
            mock_post_detail.url = "https://example.com/charges"

            session = MockSession.return_value
            session.get.return_value = mock_get
            session.post.side_effect = [mock_post_all, mock_post_detail]

            records = fetch_missoula_bookings("https://example.com/roster")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Doe, John Alan")
        self.assertIn("DUI", records[0].charges_summary)
        self.assertIn("Bond $5,000.00", records[0].charges_summary)
        self.assertEqual(records[0].source_url, "https://example.com/charges")


if __name__ == "__main__":
    unittest.main()
