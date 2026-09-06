"""Regression tests for failed-payment recovery.

Covers two production defects:
  * ``past_due`` was treated as terminal, revoking access and blanking
    ``stripe_subscription_id`` so a later successful retry could never restore it.
  * ``invoice.payment_failed`` was not handled at all, so a declined card
    produced no notice and no self-service way to fix it.
"""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import app as app_module
import config
import init_db
from services.monetization import dunning


SUB_ID = 'sub_test_dunning_1'
CUSTOMER_ID = 'cus_test_dunning_1'


class DunningTestBase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-dunning-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        init_db.init_database()
        init_db.migrate()

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscription_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                stripe_event_id TEXT,
                event_type TEXT,
                payload_json TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()
        conn.close()

        self.user_id = self._seed_subscriber()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _seed_subscriber(self, email: str = 'reader@example.com',
                         plan: str = 'warrant_access') -> int:
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            """
            INSERT INTO public_users (
                email, password_hash, display_name, is_active, is_subscribed,
                subscriber_plan, stripe_subscription_id, subscription_status
            ) VALUES (?, 'unused', 'Test Reader', 1, 1, ?, ?, 'active')
            """,
            (email, plan, SUB_ID),
        )
        conn.commit()
        user_id = cur.lastrowid
        conn.close()
        return user_id

    def _row(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT is_subscribed, subscriber_plan, stripe_subscription_id,
                   subscription_status
            FROM public_users WHERE id = ?
            """,
            (self.user_id,),
        ).fetchone()
        conn.close()
        return row

    def _apply(self, event: dict) -> None:
        conn = app_module.get_db()
        try:
            app_module._apply_subscription_stripe_event(conn, event)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _invoice_event(attempt_count: int = 1, amount_due: int = 399) -> dict:
        return {
            'id': f'evt_pf_{attempt_count}',
            'type': 'invoice.payment_failed',
            'data': {'object': {
                'id': 'in_test_1',
                'subscription': SUB_ID,
                'customer': CUSTOMER_ID,
                'attempt_count': attempt_count,
                'amount_due': amount_due,
                'currency': 'usd',
                'metadata': {'flow': 'warrant_access'},
            }},
        }

    @staticmethod
    def _subscription_event(status: str, event_type: str = 'customer.subscription.updated') -> dict:
        return {
            'id': f'evt_{status}',
            'type': event_type,
            'data': {'object': {
                'id': SUB_ID,
                'status': status,
                'customer': CUSTOMER_ID,
                'metadata': {'flow': 'warrant_access', 'plan': 'monthly'},
            }},
        }


class PastDueKeepsAccessTests(DunningTestBase):
    def test_invoice_payment_failed_marks_past_due_without_revoking(self) -> None:
        with mock.patch.object(dunning, '_smtp_send', return_value=True):
            self._apply(self._invoice_event())
        row = self._row()
        self.assertEqual(row['subscription_status'], 'past_due')
        self.assertEqual(row['is_subscribed'], 1)
        self.assertEqual(row['stripe_subscription_id'], SUB_ID)
        self.assertNotEqual(row['subscriber_plan'], 'free')

    def test_subscription_past_due_does_not_unlink_subscription_id(self) -> None:
        self._apply(self._subscription_event('past_due'))
        row = self._row()
        self.assertEqual(row['subscription_status'], 'past_due')
        self.assertEqual(row['is_subscribed'], 1)
        self.assertEqual(
            row['stripe_subscription_id'], SUB_ID,
            'Blanking the id here permanently orphaned subscribers whose card '
            'was later retried successfully.',
        )

    def test_unpaid_and_paused_are_also_non_terminal(self) -> None:
        for status in ('unpaid', 'paused'):
            self._apply(self._subscription_event(status))
            row = self._row()
            self.assertEqual(row['subscription_status'], status)
            self.assertEqual(row['stripe_subscription_id'], SUB_ID)
            self.assertEqual(row['is_subscribed'], 1)

    def test_invoice_paid_after_past_due_restores_active(self) -> None:
        self._apply(self._subscription_event('past_due'))
        self.assertEqual(self._row()['subscription_status'], 'past_due')

        self._apply({
            'id': 'evt_paid',
            'type': 'invoice.paid',
            'data': {'object': {'id': 'in_test_2', 'subscription': SUB_ID}},
        })
        row = self._row()
        self.assertEqual(row['subscription_status'], 'active')
        self.assertEqual(row['is_subscribed'], 1)
        self.assertEqual(row['stripe_subscription_id'], SUB_ID)

    def test_canceled_still_revokes_and_unlinks(self) -> None:
        with mock.patch.object(dunning, '_smtp_send', return_value=True):
            self._apply(self._subscription_event('canceled', 'customer.subscription.deleted'))
        row = self._row()
        self.assertEqual(row['is_subscribed'], 0)
        self.assertEqual(row['subscriber_plan'], 'free')
        self.assertEqual(row['stripe_subscription_id'], '')
        self.assertEqual(row['subscription_status'], 'canceled')

    def test_active_status_still_restores_access(self) -> None:
        self._apply(self._subscription_event('past_due'))
        self._apply(self._subscription_event('active'))
        row = self._row()
        self.assertEqual(row['subscription_status'], 'active')
        self.assertEqual(row['is_subscribed'], 1)


class DunningNotificationTests(DunningTestBase):
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def test_first_failed_attempt_emails_a_card_update_link(self) -> None:
        with mock.patch.object(dunning, '_smtp_send', return_value=True) as send:
            result = dunning.notify_payment_failed(
                self._conn(), SUB_ID, self._invoice_event()['data']['object'], 'evt_pf_1')
        self.assertTrue(result['sent'])
        self.assertEqual(result['skipped'], '')
        send.assert_called_once()
        _to, subject, body = send.call_args[0]
        self.assertIn('declined', subject)
        self.assertIn('/billing', body)

    def test_later_retry_attempts_do_not_email_again(self) -> None:
        with mock.patch.object(dunning, '_smtp_send', return_value=True) as send:
            result = dunning.notify_payment_failed(
                self._conn(), SUB_ID,
                {'id': 'in_x', 'subscription': SUB_ID, 'attempt_count': 4,
                 'amount_due': 399, 'currency': 'usd'}, 'evt_pf_4')
        self.assertFalse(result['sent'])
        self.assertEqual(result['skipped'], 'retry_attempt_4')
        send.assert_not_called()

    def test_unknown_subscription_is_skipped_not_fatal(self) -> None:
        with mock.patch.object(dunning, '_smtp_send', return_value=True) as send:
            result = dunning.notify_payment_failed(
                self._conn(), 'sub_does_not_exist',
                {'id': 'in_y', 'subscription': 'sub_does_not_exist', 'attempt_count': 1})
        self.assertFalse(result['sent'])
        self.assertEqual(result['skipped'], 'subscriber_not_found')
        send.assert_not_called()

    def test_subscriber_without_email_is_skipped(self) -> None:
        conn = self._conn()
        conn.execute("UPDATE public_users SET email = '' WHERE id = ?", (self.user_id,))
        conn.commit()
        with mock.patch.object(dunning, '_smtp_send', return_value=True) as send:
            result = dunning.notify_payment_failed(
                conn, SUB_ID,
                {'id': 'in_z', 'subscription': SUB_ID, 'attempt_count': 1})
        self.assertFalse(result['sent'])
        self.assertEqual(result['skipped'], 'no_email')
        send.assert_not_called()

    def test_smtp_failure_is_reported_not_raised(self) -> None:
        with mock.patch.object(dunning, '_smtp_send', return_value=False):
            result = dunning.notify_payment_failed(
                self._conn(), SUB_ID,
                {'id': 'in_w', 'subscription': SUB_ID, 'attempt_count': 1})
        self.assertFalse(result['sent'])
        self.assertEqual(result['skipped'], 'smtp_failed')

    def test_access_ended_email_offers_reactivation(self) -> None:
        with mock.patch.object(dunning, '_smtp_send', return_value=True) as send:
            result = dunning.notify_access_ended(self._conn(), SUB_ID, plan='pro')
        self.assertTrue(result['sent'])
        _to, subject, body = send.call_args[0]
        self.assertIn('Pro', subject)
        self.assertIn('/pricing', body)
        self.assertNotIn('retried your payment', body)

    def test_notification_is_recorded_for_audit(self) -> None:
        with mock.patch.object(dunning, '_smtp_send', return_value=True):
            dunning.notify_payment_failed(
                self._conn(), SUB_ID,
                {'id': 'in_v', 'subscription': SUB_ID, 'attempt_count': 1}, 'evt_audit')
        conn = self._conn()
        rows = conn.execute(
            "SELECT event_type, stripe_event_id FROM subscription_events WHERE user_id = ?",
            (self.user_id,),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['event_type'], 'dunning_payment_failed')
        self.assertEqual(rows[0]['stripe_event_id'], 'evt_audit')


class DunningHelperTests(unittest.TestCase):
    def test_email_content_escapes_subscriber_markup(self) -> None:
        body = dunning.payment_failed_html('<img src=x>', 'Pro', '$5', 'https://example.test/billing')
        self.assertNotIn('<img src=x>', body)
        self.assertIn('&lt;img src=x&gt;', body)
        ended = dunning.access_ended_html('<script>x</script>', 'Pro', 'https://example.test/pricing')
        self.assertNotIn('<script>', ended)

    def test_billing_url_is_absolute_and_points_at_the_portal(self) -> None:
        url = dunning.billing_url()
        self.assertTrue(url.startswith('http'))
        self.assertTrue(url.endswith('/billing'))
        self.assertNotIn('//billing', url)

    def test_plan_label_falls_back_gracefully(self) -> None:
        self.assertEqual(dunning.plan_label('pro'), 'Pro')
        self.assertEqual(dunning.plan_label('warrant_access'), 'Warrant Access')
        self.assertEqual(dunning.plan_label('weekly'), 'your subscription')
        self.assertEqual(dunning.plan_label(''), 'your subscription')

    def test_amount_str_formats_usd_and_handles_missing(self) -> None:
        self.assertEqual(dunning._amount_str({'amount_due': 399, 'currency': 'usd'}), '$3.99')
        self.assertEqual(dunning._amount_str({'amount_due': 1999}), '$19.99')
        self.assertIn('past-due', dunning._amount_str({}))


class BillingPortalRouteTests(DunningTestBase):
    def test_anonymous_visitor_is_sent_to_login(self) -> None:
        client = app_module.app.test_client()
        response = client.get('/billing')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers.get('Location', ''))

    def test_logged_in_user_without_subscription_is_redirected(self) -> None:
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess[app_module.PUBLIC_USER_SESSION_KEY] = self.user_id
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE public_users SET stripe_subscription_id = '' WHERE id = ?",
            (self.user_id,),
        )
        conn.commit()
        conn.close()
        response = client.get('/billing')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/pricing', response.headers.get('Location', ''))

    def test_logged_in_subscriber_opens_a_stripe_portal_session(self) -> None:
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess[app_module.PUBLIC_USER_SESSION_KEY] = self.user_id

        fake_sub = mock.MagicMock()
        fake_sub.get.return_value = CUSTOMER_ID
        fake_portal = {'url': 'https://billing.stripe.com/session/xyz'}

        with mock.patch.object(app_module.stripe, 'api_key', 'sk_test_x', create=True), \
             mock.patch('stripe.Subscription.retrieve', return_value=fake_sub), \
             mock.patch('stripe.billing_portal.Session.create', return_value=fake_portal) as create, \
             mock.patch.object(app_module, '_stripe_keys',
                               return_value={'secret_key': 'sk_test_x', 'webhook_secret': ''}):
            response = client.get('/billing')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], 'https://billing.stripe.com/session/xyz')
        self.assertEqual(create.call_args.kwargs['customer'], CUSTOMER_ID)
        self.assertTrue(create.call_args.kwargs['return_url'].endswith('/pricing'))

    def test_stripe_failure_flashes_instead_of_500(self) -> None:
        client = app_module.app.test_client()
        with client.session_transaction() as sess:
            sess[app_module.PUBLIC_USER_SESSION_KEY] = self.user_id
        with mock.patch('stripe.Subscription.retrieve', side_effect=RuntimeError('boom')), \
             mock.patch.object(app_module, '_stripe_keys',
                               return_value={'secret_key': 'sk_test_x', 'webhook_secret': ''}):
            response = client.get('/billing')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/pricing', response.headers['Location'])


if __name__ == '__main__':
    unittest.main()
