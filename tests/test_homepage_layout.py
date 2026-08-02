import sqlite3
import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import config
import init_db
from services.ingestion.warrants.models import ensure_warrant_schema


class HomepageLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-homepage-', suffix='.db')
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
        from init_db import ensure_jail_booking_schema as _ejbs
        with sqlite3.connect(self.db_path, timeout=30) as jb_conn:
            _ejbs(jb_conn)
        from init_db import ensure_warrant_schema as _ews
        with sqlite3.connect(self.db_path, timeout=30) as w_conn:
            _ews(w_conn)

        # Seed a warrant record so we can test homepage gating.
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ensure_warrant_schema(conn)
        run_ts = '2026-01-01 00:00:00'
        conn.execute(
            '''INSERT INTO warrants (source_record_id, county, city, person_name, dob,
               warrant_type, charges_text, issued_by, issue_date, bond_amount, bond_type,
               status, source_url, resolved_at, scraped_at, first_seen_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('test-warrant:hidden-person', 'Hidden', '', 'Hidden Person', '', 'active',
             'TEST CHARGE', 'Hidden Sheriff', '', '', '', 'active', 'https://example.gov/warrant/1',
             '', run_ts, run_ts, run_ts),
        )
        conn.commit()
        conn.close()
        conn = sqlite3.connect(self.db_path)
        for statement in [
            "ALTER TABLE bail_ad_orders ADD COLUMN simulator_logo_path TEXT",
            "ALTER TABLE bail_ad_orders ADD COLUMN simulator_target_url TEXT",
        ]:
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_homepage_renders_newsroom_layout(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Montana Blotter', html)
        self.assertIn('mb-crime-ticker', html)
        self.assertIn('public-redesign.css', html)
        self.assertIn('/counties', html)
        self.assertIn('/missing-persons', html)
        self.assertIn('Missing Persons', html)
        self.assertIn('/support', html)
        self.assertNotIn('Command Center', html)
        self.assertNotIn('mb-rss-ticker', html)
        self.assertTrue('>Get Alerts<' in html or '>Subscribe<' in html)

    def test_homepage_hides_warrant_spotlight_without_access(self) -> None:
        client = app_module.app.test_client()
        with patch.object(app_module, 'user_has_warrant_access', return_value=False):
            response = client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Hidden Person', html)
        self.assertNotIn('wanted-spotlight__grid', html)

    def test_homepage_shows_warrant_spotlight_with_access(self) -> None:
        client = app_module.app.test_client()
        with patch.object(app_module, 'user_has_warrant_access', return_value=True):
            response = client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Hidden Person', html)
        self.assertIn('wanted-spotlight__grid', html)

    def test_homepage_crime_wire_renders_public_records_and_own_rss_link(self) -> None:
        conn = sqlite3.connect(self.db_path)
        blotter_id = conn.execute(
            """
            INSERT INTO blotters (filename, county, status, file_path, source_type, upload_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                'crime-wire-test.pdf',
                'Missoula',
                'processed',
                '/tmp/crime-wire-test.pdf',
                'local_pdf',
                '2026-07-29 21:56:00',
            ),
        ).lastrowid
        post_id = conn.execute(
            """
            INSERT INTO posts (
                blotter_id, title, summary, city, county, agency_type,
                agency_name, incident_date, incident_type, created_at,
                audit_status, case_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                'Missoula daily activity report',
                'Recent public safety calls.',
                'Missoula',
                'Missoula',
                'police',
                'Missoula Police Department',
                '2026-07-29',
                'Daily Activity',
                '2026-07-29 22:00:00',
                'clean',
                'active',
            ),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO records (
                blotter_id, cfs_number, date, time, incident, incident_type,
                location, details, county, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                'CW-001',
                '2026-07-29',
                '9:56 PM',
                'Structure Fire',
                'Structure Fire',
                '5XX W BECKWITH AVE E',
                'Public incident record.',
                'Missoula',
                '2026-07-29 21:56:00',
            ),
        )
        conn.commit()
        conn.close()

        response = app_module.app.test_client().get('/')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('<section class="mb-crime-ticker"', html)
        self.assertIn('Crime Wire', html)
        self.assertIn('9:56 PM', html)
        self.assertIn('Structure Fire at 5XX W BECKWITH AVE E', html)
        self.assertIn('Missoula · Missoula Police Department', html)
        self.assertIn(f'href="/post/{post_id}"', html)
        self.assertIn('href="/feed.xml"', html)
        self.assertNotIn('rss.app', html)
        self.assertNotIn('<iframe', html)

        ticker_html = html.split('<section class="mb-crime-ticker"', 1)[1].split('</section>', 1)[0]
        self.assertEqual(ticker_html.count('Structure Fire at 5XX W BECKWITH AVE E'), 2)
        self.assertEqual(ticker_html.count('aria-hidden="true"'), 2)
        self.assertIn('tabindex="-1"', ticker_html)

    def test_homepage_crime_wire_dedupes_per_post(self) -> None:
        """Regression: a single blotter covering many records must not
        cause every ticker entry to link to the same post.

        Reproduces the 2026-07-30 production bug where the Missoula
        public-report post (id 451) covered ~97 records and the ticker
        rendered 6 items all pointing at /post/451.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        big_blotter = conn.execute(
            "INSERT INTO blotters (filename, county, status) VALUES (?, ?, ?)",
            ('big-blotter.pdf', 'Missoula', 'processed'),
        ).lastrowid
        big_post = conn.execute(
            """
            INSERT INTO posts (
                blotter_id, title, summary, city, county, agency_name,
                incident_date, created_at, audit_status, case_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                big_blotter,
                'Missoula big batch',
                'Many records under one post.',
                'Missoula',
                'Missoula',
                'Missoula Police Department',
                '2026-07-29',
                '2026-07-29 22:00:00',
                'clean',
                'active',
            ),
        ).lastrowid
        base_ts = '2026-07-29 12:00:00'
        for i in range(6):
            conn.execute(
                """
                INSERT INTO records (
                    blotter_id, date, time, incident, incident_type, location,
                    details, county, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    big_blotter,
                    '2026-07-29',
                    '9:5%d PM' % i,
                    'Mass Entry %d' % i,
                    'Mass Entry %d' % i,
                    '5XX W BECKWITH AVE %d' % i,
                    'Routine batch record %d.' % i,
                    'Missoula',
                    base_ts,
                ),
            )
        small_blotter = conn.execute(
            "INSERT INTO blotters (filename, county, status) VALUES (?, ?, ?)",
            ('small-blotter.pdf', 'Yellowstone', 'processed'),
        ).lastrowid
        small_post = conn.execute(
            """
            INSERT INTO posts (
                blotter_id, title, summary, city, county, agency_name,
                incident_date, created_at, audit_status, case_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                small_blotter,
                'Yellowstone daily report',
                'Single record.',
                'Billings',
                'Yellowstone',
                'Billings Police Department',
                '2026-07-29',
                '2026-07-29 22:30:00',
                'clean',
                'active',
            ),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO records (
                blotter_id, date, time, incident, incident_type, location,
                details, county, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                small_blotter,
                '2026-07-29',
                '11:00 AM',
                'Traffic Stop',
                'Traffic Stop',
                'GRAND AVE',
                'Routine patrol stop.',
                'Yellowstone',
                '2026-07-29 15:00:00',
            ),
        )
        conn.commit()
        conn.close()

        response = app_module.app.test_client().get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)

        ticker_html = (
            html.split('<section class="mb-crime-ticker"', 1)[1]
                .split('</section>', 1)[0]
        )

        # Count "Mass Entry" headlines only in the FIRST (visible) group
        # of the duplicated ticker track. The second group is duplicated
        # markup for the seamless scroll and `aria-hidden="true"`.
        import re
        first_group = re.split(
            r'<div class="mb-crime-ticker__group"\s+aria-hidden="true">',
            ticker_html, maxsplit=1,
        )[0]
        missoula_match = re.findall(r'Mass Entry \d', first_group)
        self.assertLessEqual(
            len(missoula_match), 1, ticker_html,
        )

        self.assertIn('11:00 AM', first_group)
        self.assertIn('Traffic Stop at GRAND AVE', first_group)
        self.assertIn('Yellowstone', first_group)

        post_hrefs = re.findall(r'href="/post/(\d+)"', first_group)
        unique_posts = set(post_hrefs)
        self.assertGreaterEqual(len(unique_posts), 2, post_hrefs)

        self.assertEqual(post_hrefs.count(str(big_post)), 1)
        self.assertEqual(post_hrefs.count(str(small_post)), 1)

    def test_crime_wire_json_returns_same_public_record_shape(self) -> None:
        conn = sqlite3.connect(self.db_path)
        blotter_id = conn.execute(
            "INSERT INTO blotters (filename, county, status) VALUES (?, ?, ?)",
            ('crime-wire-json.pdf', 'Missoula', 'processed'),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO posts (
                blotter_id, title, summary, city, county, agency_name,
                incident_date, created_at, audit_status, case_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                'Missoula daily activity report',
                'Recent public safety calls.',
                'Missoula',
                'Missoula',
                'Missoula Police Department',
                '2026-07-29',
                '2026-07-29 22:00:00',
                'clean',
                'active',
            ),
        )
        record_id = conn.execute(
            """
            INSERT INTO records (
                blotter_id, date, time, incident, incident_type, location,
                details, county, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blotter_id,
                '2026-07-29',
                '9:20 PM',
                'Suspicious Activity',
                'Suspicious Activity',
                '33XX BROOKS ST',
                'Public incident record.',
                'Missoula',
                '2026-07-29 21:20:00',
            ),
        ).lastrowid
        conn.commit()
        conn.close()

        response = app_module.app.test_client().get('/crime-wire.json?limit=4')
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/json')
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['items'][0]['record_id'], record_id)
        self.assertEqual(payload['items'][0]['incident_label'], 'Suspicious Activity')
        self.assertEqual(payload['items'][0]['location'], '33XX BROOKS ST')
        self.assertEqual(payload['items'][0]['agency_name'], 'Missoula Police Department')
        self.assertEqual(response.headers['Cache-Control'], 'public, max-age=60')

    def test_legacy_posts_route_redirects_into_homepage_filters(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/posts?county=Yellowstone&city=Billings&q=theft')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers['Location'],
            '/?county=Yellowstone&city=Billings&q=theft',
        )

    def test_public_templates_do_not_link_to_legacy_posts_route(self) -> None:
        template_root = os.path.join(os.path.dirname(app_module.__file__), 'templates')
        checked_templates = (
            'cities.html',
            'city_page.html',
            'counties.html',
            'county_page.html',
            'pattern_page.html',
            'post_detail.html',
            'posts.html',
        )

        for template_name in checked_templates:
            with self.subTest(template=template_name):
                template_path = os.path.join(template_root, template_name)
                with open(template_path, 'r', encoding='utf-8') as handle:
                    html = handle.read()
                self.assertNotIn('/posts?', html)
                self.assertNotIn('action="/posts"', html)


    def test_homepage_jail_bookings_surface_one_fresh_row_per_county(self) -> None:
        """The homepage jail bookings table should pick one fresh row per
        county rather than letting a single county's currently-held roster
        (e.g. Hill County / Havre DOCX) flood the top of the list. Rows
        with a real ``booking_at`` should be preferred over snapshot rows.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Hill County: a roster refresh timestamp but no booking_at on any row.
        hill_ts = '2026-07-29 20:43:31'
        for i in range(6):
            conn.execute(
                '''INSERT INTO jail_bookings
                   (county_slug, county_name, facility_name, person_name,
                    booking_at, charges_summary, is_current, first_seen_at,
                    last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)''',
                ('hill', 'Hill', 'Hill County Jail', f'Hill Person {i}',
                 None, '', hill_ts, hill_ts),
            )
        # Yellowstone, Missoula, Silver Bow: each gets one fresh booking today.
        today = '2026-07-29 12:00:00'
        for slug, name, person in [
            ('yellowstone', 'Yellowstone', 'Yellowstone Fresh'),
            ('missoula',    'Missoula',    'Missoula Fresh'),
            ('silver_bow',  'Silver Bow',  'Silver Bow Fresh'),
        ]:
            conn.execute(
                '''INSERT INTO jail_bookings
                   (county_slug, county_name, facility_name, person_name,
                    booking_at, charges_summary, is_current, first_seen_at,
                    last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)''',
                (slug, name, f'{name} Detention', person,
                 today, 'Fresh booking', today, today),
            )
        conn.commit()
        conn.close()

        client = app_module.app.test_client()
        response = client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)

        # Fresh-booking counties must appear ahead of Hill snapshot rows.
        # The query surfaces one row per county then fills remaining slots
        # with the next-freshest counties, so all three fresh bookings are
        # guaranteed a top-5 spot even when a Hill roster refresh exists.
        names = [r[0] for r in self._homepage_jail_booking_rows()]
        self.assertLess(names.index('Yellowstone Fresh'), names.index('Hill Person 0'))
        self.assertLess(names.index('Missoula Fresh'),    names.index('Hill Person 0'))
        self.assertLess(names.index('Silver Bow Fresh'),  names.index('Hill Person 0'))
        # All six Hill snapshot rows must never appear; the homepage should
        # only show the top-1 row per county.
        self.assertNotIn('Hill Person 1', html)
        self.assertNotIn('Hill Person 2', html)
        self.assertNotIn('Hill Person 3', html)
        self.assertNotIn('Hill Person 4', html)
        self.assertNotIn('Hill Person 5', html)

    def _homepage_jail_booking_rows(self) -> list:
        """Parse the rendered 'Latest jail bookings' table rows off the homepage."""
        import re
        client = app_module.app.test_client()
        response = client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        match = re.search(
            r'Latest jail bookings.*?</table>', html, re.DOTALL,
        )
        self.assertIsNotNone(match, 'homepage jail bookings table not found')
        block = match.group(0)
        rows = re.findall(
            r'<tr class="mb-data-table__row".*?</tr>', block, re.DOTALL,
        )
        names = []
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if not cells:
                continue
            cleaned = re.sub(r'<[^>]+>', '', cells[0]).strip()
            names.append(cleaned)
        return names


if __name__ == '__main__':
    unittest.main()
