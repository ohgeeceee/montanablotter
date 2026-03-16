import unittest
from unittest import mock

import requests

import bozeman_police_fetcher
import jail_booking_ingest
import missoula_public_report_fetcher
from email_worker import EmailWorker
from missoula_public_report_fetcher import _fetch_report_html


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error


class _FakeSession:
    def __init__(self, responses) -> None:
        self._responses = list(responses)

    def get(self, url: str, timeout: int = 45):
        return self._responses.pop(0)


class IngestionSourceTests(unittest.TestCase):
    def test_yellowstone_prompt_solver_handles_words_and_subtraction(self) -> None:
        html = '<label for="Answer" class="form-label text-uppercase w-50">7 - Three = </label>'

        answer = jail_booking_ingest._solve_yellowstone_prompt(html)

        self.assertEqual(answer, "4")

    def test_jail_booking_parser_extracts_missoula_legacy_rows(self) -> None:
        lines = [
            "Current Inmate List for Today: Saturday, March 15, 2026",
            "Name Age Booking ID Global/Jacket No Booking Date Charge Details",
            "ACKERMAN, MICHAEL J",
            "74 2026-00000628 221476 2/10/2026 1:03:31 PM Charges",
            "ALLEN, JOSEPH T",
            "39 2026-00001625 198657 3/14/2026 3:20:30 AM Charges",
            "© 2026 Missoula County",
        ]

        rows = jail_booking_ingest._parse_missoula_lines(
            lines,
            "https://webapps.missoulacounty.us/jailroster/Inmates",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].person_name, "Ackerman, Michael J")
        self.assertEqual(rows[0].booking_number, "2026-00000628")
        self.assertEqual(rows[0].booking_at, "2026-02-10 13:03:31")
        self.assertIn("official Missoula County inmate portal", rows[0].charges_summary)

    def test_jail_booking_parser_extracts_missoula_table_rows(self) -> None:
        lines = [
            "Current Inmate List for Today: Sunday, March 15, 2026",
            "Name",
            "Age",
            "Booking ID",
            "Global/Jacket No",
            "Booking Date",
            "Charge Details",
            "ACKERMAN, MICHAEL J",
            "74",
            "2026-00000628",
            "221476",
            "2/10/2026 1:03:31 PM",
            "Charges",
            "ADAMS, JOHN RAY",
            "46",
            "2026-00000181",
            "123577",
            "1/13/2026 3:11:56 PM",
            "Charges",
            "© Sunday, March 15, 2026 - Missoula County Inmate Information Portal",
        ]

        rows = jail_booking_ingest._parse_missoula_lines(
            lines,
            "https://webapps.missoulacounty.us/jailroster/Inmates",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].person_name, "Adams, John Ray")
        self.assertEqual(rows[1].booking_number, "2026-00000181")
        self.assertEqual(rows[1].booking_at, "2026-01-13 15:11:56")

    def test_missoula_charge_target_extractor_reads_postback_targets(self) -> None:
        html = """
        <a href="javascript:__doPostBack(&#39;ctl00$MainContent$ParentRepeater$ctl00$lnkCharges&#39;,&#39;&#39;)">Charges</a>
        <a href="javascript:__doPostBack(&#39;ctl00$MainContent$ParentRepeater$ctl01$lnkCharges&#39;,&#39;&#39;)">Charges</a>
        """

        targets = jail_booking_ingest._extract_missoula_charge_targets(html)

        self.assertEqual(
            targets,
            [
                "ctl00$MainContent$ParentRepeater$ctl00$lnkCharges",
                "ctl00$MainContent$ParentRepeater$ctl01$lnkCharges",
            ],
        )

    def test_missoula_charge_parser_extracts_summary(self) -> None:
        html = """
        <table class="table table-bordered table-striped">
            <tr class="ChargeRecordHeaderTopRow">
                <th class="ChargeColumnHeader">Charge(s)</th>
                <th class="CrimeTypeColumnHeader">Crime Type</th>
                <th class="ArrestingAgencyColumnHeader">Arresting Agency/Officer</th>
                <th class="BondColumnHeader">Bond/CFS</th>
                <th class="BondPostedHeader">Cash/Surety</th>
                <th class="BondPostedByHeader">Posted By</th>
            </tr>
            <tr class="ChargeRecordTopRow">
                <td>1. Fed Hold</td>
                <td>Stats</td>
                <td>USMS / Cascade Co. Transport&nbsp;</td>
                <td style="text-align:right;">$0.00</td>
                <td>Cash</td>
                <td>Agency</td>
            </tr>
        </table>
        """

        summary = jail_booking_ingest._parse_missoula_charges(html)

        self.assertIn("1. Fed Hold", summary)
        self.assertIn("Stats", summary)
        self.assertIn("USMS/Cascade Co. Transport", summary)
        self.assertIn("Bond $0.00", summary)

    def test_yellowstone_roster_parser_extracts_rows(self) -> None:
        html = """
        <table class="table table-striped _table-sm caption-top data-table">
            <tr>
                <td><a href="inmatedet.asp?Booknum=2026-00001637&LName=POLETTE&FName=AMBER&MName=ELIZABETH">POLETTE</a></td>
                <td>AMBER</td>
                <td>ELIZABETH</td>
                <td>39473</td>
                <td>Bkg-BKW</td>
                <td>$185.00</td>
                <td>03/15/2026</td>
                <td>10/17/1977</td>
            </tr>
        </table>
        """

        rows = jail_booking_ingest._parse_yellowstone_roster(
            html,
            "https://www.yellowstonecountymt.gov/Sheriff/Detention",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_name"], "POLETTE")
        self.assertEqual(rows[0]["booking_date"], "03/15/2026")
        self.assertIn("Booknum=2026-00001637", rows[0]["detail_url"])

    def test_yellowstone_charge_parser_extracts_summary(self) -> None:
        html = """
        <table class="table table-striped text-center data-table">
            <tr>
                <td>00002474</td>
                <td>UNKNOWN</td>
                <td>Misdemeanor</td>
                <td>Criminal Trespass To Property</td>
                <td>$185.00</td>
            </tr>
        </table>
        """

        summary = jail_booking_ingest._parse_yellowstone_charges(html)

        self.assertIn("Criminal Trespass To Property", summary)
        self.assertIn("Misdemeanor", summary)
        self.assertIn("Bond $185.00", summary)

    @mock.patch('missoula_public_report_fetcher.time.sleep', return_value=None)
    def test_missoula_fetch_retries_transient_server_error(self, _sleep) -> None:
        session = _FakeSession([
            _FakeResponse(500),
            _FakeResponse(200, "<html>ok</html>"),
        ])

        html = _fetch_report_html(session, "https://example.test/report")

        self.assertEqual(html, "<html>ok</html>")

    def test_email_worker_rejects_marketing_newsletter(self) -> None:
        worker = EmailWorker()

        result = worker._looks_like_blotter_email(
            "Ground AI agents and models in trusted data sources",
            '"Microsoft Azure" <azure@infoemails.microsoft.com>',
            "Trusted data sources webinar on 03/14/2026. Unsubscribe any time.",
        )

        self.assertFalse(result)

    def test_email_worker_accepts_structured_public_safety_preview(self) -> None:
        preview = {
            "total_count": 2,
            "incidents": [
                {
                    "date": "03/14/26",
                    "cfs_number": "CFS26-1001",
                    "incident_type": "Welfare Check",
                    "location": "123 Main St",
                    "details": "Officer dispatched",
                }
            ],
        }

        result = EmailWorker._preview_is_plausible_text_blotter(
            preview,
            subject="Daily Activity Log",
            sender='"Missoula County Sheriff" <dispatch@missoulacounty.us>',
            body="03/14/26 08:12 CFS26-1001 Welfare Check 123 Main St",
        )

        self.assertTrue(result)

    def test_email_worker_rejects_unstructured_preview(self) -> None:
        preview = {
            "total_count": 1,
            "incidents": [
                {
                    "date": "",
                    "cfs_number": "",
                    "incident_type": "Unknown",
                    "location": "",
                    "details": "newsletter content",
                }
            ],
        }

        result = EmailWorker._preview_is_plausible_text_blotter(
            preview,
            subject="AI product update",
            sender='"Microsoft Azure" <azure@infoemails.microsoft.com>',
            body="Read the latest product update for trusted data sources.",
        )

        self.assertFalse(result)

    @mock.patch('missoula_public_report_fetcher.ensure_source_document')
    @mock.patch('missoula_public_report_fetcher._fetch_report_html')
    def test_missoula_dry_run_does_not_create_ingestion_state(self, fetch_html, ensure_source_document) -> None:
        fetch_html.return_value = """
            <option selected="selected" value="3/14/2026">3/14/2026</option>
            <tr id="rptrDefault_ctl00_header" class="RecordTitle">
                <div class="CFSNumber">Theft <span class="agency">- MPD</span><br /><span>(2026-1)</span></div>
                <span class="IncidentDateStamp">03/14/2026<br />8:10 AM</span>
            </tr>
            <tr id="rptrDefault_ctl00_address">123 Main St</tr>
            <tr id="rptrDefault_ctl00_units">Responding Unit(s): D12</tr>
        """

        blotter_id, stats = missoula_public_report_fetcher.ingest_missoula_public_report(
            "https://example.test/report",
            dry_run=True,
        )

        self.assertEqual(blotter_id, 0)
        self.assertEqual(stats.fetched_incidents, 1)
        ensure_source_document.assert_not_called()

    @mock.patch('bozeman_police_fetcher.ensure_source_document')
    @mock.patch('bozeman_police_fetcher._fetch_features')
    def test_bozeman_dry_run_does_not_create_ingestion_state(self, fetch_features, ensure_source_document) -> None:
        dataset = bozeman_police_fetcher.DATASETS['crime']
        fetch_features.return_value = (
            [
                {
                    "attributes": {
                        "OBJECTID": 1,
                        "CFS_NUMBER": "CFS26-1001",
                        "CASE_NUMBER": "CASE-1",
                        "CRIME_TYPE": "Burglary",
                        "OFFENSE": "Burglary",
                        "REPORTED_DATE": 1710403200000,
                    }
                }
            ],
            bozeman_police_fetcher.datetime(2026, 3, 7, tzinfo=bozeman_police_fetcher.timezone.utc),
            bozeman_police_fetcher.datetime(2026, 3, 14, tzinfo=bozeman_police_fetcher.timezone.utc),
        )

        blotter_id, fetched, normalized = bozeman_police_fetcher.ingest_dataset(
            dataset,
            days_back=7,
            dry_run=True,
        )

        self.assertEqual(blotter_id, 0)
        self.assertEqual(fetched, 1)
        self.assertEqual(normalized, 1)
        ensure_source_document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
