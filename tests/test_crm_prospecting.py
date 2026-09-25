"""End-to-end tests for the staged CRM migration + prospecting engine.

Run from repo root with the staged code on sys.path:
      ./venv/bin/python3 -m pytest \
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from init_db import ensure_advertising_center_schema  # noqa: E402
from scripts.crm_migration import ensure_crm_schema, new_portal_token  # noqa: E402

from services.crm.prospecting.engine import ProspectRecord, upsert_prospect, run_ingest  # noqa: E402
from services.crm.prospecting.counties import county_for  # noqa: E402
from services.crm.prospecting.providers import directory_import  # noqa: E402


def fresh_db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_advertising_center_schema(conn)
    ensure_crm_schema(conn)
    return conn


class TestMigration(unittest.TestCase):
    def test_idempotent(self):
        conn = fresh_db()
        ensure_crm_schema(conn)  # second run must not raise

    def test_new_columns_and_tables(self):
        conn = fresh_db()
        cols = {r[1] for r in conn.execute('PRAGMA table_info(advertising_prospects)')}
        self.assertIn('target_vertical', cols)
        self.assertIn('license_number', cols)
        self.assertIn('email', cols)  # existing lead email -> requested leads.email
        for t in ('client_portals', 'crm_email_logs'):
            self.assertTrue(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (t,)).fetchone(), t)
        for v in ('crm_subscriptions', 'crm_invoices'):
            self.assertTrue(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view' AND name=?",
                (v,)).fetchone(), v)

    def test_token_entropy_unique(self):
        toks = {new_portal_token() for _ in range(200)}
        self.assertEqual(len(toks), 200)
        self.assertTrue(all(len(t) >= 32 for t in toks))


class TestDedupe(unittest.TestCase):
    def _rec(self, **kw):
        base = dict(business_name='Yellowstone Bail Bonds', category='bail',
                    source_provider='test', source_ref='r1')
        base.update(kw)
        return ProspectRecord(**base)

    def test_insert_then_business_key_match(self):
        conn = fresh_db()
        self.assertEqual(upsert_prospect(conn, self._rec()), 'inserted')
        self.assertEqual(
            upsert_prospect(conn, self._rec(business_name='YELLOWSTONE BAIL BONDS LLC'.replace(' LLC', ''),
                                            phone='406-555-0100', source_ref='r2')),
            'updated')
        self.assertEqual(conn.execute(
            'SELECT COUNT(*) c FROM advertising_prospects').fetchone()['c'], 1)

    def test_license_dedupe_and_blank_fill_only(self):
        conn = fresh_db()
        upsert_prospect(conn, self._rec(email='ops@yb.example', license_number='BB-1'))
        # operator already set email 'ops@...'; second source must NOT overwrite
        r = self._rec(license_number='BB-1', email='other@x.example',
                      website='https://yb.example', source_ref='r3')
        self.assertEqual(upsert_prospect(conn, r), 'updated')
        row = conn.execute('SELECT * FROM advertising_prospects').fetchone()
        self.assertEqual(row['email'], 'ops@yb.example')
        self.assertEqual(row['website'], 'https://yb.example')

    def test_stage_never_touched_by_ingest(self):
        conn = fresh_db()
        upsert_prospect(conn, self._rec())
        conn.execute("UPDATE advertising_prospects SET stage='active'")
        upsert_prospect(conn, self._rec(phone='406-555-0199', source_ref='r4'))
        self.assertEqual(conn.execute(
            'SELECT stage FROM advertising_prospects').fetchone()['stage'], 'active')

    def test_county_assignment_from_city(self):
        conn = fresh_db()
        upsert_prospect(conn, self._rec(city='Bozeman'))
        row = conn.execute('SELECT counties FROM advertising_prospects').fetchone()
        self.assertEqual(row['counties'], 'Gallatin')

    def test_non_montana_skipped(self):
        conn = fresh_db()
        self.assertEqual(upsert_prospect(conn, self._rec(state='ID')), 'skipped')

    def test_dry_run_writes_nothing(self):
        conn = fresh_db()
        counts = run_ingest(conn, [self._rec()], dry_run=True)
        self.assertEqual(counts['inserted'], 1)
        self.assertEqual(conn.execute(
            'SELECT COUNT(*) c FROM advertising_prospects').fetchone()['c'], 0)

    def test_source_link_rows(self):
        conn = fresh_db()
        upsert_prospect(conn, self._rec())
        self.assertTrue(conn.execute(
            'SELECT * FROM advertising_prospect_sources').fetchone())


class TestCountyResolver(unittest.TestCase):
    def test_known_city(self):
        self.assertEqual(county_for('Billings'), 'Yellowstone')
        self.assertEqual(county_for('Butte'), 'Silver Bow')

    def test_explicit_county_in_address(self):
        self.assertEqual(county_for('', '12 Main St, Gallatin County, MT'), 'Gallatin')

    def test_unknown_returns_blank(self):
        self.assertEqual(county_for('Atlantis'), '')


class TestDirectoryImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, text):
        p = Path(self.tmp.name) / name
        p.write_text(text)
        return str(p)

    def test_csv_heuristic_headers(self):
        f = self._write('exp.csv',
                        'Entity Name,Mailing City,Telephone,License #,Email\n'
                        'Big Sky PI,Bozeman,406-555-0123,PI-44,tip@bigsky.example\n')
        recs = list(directory_import.fetch_file(f, 'pi'))
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r.business_name, 'Big Sky PI')
        self.assertEqual(r.city, 'Bozeman')
        self.assertEqual(r.license_number, 'PI-44')
        self.assertEqual(r.email, 'tip@bigsky.example')

    def test_json_rows(self):
        f = self._write('exp.json',
                        '[{"business name": "Montana Process Co", "city": "Helena",'
                        ' "phone": "406-555-0177"}]')
        recs = list(directory_import.fetch_file(f, 'process_server'))
        self.assertEqual(recs[0].business_name, 'Montana Process Co')
        self.assertEqual(recs[0].county, '')  # Helena -> mapped at upsert

    def test_html_table(self):
        f = self._write('exp.html', """
        <html><body><table>
        <tr><th>Firm Name</th><th>Bar Number</th><th>City</th></tr>
        <tr><td>Doe &amp; Rose Law Firm</td><td>BAR77</td><td>Missoula</td></tr>
        </table></body></html>""")
        recs = list(directory_import.fetch_file(f, 'legal'))
        self.assertEqual(recs[0].business_name, 'Doe & Rose Law Firm')
        self.assertEqual(recs[0].license_number, 'BAR77')

    def test_import_then_county_tagged(self):
        conn = fresh_db()
        f = self._write('exp.csv', 'Business Name,City\nLucky Strike Bail,Anaconda\n')
        recs = list(directory_import.fetch_file(f, 'bail'))
        counts = run_ingest(conn, recs)
        self.assertEqual(counts['inserted'], 1)
        row = conn.execute('SELECT counties, category FROM advertising_prospects').fetchone()
        self.assertEqual(row['counties'], 'Deer Lodge')
        self.assertEqual(row['category'], 'bail')


if __name__ == '__main__':
    unittest.main()
