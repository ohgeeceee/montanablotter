import sqlite3
import unittest
from unittest import mock

import requests

import services.ingestion.fetchers.bozeman as bozeman_police_fetcher
import services.ingestion.jail_bookings as jail_booking_ingest
import services.ingestion.fetchers.missoula as missoula_public_report_fetcher
import services.ingestion.fetchers.missoula_inmate as missoula_inmate_fetcher
import services.ingestion.fetchers.yellowstone_inmate as yellowstone_fetcher
from email_worker import EmailWorker
from services.ingestion.fetchers.missoula import _fetch_report_html


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
    def test_hill_source_is_email_only_in_scheduler(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        source = conn.execute(
            """
            SELECT
                1 AS id,
                'hill' AS county_slug,
                'Hill' AS county_name,
                'Havre Police Department' AS facility_name,
                'https://www.havremt.gov/police' AS roster_url
            """
        ).fetchone()

        with mock.patch.object(jail_booking_ingest, "_record_run") as record_run, \
             mock.patch.object(jail_booking_ingest, "_mark_source_checked") as mark_checked:
            stats, status = jail_booking_ingest._run_source(conn, source)

        self.assertEqual(status, "skipped")
        self.assertEqual(stats.fetched_count, 0)
        record_run.assert_called_once()
        mark_checked.assert_not_called()

    def test_sync_records_inserts_new_jail_booking_payload_columns(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE jail_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                county_slug TEXT,
                county_name TEXT,
                facility_name TEXT,
                person_name TEXT,
                age INTEGER,
                booking_number TEXT,
                booking_at TEXT,
                charges_summary TEXT,
                source_url TEXT,
                source_record_id TEXT,
                hash_id TEXT,
                raw_json TEXT,
                booking_status TEXT,
                is_current INTEGER,
                release_at TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        source = conn.execute(
            """
            SELECT
                1 AS id,
                'missoula' AS county_slug,
                'Missoula' AS county_name,
                'Missoula County Detention Facility' AS facility_name,
                'https://example.test/roster' AS roster_url
            """
        ).fetchone()
        record = jail_booking_ingest.JailBookingRecord(
            source_record_id="booking-1",
            person_name="Example, Person",
            age=42,
            booking_number="2026-0001",
            booking_at="2026-04-21 01:02:03",
            charges_summary="Example Charge",
            source_url=None,
        )

        stats = jail_booking_ingest._sync_records(conn, source, [record])
        row = conn.execute(
            """
            SELECT hash_id, raw_json, booking_status, is_current
            FROM jail_bookings
            WHERE source_record_id = 'booking-1'
            """
        ).fetchone()

        self.assertEqual(stats.new_count, 1)
        self.assertIsNotNone(row["hash_id"])
        self.assertIn('"person_name": "Example, Person"', row["raw_json"])
        self.assertEqual(row["booking_status"], "current")
        self.assertEqual(row["is_current"], 1)

    def test_sync_records_refuses_empty_payload_when_current_bookings_exist(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE jail_bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                county_slug TEXT,
                county_name TEXT,
                facility_name TEXT,
                person_name TEXT,
                age INTEGER,
                booking_number TEXT,
                booking_at TEXT,
                charges_summary TEXT,
                source_url TEXT,
                source_record_id TEXT,
                hash_id TEXT,
                raw_json TEXT,
                booking_status TEXT,
                is_current INTEGER,
                release_at TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO jail_bookings (
                source_id, county_slug, county_name, facility_name, person_name,
                source_record_id, booking_status, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "missoula", "Missoula", "Missoula County Detention Facility", "Example, Person", "booking-1", "current", 1),
        )
        source = conn.execute(
            """
            SELECT
                1 AS id,
                'missoula' AS county_slug,
                'Missoula' AS county_name,
                'Missoula County Detention Facility' AS facility_name,
                'https://example.test/roster' AS roster_url
            """
        ).fetchone()

        with self.assertRaisesRegex(RuntimeError, "Parsed zero jail booking rows"):
            jail_booking_ingest._sync_records(conn, source, [])

        row = conn.execute(
            "SELECT booking_status, is_current FROM jail_bookings WHERE source_record_id = 'booking-1'"
        ).fetchone()
        self.assertEqual(row["booking_status"], "current")
        self.assertEqual(row["is_current"], 1)

    def test_yellowstone_prompt_solver_handles_words_and_subtraction(self) -> None:
        html = '<label for="Answer" class="form-label text-uppercase w-50">7 - Three = </label>'

        answer = yellowstone_fetcher._solve_prompt(html)

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

        rows = missoula_inmate_fetcher._parse_missoula_lines(
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

        rows = missoula_inmate_fetcher._parse_missoula_lines(
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

        targets = missoula_inmate_fetcher._extract_missoula_charge_targets(html)

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

        summary = missoula_inmate_fetcher._parse_missoula_charges(html)

        self.assertIn("1. Fed Hold", summary)
        self.assertIn("Stats", summary)
        self.assertIn("USMS/Cascade Co. Transport", summary)
        self.assertIn("Bond $0.00", summary)

    def test_yellowstone_full_roster_parser_extracts_rows(self) -> None:
        html = """
        <table class="table table-striped _table-sm caption-top data-table">
            <tr>
                <td>POLETTE</td>
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

        rows = yellowstone_fetcher._parse_full_roster(html)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_name"], "POLETTE")
        self.assertEqual(rows[0]["first_name"], "AMBER")
        self.assertEqual(rows[0]["booking_date"], "03/15/2026")
        self.assertEqual(rows[0]["jacket_number"], "39473")

    def test_yellowstone_search_result_parser_extracts_detail_links(self) -> None:
        html = """
        <table class="table table-striped table-sm caption-top data-table">
            <tr>
                <td><a href="inmatedet.asp?Booknum=2026-00001637&LName=POLETTE&FName=AMBER&MName=ELIZABETH">POLETTE, AMBER ELIZABETH </a></td>
                <td class="text-center">39473</td>
                <td class="text-center">Bkg-BKW</td>
                <td class="text-center"><a href="inmatedet.asp?Booknum=2026-00001637&LName=POLETTE&FName=AMBER&MName=ELIZABETH">View Charges</a></td>
                <td class="text-center">$185.00</td>
                <td class="text-center">03/15/2026</td>
                <td class="text-center">10/17/1977</td>
            </tr>
        </table>
        """

        rows = yellowstone_fetcher._parse_search_result(
            html,
            "https://www.yellowstonecountymt.gov/Sheriff/Detention",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["person_name"], "POLETTE, AMBER ELIZABETH")
        self.assertEqual(rows[0]["jacket_number"], "39473")
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

        summary = yellowstone_fetcher._parse_charges(html)

        self.assertIn("Criminal Trespass To Property", summary)
        self.assertIn("Misdemeanor", summary)
        self.assertIn("Bond $185.00", summary)

    def test_yellowstone_discover_roster_url_finds_dcsearch(self) -> None:
        with mock.patch("services.ingestion.fetchers.yellowstone_inmate.requests.get") as mock_get:
            mock_get.return_value = mock.Mock(
                status_code=200,
                text='<a href="/Sheriff/Detention/dcsearch.asp">Inmate Search</a>',
                raise_for_status=mock.Mock(),
            )
            url = yellowstone_fetcher.discover_roster_url("https://www.yellowstonecountymt.gov/sheriff/")
            self.assertEqual(url, "https://www.yellowstonecountymt.gov/Sheriff/Detention/dcsearch.asp")

    @mock.patch("services.ingestion.fetchers.yellowstone_inmate.requests.Session")
    def test_fetch_yellowstone_bookings_end_to_end(self, session_cls) -> None:
        prompt_page = mock.Mock(
            raise_for_status=mock.Mock(),
            text='<label for="Answer">5 + 2 = </label>',
        )
        roster_page = mock.Mock(
            raise_for_status=mock.Mock(),
            text="""
            <table class="table table-striped _table-sm caption-top data-table">
                <tr>
                    <td>DOE</td>
                    <td>JOHN</td>
                    <td></td>
                    <td>12345</td>
                    <td>Bkg-BKW</td>
                    <td>$500.00</td>
                    <td>06/04/2026</td>
                    <td>01/01/1990</td>
                </tr>
            </table>
            """,
        )
        search_page = mock.Mock(
            raise_for_status=mock.Mock(),
            text="""
            <table class="table table-striped table-sm caption-top data-table">
                <tr>
                    <td><a href="inmatedet.asp?Booknum=2026-000001&LName=DOE&FName=JOHN">DOE, JOHN </a></td>
                    <td class="text-center">12345</td>
                    <td class="text-center">Bkg-BKW</td>
                    <td class="text-center"><a href="inmatedet.asp?Booknum=2026-000001&LName=DOE&FName=JOHN">View Charges</a></td>
                    <td class="text-center">$500.00</td>
                    <td class="text-center">06/04/2026</td>
                    <td class="text-center">01/01/1990</td>
                </tr>
            </table>
            """,
        )
        charge_page = mock.Mock(
            raise_for_status=mock.Mock(),
            text="""
            <table class="table table-striped text-center data-table">
                <tr>
                    <td>00001</td>
                    <td>MPD</td>
                    <td>Misdemeanor</td>
                    <td>Theft</td>
                    <td>$500.00</td>
                </tr>
            </table>
            """,
        )

        session = mock.Mock()
        session.get.side_effect = [prompt_page, charge_page]
        session.post.side_effect = [roster_page, search_page]
        session_cls.return_value = session

        records = yellowstone_fetcher.fetch_bookings(
            "https://www.yellowstonecountymt.gov/Sheriff/Detention/dcsearch.asp",
            fetch_charges=True,
            max_charge_lookups=1,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Doe, John")
        self.assertEqual(records[0].booking_number, "2026-000001")
        self.assertEqual(records[0].booking_at, "2026-06-04 00:00:00")
        self.assertIn("Theft", records[0].charges_summary)
        self.assertIn("Misdemeanor", records[0].charges_summary)
        self.assertIn("Bond $500.00", records[0].charges_summary)

    def test_broadwater_page_url_extractor_finds_pagination(self) -> None:
        html = """
        <a href="roster.php">1</a>
        <a href="roster.php?grp=20">2</a>
        <a href="roster.php?grp=40">4</a>
        <a href="roster.php?released=1">Released</a>
        """

        urls = jail_booking_ingest._extract_broadwater_page_urls(
            html,
            "https://www.broadwatercountysheriff.org/roster.php",
        )

        self.assertEqual(
            urls,
            [
                "https://www.broadwatercountysheriff.org/roster.php",
                "https://www.broadwatercountysheriff.org/roster.php?grp=20",
                "https://www.broadwatercountysheriff.org/roster.php?grp=40",
            ],
        )

    def test_broadwater_roster_parser_extracts_records(self) -> None:
        html = """
        <div>SLAWSON, TIMOTHY JOHN</div>
        <div>Booking #:</div>
        <div>03308</div>
        <div>Booking Date:</div>
        <div>03-03-2026 - 3:52 pm</div>
        <div>Charges:</div>
        <div>DOC</div>
        <div>Bond:</div>
        <div>$0.00</div>
        <a href="roster_view.php?booking_num=03308">View Profile >>></a>
        <div>ESTRADA JUAREZ, RUBEN M</div>
        <div>Booking #:</div>
        <div>03299</div>
        <div>Booking Date:</div>
        <div>03-02-2026 - 3:38 am</div>
        <div>Charges:</div>
        <div>46-23-1012 - Probation And Parole Detention For Probation Violation</div>
        <div>61-8-356(1) - Leaving Vehicle On Highway Right-Of-Way Over 48 Hours</div>
        <div>Bond:</div>
        <div>$80000.00</div>
        <a href="roster_view.php?booking_num=03299">View Profile >>></a>
        """

        rows = jail_booking_ingest._parse_broadwater_roster(
            html,
            "https://www.broadwatercountysheriff.org/roster.php",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].person_name, "Slawson, Timothy John")
        self.assertEqual(rows[0].booking_number, "03308")
        self.assertEqual(rows[0].booking_at, "2026-03-03 15:52:00")
        self.assertIn("DOC", rows[0].charges_summary)
        self.assertIn("Bond $0.00", rows[0].charges_summary)
        self.assertEqual(rows[1].source_record_id, "broadwater:03299")
        self.assertIn("Probation And Parole Detention", rows[1].charges_summary)

    @mock.patch('services.ingestion.jail_bookings.requests.Session')
    def test_fetch_broadwater_bookings_walks_pages(self, session_cls) -> None:
        page_one = mock.Mock(
            raise_for_status=mock.Mock(),
            text="""
            <a href="roster.php?grp=20">2</a>
            <div>SLAWSON, TIMOTHY JOHN</div>
            <div>Booking #:</div>
            <div>03308</div>
            <div>Booking Date:</div>
            <div>03-03-2026 - 3:52 pm</div>
            <div>Charges:</div>
            <div>DOC</div>
            <div>Bond:</div>
            <div>$0.00</div>
            """,
        )
        page_two = mock.Mock(
            raise_for_status=mock.Mock(),
            text="""
            <div>ESTRADA JUAREZ, RUBEN M</div>
            <div>Booking #:</div>
            <div>03299</div>
            <div>Booking Date:</div>
            <div>03-02-2026 - 3:38 am</div>
            <div>Charges:</div>
            <div>46-23-1012 - Probation And Parole Detention For Probation Violation</div>
            <div>Bond:</div>
            <div>$80000.00</div>
            """,
        )
        session = mock.Mock()
        session.get.side_effect = [page_one, page_two]
        session_cls.return_value = session

        rows = jail_booking_ingest.fetch_broadwater_bookings(
            "https://www.broadwatercountysheriff.org/roster.php"
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].booking_number, "03308")
        self.assertEqual(rows[1].booking_number, "03299")
        self.assertEqual(session.get.call_count, 2)

    def test_flathead_roster_parser_extracts_entries(self) -> None:
        html = """
        <div class="inmate-entry">
            <div class="inmate-name">
                <h2>ABRAMCHUK<span class="lighten-text">, TOLY PAVLOVICH</span></h2>
            </div>
            <div class="inmate-info">
                <div class="img_mug" style="background: black url('images/inmates/20260453.JPG') center no-repeat; background-size: contain;"></div>
                <div class="inmate-stat"><h6>Age:</h6><p>18</p></div>
                <div class="inmate-stat"><h6>PIN:</h6><p>61290</p></div>
                <div class="inmate-stat"><h6>Total Bail:</h6><p>$100,000</p></div>
                <div class="inmate-stat"><h6>Court Date:</h6><p>2026-04-02</p></div>
            </div>
            <div class="inmate-disposition">
                <div class="disposition-entry">
                    <h6 class="disposition">&nbsp;Continued</h6>
                    <p class="disposition-description severity-f">CRIMINAL ENDANGERMENT</p>
                </div>
                <div class="disposition-entry">
                    <h6 class="disposition combined">&nbsp;&#8679;Combined</h6>
                    <p class="disposition-description severity-m">AGGRAVATED DUI</p>
                </div>
            </div>
            <div class="inmate-entry-footer"></div>
        </div>
        """

        rows = jail_booking_ingest._parse_flathead_roster(
            html,
            "https://apps.flathead.mt.gov/jailroster/",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].person_name, "Abramchuk, Toly Pavlovich")
        self.assertEqual(rows[0].age, 18)
        self.assertEqual(rows[0].booking_number, "20260453")
        self.assertEqual(rows[0].source_record_id, "20260453")
        self.assertIsNone(rows[0].booking_at)
        self.assertIn("CRIMINAL ENDANGERMENT", rows[0].charges_summary)
        self.assertIn("AGGRAVATED DUI", rows[0].charges_summary)

    def test_jefferson_hold_reason_summary_extracts_first_items(self) -> None:
        summary = jail_booking_ingest._summarize_jefferson_hold_reasons(
            "Local Warrant: Bench warrant;<br />Bond - Cash/Surety, $50000.00;<br />Additional Hold for DOC;"
        )

        self.assertIn("Local Warrant: Bench warrant", summary)
        self.assertIn("Bond - Cash/Surety, $50000.00", summary)
        self.assertIn("Additional Hold for DOC", summary)

    @mock.patch('services.ingestion.jail_bookings.requests.Session')
    def test_fetch_jefferson_bookings_reads_portal_api(self, session_cls) -> None:
        session = mock.Mock()
        session.post.return_value = mock.Mock(
            raise_for_status=mock.Mock(),
            json=mock.Mock(
                return_value={
                    "total_record_count": 1,
                    "records": [
                        {
                            "name": "BUSSARD, CHRISTINA LYNN",
                            "sex": "Female",
                            "arrest_date": "2026-03-11",
                            "held_for_agency": "Jefferson County Sheriff's Office",
                            "hold_reasons": "Local Warrant: Bench warrant;<br />Bond - Cash/Surety, $50000.00;",
                        }
                    ],
                }
            ),
        )
        session_cls.return_value = session

        rows = jail_booking_ingest.fetch_jefferson_bookings(
            "https://jefferson-so-mt.zuercherportal.com/#/inmates"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].person_name, "Bussard, Christina Lynn")
        self.assertEqual(rows[0].booking_at, "2026-03-11 00:00:00")
        self.assertIn("Local Warrant: Bench warrant", rows[0].charges_summary)
        self.assertEqual(rows[0].source_url, "https://jefferson-so-mt.zuercherportal.com/#/inmates")
        self.assertEqual(len(rows[0].source_record_id), 20)

    @mock.patch('services.ingestion.jail_bookings.requests.Session')
    def test_fetch_ravalli_bookings_reads_portal_api(self, session_cls) -> None:
        session = mock.Mock()
        session.post.return_value = mock.Mock(
            raise_for_status=mock.Mock(),
            json=mock.Mock(
                return_value={
                    "total_record_count": 1,
                    "records": [
                        {
                            "name": "DOE, JOHN ALBERT",
                            "sex": "Male",
                            "arrest_date": "2026-04-01",
                            "held_for_agency": "Ravalli County Sheriff's Office",
                            "hold_reasons": "",
                        }
                    ],
                }
            ),
        )
        session_cls.return_value = session

        rows = jail_booking_ingest.fetch_ravalli_bookings(
            "https://ravalli-so-mt.zuercherportal.com/#/inmates"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].person_name, "Doe, John Albert")
        self.assertEqual(rows[0].booking_at, "2026-04-01 00:00:00")
        self.assertIn("Ravalli County", rows[0].charges_summary)
        self.assertEqual(rows[0].source_url, "https://ravalli-so-mt.zuercherportal.com/#/inmates")
        self.assertEqual(len(rows[0].source_record_id), 20)

    def test_fetch_zuercher_bookings_generic_fallback_message(self) -> None:
        summary = jail_booking_ingest._summarize_zuercher_hold_reasons("", county_name="Test County")
        self.assertEqual(summary, "Charge details available on the official Test County inmate portal.")

    def test_fetch_zuercher_bookings_generic_fallback_no_county(self) -> None:
        summary = jail_booking_ingest._summarize_zuercher_hold_reasons("")
        self.assertEqual(summary, "Charge details available on the official inmate portal.")

    def test_sanders_search_parser_extracts_rows(self) -> None:
        html = """
        <table>
            <tr bgcolor="#EADCB3">
                <td>&nbsp;20-00000740</td>
                <td>&nbsp;ALDEN-ODEKIRK, KELE&nbsp;</td>
                <td>1979-03-12</td>
                <td>White</td>
                <td>Male</td>
                <td><a class="fldListing" href="viewbkg.php?bkg=13">view</a></td>
            </tr>
        </table>
        """

        rows = jail_booking_ingest._parse_sanders_search_results(
            html,
            "https://sanders-mt.publiclogs.com/jms_public",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["jacket_number"], "20-00000740")
        self.assertEqual(rows[0]["person_name"], "Alden-Odekirk, Kele")
        self.assertEqual(rows[0]["detail_url"], "https://sanders-mt.publiclogs.com/jms_public/viewbkg.php?bkg=13")

    def test_sanders_detail_parser_extracts_booking_and_charges(self) -> None:
        html = """
        <div class="srt_label">Jacket #:</div><div class="rtn_field">&nbsp;20-00000740</div>
        <div class="srt_label">Inmate's Name:</div><div class="rtn_field">&nbsp;ALDEN-ODEKIRK,&nbsp;KELE&nbsp;</div>
        <div class="srt_label">Date of Birth:</div><div class="rtn_field">&nbsp;03/12/1979</div>
        <div class="srt_label">Booking Date:</div><div class="rtn_field">&nbsp;05/14/2021 at 01:17 AM</div>
        <div class="srt_label">Arresting Agency:</div><div class="rtn_field">&nbsp;HOT_SPRINGS</div>
        <div class="srt_label">Release Date:</div><div class="rtn_field">&nbsp;00/00/0000</div>
        <table>
            <tr bgcolor="#EADCB3" valign="top">
                <td>&nbsp;1</td>
                <td>&nbsp;3-1-501(E)-WARRANT -CIVIL CONTEMPT</td>
                <td></td>
                <td>&nbsp;0</td>
            </tr>
        </table>
        """

        detail = jail_booking_ingest._parse_sanders_detail(html)

        self.assertEqual(detail["booking_number"], "20-00000740")
        self.assertEqual(detail["person_name"], "Alden-Odekirk, Kele")
        self.assertEqual(detail["booking_date"], "05/14/2021 at 01:17 AM")
        self.assertIn("3-1-501(E)-WARRANT -CIVIL CONTEMPT", detail["charges_summary"])
        self.assertIn("Bond 0", detail["charges_summary"])

    @mock.patch('services.ingestion.fetchers.missoula.time.sleep', return_value=None)
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

    @mock.patch('services.ingestion.fetchers.missoula.ensure_source_document')
    @mock.patch('services.ingestion.fetchers.missoula._fetch_report_html')
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

    @mock.patch('services.ingestion.fetchers.bozeman.ensure_source_document')
    @mock.patch('services.ingestion.fetchers.bozeman._fetch_features')
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

    def test_extract_cascade_pdf_url_finds_direct_link(self) -> None:
        html = '<a href="https://ccmtgov-my.sharepoint.com/personal/test/jailroster.pdf?e=abcd">Roster</a>'
        url = jail_booking_ingest._extract_cascade_pdf_url(html)
        self.assertEqual(url, "https://ccmtgov-my.sharepoint.com/personal/test/jailroster.pdf?e=abcd")

    def test_extract_cascade_pdf_url_fallback_to_any_sharepoint_pdf(self) -> None:
        html = '<a href="https://ccmtgov-my.sharepoint.com/personal/test/somefile.pdf">Roster</a>'
        url = jail_booking_ingest._extract_cascade_pdf_url(html)
        self.assertEqual(url, "https://ccmtgov-my.sharepoint.com/personal/test/somefile.pdf")

    def test_extract_cascade_pdf_url_returns_none_when_no_link(self) -> None:
        html = '<div>No roster here</div>'
        url = jail_booking_ingest._extract_cascade_pdf_url(html)
        self.assertIsNone(url)

    def test_parse_cascade_pdf_text_extracts_name_and_age(self) -> None:
        text = """Cascade County Jail Roster\nPage 1\nDOE, JOHN MICHAEL 35\nBooking: 03/15/2026\nCharges: Theft; Burglary\nBond $5000\nSMITH, JANE 28\nBooking: 03/14/2026\nCharges: DUI\nBond $1000"""
        records = jail_booking_ingest._parse_cascade_pdf_text(text, "https://example.test/roster.pdf")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].person_name, "Doe, John Michael")
        self.assertEqual(records[0].age, 35)
        self.assertEqual(records[0].booking_at, "2026-03-15 00:00:00")
        self.assertIn("Theft", records[0].charges_summary)
        self.assertIn("Burglary", records[0].charges_summary)
        self.assertEqual(records[1].person_name, "Smith, Jane")
        self.assertEqual(records[1].age, 28)
        self.assertEqual(records[1].booking_at, "2026-03-14 00:00:00")
        self.assertIn("DUI", records[1].charges_summary)

    def test_parse_cascade_pdf_text_skips_headers(self) -> None:
        text = "Jail Roster\nPrinted 03/15/2026\nDate: 03/15/2026\nPage 1 of 2"
        records = jail_booking_ingest._parse_cascade_pdf_text(text, "https://example.test/roster.pdf")
        self.assertEqual(len(records), 0)

    @mock.patch('services.ingestion.jail_bookings.requests.get')
    def test_fetch_pdf_text_raises_on_auth_wall(self, mock_get) -> None:
        mock_get.return_value = mock.Mock(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=b"<!DOCTYPE html><html><body>Sign in</body></html>",
            raise_for_status=mock.Mock(),
        )
        with self.assertRaises(jail_booking_ingest.SourceTemporarilyUnavailable) as ctx:
            jail_booking_ingest._fetch_pdf_text("https://ccmtgov-my.sharepoint.com/roster.pdf")
        self.assertIn("authentication", str(ctx.exception).lower())

    @mock.patch('services.ingestion.jail_bookings._fetch_pdf_text')
    @mock.patch('services.ingestion.jail_bookings._fetch_html')
    def test_fetch_cascade_bookings_end_to_end(self, mock_fetch_html, mock_fetch_pdf_text) -> None:
        mock_fetch_html.return_value = '<a href="https://ccmtgov-my.sharepoint.com/roster.pdf">PDF</a>'
        mock_fetch_pdf_text.return_value = "DOE, JOHN 30\nBooking: 03/15/2026\nCharges: Theft\nBond $0"
        records = jail_booking_ingest.fetch_cascade_bookings("https://cascadecountymt.gov/roster")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].person_name, "Doe, John")
        self.assertEqual(records[0].age, 30)
        self.assertEqual(records[0].booking_at, "2026-03-15 00:00:00")
        self.assertIn("Theft", records[0].charges_summary)

    @mock.patch('services.ingestion.jail_bookings._fetch_html')
    def test_fetch_cascade_bookings_returns_empty_when_no_pdf_link(self, mock_fetch_html) -> None:
        mock_fetch_html.return_value = "<div>No roster here</div>"
        records = jail_booking_ingest.fetch_cascade_bookings("https://cascadecountymt.gov/roster")
        self.assertEqual(len(records), 0)


if __name__ == "__main__":
    unittest.main()
