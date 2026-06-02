"""Tests for Flathead County warrant list parsing."""

from services.ingestion.warrants.flathead import (
    flathead_mugshot_url_from_html,
    parse_flathead_warrant_page,
)

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


SAMPLE_HTML_WITH_MUGSHOT = """
<a class="warrant-link" href="warrants_view.php?line=2025192008&letter=A">
<div class="warrant-entry">
<div class="warrant-name"><p>AARSTAD, <span class="lighten-text">DAWSON LEE</span></p></div>
<div class="img_mug" style="background: black url('image_thumb_script.php?f=20252575') center no-repeat;"></div>
<div class="warrant-stat"><h6>Age:</h6><p>25</p></div>
<div class="warrant-stat"><h6>Last Known Location:</h6><p>Kalispell, MT</p></div>
<div class="warrant-disposition"><p class="felony">VIOL RELEASE CONDITIONS</p></div>
</div>
</a>
"""


def test_flathead_mugshot_url_from_html():
    url = flathead_mugshot_url_from_html(SAMPLE_HTML_WITH_MUGSHOT)
    assert url == "https://apps.flathead.mt.gov/warrants/image_thumb_script.php?f=20252575"


def test_parse_flathead_warrant_page_extracts_records():
    records = parse_flathead_warrant_page(SAMPLE_PAGE)
    assert len(records) == 2
    assert records[0].source_record_id == "flathead-warrant:2002275003"
    assert records[0].person_name == "Thomas Edward Abelin"
    assert records[0].city == "ST LOUIS, MO"
    assert "PARTNER OR FAMILY MEMBER ASSAULT" in records[0].charges_text
    assert records[0].county == "Flathead"
    assert records[1].person_name == "Amanda Rae Abell"


def test_parse_flathead_warrant_page_extracts_mugshot_from_html():
    records = parse_flathead_warrant_page(SAMPLE_HTML_WITH_MUGSHOT)
    assert len(records) == 1
    assert records[0].mugshot_url.endswith("image_thumb_script.php?f=20252575")
