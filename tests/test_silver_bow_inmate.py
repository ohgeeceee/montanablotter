import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ingestion.fetchers.silver_bow_inmate import (
    _parse_roster_text,
    fetch_silver_bow_bookings,
)


PDF_HTML = """
<html><body>
<a href="/DocumentCenter/View/12345/jail-report-pdf">Jail Report PDF</a>
</body></html>
""".encode()


def test_parse_roster_text_extracts_inmates():
    text = """
Book#: 26-0937 Name: SMITH, JOHN DOE Name ID: 335 Rel. Dt: **/**/**
45-5-202 MCA Assault 06/16/26 1
Book#: 26-0938 Name: CUSTER, JANE DOE Name ID: 336 Rel. Dt: **/**/**
45-6-301 MCA Theft 06/15/26 2
"""
    bookings = _parse_roster_text(text, "https://example.com/roster.pdf")
    assert len(bookings) == 2
    assert bookings[0].booking_number == "26-0937"
    assert bookings[0].person_name == "Smith, John Doe"
    assert "45-5-202" in bookings[0].charges_summary
    assert "MCA Assault" in bookings[0].charges_summary
    assert bookings[1].person_name == "Custer, Jane Doe"


def test_fetch_returns_records_when_pdf_available():
    with patch("services.ingestion.fetchers.silver_bow_inmate.requests.Session") as mock_session, \
         patch("services.ingestion.fetchers.silver_bow_inmate.convert_from_bytes") as mock_convert, \
         patch("services.ingestion.fetchers.silver_bow_inmate.pytesseract.image_to_string", return_value=""), \
         patch(
             "services.ingestion.fetchers.silver_bow_inmate._parse_roster_text",
             return_value=[Mock(booking_number="SB-001", person_name="Test Person")],
         ):
        mock_resp = Mock()
        mock_resp.text = PDF_HTML.decode()
        mock_resp.content = b"x" * 1024
        mock_resp.headers = {"Content-Type": "application/pdf"}
        mock_resp.raise_for_status = Mock()
        session = Mock()
        session.get = Mock(return_value=mock_resp)
        mock_session.return_value = session
        mock_convert.return_value = [Mock()]

        records = fetch_silver_bow_bookings()
        assert len(records) == 1
        assert records[0].booking_number == "SB-001"


def test_fetch_handles_missing_pdf_link():
    with patch("services.ingestion.fetchers.silver_bow_inmate.requests.Session") as mock_session:
        mock_resp = Mock()
        mock_resp.text = "<html><body>No links here</body></html>"
        mock_resp.raise_for_status = Mock()
        session = Mock()
        session.get = Mock(return_value=mock_resp)
        mock_session.return_value = session

        records = fetch_silver_bow_bookings()
        assert records == []
