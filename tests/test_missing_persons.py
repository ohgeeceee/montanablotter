import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import app as app_module
import config
import init_db
import services.persons.missing as missing_persons_module


class MissingPersonsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-missing-persons-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        bootstrap_conn.commit()
        bootstrap_conn.close()

        init_db.init_database()
        init_db.migrate()
        self.admin_user_id = self._create_admin_user()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _create_admin_user(self) -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            ('missing-admin', 'not-used-in-tests', 'missing@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login_admin_session(self, client) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(self.admin_user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def _seed_missing_persons(self) -> None:
        conn = app_module.get_db()
        conn.execute(
            """
            INSERT INTO missing_persons (
                slug, full_name, county, city, last_seen_location, summary,
                source_name, source_url, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'active-person-1',
                'Active Person',
                'Yellowstone',
                'Billings',
                'Billings',
                'Active case summary for the database view.',
                'Montana DOJ Missing Persons Database',
                'https://example.com/active-person',
                'missing',
                '2026-04-25 08:00:00',
                '2026-04-25 08:00:00',
            ),
        )
        conn.execute(
            """
            INSERT INTO missing_persons (
                slug, full_name, county, city, last_seen_location, summary,
                source_name, source_url, status, resolution_summary, resolved_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'located-person-1',
                'Located Person',
                'Cascade',
                'Great Falls',
                'Great Falls',
                'Located case summary for the resolved lane.',
                'Montana DOJ Missing Persons Database',
                'https://example.com/located-person',
                'located',
                'Found safe and removed from the active list.',
                '2026-04-24 16:30:00',
                '2026-04-24 08:00:00',
                '2026-04-25 09:15:00',
            ),
        )
        conn.commit()
        conn.close()

    def _seed_rich_missing_person(self) -> str:
        conn = app_module.get_db()
        slug = 'rich-person-1'
        conn.execute(
            """
            INSERT INTO missing_persons (
                slug, full_name, county, city, last_seen_at, last_seen_location,
                summary, physical_description, contact_info, source_name, source_url,
                photo_url, status, resolution_summary, source_person_id, gender, race,
                hair_color, eye_color, height_raw, weight_lbs, age, age_missing,
                investigating_agency, aliases, photo_gallery_json, official_last_updated,
                official_last_checked, created_at, updated_at, last_alerted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slug,
                'Rich Detail Person',
                'Gallatin',
                'Bozeman',
                '2026-04-20 14:00:00',
                'Bozeman Trail',
                'Rich detail summary for the public detail page.',
                'Tall, brown hair, blue eyes.',
                'Call Gallatin County Sheriff at 406-555-1212.',
                'Montana DOJ Missing Persons Database',
                'https://example.com/rich-person',
                'https://example.com/rich-person-primary.jpg',
                'located',
                'Located safe after a community tip.',
                '77777',
                'FEMALE',
                'WHITE',
                'BROWN',
                'BLUE',
                '511',
                140,
                21,
                19,
                'GALLATIN COUNTY SHERIFF',
                'Smith, Jane; Doe, J.',
                '[{"url":"https://example.com/rich-person-primary.jpg","label":"Primary"},{"url":"https://example.com/rich-person-secondary.jpg","label":"Secondary"}]',
                '2026-04-20 18:30:00',
                '2026-04-20 18:45:00',
                '2026-04-20 09:00:00',
                '2026-04-25 10:00:00',
                '2026-04-25 09:00:00',
            ),
        )
        conn.commit()
        conn.close()
        return slug

    def test_ensure_missing_person_schema_adds_directory_columns(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        conn.execute('DROP TABLE IF EXISTS missing_persons')
        conn.execute(
            '''
            CREATE TABLE missing_persons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE,
                full_name TEXT NOT NULL,
                county TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'missing',
                updated_at TEXT DEFAULT (datetime('now'))
            )
            '''
        )
        conn.execute(
            '''
            INSERT INTO missing_persons (slug, full_name, county, status, updated_at)
            VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
            ''',
            (
                'legacy-active',
                'Legacy Active',
                'Yellowstone',
                'missing',
                '2026-04-25 08:00:00',
                'legacy-resolved',
                'Legacy Resolved',
                'Cascade',
                'located',
                '2026-04-25 08:05:00',
            ),
        )
        conn.commit()

        missing_persons_module.ensure_missing_person_schema(conn)

        columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info('missing_persons')").fetchall()
        }
        assert 'case_number' in columns
        assert 'missing_from' in columns
        assert 'height_weight' in columns
        assert 'last_synced' in columns
        assert 'is_active' in columns

        rows = conn.execute(
            '''
            SELECT full_name, status, is_active
            FROM missing_persons
            ORDER BY full_name
            '''
        ).fetchall()
        self.assertEqual(rows[0]['full_name'], 'Legacy Active')
        self.assertEqual(rows[0]['status'], 'missing')
        self.assertEqual(rows[0]['is_active'], 1)
        self.assertEqual(rows[1]['full_name'], 'Legacy Resolved')
        self.assertEqual(rows[1]['status'], 'located')
        self.assertEqual(rows[1]['is_active'], 0)

        conn.execute(
            "UPDATE missing_persons SET is_active = 99 WHERE full_name = 'Legacy Active'"
        )
        conn.commit()
        missing_persons_module.ensure_missing_person_schema(conn)
        rerun = conn.execute(
            "SELECT is_active FROM missing_persons WHERE full_name = 'Legacy Active'"
        ).fetchone()
        self.assertEqual(rerun['is_active'], 99)

    def test_normalize_directory_record_handles_bad_dates(self) -> None:
        normalized = missing_persons_module._normalize_directory_record(
            {
                'full_name': '  Jane Example  ',
                'age': '19',
                'missing_from': '  Billings / Yellowstone  ',
                'date_last_seen': 'not-a-date',
                'height_weight': ' 5-08 / 120 ',
                'case_number': ' MP-42 ',
                'status': 'missing',
            }
        )

        self.assertEqual(normalized['full_name'], 'Jane Example')
        self.assertEqual(normalized['age'], 19)
        self.assertEqual(normalized['missing_from'], 'Billings / Yellowstone')
        self.assertEqual(normalized['date_last_seen'], '')
        self.assertEqual(normalized['height_weight'], '5-08 / 120')
        self.assertEqual(normalized['case_number'], 'MP-42')
        self.assertTrue(normalized['is_active'])

    def test_missing_person_homepage_context_does_not_run_schema_migration_on_reads(self) -> None:
        self._seed_missing_persons()
        conn = app_module.get_db()
        self.addCleanup(conn.close)

        with mock.patch.object(
            missing_persons_module,
            'ensure_missing_person_schema',
            side_effect=AssertionError('request-time schema migration should not run'),
        ):
            context = missing_persons_module.missing_person_homepage_context(conn, limit=2)

        self.assertEqual(len(context['rows']), 1)
        self.assertEqual(context['rows'][0]['full_name'], 'Active Person')
        self.assertEqual(len(context['resolved_rows']), 1)
        self.assertEqual(context['resolved_rows'][0]['full_name'], 'Located Person')

    def test_ensure_missing_person_schema_migrates_legacy_delivery_schema(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        conn.execute('DROP TABLE IF EXISTS missing_person_push_subscriptions')
        conn.execute('DROP INDEX IF EXISTS idx_missing_person_alert_delivery_unique')
        conn.execute('DROP TABLE IF EXISTS missing_person_alert_deliveries')
        conn.execute('DROP TABLE IF EXISTS subscribers')
        conn.execute(
            '''
            CREATE TABLE subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            '''
        )
        conn.execute(
            '''
            INSERT INTO subscribers (email, counties, token, active)
            VALUES (?, ?, ?, ?), (?, ?, ?, ?)
            ''',
            (
                'legacy-1@example.com', 'Gallatin', 'legacy-token-1', 1,
                'legacy-2@example.com', 'Yellowstone', 'legacy-token-2', 1,
            ),
        )
        conn.execute(
            '''
            CREATE TABLE missing_person_alert_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                missing_person_id INTEGER NOT NULL,
                notification_version INTEGER NOT NULL DEFAULT 1,
                recipient_email TEXT NOT NULL,
                delivery_status TEXT NOT NULL DEFAULT 'queued',
                error_message TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
            '''
        )
        conn.execute(
            '''
            INSERT INTO missing_person_alert_deliveries (
                missing_person_id,
                notification_version,
                recipient_email,
                delivery_status,
                error_message,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (1, 1, 'legacy@example.com', 'queued', '', '2026-04-25 08:00:00'),
        )
        conn.execute(
            '''
            INSERT INTO missing_person_alert_deliveries (
                missing_person_id,
                notification_version,
                recipient_email,
                delivery_status,
                error_message,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (1, 1, ' legacy@example.com ', 'queued', '', '2026-04-25 08:05:00'),
        )
        conn.commit()

        missing_persons_module.ensure_missing_person_schema(conn)
        missing_persons_module.ensure_missing_person_schema(conn)

        subscriber_columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info('subscribers')").fetchall()
        }
        assert 'missing_person_email_opt_in' in subscriber_columns
        assert 'missing_person_sms_opt_in' in subscriber_columns
        assert 'missing_person_push_opt_in' in subscriber_columns
        assert 'phone_verified_at' in subscriber_columns

        push_columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info('missing_person_push_subscriptions')").fetchall()
        }
        assert {'subscriber_id', 'endpoint', 'p256dh_key', 'auth_key', 'active'} <= push_columns

        delivery_columns = {
            row['name']
            for row in conn.execute("PRAGMA table_info('missing_person_alert_deliveries')").fetchall()
        }
        assert {'subscriber_id', 'channel', 'recipient', 'provider_message_id', 'updated_at'} <= delivery_columns

        migrated_row = conn.execute(
            '''
            SELECT recipient, channel, updated_at
            FROM missing_person_alert_deliveries
            WHERE recipient_email = ?
            ''',
            ('legacy@example.com',),
        ).fetchone()
        assert migrated_row is not None
        assert migrated_row['recipient'] == 'legacy@example.com'
        assert migrated_row['channel'] == 'email'
        assert migrated_row['updated_at'] == '2026-04-25 08:00:00'
        spaced_row = conn.execute(
            '''
            SELECT recipient, channel, updated_at
            FROM missing_person_alert_deliveries
            WHERE recipient_email = ?
            ''',
            (' legacy@example.com ',),
        ).fetchone()
        assert spaced_row is not None
        assert spaced_row['recipient'] == ' legacy@example.com '
        assert spaced_row['channel'] == 'email'
        assert spaced_row['updated_at'] == '2026-04-25 08:05:00'

        conn.execute(
            '''
            INSERT INTO missing_person_alert_deliveries (
                missing_person_id,
                notification_version,
                subscriber_id,
                recipient_email,
                channel,
                recipient,
                delivery_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (1, 1, 1, '', 'sms', '', 'queued'),
        )
        conn.execute(
            '''
            INSERT INTO missing_person_alert_deliveries (
                missing_person_id,
                notification_version,
                subscriber_id,
                recipient_email,
                channel,
                recipient,
                delivery_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (1, 1, 2, '', 'sms', '', 'queued'),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                '''
                INSERT INTO missing_person_alert_deliveries (
                    missing_person_id,
                    notification_version,
                    subscriber_id,
                    recipient_email,
                    channel,
                    recipient,
                    delivery_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (1, 1, 1, '', 'sms', '', 'queued'),
            )

    def test_parse_official_card_records_and_counts(self) -> None:
        html = """
        <div id="resultsInfo">
            <span>158 individuals match search criteria </span>
            <span>42 missing less than a year </span>
            <span>116 missing more than a year </span>
        </div>
        <ul id="resultsList">
        <li class="personCard" data-sort="45849" data-time="1744243200" data-name="WAGNER, TRENTON CHRISTOPHER" data-agenow="17" data-agemissing="17">
            <div class="personDetailsFirst">
                <h2>Wagner, Trenton Christopher</h2>
                <p><b>Age Now: </b>17
                <br><b>Gender: </b>Male
                <br><b>Race: </b>White</p>
                <button class="personDetailsButton" onclick='OpenDetailsWindow([{"0":"640cc8"}],["03\\/20\\/2026"],"Wagner, Trenton Christopher","MALE","17","WHITE","BROWN","BLUE","510","150","04/09/2026","YELLOWSTONE COUNTY SHERIFF","","45849")'
                 aria-expanded="false" aria-label="Full details for WAGNER, TRENTON CHRISTOPHER">Full Details</button>
            </div>
            <div class="personImageContainer"><img src="images\\mmps_img\\640cc8.jpg" class="personImage" alt="Person Image"/></div>
            <div class="personDetailsSecond"><p><b>Date of Last Contact:</b> 04/09/2026</p></div>
        </li>
        </ul>
        """
        counts = missing_persons_module._parse_official_counts(html)
        records = missing_persons_module._parse_official_card_records(html)

        self.assertEqual(counts['total_active'], 158)
        self.assertEqual(counts['missing_less_than_year'], 42)
        self.assertEqual(counts['missing_more_than_year'], 116)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['source_person_id'], '45849')
        self.assertEqual(records[0]['investigating_agency'], 'YELLOWSTONE COUNTY SHERIFF')
        self.assertEqual(records[0]['age_now'], 17)
        self.assertEqual(records[0]['is_child'], 1)

    def test_sync_official_missing_persons_upserts_and_resolves(self) -> None:
        conn = app_module.get_db()
        snapshot = {
            'source_name': missing_persons_module.OFFICIAL_SOURCE_NAME,
            'official_last_updated': '2026-April-11 12:24:31 MDT',
            'official_last_checked': '2026-April-11 13:29:44 MDT',
            'stats': {
                'total_active': 1,
                'children': 1,
                'indigenous': 0,
                'missing_less_than_year': 1,
                'missing_more_than_year': 0,
            },
            'records': [
                {
                    'source_person_id': '45849',
                    'full_name': 'WAGNER, TRENTON CHRISTOPHER',
                    'gender': 'MALE',
                    'age_now': 17,
                    'age_missing': 17,
                    'race': 'WHITE',
                    'hair_color': 'BROWN',
                    'eye_color': 'BLUE',
                    'height_raw': '510',
                    'weight_lbs': 150,
                    'last_seen_at': '2026-04-09 00:00:00',
                    'investigating_agency': 'YELLOWSTONE COUNTY SHERIFF',
                    'aliases': '',
                    'photo_url': 'https://example.com/photo.jpg',
                    'photo_gallery': [{'url': 'https://example.com/photo.jpg', 'label': '03/20/2026'}],
                    'is_indigenous': 0,
                    'is_child': 1,
                }
            ],
        }

        first_result = missing_persons_module.sync_official_missing_persons(conn, actor='test_sync', snapshot=snapshot)
        conn.commit()

        self.assertEqual(first_result['created'], 1)
        self.assertEqual(first_result['active_total'], 1)

        row = conn.execute(
            """
            SELECT status, is_active, source_person_id, official_last_updated,
                   missing_from, height_weight, last_synced
            FROM missing_persons
            WHERE source_person_id = '45849'
            """
        ).fetchone()
        self.assertEqual(row['status'], 'missing')
        self.assertEqual(row['is_active'], 1)
        self.assertEqual(row['official_last_updated'], '2026-April-11 12:24:31 MDT')
        self.assertEqual(row['missing_from'], 'Yellowstone')
        self.assertEqual(row['height_weight'], "5'10\" / 150 lbs")
        self.assertEqual(row['last_synced'], '2026-April-11 13:29:44 MDT')

        empty_snapshot = {
            **snapshot,
            'stats': {
                'total_active': 0,
                'children': 0,
                'indigenous': 0,
                'missing_less_than_year': 0,
                'missing_more_than_year': 0,
            },
            'records': [],
        }
        second_result = missing_persons_module.sync_official_missing_persons(conn, actor='test_sync', snapshot=empty_snapshot)
        conn.commit()
        conn.close()

        # Empty-snapshot guard: a fetch that produced zero records must NOT
        # sweep the previously-active row to STATUS_LOCATED. The public page
        # should keep showing it until a subsequent successful run proves it
        # was actually delisted from the MT DOJ database.
        self.assertEqual(second_result['resolved'], 0)
        self.assertTrue(second_result.get('snapshot_empty'))

        check_conn = sqlite3.connect(self.db_path)
        check_conn.row_factory = sqlite3.Row
        updated = check_conn.execute(
            """
            SELECT status, is_active, resolution_summary, last_synced
            FROM missing_persons
            WHERE source_person_id = '45849'
            """
        ).fetchone()
        stats = check_conn.execute(
            "SELECT total_active, children FROM missing_person_source_stats WHERE id = 1"
        ).fetchone()
        check_conn.close()

        # Row stays MISSING until a non-empty snapshot proves it's gone.
        self.assertEqual(updated['status'], 'missing')
        self.assertEqual(updated['is_active'], 1)
        self.assertEqual(updated['resolution_summary'], '')
        # last_synced still reflects the prior successful run (snapshot
        # empty path skips the per-row UPDATE that would have touched it).
        self.assertEqual(updated['last_synced'], '2026-April-11 13:29:44 MDT')
        # Stats carry through the empty snapshot but the active count is
        # not dropped to zero — preserves last-known good number.
        self.assertEqual(stats['total_active'], 0)
        self.assertEqual(stats['children'], 0)

    def test_sync_official_missing_persons_empty_snapshot_skips_sweep(self) -> None:
        """Empty/failed snapshot preserves the existing active table (regression test).

        Without the snapshot_empty guard, a Cloudflare-blocked fetch would
        flip every previously-active row to STATUS_LOCATED in a single sync
        cycle and silently empty the public page.
        """
        conn = app_module.get_db()
        base_snapshot = {
            'source_name': missing_persons_module.OFFICIAL_SOURCE_NAME,
            'official_last_updated': '2026-July-29 14:50:01 MDT',
            'official_last_checked': '2026-07-29 22:55:01 UTC',
            'stats': {
                'total_active': 2,
                'children': 0,
                'indigenous': 0,
                'missing_less_than_year': 1,
                'missing_more_than_year': 1,
            },
            'records': [
                {
                    'source_person_id': '471',
                    'full_name': 'DECOTEAU, PEGGY JO',
                    'gender': 'FEMALE',
                    'age_now': 87,
                    'age_missing': 40,
                    'race': 'WHITE',
                    'hair_color': 'RED OR AUBURN',
                    'eye_color': 'BLUE',
                    'height_raw': '504',
                    'weight_lbs': 155,
                    'last_seen_at': '1979-07-04 00:00:00',
                    'investigating_agency': 'MINERAL COUNTY SHERIFF',
                    'aliases': '',
                    'photo_url': '',
                    'photo_gallery': [],
                    'is_indigenous': 0,
                    'is_child': 0,
                },
                {
                    'source_person_id': '999',
                    'full_name': 'DOE, JANE',
                    'gender': 'FEMALE',
                    'age_now': 30,
                    'age_missing': 25,
                    'race': 'WHITE',
                    'hair_color': 'BROWN',
                    'eye_color': 'BLUE',
                    'height_raw': '502',
                    'weight_lbs': 130,
                    'last_seen_at': '2026-04-09 00:00:00',
                    'investigating_agency': 'YELLOWSTONE COUNTY SHERIFF',
                    'aliases': '',
                    'photo_url': '',
                    'photo_gallery': [],
                    'is_indigenous': 0,
                    'is_child': 0,
                },
            ],
        }
        missing_persons_module.sync_official_missing_persons(conn, actor='guard_test', snapshot=base_snapshot)
        conn.commit()

        before = conn.execute(
            "SELECT COUNT(*) FROM missing_persons WHERE status='missing'"
        ).fetchone()[0]
        self.assertEqual(before, 2)

        # Now simulate a Cloudflare-blocked fetch (snapshot returns empty).
        empty_snapshot = dict(base_snapshot)
        empty_snapshot['stats'] = {
            'total_active': 0,
            'children': 0,
            'indigenous': 0,
            'missing_less_than_year': 0,
            'missing_more_than_year': 0,
        }
        empty_snapshot['records'] = []
        result = missing_persons_module.sync_official_missing_persons(conn, actor='guard_test', snapshot=empty_snapshot)
        conn.commit()
        conn.close()

        self.assertEqual(result['resolved'], 0)
        self.assertTrue(result['snapshot_empty'])
        self.assertFalse(result['fetch_failed'])

        check_conn = sqlite3.connect(self.db_path)
        check_conn.row_factory = sqlite3.Row
        still_missing = check_conn.execute(
            "SELECT COUNT(*) AS c FROM missing_persons WHERE status='missing'"
        ).fetchone()['c']
        check_conn.close()
        self.assertEqual(still_missing, 2)

    def test_create_and_update_missing_person_keep_is_active_in_sync(self) -> None:
        conn = app_module.get_db()
        created = missing_persons_module.create_missing_person(
            conn,
            {
                'full_name': 'Status Sync Person',
                'age': 33,
                'city': 'Bozeman',
                'county': 'Gallatin',
                'last_seen_at': '2026-04-20 00:00',
                'last_seen_location': 'Bozeman',
                'summary': 'A concise public summary for the status sync test.',
                'physical_description': 'Brown hair, blue eyes.',
                'contact_info': 'Call Gallatin County Sheriff.',
                'source_name': 'Montana Blotter',
                'source_url': 'https://example.com/status-sync',
                'photo_url': '',
                'case_number': 'MP-200',
                'missing_from': 'Bozeman / Gallatin',
                'height_weight': '5-08 / 120',
                'status': 'located',
                'resolution_summary': 'Initially resolved for the test.',
            },
            actor='test_sync',
        )
        conn.commit()

        row = conn.execute(
            'SELECT status, is_active, case_number, missing_from, height_weight, last_seen_at FROM missing_persons WHERE id = ?',
            (created['id'],),
        ).fetchone()
        self.assertEqual(row['status'], 'located')
        self.assertEqual(row['is_active'], 0)
        self.assertEqual(row['case_number'], 'MP-200')
        self.assertEqual(row['missing_from'], 'Bozeman / Gallatin')
        self.assertEqual(row['height_weight'], '5-08 / 120')
        self.assertEqual(row['last_seen_at'], '2026-04-20 00:00:00')

        missing_persons_module.update_missing_person_status(
            conn,
            created['id'],
            status='missing',
            actor='test_sync',
        )
        conn.commit()
        row = conn.execute(
            'SELECT status, is_active, case_number, missing_from, height_weight, resolution_summary FROM missing_persons WHERE id = ?',
            (created['id'],),
        ).fetchone()
        self.assertEqual(row['status'], 'missing')
        self.assertEqual(row['is_active'], 1)
        self.assertEqual(row['case_number'], 'MP-200')
        self.assertEqual(row['missing_from'], 'Bozeman / Gallatin')
        self.assertEqual(row['height_weight'], '5-08 / 120')
        self.assertEqual(row['resolution_summary'], '')

        missing_persons_module.update_missing_person_status(
            conn,
            created['id'],
            status='located',
            actor='test_sync',
        )
        conn.commit()
        row = conn.execute(
            'SELECT status, is_active, case_number, missing_from, height_weight FROM missing_persons WHERE id = ?',
            (created['id'],),
        ).fetchone()
        self.assertEqual(row['status'], 'located')
        self.assertEqual(row['is_active'], 0)
        self.assertEqual(row['case_number'], 'MP-200')
        self.assertEqual(row['missing_from'], 'Bozeman / Gallatin')
        self.assertEqual(row['height_weight'], '5-08 / 120')

    def test_create_missing_person_clears_malformed_last_seen_at(self) -> None:
        conn = app_module.get_db()
        created = missing_persons_module.create_missing_person(
            conn,
            {
                'full_name': 'Malformed Date Person',
                'age': 29,
                'city': 'Helena',
                'county': 'Lewis and Clark',
                'last_seen_at': 'not-a-date',
                'last_seen_location': 'Helena',
                'summary': 'A concise public summary for malformed date handling.',
                'physical_description': 'Dark jacket.',
                'contact_info': 'Call local law enforcement.',
                'source_name': 'Montana Blotter',
                'source_url': 'https://example.com/malformed-date',
                'photo_url': '',
                'status': 'missing',
            },
            actor='test_sync',
        )
        conn.commit()

        row = conn.execute(
            'SELECT last_seen_at FROM missing_persons WHERE id = ?',
            (created['id'],),
        ).fetchone()
        self.assertEqual(row['last_seen_at'], '')

    def test_admin_missing_persons_page_renders(self) -> None:
        client = app_module.app.test_client()
        self._login_admin_session(client)

        response = client.get('/admin/operations/missing-persons')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Missing Persons', html)
        self.assertIn('Sync Official Database', html)

    def test_public_missing_persons_page_renders_database_lanes(self) -> None:
        self._seed_missing_persons()

        client = app_module.app.test_client()
        response = client.get('/missing-persons?status=all&sort=recently_resolved')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Newest Alerts', html)
        self.assertIn('Found / Resolved Updates', html)
        self.assertIn('Active Person', html)
        self.assertIn('Located Person', html)
        self.assertIn('Located / resolved', html)
        self.assertIn('Yellowstone', html)

    def test_public_missing_person_detail_renders_full_dossier(self) -> None:
        slug = self._seed_rich_missing_person()

        client = app_module.app.test_client()
        response = client.get(f'/missing-persons/{slug}')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Rich Detail Person', html)
        self.assertIn('Photo Gallery', html)
        self.assertIn('Record Dossier', html)
        self.assertIn('Official Source ID', html)
        self.assertIn('77777', html)
        self.assertIn('GALLATIN COUNTY SHERIFF', html)
        self.assertIn('Smith, Jane, Doe, J.', html)
        self.assertIn('Located safe after a community tip.', html)
        self.assertIn('Primary', html)


if __name__ == '__main__':
    unittest.main()
