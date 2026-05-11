import sqlite3
import tempfile
import os
import unittest

from init_db import ensure_sex_offender_schema
from services.persons.sex_offender_delta import _classify_change, compute_delta


class TestSexOffenderDelta(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row
        ensure_sex_offender_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_classify_new_registration(self):
        new = {'full_name': 'John Doe', 'address_county': 'Yellowstone'}
        ctype, note = _classify_change(None, new)
        self.assertEqual(ctype, 'new_registration')
        self.assertIn('John Doe', note)

    def test_classify_removed(self):
        old = {'full_name': 'Jane Doe', 'address_county': 'Missoula'}
        ctype, note = _classify_change(old, None)
        self.assertEqual(ctype, 'removed')
        self.assertIn('Jane Doe', note)

    def test_classify_address_change(self):
        old = {'full_name': 'Bob Smith', 'address_street': '123 A St', 'address_city': 'Billings'}
        new = {'full_name': 'Bob Smith', 'address_street': '456 B St', 'address_city': 'Billings'}
        ctype, note = _classify_change(old, new)
        self.assertEqual(ctype, 'address_change')

    def test_compute_delta(self):
        self.conn.execute(
            "INSERT INTO sex_offender_snapshots (snapshot_date, total_count) VALUES (datetime('now'), 0)"
        )
        self.conn.commit()
        sid = self.conn.execute('SELECT id FROM sex_offender_snapshots').fetchone()['id']

        self.conn.execute(
            '''INSERT INTO sex_offenders (registry_id, full_name, status, address_county, raw_json)
               VALUES (?, ?, ?, ?, ?)''',
            ('MT001', 'Alice', 'active', 'Yellowstone', '{}'),
        )
        self.conn.commit()

        changes = compute_delta(self.conn, sid)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['change_type'], 'new_registration')


if __name__ == '__main__':
    unittest.main()
