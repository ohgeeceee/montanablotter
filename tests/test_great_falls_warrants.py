"""Tests for Great Falls municipal warrant parsing."""

from services.ingestion.warrants.html_table import parse_warrant_table_page

INFO_ONLY_PAGE = """
<h2>Warrants List</h2>
<p>View the active warrants list on the Court Public Access Portal.</p>
"""

TABLE_PAGE = """
<table>
  <tr><th>Name</th><th>Charge</th><th>Bond</th></tr>
  <tr><td>DOE, JANE</td><td>FAILURE TO APPEAR</td><td>$250.00</td></tr>
</table>
"""


def test_great_falls_info_only_page_returns_empty():
    records = parse_warrant_table_page(
        INFO_ONLY_PAGE,
        county="Cascade",
        city="Great Falls",
        source_url="https://greatfallsmt.gov/579/Warrants-List",
        source_prefix="great-falls-muni-warrant",
        issued_by="Great Falls Municipal Court",
        info_only_markers=("court public access portal",),
    )
    assert records == []


def test_great_falls_table_parses_records():
    records = parse_warrant_table_page(
        TABLE_PAGE,
        county="Cascade",
        city="Great Falls",
        source_url="https://greatfallsmt.gov/579/Warrants-List",
        source_prefix="great-falls-muni-warrant",
        issued_by="Great Falls Municipal Court",
    )
    assert len(records) == 1
    assert records[0].person_name == "Jane Doe"
    assert records[0].city == "Great Falls"
