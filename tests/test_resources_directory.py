import os
import re
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


_RESOURCES_ACTIVE_RE = re.compile(
    r'<a\s+href="/resources"\s+class="[^"]*\bis-active\b[^"]*"[^>]*>\s*Resources\s*</a>',
    re.IGNORECASE,
)


class ResourcesDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-resources-directory-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH
        self.previous_testing = app_module.app.config.get('TESTING')

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        conn = sqlite3.connect(self.db_path)
        init_db.ensure_attorney_ad_schema(conn)
        init_db.ensure_treatment_center_schema(conn)
        conn.execute(
            '''
            INSERT INTO attorney_referrals (
                county, name, firm, phone, email, website, practice_areas,
                blurb, is_active, sort_order, sponsored, sponsor_tier,
                logo_path, photo_path, tagline
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 10, 0, NULL, NULL, NULL, NULL)
            ''',
            (
                'Yellowstone',
                'Test Defense Attorney',
                'Test Law Firm',
                '406-555-0101',
                'attorney@example.com',
                'https://attorney.example.com',
                'Criminal defense',
                'A verified test listing.',
            ),
        )
        conn.execute(
            '''
            INSERT INTO treatment_centers (
                county, name, organization, phone, email, website, services,
                intake_url, blurb, is_active, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 10)
            ''',
            (
                'Statewide',
                'Montana Statewide Test Line',
                'Test Resource Network',
                '988',
                'help@example.com',
                'https://resource.example.com',
                'Crisis support',
                'https://resource.example.com/intake',
                'Available statewide at all hours.',
            ),
        )
        conn.commit()
        conn.close()

        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        app_module.app.config['TESTING'] = self.previous_testing
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _get_html(self, path: str) -> tuple:
        response = self.client.get(path)
        return response, response.get_data(as_text=True)

    def assert_resources_nav_is_active(self, html: str) -> None:
        self.assertRegex(html, _RESOURCES_ACTIVE_RE)

    def test_attorneys_route_renders_200(self) -> None:
        response, html = self._get_html('/attorneys')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Montana defense attorneys by county.', html)
        self.assertIn('Test Defense Attorney', html)

    def test_attorneys_route_no_placeholders(self) -> None:
        response, html = self._get_html('/attorneys')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Advertise Your Law Firm Here', html)

    def test_attorneys_route_active_nav_highlight(self) -> None:
        response, html = self._get_html('/attorneys')

        self.assertEqual(response.status_code, 200)
        self.assert_resources_nav_is_active(html)

    def test_treatment_centers_route_renders_200(self) -> None:
        response, html = self._get_html('/treatment-centers')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Montana addiction and recovery resources.', html)
        self.assertIn('Montana Statewide Test Line', html)

    def test_treatment_centers_route_shows_statewide_separator(self) -> None:
        response, html = self._get_html('/treatment-centers')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Statewide &mdash; call anytime', html)
        self.assertIn('24/7 crisis &amp; treatment lines', html)

    def test_treatment_centers_route_active_nav_highlight(self) -> None:
        response, html = self._get_html('/treatment-centers')

        self.assertEqual(response.status_code, 200)
        self.assert_resources_nav_is_active(html)

    def test_resources_index_route_renders_200(self) -> None:
        response, html = self._get_html('/resources')

        self.assertEqual(response.status_code, 200)
        self.assertIn('Help, plainly listed.', html)
        self.assertIn('href="/attorneys"', html)
        self.assertIn('href="/treatment-centers"', html)

    def test_resources_index_active_nav(self) -> None:
        response, html = self._get_html('/resources')

        self.assertEqual(response.status_code, 200)
        self.assert_resources_nav_is_active(html)


if __name__ == '__main__':
    unittest.main()
