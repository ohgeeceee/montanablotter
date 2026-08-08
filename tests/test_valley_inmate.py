from unittest import mock

from services.ingestion.fetchers.valley_inmate import _discover_pdf_url, _parse_roster_text


def _response(text):
    result = mock.Mock(text=text)
    result.raise_for_status = mock.Mock()
    return result


def test_discovers_latest_month_and_document():
    landing = '<a href="/1539/July-2026">July 2026</a><a href="/1542/August-2026">August 2026</a>'
    month = '<a href="/DocumentCenter/View/5290/August-1">August 1</a><a href="/DocumentCenter/View/5292/August-2">August 2</a>'
    with mock.patch("services.ingestion.fetchers.valley_inmate._get", side_effect=[_response(landing), _response(month)]):
        assert _discover_pdf_url("https://county.test/Jail-Roster") == "https://county.test/DocumentCenter/View/5292/August-2"


def test_parses_roster_rows_and_continuations():
    text = """
NAME HELD FOR CHARGES
BAKER, THOMAS Phillips County Sheriff's Office 45-5-503 - Example charge
continued detail
FARRAR, DONALD JR. Blaine County Sheriff's Office Warrant of Arrest
Total Records: 2
"""
    rows = _parse_roster_text(text, "https://county.test/roster.pdf")
    assert len(rows) == 2
    assert rows[0].person_name == "Baker, Thomas"
    assert "continued detail" in rows[0].charges_summary
    assert rows[1].person_name == "Farrar, Donald Jr."
    assert "Blaine County Sheriff's Office" in rows[1].charges_summary
    assert "Total Records" not in rows[1].charges_summary
