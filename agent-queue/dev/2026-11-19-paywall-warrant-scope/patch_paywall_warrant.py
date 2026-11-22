#!/usr/bin/env python3
"""
Patch: warrant-access scope fix + source-PDF paywall gate (2026-11-19).

Owner decisions (docs/audits/2026-11-19-paywall-audit.md, "Resolutions"):
  * Warrants are included with Pro and sold as the standalone warrant_access
    add-on. Plus does NOT include warrant access.
  * /record/<id>/source enforces the same preview gate as /record/<id>.

Changes:
  app.py
    1. _apply_subscription_stripe_event: stop remapping warrant_access -> plus;
       accept 'warrant_access' in the write set.
    2. public_record_source (/record/<id>/source): enforce
       preview_allowed(resource_type='source_pdf'); redirect to the gated
       record page when the preview budget is exhausted. (Closes bypass H2.)
    3. Remove the shadowed duplicate @app.route('/pricing') (the blueprint
       version in blueprints/bail_bond_ads.py wins; this copy never runs).
  services/monetization/paywall.py
    4. PLAN_HIERARCHY: add 'warrant_access': 0 (recognized, no Plus features).
    5. LEGACY_PLAN_REMAP: drop warrant_access -> plus.
    6. WARRANT_PLANS: {'plus','pro','warrant_access'} -> {'pro','warrant_access'}.
    7. Docstring prices -> live values ($7.99/$79 Plus, $26/$260 Pro).
  blueprints/bail_bond_ads.py
    8. /pricing already_subscribed: check user_has_access('plus') instead of
       user_has_warrant_access() (plain Plus subscribers were told they had no
       subscription).
    9. Legacy POST /checkout/subscription: retired to a redirect onto
       /checkout/<plus|pro>. It was still selling Insider/Professional at the
       old $7.99/$69.99 and $14.99/$129.99 price points via inline price_data.
  tests/test_stripe_checkout_redirects.py
    10. Legacy-checkout test now asserts the redirect mapping.
  tests/test_warrant_scope.py (new): regression coverage for the warrant model.

Run on vm2778:
  cd /opt/montanablotter
  ./venv/bin/python3 agent-queue/dev/2026-11-19-paywall-warrant-scope/patch_paywall_warrant.py

The script verifies every anchor occurs exactly once, leaves
*.bak-20261119-warrant-scope backups, runs py_compile and the paywall/checkout
tests, and does NOT commit — review the diff, then commit and let git_sync.sh
deploy. Afterwards run scripts/maintenance/2026-11-19-fix-warrant-access-plans.py.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
APP = REPO / 'app.py'
PAYWALL = REPO / 'services' / 'monetization' / 'paywall.py'
BBA = REPO / 'blueprints' / 'bail_bond_ads.py'
TEST_REDIRECTS = REPO / 'tests' / 'test_stripe_checkout_redirects.py'
TEST_FILE = REPO / 'tests' / 'test_warrant_scope.py'
BACKUP_SUFFIX = '.bak-20261119-warrant-scope'

APP_EDITS = [
    # 1a. Webhook: keep warrant_access as its own plan (no write-time remap).
    (
        """    # Backward compat: map legacy plan names to new tiers
    if plan == 'warrant_access':
        plan = 'plus'
    elif plan == 'insider':
        plan = 'plus'
    elif plan == 'professional':
        plan = 'pro'
""",
        """    # Backward compat: map legacy plan names to new tiers.
    # warrant_access is NOT remapped (owner decision 2026-11-19): it is a
    # standalone warrants-only add-on plan and is stored as-is.
    if plan == 'insider':
        plan = 'plus'
    elif plan == 'professional':
        plan = 'pro'
""",
    ),
    # 1b. Webhook: accept warrant_access in the write set.
    (
        "        if public_user_id.isdigit() and plan in {'plus', 'pro'}:",
        "        if public_user_id.isdigit() and plan in {'plus', 'pro', 'warrant_access'}:",
    ),
    # 2. Source-PDF gate (audit 2026-11-19, bypass H2).
    (
        """    source_pdf_name = _detail_source_pdf_name(record)
    conn.close()

    if not record or record['post_id'] is None:
        return render_template('404.html'), 404

    record_title = record['incident_type'] or record['incident'] or 'Incident Record'
""",
        """    source_pdf_name = _detail_source_pdf_name(record)
    conn.close()

    if not record or record['post_id'] is None:
        return render_template('404.html'), 404

    # Paywall: source PDFs are the pre-redaction originals — full-detail
    # content. Enforce the same preview gate as /record/<id> and bounce
    # exhausted viewers to the gated record page (audit 2026-11-19, bypass H2).
    paywall_allowed, _paywall_counts = preview_allowed(
        resource_type='source_pdf', resource_id=record_id,
    )
    if not paywall_allowed:
        flash('You have reached your free preview limit. Subscribe for full access.', 'info')
        return redirect(f'/record/{record_id}')

    record_title = record['incident_type'] or record['incident'] or 'Incident Record'
""",
    ),
    # 3. Remove shadowed duplicate /pricing route.
    (
        """@app.route('/pricing')
def pricing_page():
    keys = _stripe_keys()
    return render_template(
        'pricing.html',
        stripe_publishable_key=keys['publishable_key'],
        monthly_subscription_link=_monthly_subscription_link(),
        current_year=datetime.now().year,
    )
""",
        """# NOTE: /pricing is served by blueprints/bail_bond_ads.py::pricing_page —
# blueprints register before app.py routes, so a shadowed duplicate
# @app.route('/pricing') here (removed 2026-11-19, paywall audit) never ran.
# Do not re-add it; edit the blueprint version.
""",
    ),
]

PAYWALL_EDITS = [
    # 7. Docstring prices -> live values + warrant tier note.
    (
        """  free (level 0) — public access, limited history, ads, basic alerts
  plus (level 1) — $5.99/mo or $59/yr, 12mo history, 5 counties, watchlists
  pro  (level 2) — $19.99/mo or $199/yr, full archive, statewide, unlimited
""",
        """  free (level 0) — public access, limited history, ads, basic alerts
  plus (level 1) — $7.99/mo or $79/yr, 12mo history, 5 counties, watchlists
  pro  (level 2) — $26/mo or $260/yr, full archive, statewide, unlimited,
                   warrant pages included
  warrant_access (level 0) — standalone warrants-only add-on, no plus features
""",
    ),
    # 4. warrant_access as a first-class plan at level 0.
    (
        """PLAN_HIERARCHY = {
    'free': 0,
    'plus': 1,
    'pro': 2,
}
""",
        """PLAN_HIERARCHY = {
    'free': 0,
    # Standalone warrants-only add-on: a recognized paid plan (so active
    # subscribers resolve and user_has_warrant_access() works) but level 0 —
    # it grants no plus-tier features. Owner decision 2026-11-19.
    'warrant_access': 0,
    'plus': 1,
    'pro': 2,
}
""",
    ),
    # 5. Drop warrant_access -> plus from the read-time remap.
    (
        """# Legacy plan names written by older checkout flows. Remapped at read time to
# the current tiers (mirrors the write-time map in
# app._apply_subscription_stripe_event: insider -> plus, professional -> pro,
# warrant_access -> plus).
LEGACY_PLAN_REMAP = {
    'insider': 'plus',
    'professional': 'pro',
    'warrant_access': 'plus',
}
""",
        """# Legacy plan names written by older checkout flows. Remapped at read time to
# the current tiers (mirrors the write-time map in
# app._apply_subscription_stripe_event: insider -> plus, professional -> pro).
# warrant_access is NOT remapped: since 2026-11-19 it is a first-class
# standalone plan (see PLAN_HIERARCHY), no longer aliased to plus.
LEGACY_PLAN_REMAP = {
    'insider': 'plus',
    'professional': 'pro',
}
""",
    ),
    # 6. Warrant pages: Pro + standalone add-on only.
    (
        """# Backward compat: plans that unlock warrant pages
WARRANT_PLANS = {'plus', 'pro', 'warrant_access'}
""",
        """# Plans that unlock warrant pages (/wanted). Owner decision 2026-11-19:
# warrants are bundled with Pro and sold as the standalone warrant_access
# add-on; Plus does NOT include them.
WARRANT_PLANS = {'pro', 'warrant_access'}
""",
    ),
    # Comment in get_user_plan reflects the new model.
    (
        """            # Warrant access is sold as its own Stripe product but is treated as
            # the 'plus' tier for feature/access purposes (see app.py webhook
            # remap warrant_access -> plus). Resolve it so the gate and feature
            # matrix see a recognized plan.
""",
        """            # warrant_access is a first-class standalone plan (level 0,
            # warrants only); legacy insider/professional names resolve via
            # LEGACY_PLAN_REMAP.
""",
    ),
]

BBA_EDITS = [
    # 8. /pricing banner: Plus-or-higher, not warrant access.
    (
        """@bail_bond_ads_bp.route('/pricing')
def pricing_page():
    from services.monetization.paywall import user_has_warrant_access
    public_user_id = session.get('public_user_id')
    is_logged_in = bool(public_user_id)
    already_subscribed = is_logged_in and user_has_warrant_access()
""",
        """@bail_bond_ads_bp.route('/pricing')
def pricing_page():
    from services.monetization.paywall import user_has_access
    public_user_id = session.get('public_user_id')
    is_logged_in = bool(public_user_id)
    # "Already subscribed" means Plus or higher (paywall audit 2026-11-19 —
    # this used to check warrant access, which plain Plus subscribers lack).
    already_subscribed = is_logged_in and user_has_access('plus')
""",
    ),
]

# 9. Legacy /checkout/subscription: span replacement (decorator line through
# the final redirect of the old body).
LEGACY_CHECKOUT_START = "@bail_bond_ads_bp.route('/checkout/subscription', methods=['POST'])\ndef checkout_subscription():\n"
LEGACY_CHECKOUT_END = "    return redirect(checkout_url)\n"
LEGACY_CHECKOUT_NEW = """@bail_bond_ads_bp.route('/checkout/subscription', methods=['POST'])
def checkout_subscription():
    \"\"\"Legacy endpoint, retired 2026-11-19 (paywall audit).

    This used to build inline Stripe price_data at the old Insider/Professional
    price points ($7.99/$69.99, $14.99/$129.99), undercutting the live tiers
    (Plus $7.99/$79, Pro $26/$260). Redirect onto the current tier checkout,
    mapping legacy plan/interval names.
    \"\"\"
    plan = (request.form.get('plan') or '').strip().lower()
    interval = (request.form.get('interval') or 'monthly').strip().lower()
    plan = {'insider': 'plus', 'professional': 'pro'}.get(plan)
    if not plan:
        return redirect('/pricing')
    interval = 'annual' if interval in {'yearly', 'annual'} else 'monthly'
    return redirect(f'/checkout/{plan}?interval={interval}')
"""

TEST_REDIRECT_EDITS = [
    # 10. Legacy-checkout test asserts the redirect mapping now.
    (
        """    def test_subscription_checkout_redirects_with_dict_session(self):
        fake_session = {
            'id': 'cs_test_subscription',
            'url': 'https://checkout.stripe.com/c/pay/test_subscription',
        }

        with self.client.session_transaction() as session_:
            session_['public_user_id'] = 11

        with patch('blueprints.payments.stripe.checkout.Session.create', return_value=fake_session):
            response = self.client.post(
                '/checkout/subscription',
                data={'plan': 'insider', 'interval': 'monthly'},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], fake_session['url'])""",
        """    def test_subscription_checkout_redirects_with_dict_session(self):
        # /checkout/subscription was retired 2026-11-19: it sold the old
        # Insider/Professional tiers at pre-August prices via inline
        # price_data. It now redirects onto the current tier checkout.
        with self.client.session_transaction() as session_:
            session_['public_user_id'] = 11

        response = self.client.post(
            '/checkout/subscription',
            data={'plan': 'insider', 'interval': 'monthly'},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/checkout/plus?interval=monthly')""",
    ),
]

TEST_SOURCE = """# Warrant-access scope tests (owner decision 2026-11-19).
#
# Model: /wanted pages are included with Pro and with the standalone
# warrant_access add-on. Plus does NOT include warrant access. The standalone
# plan is level 0: it grants no Plus-tier features.
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask, session as flask_session

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestWarrantScope(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test-secret'

        self.pv_fd, self.pv_path = tempfile.mkstemp(suffix='.db')
        self.db_fd, self.db_path = tempfile.mkstemp(suffix='.db')
        self._init_dbs()

        def _tmp_pv():
            conn = sqlite3.connect(self.pv_path)
            conn.row_factory = sqlite3.Row
            return conn

        def _tmp_db():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn

        import services.monetization.paywall as paywall_module
        self._orig_connect_page_views = paywall_module.connect_page_views
        self._orig_get_db = paywall_module.get_db
        paywall_module.connect_page_views = _tmp_pv
        paywall_module.get_db = _tmp_db

        from services.monetization.paywall import (
            get_user_plan,
            user_has_access,
            user_has_warrant_access,
        )
        self.get_user_plan = get_user_plan
        self.user_has_access = user_has_access
        self.user_has_warrant_access = user_has_warrant_access

    def tearDown(self):
        import services.monetization.paywall as paywall_module
        paywall_module.connect_page_views = self._orig_connect_page_views
        paywall_module.get_db = self._orig_get_db
        os.close(self.pv_fd)
        os.unlink(self.pv_path)
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _init_dbs(self):
        conn = sqlite3.connect(self.pv_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS preview_views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                viewer_type TEXT NOT NULL DEFAULT 'anonymous',
                viewer_id TEXT NOT NULL,
                resource_type TEXT NOT NULL DEFAULT 'incident',
                resource_id INTEGER,
                viewed_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.commit()
        conn.close()

        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS public_users (
                id INTEGER PRIMARY KEY,
                email TEXT,
                is_active INTEGER DEFAULT 1,
                is_subscribed INTEGER DEFAULT 0,
                subscriber_plan TEXT DEFAULT 'scout',
                subscription_status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def _add_user(self, plan, status='active'):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                'INSERT INTO public_users (subscriber_plan, subscription_status) VALUES (?, ?)',
                (plan, status),
            ).lastrowid

    def _mock_anon(self):
        user = MagicMock()
        user.is_authenticated = False
        return patch('services.monetization.paywall.current_user', user)

    def _check(self, uid):
        with self.app.test_request_context(), self._mock_anon():
            flask_session['public_user_id'] = uid
            return (
                self.get_user_plan(),
                self.user_has_warrant_access(),
                self.user_has_access('plus'),
            )

    def test_plus_has_no_warrant_access(self):
        plan, warrant, plus = self._check(self._add_user('plus'))
        self.assertEqual(plan, 'plus')
        self.assertTrue(plus)
        self.assertFalse(warrant)

    def test_pro_includes_warrant_access(self):
        plan, warrant, plus = self._check(self._add_user('pro'))
        self.assertEqual(plan, 'pro')
        self.assertTrue(warrant)
        self.assertTrue(plus)

    def test_standalone_warrant_access_plan(self):
        plan, warrant, plus = self._check(self._add_user('warrant_access'))
        self.assertEqual(plan, 'warrant_access')
        self.assertTrue(warrant)
        self.assertFalse(plus)  # warrants only — no Plus-tier features

    def test_canceled_warrant_access_loses_access(self):
        plan, warrant, _plus = self._check(self._add_user('warrant_access', status='canceled'))
        self.assertEqual(plan, 'free')
        self.assertFalse(warrant)

    def test_legacy_plans_still_remap(self):
        plan, warrant, _plus = self._check(self._add_user('insider'))
        self.assertEqual(plan, 'plus')
        self.assertFalse(warrant)
        plan, warrant, _plus = self._check(self._add_user('professional'))
        self.assertEqual(plan, 'pro')
        self.assertTrue(warrant)


if __name__ == '__main__':
    unittest.main()
"""


def fail(msg):
    print(f'FATAL: {msg}')
    sys.exit(1)


def apply_edits(path, edits):
    text = path.read_text()
    for i, (old, new) in enumerate(edits, 1):
        count = text.count(old)
        if count == 0 and new in text:
            print(f'  already applied: edit {i} in {path.name}')
            continue
        if count != 1:
            fail(f'edit {i} anchor occurs {count}x in {path.name} (expected 1). No changes were committed; investigate before retrying.')
        text = text.replace(old, new)
    return text


def replace_span(text, start_marker, end_marker, replacement, label):
    count = text.count(start_marker)
    if count == 0 and replacement in text:
        print(f'  already applied: {label}')
        return text
    if count != 1:
        fail(f'{label}: start anchor occurs {count}x (expected 1).')
    i = text.index(start_marker)
    try:
        j = text.index(end_marker, i) + len(end_marker)
    except ValueError:
        fail(f'{label}: end anchor not found after start.')
    return text[:i] + replacement + text[j:]


def backup_and_write(path, text):
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f'  backup: {backup.name}')
    path.write_text(text)


def main():
    if not (REPO / 'app.py').exists() or not (REPO / 'MISSION.md').exists():
        fail(f'{REPO} does not look like the montanablotter repo root. Run from the deployed checkout.')

    print(f'Repo: {REPO}')

    print('Patching app.py ...')
    backup_and_write(APP, apply_edits(APP, APP_EDITS))

    print('Patching services/monetization/paywall.py ...')
    backup_and_write(PAYWALL, apply_edits(PAYWALL, PAYWALL_EDITS))

    print('Patching blueprints/bail_bond_ads.py ...')
    bba_text = apply_edits(BBA, BBA_EDITS)
    bba_text = replace_span(bba_text, LEGACY_CHECKOUT_START, LEGACY_CHECKOUT_END,
                            LEGACY_CHECKOUT_NEW, 'legacy /checkout/subscription retirement')
    backup_and_write(BBA, bba_text)

    print('Patching tests/test_stripe_checkout_redirects.py ...')
    backup_and_write(TEST_REDIRECTS, apply_edits(TEST_REDIRECTS, TEST_REDIRECT_EDITS))

    if TEST_FILE.exists() and TEST_FILE.read_text() == TEST_SOURCE:
        print(f'  already present: {TEST_FILE.relative_to(REPO)}')
    else:
        TEST_FILE.write_text(TEST_SOURCE)
        print(f'  wrote {TEST_FILE.relative_to(REPO)}')

    print('Syntax check ...')
    for target in (APP, PAYWALL, BBA, TEST_REDIRECTS, TEST_FILE):
        subprocess.run([sys.executable, '-m', 'py_compile', str(target)], check=True, cwd=REPO)
        print(f'  ok: {target.relative_to(REPO)}')

    print('Running paywall + checkout tests ...')
    proc = subprocess.run(
        [sys.executable, '-m', 'pytest',
         'tests/test_warrant_scope.py', 'tests/test_paywall.py', 'tests/test_ad_unlock.py',
         'tests/test_stripe_checkout_redirects.py', '-q'],
        cwd=REPO,
    )
    if proc.returncode != 0:
        fail('tests failed — review output above, fix or roll back (git checkout -- app.py services/monetization/paywall.py blueprints/bail_bond_ads.py tests/test_stripe_checkout_redirects.py; delete tests/test_warrant_scope.py).')

    subprocess.run(['git', 'diff', '--stat'], cwd=REPO)
    print("""
PATCH APPLIED (not committed). Next:
  1. git diff                      # review
  2. git add -A && git commit -m "fix(paywall): scope warrants to Pro + standalone add-on; gate source PDFs"
  3. git_sync.sh pushes + deploys within ~2 min
  4. ./venv/bin/python3 scripts/maintenance/2026-11-19-fix-warrant-access-plans.py          # dry run
  5. ./venv/bin/python3 scripts/maintenance/2026-11-19-fix-warrant-access-plans.py --apply  # restore legacy warrant buyers
""")


if __name__ == '__main__':
    main()
