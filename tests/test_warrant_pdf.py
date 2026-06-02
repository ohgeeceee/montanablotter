"""Tests for generic warrant PDF line parsing."""

from services.ingestion.warrants.scraper import _parse_warrant_line, parse_warrant_pdf


def test_parse_warrant_line_extracts_name_bond_and_court():
    line = "SMITH, JOHN  500.00(Cash)  FAILURE TO APPEAR  Justice of the Peace"
    record = _parse_warrant_line(line, "Rosebud", "https://example.test/warrant.pdf")
    assert record is not None
    assert record.person_name == "Smith, John"
    assert record.bond_amount == "500.00"
    assert record.bond_type == "Cash"
    assert "FAILURE TO APPEAR" in record.charges_text
    assert record.issued_by == "Justice of the Peace"
    assert record.source_record_id == "rosebud-warrant:smith-john"


def test_parse_warrant_pdf_skips_header_until_list_begins():
    pdf_text = (
        "Rosebud County Sheriff\n"
        "Warrant List\n"
        "Last, First Name\n"
        "DOE, JANE  250.00(Surety)  CRIMINAL CONTEMPT  District Court\n"
    ).encode()

    class _FakePage:
        def extract_text(self):
            return pdf_text.decode()

    class _FakePDF:
        pages = [_FakePage()]

    import services.ingestion.warrants.scraper as scraper

    original_open = scraper.pdfplumber.open

    class _Ctx:
        def __enter__(self):
            return _FakePDF()

        def __exit__(self, *args):
            return False

    scraper.pdfplumber.open = lambda _buf: _Ctx()
    try:
        records = parse_warrant_pdf(pdf_text, "https://example.test/warrant.pdf", "Rosebud")
    finally:
        scraper.pdfplumber.open = original_open

    assert len(records) == 1
    assert records[0].person_name == "Doe, Jane"
