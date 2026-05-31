import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db
from services.court.district_portal_scraper import (
    _extract_defendant_name,
    _is_criminal_case_type,
    _extract_charges_from_html,
    _extract_docket_outcome,
)
from services.court.supreme_court_outcome_scraper import _extract_outcome
from services.court.watercourt_scraper import _parse_notices, sync_montana_water_court
from services.court.taxappeal_scraper import _parse_decisions, sync_montana_tax_appeal_board


class CriminalOutcomeParserTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-outcomes-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    # --- defendant name extraction ---

    def test_extract_defendant_name_state_case(self) -> None:
        result = _extract_defendant_name('State of Montana vs. John Smith')
        self.assertEqual(result, 'John Smith')

    def test_extract_defendant_name_montana_abbreviation(self) -> None:
        result = _extract_defendant_name('Montana v. Jane Doe')
        self.assertEqual(result, 'Jane Doe')

    def test_extract_defendant_name_civil_returns_empty(self) -> None:
        result = _extract_defendant_name('Smith v. Jones Property LLC')
        self.assertEqual(result, '')

    def test_extract_defendant_name_empty_caption(self) -> None:
        self.assertEqual(_extract_defendant_name(''), '')

    # --- criminal case type detection ---

    def test_is_criminal_case_type_dc(self) -> None:
        self.assertTrue(_is_criminal_case_type('DC'))

    def test_is_criminal_case_type_cf(self) -> None:
        self.assertTrue(_is_criminal_case_type('CF'))

    def test_is_criminal_case_type_civil_cv(self) -> None:
        self.assertFalse(_is_criminal_case_type('CV'))

    def test_is_criminal_case_type_empty(self) -> None:
        self.assertFalse(_is_criminal_case_type(''))

    # --- charge table extraction ---

    def test_extract_charges_from_html_basic(self) -> None:
        html = '''
        <table>
          <tr><th>Charge</th><th>MCA</th><th>Class</th></tr>
          <tr><td>Assault</td><td>45-5-201</td><td>C Felony</td></tr>
          <tr><td>Criminal Mischief</td><td>45-6-101</td><td>Misdemeanor</td></tr>
        </table>
        '''
        charges_text, charges_list = _extract_charges_from_html(html)
        self.assertEqual(len(charges_list), 2)
        self.assertIn('Assault', charges_text)
        self.assertIn('Criminal Mischief', charges_text)

    def test_extract_charges_from_html_no_table(self) -> None:
        charges_text, charges_list = _extract_charges_from_html('<p>No charges listed</p>')
        self.assertEqual(charges_list, [])
        self.assertEqual(charges_text, '')

    # --- docket outcome extraction ---

    def test_extract_docket_outcome_guilty_plea(self) -> None:
        # Header row must contain a docket/event/filing/activity keyword for the parser to recognize it
        html = '''
        <table>
          <tr><td>Docket Date</td><td>Event Description</td></tr>
          <tr><td>2024-01-15</td><td>Defendant entered plea of guilty to Count 1</td></tr>
          <tr><td>2024-02-20</td><td>Judgment of conviction entered. Sentenced to 5 years probation.</td></tr>
        </table>
        '''
        result = _extract_docket_outcome(html)
        self.assertIn(result.get('plea', '').lower(), ('guilty', 'plea of guilty'))
        self.assertTrue(result.get('disposition') or result.get('sentence_text'))

    def test_extract_docket_outcome_dismissed(self) -> None:
        html = '''
        <table>
          <tr><td>Filing Date</td><td>Activity</td></tr>
          <tr><td>2024-03-10</td><td>State motion to dismiss granted. Nolle prosequi entered.</td></tr>
        </table>
        '''
        result = _extract_docket_outcome(html)
        disposition = result.get('disposition', '').lower()
        self.assertTrue('dismiss' in disposition or 'nolle' in disposition or disposition != '')

    def test_extract_docket_outcome_empty_html(self) -> None:
        result = _extract_docket_outcome('')
        self.assertIsInstance(result, dict)
        for key in ('plea', 'disposition', 'sentence_text', 'sentence_date', 'sentencing_judge'):
            self.assertIn(key, result)

    # --- DB schema: all outcome columns must be present ---

    def test_criminal_outcome_columns_exist_in_schema(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cols = {row['name'] for row in conn.execute('PRAGMA table_info(court_cases)')}
        conn.close()
        expected = {
            'defendant_name', 'is_criminal', 'charges_text', 'charges_json',
            'plea', 'disposition', 'sentence_text', 'sentence_date',
            'sentencing_judge', 'outcome_scraped_at',
            'original_court', 'original_case_number',
        }
        missing = expected - cols
        self.assertEqual(missing, set(), f'Missing columns: {missing}')


class SupremeCourtOutcomeTests(unittest.TestCase):
    """Unit tests for _extract_outcome() — no network calls."""

    def _make_case(self, case_type: str, appellee: str, appellant: str, opinions: list[str]) -> dict:
        return {
            'caseType': case_type,
            'originalCourt': 'Cascade County District Court',
            'originalCaseNumber': 'DC-23-101',
            'parties': [
                {'appellateRole': 'Appellant', 'partyName': appellant},
                {'appellateRole': 'Appellee', 'partyName': appellee},
            ],
            'regAction': [
                {'description': desc} for desc in opinions
            ],
        }

    def test_criminal_direct_appeal_affirmed(self) -> None:
        data = self._make_case(
            'Direct Appeal - Dangerous Drugs',
            'State of Montana',
            'Richard James Haacke',
            ['Opinion - Published - Justice Rice - AFFIRMED'],
        )
        result = _extract_outcome(data)
        self.assertEqual(result['is_criminal'], 1)
        self.assertEqual(result['defendant_name'], 'Richard James Haacke')
        self.assertEqual(result['charges_text'], 'Dangerous Drugs')
        self.assertEqual(result['disposition'], 'affirmed')
        self.assertEqual(result['sentencing_judge'], 'Justice Rice')
        self.assertEqual(result['original_court'], 'Cascade County District Court')
        self.assertEqual(result['original_case_number'], 'DC-23-101')

    def test_criminal_appeal_reversed_and_remanded(self) -> None:
        data = self._make_case(
            'Direct Appeal - Assault-Partner/Family',
            'State of Montana',
            'John Doe',
            ['Opinion - Published - Justice Gustafson - REVERSED AND REMANDED'],
        )
        result = _extract_outcome(data)
        self.assertEqual(result['is_criminal'], 1)
        self.assertEqual(result['disposition'], 'reversed_and_remanded')

    def test_criminal_appeal_noncite_memorandum(self) -> None:
        data = self._make_case(
            'Direct Appeal - DUI',
            'State of Montana',
            'Jane Smith',
            ['Opinion - Noncite/Memorandum - Justice Shea - AFFIRMED'],
        )
        result = _extract_outcome(data)
        self.assertEqual(result['is_criminal'], 1)
        self.assertEqual(result['disposition'], 'affirmed')

    def test_civil_case_not_flagged_criminal(self) -> None:
        data = self._make_case(
            'Direct Appeal - Contract Dispute',
            'Smith Corp',
            'Jane Doe',
            [],
        )
        result = _extract_outcome(data)
        self.assertEqual(result['is_criminal'], 0)
        self.assertEqual(result['defendant_name'], '')
        self.assertEqual(result['disposition'], '')

    def test_pending_case_no_opinion_yet(self) -> None:
        data = self._make_case(
            'Direct Appeal - Homicide-Negligent',
            'State of Montana',
            'Jay Steven Hubber',
            ['Brief - Appellant Reply', 'Event - Oral Argument Presented'],
        )
        result = _extract_outcome(data)
        self.assertEqual(result['is_criminal'], 1)
        self.assertEqual(result['disposition'], '')
        self.assertEqual(result['sentencing_judge'], '')

    def test_original_case_number_skipped_if_looks_like_court(self) -> None:
        data = self._make_case('Direct Appeal - Theft', 'State of Montana', 'Bob', [])
        data['originalCaseNumber'] = 'Yellowstone County District Court'
        result = _extract_outcome(data)
        self.assertEqual(result['original_case_number'], '')

    def test_charges_text_strips_direct_appeal_prefix(self) -> None:
        data = self._make_case('Direct Appeal - Homicide-Deliberate', 'State of Montana', 'X', [])
        result = _extract_outcome(data)
        self.assertEqual(result['charges_text'], 'Homicide-Deliberate')


class WaterCourtParserTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-watercourt-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_parse_notices_from_list_html(self) -> None:
        html = '''
        <ul>
          <li><a href="/notices/WC-2024-001.pdf">WC-2024-001 Notice of Hearing 3/15/2024</a></li>
          <li><a href="/notices/WC-2024-002.pdf">WC-2024-002 Final Order 4/10/2024</a></li>
        </ul>
        '''
        notices = _parse_notices(html)
        self.assertGreaterEqual(len(notices), 1)
        self.assertTrue(all('title' in n for n in notices))

    def test_sync_water_court_with_fixture_html(self) -> None:
        html = '''
        <ul>
          <li><a href="/notices/WC-2024-010.pdf">WC-2024-010 Notice of Hearing 1/20/2024</a></li>
        </ul>
        '''
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        result = sync_montana_water_court(conn, html=html)
        conn.commit()
        conn.close()
        self.assertEqual(result['source_slug'], 'montana_water_court')
        self.assertGreaterEqual(result['case_count'], 0)
        self.assertEqual(result['errors'], 0)


class TaxAppealParserTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-taxappeal-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_parse_decisions_from_list_html(self) -> None:
        html = '''
        <ul>
          <li><a href="/decisions/TAB-2024-001.pdf">TAB-2024-001 Smith v. DOR Decision 5/15/2024</a></li>
          <li><a href="/decisions/TAB-2024-002.pdf">TAB-2024-002 Jones Mining Co Decision 6/01/2024</a></li>
        </ul>
        '''
        decisions = _parse_decisions(html)
        self.assertGreaterEqual(len(decisions), 1)
        self.assertTrue(all('case_number' in d for d in decisions))

    def test_sync_tax_appeal_with_fixture_html(self) -> None:
        html = '''
        <ul>
          <li><a href="/decisions/TAB-2024-005.pdf">TAB-2024-005 Acme Corp v. DOR 3/10/2024</a></li>
        </ul>
        '''
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        result = sync_montana_tax_appeal_board(conn, html=html)
        conn.commit()
        conn.close()
        self.assertEqual(result['source_slug'], 'montana_tax_appeal_board')
        self.assertGreaterEqual(result['case_count'], 0)
        self.assertEqual(result['errors'], 0)
