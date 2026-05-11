import sqlite3
import tempfile
import os
import unittest

from init_db import ensure_sex_offender_schema
from services.persons.sex_offender_scraper import _normalize_name, _normalize_date, _upsert_offender


class TestSexOffenderScraper(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.conn = sqlite3.connect(self.db.name)
        self.conn.row_factory = sqlite3.Row
        ensure_sex_offender_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db.name)

    def test_normalize_name(self):
        self.assertEqual(_normalize_name('  john   doe  '), 'John Doe')

    def test_normalize_date(self):
        self.assertEqual(_normalize_date('05/11/1985'), '1985-05-11')
        self.assertIsNone(_normalize_date(''))

    def test_upsert_creates_and_updates(self):
        record = {
            'registry_id': 'MT12345',
            'full_name': 'John Doe',
            'date_of_birth': '1985-05-11',
            'tier': 'II',
            'risk_level': 'Moderate',
            'status': 'active',
            'address_street': '123 Main St',
            'address_city': 'Billings',
            'address_county': 'Yellowstone',
            'address_zip': '59101',
            'employer_name': '',
            'employer_address': '',
            'school_name': '',
            'school_address': '',
            'offense_description': 'Sexual Assault',
            'conviction_date': '2010-01-15',
            'conviction_state': 'MT',
            'conviction_county': 'Yellowstone',
            'photo_url': '',
            'source_url': 'https://svor.doj.mt.gov/detail/MT12345',
            'raw_json': '{}',
        }
        oid, is_new = _upsert_offender(self.conn, record)
        self.assertTrue(is_new)

        record['tier'] = 'III'
        oid2, is_new2 = _upsert_offender(self.conn, record)
        self.assertFalse(is_new2)
        self.assertEqual(oid, oid2)

        row = self.conn.execute('SELECT tier FROM sex_offenders WHERE id = ?', (oid,)).fetchone()
        self.assertEqual(row['tier'], 'III')


if __name__ == '__main__':
    unittest.main()
