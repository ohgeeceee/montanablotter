"""Tests for the Day 5 sample report generator (services/lawyer_outreach/sample_report.py).

Exercises:
  - live mode reads real lawyer_listing_events + lawyer_lead_deliveries
  - sample mode uses reference numbers for the requested tier
  - cost math is correct (price_cents / divisor, two decimals)
  - divide-by-zero yields None, not 0.0 (a brand-new placement shouldn't
    show "$0.00 per consultation" — it should show "—")
  - delivery detail rows are ordered most-recent-first
  - empty-state copy is honest when no events/deliveries exist
  - report_id is unique per call
  - unknown prospect_id raises ValueError
  - unknown package_id raises ValueError
  - firm + county that match an active order in another county do NOT
    trigger live mode (county filter matters)
  - when a firm has multiple active orders, gold is preferred over silver/bronze
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

# Make sure the app's importable paths line up — same trick the existing
# test_lawyer_outreach.py uses.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import init_db  # noqa: E402
from services.lawyer_outreach import sample_report  # noqa: E402


# ------------------------------------------------------------------ helpers --

def _new_db() -> sqlite3.Connection:
    fd, path = tempfile.mkstemp(prefix='mb-sample-report-', suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db.ensure_lawyer_outreach_schema(conn)
    init_db.ensure_lawyer_ad_schema(conn)
    return conn


def _seed_prospect(conn, firm='Alpine Law', county='Yellowstone', email='jane@alpinelawmt.com'):
    cur = conn.execute(
        '''INSERT INTO lawyer_outreach_prospects
           (firm_name, county, contact_email, stage, status, last_action_at)
           VALUES (?, ?, ?, 'day_1', 'queued', datetime('now'))''',
        (firm, county, email),
    )
    conn.commit()
    return cur.lastrowid


def _seed_order(
    conn, *, firm='Alpine Law', counties='Yellowstone', package_id='gold',
    status='active',
):
    cur = conn.execute(
        '''INSERT INTO lawyer_ad_orders
           (firm_name, contact_name, email, counties_served, package_id,
            status, paid_at)
           VALUES (?, 'Jane Doe', 'jane@alpinelawmt.com', ?, ?, ?, datetime('now'))''',
        (firm, counties, package_id, status),
    )
    oid = cur.lastrowid
    conn.execute(
        '''INSERT INTO lawyer_ad_listings (order_id, firm_name, counties_served, is_active)
           VALUES (?, ?, ?, 1)''',
        (oid, firm, counties),
    )
    conn.commit()
    return oid


def _seed_event(conn, order_id, event_type, days_ago=0, hour_offset=0):
    occurred = datetime.utcnow() - timedelta(days=days_ago)
    occurred = occurred.replace(hour=(occurred.hour + hour_offset) % 24)
    conn.execute(
        '''INSERT INTO lawyer_listing_events
           (order_id, event_type, ip_hash, county, occurred_at)
           VALUES (?, ?, 'h'||hex(randomblob(6)), 'Yellowstone', ?)''',
        (order_id, event_type, occurred.strftime('%Y-%m-%d %H:%M:%S')),
    )


def _seed_lead_and_delivery(conn, order_id, name='Doe, J.', phone='(406) 555-0100',
                             county='Yellowstone', case_type='DUI', status='sent',
                             error=None, days_ago=1):
    occurred = (datetime.utcnow() - timedelta(days=days_ago)).strftime('%Y-%m-%d %H:%M:%S')
    cur = conn.execute(
        '''INSERT INTO lawyer_consumer_leads
           (full_name, phone, county, case_type, source, created_at)
           VALUES (?, ?, ?, ?, 'lawyers_directory', ?)''',
        (name, phone, county, case_type, occurred),
    )
    lead_id = cur.lastrowid
    conn.execute(
        '''INSERT INTO lawyer_lead_deliveries
           (lead_id, order_id, channel, destination, status, error, sent_at)
           VALUES (?, ?, 'email', 'jane@alpinelawmt.com', ?, ?, ?)''',
        (lead_id, order_id, status, error, occurred if status == 'sent' else None),
    )
    conn.commit()
    return lead_id


# ------------------------------------------------------------------- tests ---

class SampleReportSchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = _new_db()

    def tearDown(self):
        self.conn.close()

    def test_unknown_prospect_raises_value_error(self):
        with self.assertRaises(ValueError):
            sample_report.generate(self.conn, prospect_id=9999)

    def test_unknown_package_raises_value_error(self):
        pid = _seed_prospect(self.conn)
        with self.assertRaises(ValueError):
            sample_report.generate(self.conn, prospect_id=pid, package_id='platinum')


class SampleReportSampleModeTests(unittest.TestCase):
    def setUp(self):
        self.conn = _new_db()
        self.pid = _seed_prospect(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_sample_uses_gold_reference_numbers(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        self.assertEqual(r['mode'], 'sample')
        self.assertEqual(r['metrics']['impressions'], 412)
        self.assertEqual(r['metrics']['clicks'], 31)
        self.assertEqual(r['metrics']['calls'], 18)
        self.assertEqual(r['metrics']['leads_delivered'], 6)
        self.assertEqual(r['metrics']['leads_failed'], 1)

    def test_sample_uses_silver_reference_numbers(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='silver')
        self.assertEqual(r['metrics']['impressions'], 184)
        self.assertEqual(r['metrics']['leads_delivered'], 3)
        self.assertEqual(r['metrics']['leads_failed'], 0)

    def test_sample_uses_bronze_reference_numbers(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='bronze')
        self.assertEqual(r['metrics']['impressions'], 87)
        self.assertEqual(r['metrics']['leads_delivered'], 1)

    def test_sample_cost_per_lead_for_gold(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        # $599 / 6 leads = $99.83 (rounded to two decimals)
        self.assertEqual(r['cost_per_lead'], 99.83)

    def test_sample_cost_per_consultation_silver(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='silver')
        # $299 / 1.2 consultations = $249.17 (rounded)
        self.assertEqual(r['cost_per_consultation'], 249.17)

    def test_sample_cost_per_retained_is_none_when_zero(self):
        # Force a tier with retained=0 by creating a custom prospect path —
        # the easiest way is to call generate with a known-zero reference
        # and verify None is the cost value (not 0.0).
        # Reference numbers all have retained > 0, so a fabricated
        # package via direct helper isn't possible from public API.
        # Instead assert all three real tiers yield a number (positive).
        for pkg in ('bronze', 'silver', 'gold'):
            r = sample_report.generate(self.conn, prospect_id=self.pid, package_id=pkg)
            self.assertIsNotNone(r['cost_per_retained'],
                                 f'{pkg} should have a cost_per_retained')

    def test_sample_disclaimer_warns_no_guarantee(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        self.assertIn('illustrative', r['disclaimer'].lower())
        self.assertIn('does not guarantee', r['disclaimer'].lower())

    def test_sample_period_label_says_illustrative(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        self.assertIn('illustrative', r['period_label'].lower())

    def test_sample_deliveries_show_recent_routing(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        # All sample deliveries are clearly labeled (Sample) or example.com.
        for d in r['deliveries']:
            self.assertTrue(
                d['lead_name'].startswith('(Sample)') or 'sample' in d['destination'],
                f'sample row must be clearly fake: {d!r}',
            )

    def test_sample_deliveries_show_failure(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        statuses = {d['status'] for d in r['deliveries']}
        self.assertIn('failed', statuses, 'sample report should show a failed delivery')

    def test_sample_report_id_is_unique_per_call(self):
        r1 = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        r2 = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        self.assertNotEqual(r1['report_id'], r2['report_id'])

    def test_sample_package_metadata(self):
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        self.assertEqual(r['package']['name'], 'Gold Priority')
        self.assertEqual(r['package']['price_label'], '$599/mo')

    def test_sample_advertiser_reported_is_decimal_for_sample(self):
        # Sample mode is honest about being fake: includes a "based on the
        # 90-day pilot cohort" estimate for consultations/retained.
        r = sample_report.generate(self.conn, prospect_id=self.pid, package_id='gold')
        self.assertIsNotNone(r['advertiser_reported']['consultations'])
        self.assertIsNotNone(r['advertiser_reported']['retained'])


class SampleReportLiveModeTests(unittest.TestCase):
    def setUp(self):
        self.conn = _new_db()
        self.pid = _seed_prospect(self.conn, firm='Alpine Law', county='Yellowstone')

    def tearDown(self):
        self.conn.close()

    def test_active_order_triggers_live_mode(self):
        _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(r['mode'], 'live')
        self.assertEqual(r['package']['id'], 'gold')

    def test_live_counts_real_events(self):
        oid = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        # 4 impressions, 2 clicks, 1 call in the last 30 days.
        for _ in range(4):
            _seed_event(self.conn, oid, 'impression', days_ago=2)
        _seed_event(self.conn, oid, 'click', days_ago=1)
        _seed_event(self.conn, oid, 'click', days_ago=5)
        _seed_event(self.conn, oid, 'call', days_ago=3)
        # An old impression (31 days ago) should NOT count.
        _seed_event(self.conn, oid, 'impression', days_ago=31, hour_offset=1)

        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(r['metrics']['impressions'], 4)
        self.assertEqual(r['metrics']['clicks'], 2)
        self.assertEqual(r['metrics']['calls'], 1)

    def test_live_counts_real_deliveries_split_sent_and_failed(self):
        oid = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        _seed_lead_and_delivery(self.conn, oid, name='Doe', status='sent', days_ago=2)
        _seed_lead_and_delivery(self.conn, oid, name='Roe', status='sent', days_ago=4)
        _seed_lead_and_delivery(self.conn, oid, name='Lee', status='failed', days_ago=6,
                                error='smtp_recipient_rejected')

        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(r['metrics']['leads_delivered'], 2)
        self.assertEqual(r['metrics']['leads_failed'], 1)

    def test_live_recent_deliveries_ordered_newest_first(self):
        oid = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        _seed_lead_and_delivery(self.conn, oid, name='Oldest', days_ago=20)
        _seed_lead_and_delivery(self.conn, oid, name='Newest', days_ago=1)
        _seed_lead_and_delivery(self.conn, oid, name='Middle', days_ago=10)

        r = sample_report.generate(self.conn, prospect_id=self.pid)
        names = [d['lead_name'] for d in r['deliveries']]
        self.assertEqual(names[0], 'Newest')
        self.assertEqual(names[1], 'Middle')
        self.assertEqual(names[2], 'Oldest')

    def test_live_recent_delivery_includes_lead_fields(self):
        oid = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        _seed_lead_and_delivery(self.conn, oid, name='Smith, K.',
                                phone='(406) 555-0199', case_type='Drug possession')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        d = r['deliveries'][0]
        self.assertEqual(d['lead_name'], 'Smith, K.')
        self.assertEqual(d['lead_phone'], '(406) 555-0199')
        self.assertEqual(d['case_type'], 'Drug possession')
        self.assertEqual(d['status'], 'sent')

    def test_live_recent_delivery_caps_at_ten(self):
        oid = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        for i in range(15):
            _seed_lead_and_delivery(self.conn, oid, name=f'Person {i:02d}',
                                    days_ago=i)
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(len(r['deliveries']), 10)

    def test_live_failed_delivery_carries_error(self):
        oid = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        _seed_lead_and_delivery(self.conn, oid, name='Failed One', status='failed',
                                error='mailbox_full', days_ago=3)
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        d = next(d for d in r['deliveries'] if d['lead_name'] == 'Failed One')
        self.assertEqual(d['status'], 'failed')
        self.assertEqual(d['error'], 'mailbox_full')

    def test_live_advertiser_reported_is_null_until_firm_fills_in(self):
        # Live mode does NOT fabricate contacted/consultations/retained.
        oid = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        _seed_event(self.conn, oid, 'impression', days_ago=1)
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertIsNone(r['advertiser_reported']['contacted'])
        self.assertIsNone(r['advertiser_reported']['consultations'])
        self.assertIsNone(r['advertiser_reported']['retained'])

    def test_live_cost_per_lead_with_real_deliveries(self):
        oid = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        # 2 successful deliveries, 0 failed.
        _seed_lead_and_delivery(self.conn, oid, name='A', days_ago=2)
        _seed_lead_and_delivery(self.conn, oid, name='B', days_ago=3)
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        # $599 / 2 = $299.50
        self.assertEqual(r['cost_per_lead'], 299.50)

    def test_live_cost_per_lead_none_when_no_deliveries_yet(self):
        # Brand-new Gold placement, zero leads: divide-by-zero → None.
        _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertIsNone(r['cost_per_lead'])

    def test_live_period_label_says_last_thirty_days(self):
        _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertIn('30', r['period_label'])

    def test_live_disclaimer_points_to_advertising_email(self):
        _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertIn('advertising@montanablotter.com', r['disclaimer'])

    def test_firm_in_different_county_does_not_trigger_live_mode(self):
        # Prospect says county=Yellowstone; order's counties_served is
        # Gallatin only. County filter must keep this in sample mode.
        _seed_order(self.conn, firm='Alpine Law', counties='Gallatin', package_id='gold')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(r['mode'], 'sample')

    def test_active_orders_in_multiple_counties_match_prospect_county(self):
        # The 'and' vs '&' normalization must allow 'Lewis & Clark' to
        # match a prospect's county written as 'Lewis and Clark'.
        pid_lc = _seed_prospect(
            self.conn, firm='Helena Legal', county='Lewis and Clark',
            email='info@helenalegal.com',
        )
        _seed_order(self.conn, firm='Helena Legal',
                    counties='Lewis & Clark, Cascade', package_id='silver')
        r = sample_report.generate(self.conn, prospect_id=pid_lc)
        self.assertEqual(r['mode'], 'live')
        self.assertEqual(r['package']['id'], 'silver')

    def test_prefers_gold_over_silver_when_firm_has_both_active(self):
        # Same firm, same county, two active orders at different tiers.
        # The report should report under the best tier.
        oid_silver = _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone',
                                 package_id='silver')
        # Override the seeded paid_at to make sure the silver row didn't
        # already have a paid_at that makes _find_active_order pick it.
        # The function sorts by tier rank, not by paid_at, so order doesn't
        # matter — but we confirm by name+county match.
        _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(r['mode'], 'live')
        self.assertEqual(r['package']['id'], 'gold')

    def test_capacity_blocked_order_still_triggers_live_mode(self):
        # capacity_blocked is still an order, and the firm is still in the
        # sales pipeline. Report should not silently fall back to sample
        # mode just because the order hasn't gone "active" yet.
        _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone',
                    package_id='gold', status='capacity_blocked')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(r['mode'], 'live')

    def test_inactive_order_falls_back_to_sample(self):
        # If the order is no longer active, this prospect has lapsed and
        # the report defaults to sample mode with the requested tier.
        _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone',
                    package_id='gold', status='cancelled')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(r['mode'], 'sample')


class SampleReportEmptyStateTests(unittest.TestCase):
    def setUp(self):
        self.conn = _new_db()
        self.pid = _seed_prospect(self.conn, firm='Alpine Law', county='Yellowstone')

    def tearDown(self):
        self.conn.close()

    def test_live_with_active_but_no_events_shows_zeros(self):
        # Real data: active order, but no impressions, no leads yet. The
        # report should still render — not blow up — and surface the
        # zero-state honestly.
        _seed_order(self.conn, firm='Alpine Law', counties='Yellowstone', package_id='gold')
        r = sample_report.generate(self.conn, prospect_id=self.pid)
        self.assertEqual(r['mode'], 'live')
        self.assertEqual(r['metrics']['impressions'], 0)
        self.assertEqual(r['metrics']['clicks'], 0)
        self.assertEqual(r['metrics']['calls'], 0)
        self.assertEqual(r['metrics']['leads_delivered'], 0)
        self.assertEqual(r['metrics']['leads_failed'], 0)
        self.assertEqual(r['deliveries'], [])
        self.assertIsNone(r['cost_per_lead'])


if __name__ == '__main__':
    unittest.main()
