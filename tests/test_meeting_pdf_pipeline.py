import unittest

from meeting_pdf_pipeline.chunking import chunk_markdown
from meeting_pdf_pipeline.extractor import (
    MeetingPDFMarkdownExtractor,
    _extract_row_from_line,
    _clean_money,
    _normalise_date,
)
from meeting_pdf_pipeline.store import _vector_literal


class MeetingPDFPipelineTests(unittest.TestCase):
    def test_chunk_markdown_splits_large_sections(self) -> None:
        markdown = "## Heading\n\n" + ("alpha " * 250) + "\n\n" + ("beta " * 250)
        chunks = chunk_markdown(markdown, max_chars=500, overlap=50)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.strip() for chunk in chunks))

    def test_vector_literal_formats_pgvector_input(self) -> None:
        literal = _vector_literal([0.125, 1.5, -0.75])
        self.assertEqual(literal, "[0.125,1.5,-0.75]")

    def test_markdown_to_text_removes_headings(self) -> None:
        text = MeetingPDFMarkdownExtractor._markdown_to_text("## Page 1\n\nAgenda Item 1\n\n### Notes")
        self.assertEqual(text, "Page 1\nAgenda Item 1\nNotes")

    def test_looks_useful_requires_meaningful_text(self) -> None:
        self.assertFalse(MeetingPDFMarkdownExtractor._looks_useful("x" * 10))
        self.assertTrue(MeetingPDFMarkdownExtractor._looks_useful("Agenda item " * 10))

    def test_extract_row_from_line_parses_common_roster_line(self) -> None:
        row = _extract_row_from_line("SMITH, JOHN - Assault 03/14/2026 $25,000")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["name"], "SMITH, JOHN")
        self.assertIn("Assault", row["charge"])
        self.assertEqual(row["date"], "2026-03-14")
        self.assertEqual(row["bond"], "$25000")

    def test_money_and_date_normalizers_keep_fallbacks(self) -> None:
        self.assertEqual(_clean_money("No Bond"), "No Bond")
        self.assertEqual(_clean_money("$100,000.00"), "$100000.00")
        self.assertEqual(_normalise_date("2026-04-21"), "2026-04-21")
        self.assertEqual(_normalise_date("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
