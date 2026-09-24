"""Regression coverage for the 2026-09-23 server-error repair.

Root causes guarded here:
  1. url_for('payments.wanted_subscribe') BuildError after the endpoint moved to
     the bail_bond_ads blueprint (wanted pages 500'd on the signin-wall redirect).
  2. NameError for bail helpers app.py forgot to import from the blueprint.
  3. Missing services/ops/status.py broke /status and /api/status (and the
     footer badge on every page).
  4. Missing templates public_sources.html / careers.html broke /sources /careers.
"""
import unittest

import app as app_module
import config
import init_db


class TestServerErrorRepair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure the active DB (possibly a fresh/empty temp file under CI env)
        # has the full schema before the smoke matrix hits table-backed pages.
        init_db.init_database()
        init_db.migrate()

    def setUp(self):
        app_module.app.config['TESTING'] = True
        self.client = app_module.app.test_client()

    # -- endpoint contract ----------------------------------------------------
    def test_wanted_subscribe_endpoint_lives_on_bail_bond_ads(self):
        rule = app_module.app.url_map.bind('localhost')
        ep = rule.match('/wanted/subscribe')[0]
        self.assertEqual(ep, 'bail_bond_ads.wanted_subscribe')

    def test_app_imports_bail_helpers_used_at_runtime(self):
        for name in ('_bail_ad_checkout_ready', '_bail_ad_public_packages',
                     '_bail_lead_routing_targets'):
            self.assertTrue(callable(getattr(app_module, name, None)),
                            f'app.{name} missing (NameError source)')

    # -- module / templates exist --------------------------------------------
    def test_ops_status_module_contract(self):
        from services.ops import status
        self.assertTrue(callable(status.summarize))
        payload = status.summarize(local_ok=True)
        self.assertTrue(payload['ok'])
        self.assertIn(payload['source'], ('self', 'self+uptimerobot',
                                          'uptimerobot', 'none'))

    def test_repaired_templates_present(self):
        import jinja2
        loader = app_module.app.jinja_loader
        for tpl in ('public_sources.html', 'careers.html', 'public_status.html'):
            loader.get_source(app_module.app.jinja_env, tpl)  # raises TemplateNotFound

    # -- smoke matrix ---------------------------------------------------------
    def test_previously_500ing_pages_now_serve(self):
        cases = {
            '/wanted': (200, 301, 302),            # gate redirect or paywall ok
            '/warrants': (200, 301, 302),
            '/wanted/subscribe': (200, 301, 302),
            '/support': (200, 301, 302),
            '/status': (200, 301, 302),
            '/api/status': (200,),
            '/sources': (200, 301, 302),
            '/careers': (200, 301, 302),
        }
        for path, ok in cases.items():
            r = self.client.get(path, follow_redirects=False)
            self.assertIn(r.status_code, ok, f'{path} -> {r.status_code}')

    def test_api_status_json_shape(self):
        import json
        r = self.client.get('/api/status')
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.data)
        self.assertIn('ok', data)
        self.assertIn('source', data)


if __name__ == '__main__':
    unittest.main()
