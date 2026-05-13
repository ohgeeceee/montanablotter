import os
import sqlite3
import tempfile
import unittest
import csv
import json
from pathlib import Path

from init_db import ensure_code_violation_schema, ensure_civil_filing_schema
from services.ingestion.civil_filings import (
    classify_civil_filing,
    extract_parties_from_caption,
    ingest_civil_filings,
)
from services.ingestion.civil_filings_import import parse_import_file
from services.ingestion.icourtcase_civil import parse_icourtcase_search_results, default_checkpoint
from services.ingestion.icourtcase_civil import (
    load_source_checkpoint,
    run_daily_county_ingest,
    save_source_checkpoint,
)


class TestCivilFilingSchema(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_ensure_civil_filing_schema_creates_tables(self):
        ensure_code_violation_schema(self.conn)
        ensure_civil_filing_schema(self.conn)

        tables = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

        self.assertIn("civil_filing_sources", tables)
        self.assertIn("civil_filings", tables)


class TestCivilFilingIngest(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row
        ensure_code_violation_schema(self.conn)
        ensure_civil_filing_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_classify_civil_filing_maps_known_case_codes(self):
        self.assertEqual(classify_civil_filing(case_type_code='UD', caption='ABC v. Doe'), 'eviction')
        self.assertEqual(classify_civil_filing(case_type_code='DV', caption='Order of Protection'), 'restraining_order')
        self.assertEqual(classify_civil_filing(case_type_code='CC', caption='Money judgment'), 'civil_judgment')
        self.assertEqual(classify_civil_filing(case_type_code='', caption='Construction Lien Notice'), 'lien')
        self.assertEqual(classify_civil_filing(case_type_code='', caption='Final Judgment Entered'), 'civil_judgment')

    def test_extract_parties_from_caption(self):
        plaintiff, defendant = extract_parties_from_caption('ABC Properties LLC v. Jane Doe')
        self.assertEqual(plaintiff, 'ABC Properties LLC')
        self.assertEqual(defendant, 'Jane Doe')

    def test_ingest_civil_filings_creates_rows_and_property_link(self):
        records = [{
            'county': 'Yellowstone',
            'city': 'Billings',
            'case_number': 'UD-26-1234',
            'case_type_code': 'UD',
            'caption': 'ABC Properties LLC v. Jane Doe',
            'plaintiff_name': 'ABC Properties LLC',
            'defendant_name': 'Jane Doe',
            'address': '123 Main St, Billings, MT 59101',
            'filing_date': '2026-05-11',
            'case_status': 'Open',
        }]
        result = ingest_civil_filings(
            self.conn,
            source_key='import-yellowstone',
            display_name='Yellowstone Import',
            adapter_type='import_json',
            county='Yellowstone',
            records=records,
        )
        self.assertEqual(result['inserted'], 1)
        self.assertEqual(result['updated'], 0)

        row = self.conn.execute('SELECT * FROM civil_filings').fetchone()
        self.assertEqual(row['filing_class'], 'eviction')
        self.assertEqual(row['case_number'], 'UD-26-1234')

        addr = self.conn.execute('SELECT * FROM property_addresses').fetchone()
        self.assertEqual(addr['city'], 'Billings')

    def test_reingest_updates_instead_of_duplicate_insert(self):
        records = [{
            'county': 'Lewis and Clark',
            'city': 'Helena',
            'case_number': 'DV-26-11',
            'case_type_code': 'DV',
            'caption': 'State v. Doe',
            'filing_date': '2026-05-10',
            'case_status': 'Open',
        }]
        ingest_civil_filings(
            self.conn,
            source_key='helena',
            display_name='Helena',
            adapter_type='import_json',
            county='Lewis and Clark',
            records=records,
        )
        result = ingest_civil_filings(
            self.conn,
            source_key='helena',
            display_name='Helena',
            adapter_type='import_json',
            county='Lewis and Clark',
            records=records,
        )
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['inserted'], 0)

    def test_parse_import_file_reads_json_records(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump([{'county': 'Yellowstone', 'case_number': 'UD-1'}], handle)
            path = handle.name
        try:
            rows = parse_import_file(path, file_format='json')
            self.assertEqual(rows[0]['case_number'], 'UD-1')
        finally:
            os.unlink(path)

    def test_parse_import_file_reads_csv_records(self):
        with tempfile.NamedTemporaryFile('w', suffix='.csv', newline='', delete=False) as handle:
            writer = csv.DictWriter(handle, fieldnames=['county', 'case_number'])
            writer.writeheader()
            writer.writerow({'county': 'Yellowstone', 'case_number': 'UD-1'})
            path = handle.name
        try:
            rows = parse_import_file(path, file_format='csv')
            self.assertEqual(rows[0]['county'], 'Yellowstone')
        finally:
            os.unlink(path)

    def test_parse_import_file_rejects_unknown_format(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as handle:
            handle.write('nope')
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                parse_import_file(path, file_format='txt')
        finally:
            os.unlink(path)

    def test_parse_icourtcase_search_results_extracts_case_rows(self):
        fixture_path = Path('/root/montanablotter/tests/fixtures/icourtcase_civil_search.html')
        html = fixture_path.read_text(encoding='utf-8')
        rows = parse_icourtcase_search_results(html, county='Yellowstone')
        self.assertEqual(rows[0]['case_number'], 'UD-26-1234')
        self.assertEqual(rows[0]['case_type_code'], 'UD')
        self.assertEqual(rows[0]['county'], 'Yellowstone')
        self.assertEqual(rows[0]['plaintiff_name'], 'ABC Properties LLC')
        self.assertEqual(rows[0]['defendant_name'], 'Jane Doe')

    def test_default_checkpoint_is_county_scoped(self):
        checkpoint = default_checkpoint(county='Yellowstone', date_from='2026-05-01', page=3)
        self.assertEqual(
            checkpoint,
            {
                'county': 'Yellowstone',
                'date_from': '2026-05-01',
                'page': 3,
            },
        )

    def test_checkpoint_round_trip(self):
        ingest_civil_filings(
            self.conn,
            source_key='icourtcase-yellowstone',
            display_name='iCourtCase Yellowstone',
            adapter_type='icourtcase',
            county='Yellowstone',
            records=[],
        )
        checkpoint = {'county': 'Yellowstone', 'date_from': '2026-05-01', 'page': 2}
        save_source_checkpoint(self.conn, source_key='icourtcase-yellowstone', checkpoint=checkpoint)
        loaded = load_source_checkpoint(self.conn, source_key='icourtcase-yellowstone', county='Yellowstone')
        self.assertEqual(loaded['county'], 'Yellowstone')
        self.assertEqual(loaded['date_from'], '2026-05-01')
        self.assertEqual(loaded['page'], 2)

    def test_run_daily_county_ingest_uses_checkpoint_and_updates_it(self):
        ingest_civil_filings(
            self.conn,
            source_key='icourtcase-yellowstone',
            display_name='iCourtCase Yellowstone',
            adapter_type='icourtcase',
            county='Yellowstone',
            records=[],
        )
        save_source_checkpoint(
            self.conn,
            source_key='icourtcase-yellowstone',
            checkpoint={'county': 'Yellowstone', 'date_from': '2026-05-01', 'page': 1},
        )

        fixture_path = Path('/root/montanablotter/tests/fixtures/icourtcase_civil_search.html')
        html = fixture_path.read_text(encoding='utf-8')

        def fake_fetcher(url: str) -> str:
            return html if 'page=1' in url else '<html><body><table></table></body></html>'

        result = run_daily_county_ingest(
            self.conn,
            county='Yellowstone',
            source_key='icourtcase-yellowstone',
            max_pages=3,
            delay_seconds=0,
            fetcher=fake_fetcher,
        )
        self.assertEqual(result['fetched'], 1)
        row = self.conn.execute(
            'SELECT checkpoint_json FROM civil_filing_sources WHERE source_key = ?',
            ('icourtcase-yellowstone',),
        ).fetchone()
        payload = json.loads(row['checkpoint_json'])
        self.assertEqual(payload['county'], 'Yellowstone')


if __name__ == '__main__':
    unittest.main()
