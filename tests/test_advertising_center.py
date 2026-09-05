"""Isolated fixtures for sales records, authorization, and honest media-kit metrics."""
import os
import secrets
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

import init_db
from services.monetization.advertising_center import (
    business_key, county_list, validate_prospect, source_groups, link_group, media_facts,
)


class AdvertisingDataTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        init_db.ensure_advertising_center_schema(self.conn)
        init_db.ensure_advertise_sales_lead_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def add_source(self, name='Test Company', email='sales@example.test'):
        self.conn.execute('INSERT INTO advertise_sales_leads(product,firm_or_agency,email,county) VALUES (?,?,?,?)',
                          ('bail', name, email, 'Cascade'))
        self.conn.commit()

    def test_idempotent_schema_preserves_records(self):
        self.add_source()
        pid = link_group(self.conn, business_key('Test Company'))
        init_db.ensure_advertising_center_schema(self.conn)
        self.assertEqual(self.conn.execute('SELECT id FROM advertising_prospects').fetchone()[0], pid)

    def test_matching_sources_link_once_without_changing_sources(self):
        self.add_source()
        self.add_source('TEST COMPANY')
        self.assertEqual(len(source_groups(self.conn)), 1)
        pid = link_group(self.conn, business_key('Test Company'))
        self.assertEqual(len(source_groups(self.conn)), 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertising_prospect_sources').fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertise_sales_leads').fetchone()[0], 2)
        self.add_source('Test Company', 'different@example.test')
        self.assertEqual(link_group(self.conn, business_key('Test Company')), pid)
        self.assertEqual(self.conn.execute('SELECT email FROM advertising_prospects').fetchone()[0], 'sales@example.test')

    def test_conflicting_emails_not_selected_automatically(self):
        self.add_source()
        self.add_source(email='second@example.test')
        link_group(self.conn, business_key('Test Company'))
        self.assertEqual(self.conn.execute('SELECT email FROM advertising_prospects').fetchone()[0], '')

    def test_manual_link_different_name_to_existing_business(self):
        self.add_source()
        pid = link_group(self.conn, business_key('Test Company'))
        self.add_source('Test Company Branch')
        self.assertEqual(link_group(self.conn, business_key('Test Company Branch'), pid), pid)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertising_prospects').fetchone()[0], 1)

    def test_validation(self):
        form = MultiDict({'business_name': 'Demo', 'email': 'BAD EMAIL'})
        with self.assertRaises(ValueError):
            validate_prospect(form)
        form['email'] = 'sales@example.test'
        form['next_follow_up'] = '2026-02-31'
        with self.assertRaises(ValueError):
            validate_prospect(form)
        form['next_follow_up'] = ''
        form.setlist('counties', ['Not Montana'])
        with self.assertRaises(ValueError):
            validate_prospect(form)
        form.setlist('counties', ['Cascade', 'Cascade'])
        self.assertEqual(validate_prospect(form)['counties'], 'Cascade')
        self.assertEqual(county_list('["Lewis & Clark","Cascade"]'), ['Cascade', 'Lewis and Clark'])

    def test_media_counts_exact_paths_dates_and_explicit_subscriptions(self):
        self.conn.execute('CREATE TABLE subscribers(counties TEXT,active INTEGER)')
        self.conn.executemany('INSERT INTO subscribers VALUES (?,?)',
                             [('Cascade, Park',1), ('["Cascade"]',1), ('',1), ('all',1), ('Cascade',0)])
        pv = sqlite3.connect(':memory:')
        pv.row_factory = sqlite3.Row
        pv.execute('CREATE TABLE page_views(path TEXT,created_at TEXT)')
        pv.executemany('INSERT INTO page_views VALUES (?,?)', [
            ('/county/cascade','2026-09-01 00:00:00'),
            ('/county/cascade/2026-08-31/','2026-09-01 00:00:00'),
            ('/county/cascade-other','2026-09-01 00:00:00'),
            ('/county/cascade','2026-01-01 00:00:00'),
            ('/county/cascade','2027-01-01 00:00:00'),
            ('/admin/cascade','2026-09-01 00:00:00')])
        facts = media_facts(self.conn, pv, 'Cascade', datetime(2026,9,3,tzinfo=timezone.utc))
        self.assertEqual(facts['requests'], 2)
        self.assertEqual(facts['county_subscribers'], 2)
        self.assertNotIn('email', facts)
        pv.close()


class AdvertisingRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import app only after caller has configured disposable DB paths.
        if not os.environ.get('MB_DB_PATH', '').startswith('/tmp/'):
            raise RuntimeError('Run with MB_DB_PATH and MB_PAGE_VIEWS_DB_PATH in /tmp, MB_TURSO_ENABLED=false.')
        import app
        cls.app_module = app
        cls.app = app.app
        cls.app.config.update(TESTING=True)

    def setUp(self):
        import config
        self.conn = sqlite3.connect(config.DB_PATH)
        self.conn.row_factory = sqlite3.Row
        init_db.ensure_advertising_center_schema(self.conn)
        init_db.ensure_advertise_sales_lead_schema(self.conn)
        self.conn.execute('DELETE FROM advertising_prospect_sources')
        self.conn.execute('DELETE FROM advertising_prospects')
        self.conn.execute('DELETE FROM advertise_sales_leads')
        self.conn.execute("INSERT OR REPLACE INTO users(id,username,password,role,is_active) VALUES (991,'advertising-test','unused','super_admin',1)")
        self.conn.commit()
        self.client = self.app.test_client()
        self.token = secrets.token_urlsafe(32)
        self.login('super_admin')
        from blueprints.admin.advertising_center import _facts_cache
        _facts_cache.clear()

    def tearDown(self):
        self.conn.close()

    def login(self, role):
        self.conn.execute('UPDATE users SET role=? WHERE id=991', (role,))
        self.conn.commit()
        with self.client.session_transaction() as session:
            session['_user_id'] = '991'
            session['_fresh'] = True
            session['_csrf_token'] = self.token

    def payload(self, **kwargs):
        data = {'business_name':'Synthetic Test Business','email':'owner@example.test',
                'stage':'new','category':'general','csrf_token':self.token,'counties':['Cascade']}
        data.update(kwargs)
        return data

    def create(self):
        response = self.client.post('/admin/revenue/advertising/new', data=self.payload())
        self.assertEqual(response.status_code,303)
        return response.headers['Location']

    def test_create_render_edit_and_duplicate(self):
        location = self.create()
        response = self.client.get(location)
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Synthetic Test Business',response.data)
        duplicate = self.client.post('/admin/revenue/advertising/new',data=self.payload(business_name='SYNTHETIC TEST BUSINESS'))
        self.assertEqual(duplicate.status_code,400)
        self.assertIn(b'already exists',duplicate.data)
        result = self.client.post(location,data=self.payload(version=1,stage='proposal_sent',next_follow_up='2026-01-01'))
        self.assertEqual(result.status_code,303)
        self.assertIn(b'Synthetic Test Business',self.client.get('/admin/revenue/advertising?due=1').data)

    def test_stale_edit_rejected(self):
        location = self.create()
        self.client.post(location,data=self.payload(version=1,stage='contacted'))
        stale = self.client.post(location,data=self.payload(version=1,stage='lost'))
        self.assertEqual(stale.status_code,400)
        self.assertIn(b'Someone else updated',stale.data)

    def test_csrf_and_permissions(self):
        denied = self.client.post('/admin/revenue/advertising/new',data={'business_name':'No token'}, headers={'Accept':'application/json'})
        self.assertEqual(denied.status_code,400)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertising_prospects').fetchone()[0],0)
        self.login('read_only')
        self.assertEqual(self.client.get('/admin/revenue/advertising').status_code,200)
        self.assertEqual(self.client.post('/admin/revenue/advertising/new',data=self.payload()).status_code,403)
        self.assertEqual(self.client.post('/admin/revenue/advertising/link-source',data={'csrf_token':self.token}).status_code,403)
        self.login('editor')
        self.assertEqual(self.client.get('/admin/revenue/advertising').status_code,403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/admin/revenue/advertising').status_code,302)

    def test_source_link_via_route(self):
        self.conn.execute("INSERT INTO advertise_sales_leads(product,firm_or_agency,email) VALUES ('lawyer','Synthetic Firm','contact@example.test')")
        self.conn.commit()
        response = self.client.post('/admin/revenue/advertising/link-source',data={'source_key':'synthetic firm','csrf_token':self.token})
        self.assertEqual(response.status_code,303)
        self.assertIn(b'Synthetic Firm',self.client.get(response.headers['Location']).data)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertise_sales_leads').fetchone()[0],1)

    def test_public_kit_contains_no_prospect_data(self):
        self.create()
        with self.client.session_transaction() as session:
            session.clear()
        result = self.client.get('/advertise/media-kit/cascade')
        self.assertEqual(result.status_code,200)
        self.assertIn(b'not verified people',result.data)
        self.assertNotIn(b'owner@example.test',result.data)
        self.assertNotIn(b'Synthetic Test Business',result.data)
        self.assertEqual(self.client.get('/advertise/media-kit/not-a-county').status_code,404)

    def test_kit_database_error_displays_unavailable_not_zero(self):
        with patch('blueprints.admin.advertising_center.media_facts',side_effect=sqlite3.OperationalError('test')):
            response = self.client.get('/advertise/media-kit/cascade')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Audience figures are temporarily unavailable',response.data)

    def test_html_escaped(self):
        location = self.client.post('/admin/revenue/advertising/new',data=self.payload(business_name='<script>alert(1)</script>')).headers['Location']
        response = self.client.get(location)
        self.assertNotIn(b'<script>alert(1)</script>',response.data)
        self.assertIn(b'&lt;script&gt;',response.data)

    def inquiry_payload(self, **kwargs):
        data = {'business_name':'Synthetic Advertiser','contact_name':'Test Contact',
                'email':'advertiser@example.test','phone':'','message':'Newsletter placement please',
                'product':'general','contact_ok':'yes','csrf_token':self.token}
        data.update(kwargs)
        return data

    def test_inquiry_saved_once_and_visible_in_source_inbox(self):
        path = '/advertise/media-kit/cascade/inquire'
        result = self.client.post(path,data=self.inquiry_payload())
        self.assertEqual(result.status_code,303)
        self.assertIn(b'your request is in our sales inbox',self.client.get(result.headers['Location']).data)
        self.client.post(path,data=self.inquiry_payload())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertise_sales_leads').fetchone()[0],1)
        source = self.conn.execute('SELECT * FROM advertise_sales_leads').fetchone()
        self.assertEqual(source['county'],'Cascade')
        self.assertEqual(source['source'],'county_media_kit')
        self.assertEqual(source['status'],'new')
        inbox = self.client.get('/admin/revenue/advertising?source_q=Synthetic+Advertiser')
        self.assertIn(b'Newsletter placement please',inbox.data)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertising_prospects').fetchone()[0],0)

    def test_inquiry_accepts_anonymous_advertiser_with_csrf(self):
        with self.client.session_transaction() as session:
            session.pop('_user_id',None)
        response = self.client.post('/advertise/media-kit/cascade/inquire',data=self.inquiry_payload())
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertise_sales_leads').fetchone()[0],1)

    def test_inquiry_csrf_invalid_fields_and_honeypot(self):
        path = '/advertise/media-kit/cascade/inquire'
        self.assertEqual(self.client.post(path,data=self.inquiry_payload(csrf_token='wrong')).status_code,400)
        self.assertEqual(self.client.post(path,data=self.inquiry_payload(email='invalid')).status_code,400)
        self.assertEqual(self.client.post(path,data=self.inquiry_payload(contact_ok='')).status_code,400)
        self.assertEqual(self.client.post(path,data=self.inquiry_payload(product='bad')).status_code,400)
        self.assertEqual(self.client.post('/advertise/media-kit/invalid/inquire',data=self.inquiry_payload()).status_code,404)
        self.assertEqual(self.client.post(path,data=self.inquiry_payload(company_url='spam')).status_code,303)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertise_sales_leads').fetchone()[0],0)

    def test_inquiry_rate_limit(self):
        path = '/advertise/media-kit/cascade/inquire'
        for i in range(5):
            self.assertEqual(self.client.post(path,data=self.inquiry_payload(message=f'Request {i}')).status_code,303)
        response = self.client.post(path,data=self.inquiry_payload(message='One more'))
        self.assertEqual(response.status_code,400)
        self.assertIn(b'Too many requests',response.data)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertise_sales_leads').fetchone()[0],5)

    def test_inquiry_database_error_is_not_success(self):
        with patch('blueprints.admin.advertising_center.save_inquiry',side_effect=sqlite3.OperationalError('test failure')):
            response = self.client.post('/advertise/media-kit/cascade/inquire',data=self.inquiry_payload())
        self.assertEqual(response.status_code,503)
        self.assertIn(b'could not save your request',response.data)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM advertise_sales_leads').fetchone()[0],0)

    def test_proposal_uses_published_annual_price_and_excludes_internal_notes(self):
        location = self.create()
        self.conn.execute("UPDATE advertising_prospects SET notes='PRIVATE INTERNAL NOTE'")
        self.conn.commit()
        result = self.client.get(location+'/proposal?county=Cascade&package=gold&cycle=annual&format=txt')
        self.assertEqual(result.status_code,200)
        self.assertIn(b'$6,110.00 per year, billed annually',result.data)
        self.assertIn(b'/advertise/media-kit/cascade',result.data)
        self.assertNotIn(b'PRIVATE INTERNAL NOTE',result.data)
        self.assertNotIn(b'owner@example.test',result.data)
        self.assertIn('no-store',result.headers['Cache-Control'])
        self.assertEqual(self.conn.execute('SELECT stage FROM advertising_prospects').fetchone()[0],'new')

    def test_proposal_custom_preview_validation_and_auth(self):
        location = self.create()
        result = self.client.get(location+'/proposal')
        self.assertEqual(result.status_code,200)
        self.assertIn(b'to be agreed in writing',result.data)
        self.assertEqual(self.client.get(location+'/proposal?county=invalid').status_code,400)
        self.assertEqual(self.client.get(location+'/proposal?package=unknown').status_code,400)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get(location+'/proposal?format=txt').status_code,302)

    def test_kit_has_share_tools_and_no_private_cache(self):
        response = self.client.get('/advertise/media-kit/cascade')
        self.assertIn(b'Copy media-kit link',response.data)
        self.assertIn(b'Request advertising information',response.data)
        self.assertEqual(response.headers['Cache-Control'],'private, no-store')
