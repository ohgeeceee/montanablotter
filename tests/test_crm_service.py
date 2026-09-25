"""Tests for the CRM service layer (portals, email logs, rollups)."""
import sqlite3
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from init_db import ensure_advertising_center_schema  # noqa: E402
from scripts.crm_migration import ensure_crm_schema  # noqa: E402
from services.crm import crm_service  # noqa: E402


def fresh_db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    ensure_advertising_center_schema(conn)
    # minimal stand-ins for the order tables the rollup views UNION over
    for t in ('lawyer_ad_orders', 'bail_ad_orders'):
        conn.execute(f'''CREATE TABLE {t} (
            id INTEGER PRIMARY KEY AUTOINCREMENT, firm_name TEXT, business_name TEXT,
            contact_name TEXT, email TEXT, phone TEXT, billing_cycle TEXT,
            amount_cents INTEGER, currency TEXT, status TEXT,
            provider TEXT, provider_subscription_id TEXT, provider_customer_id TEXT,
            onboarding_token TEXT, paid_at TEXT, cancelled_at TEXT,
            created_at TEXT, updated_at TEXT)''')
    conn.execute('''CREATE TABLE lawyer_ad_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, invoice_number TEXT,
        amount_cents INTEGER, currency TEXT, status TEXT, period_start TEXT,
        period_end TEXT, issued_at TEXT, paid_at TEXT, provider_invoice_id TEXT)''')
    ensure_crm_schema(conn)
    conn.execute("INSERT INTO advertising_prospects (business_name, business_key,"
                 " category, email, counties)"
                 " VALUES ('Lucky Strike Bail','lucky strike bail','bail','ops@ls.example','Cascade')")
    conn.commit()
    return conn


class TestDirectoryJoin(unittest.TestCase):
    """§A5: portal listing edits surface on the public directory via the join."""

    def _bail_portal_with_edits(self, conn):
        conn.execute("INSERT INTO bail_ad_orders (business_name, amount_cents,"
                     " billing_cycle, status, provider, provider_subscription_id)"
                     " VALUES ('Lucky Strike Bail', 20000, 'monthly', 'active',"
                     " 'stripe', 'sub_x')")
        p = crm_service.open_portal(conn, 1, 'featured_listing', slug='lucky')
        row = conn.execute('SELECT * FROM client_portals WHERE id = ?',
                           (p['portal_id'],)).fetchone()
        conn.execute("UPDATE client_portals SET primary_order_table ="
                     " 'bail_ad_orders', primary_order_id = 1 WHERE id = ?",
                     (row['id'],))
        crm_service.update_listing(conn, row['id'], bio='Fast release, 24/7.',
                                   display_phone='(406) 555-0100',
                                   logo_url='/static/crm_logos/lucky.png')
        crm_service.set_features(conn, row['id'], ['listing_editor'])
        conn.execute("UPDATE client_portals SET portal_status = 'active' "
                     "WHERE id = ?", (row['id'],))
        conn.commit()
        return row['id']

    def test_override_overlay_on_bail_listings(self):
        conn = fresh_db()
        self._bail_portal_with_edits(conn)
        listings = [{'id': 1, 'business_name': 'Lucky Strike Bail',
                     'phone': '406-555-9999', 'phone_href': 'tel:4065559999',
                     'sms_href': 'sms:4065559999',
                     'body_copy': 'default copy', 'target_url': '',
                     'logo_path': '/static/old.png'}]
        crm_service.apply_directory_overrides(conn, listings, 'bail')
        self.assertEqual(listings[0]['body_copy'], 'Fast release, 24/7.')
        self.assertEqual(listings[0]['phone'], '(406) 555-0100')
        self.assertEqual(listings[0]['phone_href'], 'tel:14065550100')
        self.assertEqual(listings[0]['logo_path'], '/static/crm_logos/lucky.png')

    def test_inactive_order_override_not_applied(self):
        conn = fresh_db()
        pid = self._bail_portal_with_edits(conn)
        conn.execute("UPDATE bail_ad_orders SET status = 'cancelled' WHERE id = 1")
        conn.commit()
        listings = [{'id': 1, 'phone': '406-555-9999', 'body_copy': 'default',
                     'target_url': '', 'logo_path': '/static/old.png'}]
        crm_service.apply_directory_overrides(conn, listings, 'bail')
        self.assertEqual(listings[0]['body_copy'], 'default')

    def test_lawyer_overlay_uses_order_id_key(self):
        conn = fresh_db()
        self._bail_portal_with_edits(conn)
        listings = [{'id': 1, 'phone': 'x', 'body_copy': 'd',
                     'target_url': '', 'logo_path': ''}]
        crm_service.apply_directory_overrides(conn, listings, 'lawyer')
        self.assertEqual(listings[0]['body_copy'], 'd')  # wrong vertical -> untouched


class TestPortals(unittest.TestCase):
    def test_open_idempotent_per_lead_tier(self):
        conn = fresh_db()
        a = crm_service.open_portal(conn, 1, 'banner')
        b = crm_service.open_portal(conn, 1, 'banner')
        self.assertTrue(a['created'])
        self.assertFalse(b['created'])
        self.assertEqual(a['portal_id'], b['portal_id'])
        self.assertEqual(len(conn.execute('SELECT * FROM client_portals').fetchall()), 1)

    def test_two_tiers_two_portals(self):
        conn = fresh_db()
        crm_service.open_portal(conn, 1, 'banner')
        crm_service.open_portal(conn, 1, 'featured_listing')
        self.assertEqual(len(conn.execute('SELECT * FROM client_portals').fetchall()), 2)

    def test_rejects_bad_tier_and_lead(self):
        conn = fresh_db()
        with self.assertRaises(ValueError):
            crm_service.open_portal(conn, 1, 'diamond')
        with self.assertRaises(ValueError):
            crm_service.open_portal(conn, 999, 'banner')

    def test_slug_unique_collision_falls_back_to_token(self):
        conn = fresh_db()
        r1 = crm_service.open_portal(conn, 1, 'banner', slug='lucky-strike')
        conn.execute("INSERT INTO advertising_prospects (business_name, business_key, category)"
                     " VALUES ('Other','other','bail')")
        r2 = crm_service.open_portal(conn, 2, 'banner', slug='lucky-strike')
        self.assertEqual(r1['slug'], 'lucky-strike')
        self.assertEqual(r2['slug'], '')
        self.assertTrue(len(r2['access_token']) >= 32)

    def test_status_transitions_and_guards(self):
        conn = fresh_db()
        p = crm_service.open_portal(conn, 1, 'banner')
        self.assertTrue(crm_service.set_portal_status(conn, p['portal_id'], 'active'))
        self.assertTrue(crm_service.portal_context(conn, p['access_token']))
        crm_service.set_portal_status(conn, p['portal_id'], 'closed')
        self.assertIsNone(crm_service.portal_context(conn, p['access_token']))
        with self.assertRaises(ValueError):
            crm_service.set_portal_status(conn, p['portal_id'], 'zombie')

    def test_portal_context_by_slug_and_rejects_garbage(self):
        conn = fresh_db()
        p = crm_service.open_portal(conn, 1, 'banner', slug='lucky')
        self.assertTrue(crm_service.portal_context(conn, 'lucky'))
        self.assertIsNone(crm_service.portal_context(conn, 'not-a-real-token'))
        self.assertIsNone(crm_service.portal_context(conn, ''))
        self.assertIsNone(crm_service.portal_context(conn, p['access_token'][:10]))


class TestEmailLog(unittest.TestCase):
    def test_log_and_thread(self):
        conn = fresh_db()
        eid = crm_service.log_email(conn, 1, 'outbound', 'Intro', 'Hi there',
                                    to_address='ops@ls.example', tracking_id='<m1@mb>')
        crm_service.log_email(conn, 1, 'inbound', 'Re: Intro', 'Interested',
                              from_address='ops@ls.example')
        thread = crm_service.email_thread(conn, 1)
        self.assertEqual(len(thread), 2)
        self.assertEqual(thread[0]['subject'], 'Re: Intro')  # newest first
        row = conn.execute('SELECT * FROM crm_email_logs WHERE id = ?', (eid,)).fetchone()
        self.assertEqual(row['provider'], 'manual')
        self.assertEqual(row['send_status'], 'logged')

    def test_tracking_id_unique(self):
        conn = fresh_db()
        crm_service.log_email(conn, 1, 'outbound', 'a', 'b', tracking_id='<dup>')
        with self.assertRaises(sqlite3.IntegrityError):
            crm_service.log_email(conn, 1, 'outbound', 'c', 'd', tracking_id='<dup>')

    def test_validation(self):
        conn = fresh_db()
        with self.assertRaises(ValueError):
            crm_service.log_email(conn, 1, 'sideways', 'a', 'b')
        with self.assertRaises(ValueError):
            crm_service.log_email(conn, 1, 'outbound', 'a', 'b', send_status='yolo')


class TestRollups(unittest.TestCase):
    def test_empty_views_readable(self):
        conn = fresh_db()
        self.assertEqual(crm_service.billing_rollup(conn), [])
        self.assertEqual(crm_service.invoice_rollup(conn), [])

    def test_rollup_for_lead_with_no_orders(self):
        conn = fresh_db()
        p = crm_service.open_portal(conn, 1, 'banner')
        self.assertEqual(crm_service.billing_rollup(conn, 1), [])
        self.assertEqual(crm_service.invoice_rollup(conn, 1), [])

    def test_view_reflects_underlying_order(self):
        conn = fresh_db()
        conn.execute('''INSERT INTO lawyer_ad_orders
            (firm_name, email, provider_subscription_id, amount_cents,
             billing_cycle, status)
            VALUES ('Doe Law','d@x.example','sub_123', 5900, 'monthly', 'active')''')
        conn.execute("INSERT INTO client_portals (lead_id, access_token, tier,"
                     " primary_order_table, primary_order_id)"
                     " VALUES (1,'tok-x-0123456789abcdef','banner',"
                     " 'lawyer_ad_orders',1)")
        subs = crm_service.billing_rollup(conn, 1)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]['stripe_subscription_id'], 'sub_123')
        self.assertEqual(subs[0]['amount_cents'], 5900)


class TestListingEditor(unittest.TestCase):
    def _portal(self, conn):
        p = crm_service.open_portal(conn, 1, 'featured_listing', slug='lucky')
        return conn.execute('SELECT cp.*, ap.business_name FROM client_portals cp '
                            'JOIN advertising_prospects ap ON ap.id = cp.lead_id '
                            'WHERE cp.id = ?', (p['portal_id'],)).fetchone()

    def test_update_and_read_back(self):
        conn = fresh_db()
        p = self._portal(conn)
        pid = p['id']
        crm_service.update_listing(conn, pid, bio='  Licensed 1998.  ',
                                   display_phone='(406) 555-0100',
                                   direct_line='406.555.0101',
                                   direct_link='https://lucky.example/bail',
                                   logo_url='/static/crm_logos/x.png')
        row = conn.execute('SELECT * FROM client_portals WHERE id = ?', (pid,)).fetchone()
        self.assertEqual(row['bio'], 'Licensed 1998.')          # trimmed
        self.assertEqual(row['display_phone'], '(406) 555-0100')
        self.assertEqual(row['direct_link'], 'https://lucky.example/bail')
        self.assertEqual(row['logo_url'], '/static/crm_logos/x.png')
        self.assertTrue(row['listing_updated_at'])

    def test_validation_rejects_bad_link_and_phone(self):
        conn = fresh_db()
        p = self._portal(conn)
        with self.assertRaises(ValueError):
            crm_service.update_listing(conn, p['id'], direct_link='javascript:alert(1)')
        with self.assertRaises(ValueError):
            crm_service.update_listing(conn, p['id'], display_phone='call me')

    def test_feature_toggles(self):
        conn = fresh_db()
        p = self._portal(conn)
        self.assertTrue(crm_service.feature_enabled(p, 'metrics'))  # default all on
        crm_service.set_features(conn, p['id'], ['listing_editor', 'billing'])
        p2 = self._portal(conn)
        self.assertFalse(crm_service.feature_enabled(p2, 'metrics'))
        self.assertTrue(crm_service.feature_enabled(p2, 'billing'))
        with self.assertRaises(ValueError):
            crm_service.set_features(conn, p['id'], ['teleport'])


class TestMetricsAndReceipt(unittest.TestCase):
    def _wired_portal(self, conn):
        conn.execute('''CREATE TABLE bail_ad_events (id INTEGER PRIMARY KEY,
            order_id INT, event_type TEXT, county TEXT, created_at TEXT)''')
        conn.execute('''CREATE TABLE bail_consumer_leads (id INTEGER PRIMARY KEY,
            full_name TEXT, phone TEXT, email TEXT, county TEXT, jail_facility TEXT,
            notes TEXT, routed_order_ids TEXT, created_at TEXT)''')
        for et, n in (('impression', 10), ('click', 2)):
            for i in range(n):
                conn.execute("INSERT INTO bail_ad_events (order_id, event_type, county,"
                             " created_at) VALUES (1,?, 'Cascade',"
                             " datetime('now','-1 day'))", (et,))
        conn.execute("INSERT INTO bail_ad_events (order_id, event_type, county,"
                     " created_at) VALUES (1,'impression','Referrer:docx',"
                     " datetime('now','-1 day'))")   # dirty county -> Other
        conn.execute("INSERT INTO bail_consumer_leads (full_name, phone, county,"
                     " jail_facility, routed_order_ids, created_at)"
                     " VALUES ('Jo Q','4065551234','Cascade','CSO','1,7',"
                     " datetime('now'))")
        conn.execute("INSERT INTO bail_consumer_leads (full_name, phone, county,"
                     " jail_facility, routed_order_ids, created_at)"
                     " VALUES ('Not Mine','','','X','9','datetime')")
        conn.execute("INSERT INTO bail_ad_orders (id, business_name, status)"
                     " VALUES (1,'Lucky Strike','active')")
        p = crm_service.open_portal(conn, 1, 'banner')
        conn.execute("UPDATE client_portals SET primary_order_table='bail_ad_orders',"
                     " primary_order_id=1 WHERE id=?", (p['portal_id'],))
        conn.commit()
        return conn.execute('SELECT cp.*, ap.business_name FROM client_portals cp '
                            'JOIN advertising_prospects ap ON ap.id = cp.lead_id '
                            'WHERE cp.id = ?', (p['portal_id'],)).fetchone()

    def test_metrics_totals_and_county_buckets(self):
        conn = fresh_db()
        prow = self._wired_portal(conn)
        m = crm_service.portal_metrics(conn, prow)
        self.assertEqual(m['totals']['impressions'], 11)
        self.assertEqual(m['totals']['clicks'], 2)
        names = {c['county'] for c in m['counties']}
        self.assertIn('Cascade', names)
        self.assertIn('Other', names)          # dirty county folded, not shown raw

    def test_lead_receipt_filters_routed(self):
        conn = fresh_db()
        prow = self._wired_portal(conn)
        rows = crm_service.lead_receipt(conn, prow)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['person'], 'Jo Q')
        self.assertEqual(rows[0]['county'], 'Cascade')

    def test_metrics_degrade_without_event_table(self):
        conn = fresh_db()
        p = crm_service.open_portal(conn, 1, 'banner')
        prow = conn.execute('SELECT cp.*, ap.business_name FROM client_portals cp '
                            'JOIN advertising_prospects ap ON ap.id = cp.lead_id '
                            'WHERE cp.id = ?', (p['portal_id'],)).fetchone()
        # no primary order at all -> zeros, not crash
        m = crm_service.portal_metrics(conn, prow)
        self.assertEqual(m['totals']['impressions'], 0)


class TestAdminTabSupport(unittest.TestCase):
    def test_mrr_prorates_annual(self):
        conn = fresh_db()
        conn.execute("INSERT INTO bail_ad_orders (business_name, amount_cents,"
                     " billing_cycle, status, provider_subscription_id)"
                     " VALUES ('A', 12000, 'annual', 'active', 'sub_a')")
        conn.execute("INSERT INTO lawyer_ad_orders (firm_name, amount_cents,"
                     " billing_cycle, status, provider_subscription_id)"
                     " VALUES ('B', 20000, 'monthly', 'active', 'sub_b')")
        conn.execute("INSERT INTO lawyer_ad_orders (firm_name, amount_cents,"
                     " billing_cycle, status, provider_subscription_id)"
                     " VALUES ('C', 5000, 'monthly', 'canceled', 'sub_c')")
        mrr = crm_service.mrr_summary(conn)
        self.assertEqual(mrr['mrr_cents'], 1000 + 20000)   # 12k/12 + 20k/mo
        self.assertEqual(mrr['active_subscriptions'], 2)
        self.assertEqual(mrr['by_status'].get('canceled'), 1)

    def test_admin_clients_search(self):
        conn = fresh_db()
        crm_service.open_portal(conn, 1, 'banner', slug='lucky')
        self.assertEqual(len(crm_service.admin_clients(conn)), 1)
        self.assertEqual(len(crm_service.admin_clients(conn, search='Lucky')), 1)
        self.assertEqual(crm_service.admin_clients(conn, search='Nope'), [])
        self.assertEqual(crm_service.admin_clients(conn, status='active'), [])

    def test_stripe_link_reads_order_customer(self):
        conn = fresh_db()
        conn.execute("INSERT INTO lawyer_ad_orders (firm_name, provider_customer_id,"
                     " status) VALUES ('Doe', 'cus_999', 'active')")
        p = crm_service.open_portal(conn, 1, 'banner')
        conn.execute("UPDATE client_portals SET primary_order_table='lawyer_ad_orders',"
                     " primary_order_id=1 WHERE id=?", (p['portal_id'],))
        prow = conn.execute('SELECT cp.*, ap.business_name FROM client_portals cp '
                            'JOIN advertising_prospects ap ON ap.id = cp.lead_id '
                            'WHERE cp.id = ?', (p['portal_id'],)).fetchone()
        self.assertEqual(crm_service.stripe_link(conn, prow), 'cus_999')


if __name__ == '__main__':
    unittest.main()
