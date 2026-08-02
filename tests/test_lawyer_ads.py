import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import config
import init_db
import blueprints.payments as payments_module
from blueprints.lawyer_ads import (
    _county_matches,
    _deliver_lawyer_lead,
    apply_stripe_lawyer_ad_event,
)


class LawyerAdSchemaTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-ads-', suffix='.db')
        os.close(fd)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_lawyer_schema_has_lead_delivery_table(self):
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lawyer_lead_deliveries'"
        ).fetchone()
        self.assertIsNotNone(row)


class LawyerAdMatchingTests(unittest.TestCase):
    def test_county_matching_ignores_case_and_spacing(self):
        self.assertTrue(_county_matches('Gallatin, Yellowstone', 'yellowstone'))
        self.assertTrue(_county_matches('Gallatin,  Lewis and Clark', 'Lewis and Clark'))
        self.assertFalse(_county_matches('Gallatin, Yellowstone', 'Flathead'))

    def test_slug_county_handles_ampersand_variants(self):
        from blueprints.lawyer_ads import _slug_county
        self.assertEqual(_slug_county('Lewis & Clark'), 'lewis-and-clark')
        self.assertEqual(_slug_county('Lewis and Clark'), 'lewis-and-clark')
        self.assertEqual(_slug_county('lewis &  clark'), 'lewis-and-clark')
        self.assertEqual(_slug_county('Lewis and  Clark'), 'lewis-and-clark')


class LawyerAdRouteTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-route-', suffix='.db')
        os.close(fd)
        self.previous_config_db = config.DB_PATH
        self.previous_init_db = init_db.DB_PATH
        self.previous_app_db = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(conn)
        for firm, counties in (
            ('Gallatin Defense', 'Gallatin'),
            ('Flathead Defense', 'Flathead'),
        ):
            cur = conn.execute(
                '''INSERT INTO lawyer_ad_orders
                   (firm_name, email, counties_served, package_id, status, paid_at)
                   VALUES (?, ?, ?, 'bronze', 'active', datetime('now'))''',
                (firm, f'{firm.lower().replace(" ", "_")}@example.com', counties),
            )
            conn.execute(
                '''INSERT INTO lawyer_ad_listings
                   (order_id, firm_name, counties_served, is_active)
                   VALUES (?, ?, ?, 1)''',
                (cur.lastrowid, firm, counties),
            )
        conn.commit()
        conn.close()
        self.client = app_module.app.test_client()

    def tearDown(self):
        config.DB_PATH = self.previous_config_db
        init_db.DB_PATH = self.previous_init_db
        app_module.config.DB_PATH = self.previous_app_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_county_page_only_shows_listings_serving_that_county(self):
        response = self.client.get('/lawyers/gallatin')
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Gallatin Defense', body)
        self.assertNotIn('Flathead Defense', body)

    def test_county_page_intake_source_is_attributed(self):
        # Hidden source on the county-scoped form should be tagged with the slug,
        # so admins can see which county URL produced each lead.
        body = self.client.get('/lawyers/gallatin').get_data(as_text=True)
        self.assertIn(
            'name="source" value="lawyers_directory:gallatin"',
            body,
        )
        body_root = self.client.get('/lawyers').get_data(as_text=True)
        self.assertIn('name="source" value="lawyers_directory"', body_root)

    def test_directory_page_renders_dynamic_county_dropdown(self):
        response = self.client.get('/lawyers')
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('<option value="Lewis and Clark"', body)
        # Live coverage count is rendered next to counties with active listings.
        self.assertIn('Gallatin (1)', body)
        # Every Montana county is reachable from the dropdown, not just the 13 hardcoded ones.
        self.assertIn('<option value="Ravalli"', body)
        self.assertIn('<option value="Toole"', body)

    def test_impression_event_increments_listing_metric(self):
        conn = sqlite3.connect(self.db_path)
        order_id = conn.execute(
            "SELECT id FROM lawyer_ad_orders WHERE firm_name = 'Gallatin Defense'"
        ).fetchone()[0]
        conn.close()
        response = self.client.post(
            '/api/lawyer-ads/event',
            json={'event_type': 'impression', 'order_id': order_id, 'county': 'Gallatin'},
        )
        self.assertEqual(response.status_code, 204)
        conn = sqlite3.connect(self.db_path)
        impressions = conn.execute(
            'SELECT impressions FROM lawyer_ad_listings WHERE order_id = ?',
            (order_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(impressions, 1)

    def test_impression_event_ignored_for_unknown_or_inactive_order(self):
        # Unknown order — no metric, no event row.
        response = self.client.post(
            '/api/lawyer-ads/event',
            json={'event_type': 'impression', 'order_id': 9999, 'county': 'Gallatin'},
        )
        self.assertEqual(response.status_code, 204)
        conn = sqlite3.connect(self.db_path)
        event_rows = conn.execute(
            'SELECT COUNT(*) FROM lawyer_consumer_lead_events'
        ).fetchone()[0]
        conn.close()
        self.assertEqual(event_rows, 0)

        # Cancelled order — also no metric.
        conn = sqlite3.connect(self.db_path)
        cancelled_order_id = conn.execute(
            "SELECT id FROM lawyer_ad_orders WHERE firm_name = 'Flathead Defense'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE lawyer_ad_orders SET status = 'cancelled' WHERE id = ?",
            (cancelled_order_id,),
        )
        conn.commit()
        conn.close()
        response = self.client.post(
            '/api/lawyer-ads/event',
            json={'event_type': 'impression', 'order_id': cancelled_order_id, 'county': 'Flathead'},
        )
        self.assertEqual(response.status_code, 204)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            'SELECT impressions FROM lawyer_ad_listings WHERE order_id = ?',
            (cancelled_order_id,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_control_panel_updates_allowed_listing_fields(self):
        import secrets as _secrets
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE lawyer_ad_orders SET onboarding_token = 'token-123' WHERE firm_name = 'Gallatin Defense'")
        conn.commit()
        conn.close()
        # Prime a session CSRF token (same helper used by the CSRF test).
        with self.client.session_transaction() as sess:
            sess['_csrf_token'] = _secrets.token_urlsafe(32)
            csrf = sess['_csrf_token']
        response = self.client.post(
            '/lawyer-control-panel/token-123/update',
            data={
                'form_token': 'token-123',
                'csrf_token': csrf,
                'phone': '406-555-0123',
                'website': 'gallatindefense.example.com',
                'description': 'Local criminal defense representation.',
                'cta_text': 'Request a consultation',
                'target_url': 'gallatindefense.example.com/contact',
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            '''SELECT o.phone, o.website, l.description, l.target_url
               FROM lawyer_ad_orders o JOIN lawyer_ad_listings l ON l.order_id = o.id
               WHERE o.onboarding_token = 'token-123' '''
        ).fetchone()
        conn.close()
        self.assertEqual(
            row,
            (
                '406-555-0123',
                'https://gallatindefense.example.com',
                'Local criminal defense representation.',
                'https://gallatindefense.example.com/contact',
            ),
        )

    def test_intake_requires_explicit_lead_sharing_consent(self):
        response = self.client.post(
            '/lawyers/intake',
            data={
                'full_name': 'Test Consumer',
                'phone': '406-555-0100',
                'county': 'Gallatin',
                'return_path': '/lawyers',
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.db_path)
        count = conn.execute('SELECT COUNT(*) FROM lawyer_consumer_leads').fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


class LawyerLeadDeliveryTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-delivery-', suffix='.db')
        os.close(fd)
        self.previous_config_db = config.DB_PATH
        self.previous_app_db = app_module.config.DB_PATH
        config.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(conn)
        cur = conn.execute(
            '''INSERT INTO lawyer_ad_orders
               (firm_name, email, counties_served, package_id, status)
               VALUES ('Test Firm', 'lead@example.com', 'Gallatin', 'gold', 'active')'''
        )
        self.order_id = cur.lastrowid
        cur = conn.execute(
            '''INSERT INTO lawyer_consumer_leads
               (full_name, phone, county, consent_at, consent_text_version)
               VALUES ('Test Consumer', '406-555-0100', 'Gallatin', datetime('now'), 'lawyer-lead-v1')'''
        )
        self.lead_id = cur.lastrowid
        self.order = conn.execute(
            'SELECT id, firm_name, email, phone, package_id FROM lawyer_ad_orders WHERE id = ?',
            (self.order_id,),
        ).fetchone()
        conn.commit()
        conn.close()

    def tearDown(self):
        config.DB_PATH = self.previous_config_db
        app_module.config.DB_PATH = self.previous_app_db
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    @patch('blueprints.lawyer_ads._send_lawyer_lead_email', return_value=(True, ''))
    def test_delivery_records_success(self, _send_email):
        _deliver_lawyer_lead(
            self.lead_id,
            [self.order],
            {'full_name': 'Test Consumer', 'phone': '406-555-0100', 'county': 'Gallatin'},
        )
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            '''SELECT status, destination FROM lawyer_lead_deliveries
               WHERE lead_id = ? AND order_id = ?''',
            (self.lead_id, self.order_id),
        ).fetchone()
        conn.close()
        self.assertEqual(row, ('sent', 'lead@example.com'))


class LawyerAdWebhookTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-lawyer-webhook-', suffix='.db')
        os.close(fd)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_db.ensure_lawyer_ad_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_subscription_deleted_deactivates_listing(self):
        event = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'id': 'cs_lawyer_1',
                'customer': 'cus_lawyer_1',
                'subscription': 'sub_lawyer_1',
                'metadata': {
                    'flow': 'lawyer_ad',
                    'firm_name': 'Test Law',
                    'email': 'test@example.com',
                    'counties_served': 'Gallatin',
                    'package_id': 'bronze',
                    'billing_cycle': 'monthly',
                },
            }},
        }
        apply_stripe_lawyer_ad_event(self.conn, event)
        deleted = {
            'type': 'customer.subscription.deleted',
            'data': {'object': {
                'id': 'sub_lawyer_1',
                'metadata': {'flow': 'lawyer_ad'},
            }},
        }
        apply_stripe_lawyer_ad_event(self.conn, deleted)
        row = self.conn.execute(
            '''SELECT o.status, l.is_active
               FROM lawyer_ad_orders o JOIN lawyer_ad_listings l ON l.order_id = o.id
               WHERE o.provider_subscription_id = ?''',
            ('sub_lawyer_1',),
        ).fetchone()
        self.assertEqual(dict(row), {'status': 'cancelled', 'is_active': 0})


class LawyerAdWebhookWiringTests(unittest.TestCase):
    """Regression: the live Stripe webhook dispatcher in blueprints.payments
    must route events with metadata.flow == 'lawyer_ad' into
    blueprints.lawyer_ads.apply_stripe_lawyer_ad_event. Until 2026-07-30 the
    function existed and was unit-tested, but the dispatcher never called it,
    so no Stripe checkout ever activated a paid listing.
    """

    def test_dispatcher_invokes_lawyer_ad_handler(self):
        import blueprints.lawyer_ads as la
        # Sanity: the symbol exists where the dispatcher imports it from.
        self.assertTrue(callable(getattr(la, 'apply_stripe_lawyer_ad_event', None)))

        # The webhook handler imports apply_stripe_lawyer_ad_event inside the
        # try block, so we replace the underlying function with a wrapper
        # that records the call while still being importable by name.
        from blueprints import lawyer_ads as la_mod
        original_handler = la_mod.apply_stripe_lawyer_ad_event
        called = {}

        def spy_handler(conn, event):
            called['lawyer'] = (conn, event)
            return None

        la_mod.apply_stripe_lawyer_ad_event = spy_handler
        try:
            with patch.object(
                app_module, '_apply_stripe_bail_ad_event', lambda conn, event: None
            ), patch(
                'blueprints.recovery_ads.apply_stripe_recovery_ad_event',
                lambda conn, event: None,
            ), patch.object(
                app_module, '_apply_stripe_event', lambda *a, **kw: None
            ):
                from flask import Flask
                app = Flask(__name__)
                with app.test_request_context(
                    '/webhooks/stripe', method='POST', data=b'{}'
                ):
                    with patch.object(
                        payments_module, '_app', lambda: app_module
                    ), patch.object(
                        app_module, '_stripe_ready_for_webhooks', lambda: True
                    ), patch.object(
                        app_module, '_client_ip', lambda: '127.0.0.1'
                    ), patch.object(
                        app_module, '_stripe_keys',
                        lambda: {'secret_key': 'sk_test_x', 'webhook_secret': 'whsec_x'},
                    ), patch(
                        'stripe.Webhook.construct_event',
                        lambda payload, sig, secret: {
                            'id': 'evt_lawyer_dispatch',
                            'type': 'checkout.session.completed',
                            'data': {'object': {
                                'id': 'cs_lawyer_dispatch',
                                'metadata': {'flow': 'lawyer_ad'},
                            }},
                        },
                    ), patch(
                        'blueprints.payments.get_db', lambda: _StubConn()
                    ):
                        resp = payments_module.stripe_webhook()
            self.assertEqual(resp[1], 200)
            self.assertIn(
                'lawyer', called,
                'Stripe webhook did not invoke apply_stripe_lawyer_ad_event — '
                'lawyer_ad checkouts would never activate listings.',
            )
        finally:
            la_mod.apply_stripe_lawyer_ad_event = original_handler


class _StubConn:
    """Just enough to satisfy the dispatcher's INSERT ... ON CONFLICT path
    without touching the real database. The lawyer handler is patched out
    so no queries against lawyer_ad_orders ever fire."""

    def __init__(self):
        self._executes = 0

    def execute(self, *a, **kw):
        self._executes += 1
        # Let the first INSERT (to payment_webhook_events) succeed so the
        # dispatcher body runs, then let everything else pass too. The
        # final UPDATE on payment_webhook_events is harmless.
        return self

    def commit(self):
        return None

    def close(self):
        return None


if __name__ == '__main__':
    unittest.main()
