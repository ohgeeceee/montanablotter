import unittest

from meeting_pdf_pipeline.chunking import chunk_markdown
from meeting_pdf_pipeline.extractor import MeetingPDFMarkdownExtractor
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


if __name__ == "__main__":
    unittest.main()
