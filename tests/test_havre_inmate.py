"""Tests for services.ingestion.fetchers.havre_inmate.

Layered tests:
  1. Helpers — date/name/charge normalization (no docx, no DB)
  2. Row parser — `_row_to_record` accepts a list of cell strings
  3. docx extraction — `_extract_docx_records` walks paragraphs/tables
  4. docx fetch — `fetch_havre_bookings` against a synthetic .docx

The real HPD file is not available at the time of writing, so the
paragraph/table shapes are best-guess. These tests assert the parser's
*contract* (returns a JailBookingRecord with required fields populated)
and the *defensive* behavior (skips headers, skips empty rows). The
first real HPD email will likely need the heuristics in `_row_to_record`
tuned further; the extension point is exercised here.
"""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, "/root/montanablotter")

from services.ingestion.fetchers.havre_inmate import (  # noqa: E402
    _BOND_PREFIX,
    _coerce_age,
    _extract_docx_records,
    _looks_like_header,
    _normalize_booking_datetime,
    _normalize_charges,
    _normalize_person_name,
    _person_slug,
    _row_to_record,
    fetch_havre_bookings,
)


# ---------------------------------------------------------------------------
# Layer 1: helpers
# ---------------------------------------------------------------------------


class HavreHelpersTests(unittest.TestCase):
    def test_normalize_booking_datetime_us_slash_24h(self) -> None:
        self.assertEqual(
            _normalize_booking_datetime("1/15/2026 14:30"),
            "2026-01-15 14:30:00",
        )

    def test_normalize_booking_datetime_us_slash_ampm(self) -> None:
        self.assertEqual(
            _normalize_booking_datetime("1/15/2026 9:30 AM"),
            "2026-01-15 09:30:00",
        )

    def test_normalize_booking_datetime_iso(self) -> None:
        self.assertEqual(
            _normalize_booking_datetime("2026-01-15 14:30:00"),
            "2026-01-15 14:30:00",
        )

    def test_normalize_booking_datetime_date_only(self) -> None:
        self.assertEqual(
            _normalize_booking_datetime("1/15/2026"),
            "2026-01-15 00:00:00",
        )

    def test_normalize_booking_datetime_strips_timezone(self) -> None:
        self.assertEqual(
            _normalize_booking_datetime("2026-01-15 14:30:00 UTC"),
            "2026-01-15 14:30:00",
        )

    def test_normalize_booking_datetime_garbage(self) -> None:
        self.assertIsNone(_normalize_booking_datetime("not a date"))
        self.assertIsNone(_normalize_booking_datetime(""))
        self.assertIsNone(_normalize_booking_datetime(None))

    def test_normalize_person_name_last_first(self) -> None:
        self.assertEqual(_normalize_person_name("DOE, JOHN ALAN"), "Doe, John Alan")

    def test_normalize_person_name_single(self) -> None:
        self.assertEqual(_normalize_person_name("john doe"), "John Doe")

    def test_normalize_person_name_strips_garbage(self) -> None:
        self.assertEqual(
            _normalize_person_name("Inmate DOE, JOHN"),
            "Doe, John",
        )
        self.assertEqual(
            _normalize_person_name("Defendant DOE, JOHN"),
            "Doe, John",
        )

    def test_normalize_person_name_empty(self) -> None:
        self.assertEqual(_normalize_person_name(""), "")
        self.assertEqual(_normalize_person_name(None), "")

    def test_coerce_age_plain_int(self) -> None:
        # Standalone numeric age values are accepted.
        self.assertEqual(_coerce_age(35), 35)
        self.assertEqual(_coerce_age("35"), 35)

    def test_coerce_age_rejects_labeled_age(self) -> None:
        # "Age: 35 yrs" used to be accepted but Havre rosters have no age
        # column, so any non-standalone-numeric cell is rejected.
        self.assertIsNone(_coerce_age("Age: 35 yrs"))

    def test_coerce_age_out_of_range(self) -> None:
        self.assertIsNone(_coerce_age(0))
        self.assertIsNone(_coerce_age(200))
        self.assertIsNone(_coerce_age(""))

    def test_coerce_age_rejects_bond_amount(self) -> None:
        self.assertIsNone(_coerce_age("$10,000"))

    def test_coerce_age_rejects_case_number(self) -> None:
        self.assertIsNone(_coerce_age("DC-21-2025-0029"))

    def test_coerce_age_none(self) -> None:
        self.assertIsNone(_coerce_age(None))

    def test_normalize_charges_with_bond(self) -> None:
        bond_match = _BOND_PREFIX.search("Bond: $5,000.00 cash")
        self.assertIsNotNone(bond_match)
        self.assertEqual(
            _normalize_charges("DUI - 1st offense", bond="$5,000.00"),
            "DUI - 1st offense; Bond $5,000.00",
        )

    def test_normalize_charges_no_bond_marker(self) -> None:
        self.assertEqual(
            _normalize_charges("DUI", bond="NO BOND"),
            "DUI",
        )

    def test_normalize_charges_empty_falls_through(self) -> None:
        self.assertEqual(
            _normalize_charges(""),
            "Charge details pending from official HCSO roster.",
        )

    def test_looks_like_header_true(self) -> None:
        self.assertTrue(_looks_like_header(["Name", "Age", "Booking Date", "Charge"]))
        self.assertTrue(_looks_like_header(["INMATE", "BOOKING #", "ARREST DATE"]))

    def test_looks_like_header_false(self) -> None:
        self.assertFalse(_looks_like_header(["DOE, JOHN", "35", "1/15/2026"]))
        self.assertFalse(_looks_like_header([]))
        self.assertFalse(_looks_like_header(["", "", ""]))

    def test_person_slug_basic(self) -> None:
        self.assertEqual(_person_slug("Doe, John"), "doe-john")
        self.assertEqual(_person_slug("O'Brien, Mary-Kate"), "o-brien-mary-kate")


# ---------------------------------------------------------------------------
# Layer 2: row parser
# ---------------------------------------------------------------------------


class HavreRowParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source_path = Path(tempfile.gettempdir()) / "fake_hpd_test.docx"

    def test_row_to_record_standard_5col(self) -> None:
        row = ["DOE, JOHN", "35", "1/15/2026 14:30", "DUI - 1st offense", "Bond: $5,000.00 cash"]
        rec = _row_to_record(row, source_path=self.source_path, row_idx=0)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.person_name, "Doe, John")
        # Havre rosters have no age column; a standalone "35" cell is
        # accepted by _coerce_age as a numeric age, but the live Havre
        # roster has no age column at all, so age is None in practice.
        # Unit tests that pass a standalone numeric cell still get an age.
        self.assertEqual(rec.age, 35)
        self.assertEqual(rec.booking_at, "2026-01-15 14:30:00")
        self.assertIn("DUI", rec.charges_summary)
        self.assertIn("Bond $5,000.00", rec.charges_summary)
        # source_url is None for havre rows: raw DOCX is no longer exposed
        # at /uploads/ (serve_upload returns 403 for *.docx).
        self.assertIsNone(rec.source_url)
        self.assertTrue(rec.source_record_id.startswith("havre-docx:fake_hpd_test:r0:"))

    def test_row_to_record_3col_minimal(self) -> None:
        row = ["SMITH, JANE", "28", "3/20/2026 09:15"]
        rec = _row_to_record(row, source_path=self.source_path, row_idx=1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.person_name, "Smith, Jane")
        # Havre parser no longer extracts age from rosters (the roster has
        # no age column). A numeric "28" cell would be accepted by the
        # strict _coerce_age, but we set age=None unconditionally.
        self.assertIsNone(rec.age)
        self.assertEqual(rec.booking_at, "2026-03-20 09:15:00")

    def test_row_to_record_skips_header(self) -> None:
        row = ["Name", "Age", "Booking Date", "Charge Details"]
        rec = _row_to_record(row, source_path=self.source_path, row_idx=0)
        self.assertIsNone(rec)

    def test_row_to_record_skips_short_row(self) -> None:
        rec = _row_to_record(["DOE, JOHN"], source_path=self.source_path, row_idx=0)
        self.assertIsNone(rec)

    def test_row_to_record_skips_garbage_no_name(self) -> None:
        rec = _row_to_record(
            ["1/15/2026", "1/16/2026", "1/17/2026"],
            source_path=self.source_path,
            row_idx=0,
        )
        self.assertIsNone(rec)

    def test_row_to_record_handles_empty_age(self) -> None:
        row = ["DOE, JOHN", "", "1/15/2026 14:30", "Disorderly Conduct"]
        rec = _row_to_record(row, source_path=self.source_path, row_idx=0)
        self.assertIsNotNone(rec)
        self.assertIsNone(rec.age)
        self.assertIn("Disorderly Conduct", rec.charges_summary)


# ---------------------------------------------------------------------------
# Layer 3: docx extraction
# ---------------------------------------------------------------------------


class HavreDocxExtractionTests(unittest.TestCase):
    """Test _extract_docx_records with a synthetic docx built using stdlib.

    The synthetic docx has the minimum valid structure (4 parts in a ZIP)
    plus paragraphs and table rows that mirror real HPD shapes.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="havre_test_")
        self.docx_path = Path(self.tmpdir) / "havre_sample.docx"

    def tearDown(self) -> None:
        if self.docx_path.exists():
            self.docx_path.unlink()
        try:
            Path(self.tmpdir).rmdir()
        except OSError:
            pass

    def _build_minimal_docx(self, *, paragraphs: list[str] | None = None,
                            table_rows: list[list[str]] | None = None) -> None:
        """Build a minimal valid .docx with optional paragraphs and a single
        table. The table shape is one row per entry; cells are the strings.
        """
        # Build the document.xml body
        body_parts: list[str] = []
        for p in (paragraphs or []):
            # Escape XML special chars in the paragraph text
            safe = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body_parts.append(
                f'<w:p><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'
            )
        if table_rows:
            rows_xml = []
            for row in table_rows:
                cells_xml = []
                for cell in row:
                    safe = cell.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    cells_xml.append(
                        f'<w:tc><w:p><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p></w:tc>'
                    )
                rows_xml.append(f'<w:tr>{"".join(cells_xml)}</w:tr>')
            body_parts.append(f'<w:tbl>{"".join(rows_xml)}</w:tbl>')
        body_xml = "".join(body_parts) or '<w:p/>'

        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{body_xml}</w:body>'
            '</w:document>'
        )

        content_types_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        )

        rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        )

        with zipfile.ZipFile(self.docx_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", content_types_xml)
            z.writestr("_rels/.rels", rels_xml)
            z.writestr("word/document.xml", document_xml)

    def test_extract_paragraphs(self) -> None:
        self._build_minimal_docx(paragraphs=[
            "DOE, JOHN, 35, 1/15/2026 14:30, DUI - 1st offense",
            "SMITH, JANE, 28, 3/20/2026 09:15, Disorderly Conduct",
        ])
        rows = _extract_docx_records(self.docx_path)
        self.assertEqual(len(rows), 2)
        # Each paragraph arrives as a single-cell list
        self.assertEqual(len(rows[0][1]), 1)
        self.assertIn("DOE, JOHN", rows[0][1][0])

    def test_extract_table(self) -> None:
        self._build_minimal_docx(table_rows=[
            ["Name", "Age", "Booking Date", "Charge"],
            ["DOE, JOHN ALAN", "35", "1/15/2026 14:30", "DUI - 1st offense"],
            ["SMITH, JANE", "28", "3/20/2026 09:15", "Disorderly Conduct"],
        ])
        rows = _extract_docx_records(self.docx_path)
        self.assertEqual(len(rows), 3)
        # First row is the header (4 cells); data rows have 4 cells each
        self.assertEqual(rows[0][1], ["Name", "Age", "Booking Date", "Charge"])
        self.assertEqual(rows[1][1], ["DOE, JOHN ALAN", "35", "1/15/2026 14:30", "DUI - 1st offense"])

    def test_extract_mixed_paragraphs_and_table(self) -> None:
        self._build_minimal_docx(
            paragraphs=["Havre Daily Roster - 12/24/2025"],
            table_rows=[
                ["DOE, JOHN", "35", "1/15/2026 14:30", "DUI"],
            ],
        )
        rows = _extract_docx_records(self.docx_path)
        self.assertEqual(len(rows), 2)
        self.assertIn("Havre Daily Roster", rows[0][1][0])
        self.assertEqual(rows[1][1][0], "DOE, JOHN")

    def test_extract_skips_empty_paragraphs(self) -> None:
        # Mix of empty and non-empty paragraphs
        self._build_minimal_docx(paragraphs=["DOE, JOHN, 35, 1/15/2026, DUI", ""])
        rows = _extract_docx_records(self.docx_path)
        self.assertEqual(len(rows), 1)

    def test_extract_invalid_file_raises(self) -> None:
        # A non-docx file should produce a clear error
        bad = Path(self.tmpdir) / "not_a_docx.txt"
        bad.write_text("this is not a docx")
        with self.assertRaises(ValueError) as ctx:
            _extract_docx_records(bad)
        self.assertIn("Could not read", str(ctx.exception))


# ---------------------------------------------------------------------------
# Layer 4: end-to-end docx fetch
# ---------------------------------------------------------------------------


class HavreDocxFetchTests(unittest.TestCase):
    """Build a synthetic HPD-style .docx and verify that
    `fetch_havre_bookings` parses records out."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="havre_test_")
        self.docx_path = Path(self.tmpdir) / "havre_sample.docx"

    def tearDown(self) -> None:
        if self.docx_path.exists():
            self.docx_path.unlink()
        try:
            Path(self.tmpdir).rmdir()
        except OSError:
            pass

    def _build_sample_docx_table(self) -> None:
        """Build a docx with a 4-col table: Name, Age, Booking Date, Charge."""
        rows = [
            ["Name", "Age", "Booking Date", "Charge"],
            ["DOE, JOHN ALAN", "35", "1/15/2026 14:30", "DUI - 1st offense; Bond $5,000.00"],
            ["SMITH, JANE", "28", "3/20/2026 09:15", "Disorderly Conduct"],
            ["O'BRIEN, PATRICK", "42", "5/1/2026 22:00", "DWI; Bond $1,500.00 cash"],
        ]
        body_parts = []
        rows_xml = []
        for row in rows:
            cells_xml = []
            for cell in row:
                safe = cell.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                cells_xml.append(
                    f'<w:tc><w:p><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p></w:tc>'
                )
            rows_xml.append(f'<w:tr>{"".join(cells_xml)}</w:tr>')
        body_parts.append(f'<w:tbl>{"".join(rows_xml)}</w:tbl>')

        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{"".join(body_parts)}</w:body>'
            '</w:document>'
        )
        content_types_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        )
        rels_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        )

        with zipfile.ZipFile(self.docx_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", content_types_xml)
            z.writestr("_rels/.rels", rels_xml)
            z.writestr("word/document.xml", document_xml)

    def test_fetch_havre_bookings_from_synthetic_docx(self) -> None:
        self._build_sample_docx_table()
        self.assertTrue(self.docx_path.exists(), "DOCX fixture was not written")

        records = fetch_havre_bookings(self.docx_path)

        # 3 data rows (the header is skipped via _looks_like_header)
        self.assertEqual(len(records), 3)
        names = {r.person_name for r in records}
        self.assertEqual(names, {"Doe, John Alan", "Smith, Jane", "O'Brien, Patrick"})

        for rec in records:
            # source_url is None for havre rows: the raw DOCX is no longer
            # exposed at /uploads/ (serve_upload returns 403 for *.docx).
            # The DOCX is still archived in uploads/ for operator reference;
            # the jail-bookings page shows the rendered data but no
            # "Open Official Source" button.
            self.assertIsNone(rec.source_url)
            self.assertTrue(rec.source_record_id.startswith("havre-docx:"))

    def test_fetch_havre_bookings_from_paragraph_docx(self) -> None:
        """Test the paragraph-style fallback: one inmate per paragraph,
        comma-separated fields."""
        body_parts = []
        for p in [
            "DOE, JOHN, DUI - 1st offense, 1/15/2026 14:30",
            "SMITH, JANE, Disorderly Conduct, 3/20/2026 09:15",
        ]:
            safe = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            body_parts.append(
                f'<w:p><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'
            )
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{"".join(body_parts)}</w:body>'
            '</w:document>'
        )
        with zipfile.ZipFile(self.docx_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                '</Types>'
            ))
            z.writestr("_rels/.rels", (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '</Relationships>'
            ))
            z.writestr("word/document.xml", document_xml)

        records = fetch_havre_bookings(self.docx_path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].person_name, "Doe, John")
        # Havre rosters have no age column; age is always None.
        self.assertIsNone(records[0].age)
        self.assertEqual(records[0].booking_at, "2026-01-15 14:30:00")

    def test_fetch_havre_bookings_rejects_non_docx(self) -> None:
        bad = Path(self.tmpdir) / "fake.pdf"
        bad.write_bytes(b"%PDF-1.4 fake")
        with self.assertRaises(ValueError) as ctx:
            fetch_havre_bookings(bad)
        self.assertIn("expects a .docx", str(ctx.exception))

    def test_fetch_havre_bookings_404_on_missing_file(self) -> None:
        missing = Path(self.tmpdir) / "does_not_exist.docx"
        with self.assertRaises(FileNotFoundError):
            fetch_havre_bookings(missing)


class ServeUploadDocxGuardTests(unittest.TestCase):
    """app.serve_upload must refuse to serve any *.docx file from /uploads/.

    The HCSO daily jail roster DOCX is a full unredacted roster (names, ages,
    charges, bond amounts, warrant details) that the email worker archives
    for operator reference. It's never intended for anonymous web
    distribution — the rendered booking data is shown on /jail-bookings/hill
    but the raw DOCX is not. PDFs (police blotters) continue to serve.
    """

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, "/root/montanablotter")
        import app as app_module
        app_module.app.config["TESTING"] = True
        cls.client = app_module.app.test_client()

    def test_docx_returns_403(self) -> None:
        for path in (
            "/uploads/2026-06-09_HAVRE.docx",
            "/uploads/JAILROSTER - 12-24-25.docx",
            "/uploads/FOO.DOCX",
            "/uploads/archive/nested.docx",
        ):
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(
                    resp.status_code, 403,
                    f"Expected 403 for {path}, got {resp.status_code}",
                )

    def test_pdf_still_serves(self) -> None:
        # 0304 log.pdf is a real police-blotter PDF in uploads/ — must
        # continue to serve normally.
        resp = self.client.get("/uploads/0304 log.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.headers.get("Content-Type", "").split(";")[0].strip(),
            "application/pdf",
        )


if __name__ == "__main__":
    unittest.main()
