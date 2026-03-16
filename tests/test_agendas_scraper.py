import unittest

from agendas_scraper.config import CityScrapeConfig
from agendas_scraper.models import AgendaDocument, MeetingRecord
from agendas_scraper.factory import create_provider
from agendas_scraper.link_detection import detect_target_type
from agendas_scraper.providers import CivicClerkProvider, CustomHTMLProvider, GranicusProvider, MunicipalImpactProvider, MuniCodeProvider, PrimeGovProvider


class AgendasScraperTests(unittest.TestCase):
    def test_nested_page_agenda_falls_back_to_meeting_page_when_no_child_docs_exist(self) -> None:
        class StubProvider(GranicusProvider):
            def classify_link_target(self, context, url: str, *, label: str = ""):
                return "nested_page"

            def extract_nested_documents(self, context, url: str):
                return []

        provider = StubProvider(
            CityScrapeConfig(
                slug="stub-city",
                name="Stub City Council",
                provider="granicus",
                url="https://example.gov/AgendaCenter",
            )
        )

        meeting = provider._hydrate_meeting(
            None,
            MeetingRecord(
                title="Regular Session",
                documents=[
                    AgendaDocument(
                        label="Agenda",
                        url="https://example.gov/meetings/regular-session",
                        document_type="agenda",
                    )
                ],
            ),
        )

        self.assertEqual(meeting.meeting_page_url, "https://example.gov/meetings/regular-session")
        self.assertEqual(len(meeting.documents), 1)
        self.assertEqual(meeting.documents[0].target_type, "nested_page")
        self.assertEqual(meeting.documents[0].url, "https://example.gov/meetings/regular-session")

    def test_nested_page_hydration_can_be_disabled_per_source(self) -> None:
        class StubProvider(GranicusProvider):
            def classify_link_target(self, context, url: str, *, label: str = ""):
                return "nested_page"

            def extract_nested_documents(self, context, url: str):
                raise AssertionError("nested documents should not be fetched")

        provider = StubProvider(
            CityScrapeConfig(
                slug="stub-city",
                name="Stub City Council",
                provider="granicus",
                url="https://example.gov/AgendaCenter",
                metadata={"disable_nested_document_hydration": True},
            )
        )

        meeting = provider._hydrate_meeting(
            None,
            MeetingRecord(
                title="Regular Session",
                documents=[
                    AgendaDocument(
                        label="Agenda",
                        url="https://example.gov/meetings/regular-session",
                        document_type="agenda",
                    )
                ],
            ),
        )

        self.assertEqual(meeting.meeting_page_url, "https://example.gov/meetings/regular-session")
        self.assertEqual(len(meeting.documents), 1)
        self.assertEqual(meeting.documents[0].url, "https://example.gov/meetings/regular-session")

    def test_detects_direct_pdf_link(self) -> None:
        self.assertEqual(
            detect_target_type("https://example.gov/DocumentCenter/View/123/agenda-packet"),
            "pdf",
        )

    def test_detects_nested_calendar_page(self) -> None:
        self.assertEqual(
            detect_target_type(
                "https://example.gov/AgendaCenter",
                label="Agenda Details",
            ),
            "nested_page",
        )

    def test_prefers_content_type_when_available(self) -> None:
        self.assertEqual(
            detect_target_type(
                "https://example.gov/download?id=7",
                content_type="application/pdf",
            ),
            "pdf",
        )

    def test_factory_builds_granicus_provider(self) -> None:
        provider = create_provider(
            CityScrapeConfig(
                slug="billings",
                name="Billings City Council",
                provider="granicus",
                url="https://example.gov/AgendaCenter",
            )
        )
        self.assertIsInstance(provider, GranicusProvider)

    def test_factory_builds_municode_provider(self) -> None:
        provider = create_provider(
            CityScrapeConfig(
                slug="missoula",
                name="Missoula County Board of Commissioners",
                provider="municode",
                url="https://example.gov/calendar",
            )
        )
        self.assertIsInstance(provider, MuniCodeProvider)

    def test_factory_builds_municipalimpact_provider(self) -> None:
        provider = create_provider(
            CityScrapeConfig(
                slug="miles-city-council",
                name="Miles City Council",
                provider="municipalimpact",
                url="https://cityofmiles.org/agendas",
            )
        )
        self.assertIsInstance(provider, MunicipalImpactProvider)

    def test_factory_builds_primegov_provider(self) -> None:
        provider = create_provider(
            CityScrapeConfig(
                slug="helena-city-commission",
                name="Helena City Commission",
                provider="primegov",
                url="https://helenamt.primegov.com/public/portal?fromiframe=1",
            )
        )
        self.assertIsInstance(provider, PrimeGovProvider)

    def test_factory_builds_custom_provider(self) -> None:
        provider = create_provider(
            CityScrapeConfig(
                slug="great-falls",
                name="Great Falls City Commission",
                provider="custom_html",
                url="https://example.gov/meetings",
            )
        )
        self.assertIsInstance(provider, CustomHTMLProvider)

    def test_factory_builds_civicclerk_provider(self) -> None:
        provider = create_provider(
            CityScrapeConfig(
                slug="missoula-county-commissioners",
                name="Missoula County Commissioners",
                provider="civicclerk",
                url="https://missoulacomt.portal.civicclerk.com/",
            )
        )
        self.assertIsInstance(provider, CivicClerkProvider)

    def test_selector_driven_provider_honors_include_exclude_keywords(self) -> None:
        provider = GranicusProvider(
            CityScrapeConfig(
                slug="kalispell-city-council",
                name="Kalispell City Council",
                provider="granicus",
                url="https://example.gov/AgendaCenter",
                metadata={"include_keywords": ["city council"], "exclude_keywords": ["planning board"]},
            )
        )
        self.assertTrue(provider._matches_title_filters("Regular City Council Meeting"))
        self.assertFalse(provider._matches_title_filters("Planning Board"))
        self.assertFalse(provider._matches_title_filters("Parks Committee"))

    def test_legacy_row_detection_matches_1990s_table_row(self) -> None:
        provider = CustomHTMLProvider(
            CityScrapeConfig(
                slug="legacy-city",
                name="Legacy City Council",
                provider="custom_html",
                url="https://example.gov/legacy",
            )
        )
        self.assertTrue(provider._looks_like_legacy_meeting_row("City Commission Agenda Minutes March 14 2026"))

    def test_legacy_row_detection_rejects_non_meeting_row(self) -> None:
        provider = CustomHTMLProvider(
            CityScrapeConfig(
                slug="legacy-city",
                name="Legacy City Council",
                provider="custom_html",
                url="https://example.gov/legacy",
            )
        )
        self.assertFalse(provider._looks_like_legacy_meeting_row("Department directory and contact information"))

    def test_legacy_row_detection_uses_configured_keywords(self) -> None:
        provider = CustomHTMLProvider(
            CityScrapeConfig(
                slug="legacy-city",
                name="Legacy City Council",
                provider="custom_html",
                url="https://example.gov/legacy",
                metadata={"legacy_row_keywords": ["board docs", "agenda"]},
            )
        )
        self.assertTrue(provider._looks_like_legacy_meeting_row("Board Docs June 2026"))

    def test_civicclerk_parses_event_id_from_row_id(self) -> None:
        self.assertEqual(CivicClerkProvider._event_id_from_row_id("eventListRow-9998"), "9998")
        self.assertEqual(CivicClerkProvider._event_id_from_row_id("row-without-match"), "")

    def test_civicclerk_document_type_classifier(self) -> None:
        self.assertEqual(CivicClerkProvider._document_type_for_label("Agenda Packet"), "packet")
        self.assertEqual(CivicClerkProvider._document_type_for_label("Approved Minutes"), "minutes")
        self.assertEqual(CivicClerkProvider._document_type_for_label("Regular Agenda"), "agenda")
        self.assertEqual(CivicClerkProvider._document_type_for_label("Attachment 7A"), "attachment")

    def test_civicclerk_parses_event_details_text(self) -> None:
        title, category = CivicClerkProvider._parse_event_details(
            "Commissioners' Public Meeting\nAgenda Posted on:\nMarch 9, 2026 8:56 AM\nPublic Meetings"
        )
        self.assertEqual(title, "Commissioners' Public Meeting")
        self.assertEqual(category, "Public Meetings")

    def test_civicclerk_parses_date_details_text(self) -> None:
        starts_at = CivicClerkProvider._parse_date_details("Thursday\nMAR 12,\n2026\n2:00 PM MDT")
        self.assertEqual(starts_at, "MAR 12, 2026 2:00 PM")

    def test_civicclerk_filters_to_commission_and_council_by_default(self) -> None:
        provider = CivicClerkProvider(
            CityScrapeConfig(
                slug="missoula-county-commissioners",
                name="Missoula County Commissioners",
                provider="civicclerk",
                url="https://missoulacomt.portal.civicclerk.com/",
            )
        )
        self.assertTrue(provider._matches_filters(title="Commissioners' Public Meeting", category="Public Meetings"))
        self.assertFalse(provider._matches_filters(title="Fair Event Committee", category="Public Meetings"))

    def test_civicclerk_honors_configured_keywords_and_document_cap(self) -> None:
        provider = CivicClerkProvider(
            CityScrapeConfig(
                slug="legacy-civicclerk",
                name="Legacy Board",
                provider="civicclerk",
                url="https://example.gov/",
                metadata={"include_keywords": ["board"], "max_documents": 3},
            )
        )
        self.assertTrue(provider._matches_filters(title="Board of Adjustment", category="Hearings"))
        self.assertFalse(provider._matches_filters(title="City Council", category="Hearings"))
        self.assertEqual(provider._max_documents(), 3)

    def test_municipalimpact_extracts_numeric_date(self) -> None:
        self.assertEqual(MunicipalImpactProvider._extract_date("2-10-2026 Agenda"), "02/10/2026")

    def test_municipalimpact_extracts_two_digit_year_date(self) -> None:
        self.assertEqual(MunicipalImpactProvider._extract_date("12-9-25 Council Agenda"), "12/09/2025")

    def test_municipalimpact_extracts_named_month_date(self) -> None:
        self.assertEqual(MunicipalImpactProvider._extract_date("August 6, 2025 agenda"), "August 6, 2025")

    def test_municipalimpact_honors_keyword_filters(self) -> None:
        provider = MunicipalImpactProvider(
            CityScrapeConfig(
                slug="miles-city-council",
                name="Miles City Council",
                provider="municipalimpact",
                url="https://cityofmiles.org/agendas",
                metadata={"include_keywords": ["agenda"], "exclude_keywords": ["minutes"]},
            )
        )
        self.assertTrue(provider._matches_filters("2-10-2026 Agenda"))
        self.assertFalse(provider._matches_filters("2-10-2026 Minutes"))


if __name__ == "__main__":
    unittest.main()
