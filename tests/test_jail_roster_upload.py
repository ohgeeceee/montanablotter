import unittest
from unittest import mock

from services.ingestion import jail_roster_upload


class JailRosterUploadTests(unittest.TestCase):
    def test_parse_roster_text_dedupes_duplicate_names_without_mutating_frozen_record(self):
        text = """
SMITH, JOHN - Theft
SMITH, JOHN - Burglary
        """.strip()

        records = jail_roster_upload._parse_roster_text(text, "https://example.test/roster.pdf")

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].source_record_id, "upload:smith,-john")
        self.assertEqual(records[1].source_record_id, "upload:smith,-john:1")
        self.assertEqual(records[0].charges_summary, "Theft")
        self.assertEqual(records[1].charges_summary, "Burglary")

    def test_parse_roster_pdf_dedupes_duplicate_table_rows_without_mutating_frozen_record(self):
        table_rows = [
            ["Name", "Charges"],
            ["SMITH, JOHN", "Theft"],
            ["SMITH, JOHN", "Burglary"],
        ]

        with mock.patch.object(jail_roster_upload, "_extract_tables_from_pdf", return_value=[table_rows]), \
             mock.patch.object(jail_roster_upload, "_extract_text_from_pdf", return_value=""):
            records = jail_roster_upload._parse_roster_pdf(b"%PDF-1.4 fake", "https://example.test/roster.pdf")

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].source_record_id, "upload:smith,-john")
        self.assertEqual(records[1].source_record_id, "upload:smith,-john:1")
        self.assertEqual(records[0].charges_summary, "Theft")
        self.assertEqual(records[1].charges_summary, "Burglary")
