import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import app as app_module
import config
import init_db
from services.monetization.black_friday import (
    CAMPAIGN_SLUG,
    campaign_is_live,
    is_new_customer,
)


class BlackFridayCampaignTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='mb-black-friday-', suffix='.db')
        os.close(fd)
        self.previous = {
            'db_path': config.DB_PATH,
            'init_db_path': init_db.DB_PATH,
            'promotion_code': getattr(config, 'BLACK_FRIDAY_PROMOTION_CODE_ID', ''),
            'start_at': getattr(config, 'BLACK_FRIDAY_START_AT', ''),
            'end_at': getattr(config, 'BLACK_FRIDAY_END_AT', ''),
            'stripe_secret': getattr(config, 'STRIPE_SECRET_KEY', ''),
            'stripe_publishable': getattr(config, 'STRIPE_PUBLISHABLE_KEY', ''),
        }
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        config.BLACK_FRIDAY_PROMOTION_CODE_ID = 'promo_test_black_friday'
        config.BLACK_FRIDAY_START_AT = '2026-11-27T07:00:00+00:00'
        config.BLACK_FRIDAY_END_AT = '2026-12-01T07:00:00+00:00'
        config.STRIPE_SECRET_KEY = 'sk_test_black_friday'
        config.STRIPE_PUBLISHABLE_KEY = 'pk_test_black_friday'
        app_module.app.config['TESTING'] = True
        self.now_patch = patch(
            'services.monetization.black_friday._utcnow',
            return_value=datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc),
        )
        self.now_patch.start()
        init_db.init_database()
        init_db.migrate()
        self.client = app_module.app.test_client()

        conn = app_module.get_db()
        for user_id, email in (
            (11, 'new@example.com'),
            (12, 'current@example.com'),
            (13, 'former@example.com'),
        ):
            conn.execute(
                '''
                INSERT INTO public_users (id, email, display_name, password_hash, is_active)
                VALUES (?, ?, ?, 'hash', 1)
                ''',
                (user_id, email, f'User {user_id}'),
            )
        conn.execute(
            '''
            UPDATE public_users
            SET subscriber_plan = 'pro', stripe_subscription_id = 'sub_current',
                subscription_status = 'active', subscription_activated_at = datetime('now')
            WHERE id = 12
            '''
        )
        conn.execute(
            '''
            UPDATE public_users
            SET subscription_status = 'canceled', subscription_canceled_at = datetime('now')
            WHERE id = 13
            '''
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.now_patch.stop()
        config.DB_PATH = self.previous['db_path']
        init_db.DB_PATH = self.previous['init_db_path']
        app_module.config.DB_PATH = config.DB_PATH
        config.BLACK_FRIDAY_PROMOTION_CODE_ID = self.previous['promotion_code']
        config.BLACK_FRIDAY_START_AT = self.previous['start_at']
        config.BLACK_FRIDAY_END_AT = self.previous['end_at']
        config.STRIPE_SECRET_KEY = self.previous['stripe_secret']
        config.STRIPE_PUBLISHABLE_KEY = self.previous['stripe_publishable']
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_campaign_window_is_inclusive_of_start_and_exclusive_of_end(self):
        self.assertFalse(campaign_is_live(datetime(2026, 11, 27, 6, 59, tzinfo=timezone.utc)))
        self.assertTrue(campaign_is_live(datetime(2026, 11, 27, 7, 0, tzinfo=timezone.utc)))
        self.assertTrue(campaign_is_live(datetime(2026, 12, 1, 6, 59, tzinfo=timezone.utc)))
        self.assertFalse(campaign_is_live(datetime(2026, 12, 1, 7, 0, tzinfo=timezone.utc)))

    def test_campaign_stays_hidden_without_stripe_promotion_code(self):
        config.BLACK_FRIDAY_PROMOTION_CODE_ID = ''
        response = self.client.get('/pricing')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Choose Your Plan', html)
        self.assertNotIn('Half price. A full year of insight.', html)
        self.assertEqual(self.client.get('/black-friday').status_code, 302)

    def test_landing_page_links_new_visitors_to_annual_discounted_checkout(self):
        response = self.client.get('/black-friday')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('$39.50', html)
        self.assertIn('$130', html)
        self.assertIn('BLACKFRIDAY50', html)
        self.assertIn('interval%3Dannual', html)
        self.assertIn('campaign%3Dblack-friday-2026', html)
        self.assertIn('Renews at $79/year', html)
        self.assertIn('Renews at $260/year', html)

    def test_homepage_shows_campaign_banner(self):
        response = self.client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('New customers get 50% off their first year of Plus or Pro.', html)
        self.assertIn('href="/black-friday"', html)

    def test_pricing_page_preserves_campaign_through_registration(self):
        response = self.client.get('/pricing')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Half price. A full year of insight.', html)
        self.assertIn('Create Account &amp; Get Plus for $39.50', html)
        self.assertIn('Create Account &amp; Get Pro for $130', html)
        self.assertIn('campaign%3Dblack-friday-2026', html)

    def test_new_customer_annual_checkout_applies_promotion_and_metadata(self):
        fake_session = {
            'id': 'cs_test_black_friday',
            'url': 'https://checkout.stripe.com/c/pay/test_black_friday',
        }
        with self.client.session_transaction() as browser_session:
            browser_session['public_user_id'] = 11
        with patch(
            'blueprints.bail_bond_ads.stripe.checkout.Session.create',
            return_value=fake_session,
        ) as create_session:
            response = self.client.get(
                f'/checkout/plus?interval=annual&campaign={CAMPAIGN_SLUG}',
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], fake_session['url'])
        checkout = create_session.call_args.kwargs
        self.assertEqual(
            checkout['discounts'],
            [{'promotion_code': 'promo_test_black_friday'}],
        )
        self.assertEqual(checkout['metadata']['campaign'], CAMPAIGN_SLUG)
        self.assertEqual(checkout['subscription_data']['metadata']['campaign'], CAMPAIGN_SLUG)
        self.assertEqual(checkout['line_items'][0]['price'], 'price_1U4Pw0GL8T8btZcur4hvliu2')

    def test_pro_campaign_checkout_uses_advertised_annual_price(self):
        fake_session = {
            'id': 'cs_test_black_friday_pro',
            'url': 'https://checkout.stripe.com/c/pay/test_black_friday_pro',
        }
        with self.client.session_transaction() as browser_session:
            browser_session['public_user_id'] = 11
        with patch(
            'blueprints.bail_bond_ads.stripe.checkout.Session.create',
            return_value=fake_session,
        ) as create_session:
            response = self.client.get(
                f'/checkout/pro?interval=annual&campaign={CAMPAIGN_SLUG}',
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        checkout = create_session.call_args.kwargs
        self.assertEqual(
            checkout['line_items'][0]['price'],
            'price_1U4Pw0GL8T8btZucsRif7sb',
        )
        self.assertEqual(
            checkout['discounts'],
            [{'promotion_code': 'promo_test_black_friday'}],
        )

    def test_monthly_checkout_cannot_use_annual_campaign(self):
        with self.client.session_transaction() as browser_session:
            browser_session['public_user_id'] = 11
        with patch('blueprints.bail_bond_ads.stripe.checkout.Session.create') as create_session:
            response = self.client.get(
                f'/checkout/plus?interval=monthly&campaign={CAMPAIGN_SLUG}',
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/black-friday')
        create_session.assert_not_called()

    def test_current_and_former_subscribers_are_not_new_customers(self):
        self.assertTrue(is_new_customer(11))
        self.assertFalse(is_new_customer(12))
        self.assertFalse(is_new_customer(13))
        with self.client.session_transaction() as browser_session:
            browser_session['public_user_id'] = 12
        with patch('blueprints.bail_bond_ads.stripe.checkout.Session.create') as create_session:
            response = self.client.get(
                f'/checkout/pro?interval=annual&campaign={CAMPAIGN_SLUG}',
                follow_redirects=False,
            )
        self.assertEqual(response.headers['Location'], '/black-friday')
        create_session.assert_not_called()

    def test_discounted_stripe_failure_never_falls_back_to_full_price_link(self):
        with self.client.session_transaction() as browser_session:
            browser_session['public_user_id'] = 11
        with patch(
            'blueprints.bail_bond_ads.stripe.checkout.Session.create',
            side_effect=RuntimeError('Stripe unavailable'),
        ):
            response = self.client.get(
                f'/checkout/pro?interval=annual&campaign={CAMPAIGN_SLUG}',
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/black-friday')
        self.assertNotIn('buy.stripe.com', response.headers['Location'])

    def test_regular_annual_checkout_still_works_without_campaign(self):
        fake_session = {
            'id': 'cs_test_regular',
            'url': 'https://checkout.stripe.com/c/pay/test_regular',
        }
        with self.client.session_transaction() as browser_session:
            browser_session['public_user_id'] = 11
        with patch(
            'blueprints.bail_bond_ads.stripe.checkout.Session.create',
            return_value=fake_session,
        ) as create_session:
            response = self.client.get('/checkout/plus?interval=annual')
        self.assertEqual(response.headers['Location'], fake_session['url'])
        self.assertNotIn('discounts', create_session.call_args.kwargs)
        self.assertNotIn('campaign', create_session.call_args.kwargs['metadata'])
        self.assertEqual(
            create_session.call_args.kwargs['line_items'][0]['price'],
            'price_1U1ASIGL8T8btZcuVdB0VRSx',
        )


if __name__ == '__main__':
    unittest.main()
