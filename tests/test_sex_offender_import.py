import csv
import json
import os
import sqlite3
import tempfile
import unittest

import config
from init_db import ensure_sex_offender_schema
from services.persons.sex_offender_import import import_sex_offender_cache


class TestSexOffenderImport(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row
        ensure_sex_offender_schema(self.conn)
        self.conn.close()
        self.prev_config_db = config.DB_PATH
        config.DB_PATH = self.db.name
        self.prev_db = os.environ.get('MB_DB_PATH')
        os.environ['MB_DB_PATH'] = self.db.name

    def tearDown(self):
        config.DB_PATH = self.prev_config_db
        if self.prev_db is None:
            os.environ.pop('MB_DB_PATH', None)
        else:
            os.environ['MB_DB_PATH'] = self.prev_db
        os.unlink(self.db.name)

    def test_import_json_records(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump(
                [
                    {
                        'registry_id': 'MT-JSON-1',
                        'full_name': 'john doe',
                        'address_city': 'Billings',
                        'address_county': 'Yellowstone',
                    }
                ],
                handle,
            )
            path = handle.name
        try:
            result = import_sex_offender_cache(file_path=path, file_format='json', source_label='unit_json')
            self.assertEqual(result['records'], 1)
            conn = sqlite3.connect(self.db.name)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT full_name FROM sex_offenders WHERE registry_id='MT-JSON-1'").fetchone()
            conn.close()
            self.assertEqual(row['full_name'], 'John Doe')
        finally:
            os.unlink(path)

    def test_import_csv_records(self):
        with tempfile.NamedTemporaryFile('w', suffix='.csv', newline='', delete=False) as handle:
            writer = csv.DictWriter(handle, fieldnames=['registry_id', 'full_name', 'address_city', 'address_county'])
            writer.writeheader()
            writer.writerow(
                {
                    'registry_id': 'MT-CSV-1',
                    'full_name': 'jane doe',
                    'address_city': 'Missoula',
                    'address_county': 'Missoula',
                }
            )
            path = handle.name
        try:
            result = import_sex_offender_cache(file_path=path, file_format='csv', source_label='unit_csv')
            self.assertEqual(result['records'], 1)
            conn = sqlite3.connect(self.db.name)
            count = conn.execute("SELECT COUNT(*) FROM sex_offenders WHERE registry_id='MT-CSV-1'").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)
        finally:
            os.unlink(path)

    def test_full_sync_marks_missing_removed(self):
        conn = sqlite3.connect(self.db.name)
        conn.execute(
            """
            INSERT INTO sex_offenders (registry_id, full_name, status, raw_json)
            VALUES ('MT-OLD-1', 'Old Person', 'active', '{}')
            """
        )
        conn.commit()
        conn.close()

        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
            json.dump([{'registry_id': 'MT-NEW-1', 'full_name': 'New Person'}], handle)
            path = handle.name
        try:
            import_sex_offender_cache(file_path=path, file_format='json', full_sync=True, source_label='unit_sync')
            conn = sqlite3.connect(self.db.name)
            conn.row_factory = sqlite3.Row
            old_status = conn.execute("SELECT status FROM sex_offenders WHERE registry_id='MT-OLD-1'").fetchone()['status']
            new_status = conn.execute("SELECT status FROM sex_offenders WHERE registry_id='MT-NEW-1'").fetchone()['status']
            conn.close()
            self.assertEqual(old_status, 'removed')
            self.assertEqual(new_status, 'active')
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()
