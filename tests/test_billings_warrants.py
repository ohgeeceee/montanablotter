"""Tests for Billings PD warrant list parsing."""

from services.ingestion.warrants.billings import parse_billings_warrant_page

INFO_ONLY_PAGE = """
<h2>City of Billings Warrants</h2>
<p>Please call the Billings Municipal Court or the Billings Police Department to confirm that you have an active warrant.</p>
<p>You can also access the Montana Public Access Portal to see if you have an active warrant for Billings Municipal Court.</p>
<p>Please select Billings Municipal Court in the court dropdown.</p>
"""

TABLE_PAGE = """
<h2>City of Billings Warrants</h2>
<table>
  <tr><th>Name</th><th>Charge</th><th>Bond</th></tr>
  <tr><td>SMITH, JOHN A</td><td>FAILURE TO APPEAR</td><td>$500.00</td></tr>
  <tr><td>JONES, MARY</td><td>CRIMINAL CONTEMPT</td><td></td></tr>
</table>
"""


def test_parse_billings_info_only_page_returns_empty():
    records = parse_billings_warrant_page(INFO_ONLY_PAGE)
    assert records == []


def test_parse_billings_warrant_table_extracts_records():
    records = parse_billings_warrant_page(TABLE_PAGE)
    assert len(records) == 2
    assert records[0].source_record_id == "billings-pd-warrant:john-a-smith"
    assert records[0].person_name == "John A Smith"
    assert records[0].county == "Yellowstone"
    assert records[0].city == "Billings"
    assert records[0].charges_text == "FAILURE TO APPEAR"
    assert records[0].bond_amount == "$500.00"
    assert records[0].issued_by == "Billings Municipal Court"
    assert records[1].person_name == "Mary Jones"
