"""Tests for Lewis and Clark Justice Court warrant list parsing."""

from services.ingestion.warrants.lewis_clark import fetch_lewis_clark_warrants
from services.ingestion.warrants.list_items import parse_li_warrant_list


SAMPLE_HTML = """
<ul>
<li>Abbott, Alexandra Nicole</li>
<li>Abdullah, Jamal Wayne Ii</li>
<li>Home</li>
<li>Contact Us</li>
</ul>
"""


def test_parse_li_warrant_list_extracts_names():
    records = parse_li_warrant_list(
        SAMPLE_HTML,
        county="Lewis and Clark",
        source_url="https://example.test/warrants",
        source_prefix="lewis-clark-jc-warrant",
        issued_by="Lewis and Clark County Justice Court",
    )
    assert len(records) == 2
    assert records[0].person_name == "Alexandra Nicole Abbott"
    assert records[0].county == "Lewis and Clark"
    assert records[0].issued_by == "Lewis and Clark County Justice Court"
    assert records[0].source_record_id == "lewis-clark-jc-warrant:alexandra-nicole-abbott"


def test_fetch_lewis_clark_warrants_parses_live_page():
    from services.ingestion.http_client import make_ingest_session, public_dns_fallback

    session = make_ingest_session(
        user_agent="Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)"
    )
    with public_dns_fallback():
        records = fetch_lewis_clark_warrants(session)
    assert len(records) > 1000
