import sqlite3
import tempfile
import os
import json
import unittest

from init_db import ensure_code_violation_schema
from services.ingestion.code_violations import ingest_records, _slugify_address, _hash_record, _normalize_date
from services.ingestion.property_addresses import parse_address_parts, slugify_address


class TestCodeViolationIngest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row
        ensure_code_violation_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_slugify_address(self):
        self.assertEqual(_slugify_address('123 Main St', 'Missoula', 'MT', '59801'), '123-main-st-missoula-mt-59801')
        self.assertEqual(slugify_address('123 Main St', 'Missoula', 'MT', '59801'), '123-main-st-missoula-mt-59801')

    def test_parse_address_parts_extracts_zip(self):
        parsed = parse_address_parts('456 Oak Ave, Billings, MT 59101', fallback_city='Billings')
        self.assertEqual(parsed['street'], '456 Oak Ave')
        self.assertEqual(parsed['zip_code'], '59101')
        self.assertEqual(parsed['city'], 'Billings')
        self.assertEqual(parsed['state'], 'MT')

    def test_normalize_date(self):
        self.assertEqual(_normalize_date('05/11/2026'), '2026-05-11')
        self.assertEqual(_normalize_date('2026-05-11'), '2026-05-11')
        self.assertIsNone(_normalize_date(''))

    def test_ingest_creates_source_and_violation(self):
        records = [
            {
                'address': '456 Oak Ave, Billings, MT 59101',
                'violation_type': 'Abandoned Vehicle',
                'status': 'open',
                'date_issued': '2026-04-01',
                'owner_name': 'John Doe',
            }
        ]
        result = ingest_records(
            self.conn,
            source_key='billings',
            display_name='Billings Code Enforcement',
            city='Billings',
            records=records,
        )
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['updated'], 0)

        row = self.conn.execute('SELECT * FROM code_violations').fetchone()
        self.assertEqual(row['violation_type'], 'Abandoned Vehicle')
        self.assertEqual(row['status'], 'open')

        addr = self.conn.execute('SELECT * FROM property_addresses').fetchone()
        self.assertEqual(addr['city'], 'Billings')

    def test_idempotent_reingest_updates(self):
        records = [
            {'address': '789 Pine Rd, Helena, MT 59601', 'violation_type': 'Permit Violation', 'status': 'open', 'date_issued': '2026-03-01'}
        ]
        ingest_records(self.conn, source_key='helena', display_name='Helena', city='Helena', records=records)
        result = ingest_records(self.conn, source_key='helena', display_name='Helena', city='Helena', records=records)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['inserted'], 0)

    def test_ingest_updates_source_run_metadata(self):
        records = [
            {
                'address': '456 Oak Ave, Billings, MT 59101',
                'violation_type': 'Abandoned Vehicle',
                'status': 'open',
                'date_issued': '2026-04-01',
            }
        ]
        ingest_records(
            self.conn,
            source_key='billings',
            display_name='Billings Code Enforcement',
            city='Billings',
            records=records,
        )
        source = self.conn.execute(
            'SELECT last_success_at, latest_error FROM code_violation_sources WHERE source_key = ?',
            ('billings',),
        ).fetchone()
        self.assertIsNotNone(source['last_success_at'])
        self.assertEqual(source['latest_error'], '')


if __name__ == '__main__':
    unittest.main()
