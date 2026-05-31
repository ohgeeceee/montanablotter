"""Tests for Flathead County warrant list parsing."""

from services.ingestion.warrants.flathead import parse_flathead_warrant_page

SAMPLE_PAGE = """
[
ABELIN,THOMAS EDWARD
###### Age:
61
###### Last Known Location:
ST LOUIS, MO
PARTNER OR FAMILY MEMBER ASSAULT, CAUSING BODILY INJURY TO PARTNER OR FAMILY MEMBER-1ST OFFENSE
](warrants_view.php?line=2002275003&letter=)[
ABELL,AMANDA RAE
###### Age:
47
###### Last Known Location:
GRAND JUNCTION, CO
ISSUING A BAD CHECK
](warrants_view.php?line=1999172006&letter=)
"""


def test_parse_flathead_warrant_page_extracts_records():
    records = parse_flathead_warrant_page(SAMPLE_PAGE)
    assert len(records) == 2
    assert records[0].source_record_id == "flathead-warrant:2002275003"
    assert records[0].person_name == "Thomas Edward Abelin"
    assert records[0].city == "ST LOUIS, MO"
    assert "PARTNER OR FAMILY MEMBER ASSAULT" in records[0].charges_text
    assert records[0].county == "Flathead"
    assert records[1].person_name == "Amanda Rae Abell"
