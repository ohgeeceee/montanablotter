from pathlib import Path
from unittest import mock

from services.ingestion.fetchers.flathead_inmate import _parse_flathead_roster
from services.ingestion.fetchers.public_roster_inmate import (
    fetch_fallon_bookings,
    fetch_fergus_bookings,
    fetch_glacier_bookings,
    fetch_roosevelt_bookings,
)


def response(*, text="", content=b""):
    result = mock.Mock(text=text, content=content)
    result.raise_for_status = mock.Mock()
    return result


def test_fallon_html_parser():
    html = '<h3>Current Inmates:</h3><p><strong>DOE, JOHN:</strong> Theft - Bond $1,000</p><p>Last updated: 8-2-2026</p><h3>Visitation Hours:</h3>'
    with mock.patch("services.ingestion.fetchers.public_roster_inmate._get", return_value=response(text=html)):
        rows = fetch_fallon_bookings("https://fallon.test/sheriff")
    assert len(rows) == 1
    assert rows[0].person_name == "Doe, John"
    assert rows[0].source_record_id == "fallon:doe,-john"


def test_fergus_accordion_parser():
    html = '<span class="sppb-panel-title" aria-label="DOE, JOHN">Doe</span><div class="sppb-addon-content"><p>Booked in on: 5/8/2026</p><p>Charge: Theft</p></div>'
    with mock.patch("services.ingestion.fetchers.public_roster_inmate._get", return_value=response(text=html)):
        rows = fetch_fergus_bookings("https://fergus.test/roster")
    assert len(rows) == 1
    assert rows[0].booking_at == "2026-05-08 00:00:00"


def test_glacier_discovers_pdf_and_parses_rows():
    search = mock.Mock()
    search.json.return_value = [{"url": "https://glacier.test/active-inmate-report/"}]
    search.raise_for_status = mock.Mock()
    post = response(text='<a href="/uploads/active_inmate_report.pdf">Active Inmate Report</a>')
    text = '(2000001295) DEROCHE, JENNA          6            0        07/14/2026'
    with mock.patch("services.ingestion.fetchers.public_roster_inmate._get", side_effect=[search, post]), \
         mock.patch("services.ingestion.fetchers.public_roster_inmate._pdf_text", return_value=text):
        rows = fetch_glacier_bookings("https://glacier.test/category/jail-roster/")
    assert len(rows) == 1
    assert rows[0].booking_number == "2000001295"


def test_roosevelt_discovers_pdf_and_parses_rows():
    html = '<a href="/CURRENT-INMATES-CHARGES-8-2-26.pdf">Jail Roster</a>'
    text = "12/30/25 18:10 AZURE, T'SHAUNA 27"
    with mock.patch("services.ingestion.fetchers.public_roster_inmate._get", return_value=response(text=html)), \
         mock.patch("services.ingestion.fetchers.public_roster_inmate._pdf_text", return_value=text):
        rows = fetch_roosevelt_bookings("https://roosevelt.test/sheriff/")
    assert len(rows) == 1
    assert rows[0].person_name == "Azure, T'Shauna"


def test_flathead_current_article_markup():
    html = '''<article class="inmate-entry"><div class="inmate-name"><h2>DOE<span>, JOHN</span></h2></div><div class="inmate-stat"><span class="stat-label">Age:</span><p>31</p></div><div class="inmate-stat"><span class="stat-label">PIN:</span><p>123</p></div><span class="disposition-description">THEFT</span></article>'''
    rows = _parse_flathead_roster(html, "https://flathead.test/")
    assert len(rows) == 1
    assert rows[0].age == 31
    assert rows[0].source_record_id == "123"
