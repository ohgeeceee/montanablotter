"""
Montana Blotter - Simplified Free & Open Source Version
Public browse + Admin panel only (no memberships)
"""

import os
import sqlite3
import hashlib
import json
import csv
import io
import hmac
import secrets
import smtplib
import urllib.error
import urllib.request
from html import escape
from html.parser import HTMLParser
from email.mime.text import MIMEText
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, Response, session, abort
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import config
from dedupe import incident_key_set
from facebook_publisher import (
    load_facebook_settings,
    mask_token,
    publish_queue_item,
    queue_post,
    queue_recent_posts,
    run_facebook_queue,
    save_facebook_settings,
)

try:
    import stripe
except Exception:
    stripe = None

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
BASE_URL = config.BASE_URL
app.config.update(
    SESSION_COOKIE_HTTPONLY=config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=config.SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
)


def _slugify_key(value):
    import re as _re
    return _re.sub(r'[^a-z0-9]+', '-', (value or '').strip().lower()).strip('-')


COUNTY_DIRECTORY = {
    'beaverhead': {'name': 'Beaverhead', 'roster_url': 'https://beaverheadcountymt.gov/departments/sheriff/', 'phone': '406-683-3700', 'has_online_roster': True},
    'big-horn': {'name': 'Big Horn', 'roster_url': 'https://www.bighorncountymt.gov/239/Detention', 'phone': '406-665-9780', 'has_online_roster': True},
    'blaine': {'name': 'Blaine', 'roster_url': None, 'phone': '406-357-3260', 'has_online_roster': False},
    'broadwater': {'name': 'Broadwater', 'roster_url': 'https://www.broadwatercountysheriff.org/roster.php', 'phone': '406-266-3445', 'has_online_roster': True},
    'carbon': {'name': 'Carbon', 'roster_url': 'https://carbonmt.gov/sheriff/', 'phone': '406-446-1234', 'has_online_roster': True},
    'carter': {'name': 'Carter', 'roster_url': None, 'phone': '406-775-8741', 'has_online_roster': False},
    'cascade': {'name': 'Cascade', 'roster_url': 'https://www.cascadecountymt.gov/314/Inmate-Roster', 'phone': '406-454-6840', 'has_online_roster': True},
    'chouteau': {'name': 'Chouteau', 'roster_url': None, 'phone': '406-622-3660', 'has_online_roster': False},
    'custer': {'name': 'Custer', 'roster_url': 'https://www.custercountysheriff.com/inmate-search', 'phone': '406-874-3300', 'has_online_roster': True},
    'daniels': {'name': 'Daniels', 'roster_url': None, 'phone': '406-487-2691', 'has_online_roster': False},
    'dawson': {'name': 'Dawson', 'roster_url': 'https://www.dawsoncountymontana.com/sheriff', 'phone': '406-377-7600', 'has_online_roster': True},
    'deer-lodge': {'name': 'Deer Lodge', 'roster_url': None, 'phone': '406-563-5421', 'has_online_roster': False},
    'fallon': {'name': 'Fallon', 'roster_url': None, 'phone': '406-778-2879', 'has_online_roster': False},
    'fergus': {'name': 'Fergus', 'roster_url': 'https://fergusmt.gov/detention-center-roster', 'phone': '406-535-3860', 'has_online_roster': True},
    'flathead': {'name': 'Flathead', 'roster_url': 'https://apps.flathead.mt.gov/jailroster/', 'phone': '406-758-5610', 'has_online_roster': True},
    'gallatin': {'name': 'Gallatin', 'roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates', 'phone': '406-582-2100', 'has_online_roster': True},
    'garfield': {'name': 'Garfield', 'roster_url': None, 'phone': '406-557-2540', 'has_online_roster': False},
    'glacier': {'name': 'Glacier', 'roster_url': 'https://glaciercountymt.gov/category/jail-roster/', 'phone': '406-873-4600', 'has_online_roster': True},
    'golden-valley': {'name': 'Golden Valley', 'roster_url': None, 'phone': '406-568-2321', 'has_online_roster': False},
    'granite': {'name': 'Granite', 'roster_url': 'https://granitecountyjail.org/', 'phone': '406-859-3771', 'has_online_roster': True},
    'hill': {'name': 'Hill', 'roster_url': 'https://vinelink.vineapps.com/state/mt', 'phone': '406-265-5481', 'has_online_roster': True},
    'jefferson': {'name': 'Jefferson', 'roster_url': 'https://www.jeffersoncountysheriffmt.gov/', 'phone': '406-225-4075', 'has_online_roster': True},
    'judith-basin': {'name': 'Judith Basin', 'roster_url': None, 'phone': '406-535-3860', 'has_online_roster': False},
    'lake': {'name': 'Lake', 'roster_url': None, 'phone': '406-883-7301', 'has_online_roster': False},
    'lewis-and-clark': {'name': 'Lewis and Clark', 'roster_url': 'https://www.lccountymt.gov/Sheriff/Detention-Center', 'phone': '406-447-8270', 'has_online_roster': True},
    'liberty': {'name': 'Liberty', 'roster_url': None, 'phone': '406-759-5171', 'has_online_roster': False},
    'lincoln': {'name': 'Lincoln', 'roster_url': 'http://inmateroster.lincolncountysheriff.us/', 'phone': '406-293-0242', 'has_online_roster': True},
    'madison': {'name': 'Madison', 'roster_url': 'https://webportal.mcits.site/NewWorld.InmateInquiry/MadisonCountyJail', 'phone': '406-843-5351', 'has_online_roster': True},
    'mccone': {'name': 'McCone', 'roster_url': None, 'phone': '406-485-3405', 'has_online_roster': False},
    'meagher': {'name': 'Meagher', 'roster_url': None, 'phone': '406-547-3397', 'has_online_roster': False},
    'mineral': {'name': 'Mineral', 'roster_url': 'https://co.mineral.mt.us/departments/sheriff/', 'phone': '406-822-3534', 'has_online_roster': True},
    'missoula': {'name': 'Missoula', 'roster_url': 'https://webapps.missoulacounty.us/jailroster/Inmates', 'phone': '406-258-4780', 'has_online_roster': True},
    'musselshell': {'name': 'Musselshell', 'roster_url': None, 'phone': '406-323-1122', 'has_online_roster': False},
    'park': {'name': 'Park', 'roster_url': 'https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/', 'phone': '406-222-4172', 'has_online_roster': True},
    'petroleum': {'name': 'Petroleum', 'roster_url': None, 'phone': '406-429-6551', 'has_online_roster': False},
    'phillips': {'name': 'Phillips', 'roster_url': 'https://phillipscosheriff.com/inmates/', 'phone': '406-654-2020', 'has_online_roster': True},
    'pondera': {'name': 'Pondera', 'roster_url': 'https://ponderacountyjail.org/inmate-search/', 'phone': '406-271-4100', 'has_online_roster': True},
    'powder-river': {'name': 'Powder River', 'roster_url': None, 'phone': '406-436-2260', 'has_online_roster': False},
    'powell': {'name': 'Powell', 'roster_url': 'https://www.powellcountymt.gov/sheriff/page/detention-facility', 'phone': '406-846-2711', 'has_online_roster': True},
    'prairie': {'name': 'Prairie', 'roster_url': None, 'phone': '406-635-5738', 'has_online_roster': False},
    'ravalli': {'name': 'Ravalli', 'roster_url': 'https://ravallicounty.gov/239/Adult-Detention-Center', 'phone': '406-375-4060', 'has_online_roster': True},
    'richland': {'name': 'Richland', 'roster_url': None, 'phone': '406-433-2919', 'has_online_roster': False},
    'roosevelt': {'name': 'Roosevelt', 'roster_url': None, 'phone': '406-653-6230', 'has_online_roster': False},
    'rosebud': {'name': 'Rosebud', 'roster_url': None, 'phone': '406-346-2715', 'has_online_roster': False},
    'sanders': {'name': 'Sanders', 'roster_url': 'https://sanders-mt.publiclogs.com/', 'phone': '406-827-3584', 'has_online_roster': True},
    'sheridan': {'name': 'Sheridan', 'roster_url': None, 'phone': '406-765-1200', 'has_online_roster': False},
    'silver-bow': {'name': 'Silver Bow', 'roster_url': 'https://co.silverbow.mt.us/3274/Detention-Center', 'phone': '406-497-1120', 'has_online_roster': True},
    'stillwater': {'name': 'Stillwater', 'roster_url': None, 'phone': '406-322-5326', 'has_online_roster': False},
    'sweet-grass': {'name': 'Sweet Grass', 'roster_url': None, 'phone': '406-932-5143', 'has_online_roster': False},
    'teton': {'name': 'Teton', 'roster_url': None, 'phone': '406-466-5781', 'has_online_roster': False},
    'toole': {'name': 'Toole', 'roster_url': None, 'phone': '406-434-5585', 'has_online_roster': False},
    'treasure': {'name': 'Treasure', 'roster_url': None, 'phone': '406-342-5211', 'has_online_roster': False},
    'valley': {'name': 'Valley', 'roster_url': 'https://www.valleycountymt.gov/1288/Jail-Roster', 'phone': '406-228-9355', 'has_online_roster': True},
    'wheatland': {'name': 'Wheatland', 'roster_url': None, 'phone': '406-632-4311', 'has_online_roster': False},
    'wibaux': {'name': 'Wibaux', 'roster_url': None, 'phone': '406-796-2415', 'has_online_roster': False},
    'yellowstone': {'name': 'Yellowstone', 'roster_url': 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp', 'phone': '406-256-2929', 'has_online_roster': True},
}

NEW_CITY_SLUGS = (
    'laurel',
    'hardin',
    'belgrade',
    'manhattan',
    'whitefish',
    'columbia-falls',
    'east-helena',
    'anaconda',
    'red-lodge',
    'livingston',
)

PATTERN_DEFINITIONS = {
    'dui-activity': {
        'slug': 'dui-activity',
        'label': 'DUI Activity',
        'short_label': 'DUI',
        'hero_label': 'Montana DUI Pattern Page',
        'description': 'Track DUI-related incident activity, repeat locations, and recent public records tied to impaired driving enforcement.',
        'title_statewide': 'Montana DUI Activity',
        'title_county': '{county} County DUI Activity',
        'meta_statewide': 'Track Montana DUI activity with recent records, top counties, and direct paths into county-level public safety pages.',
        'meta_county': 'Track DUI activity in {county} County with recent records, related reports, and direct links into county-level Montana public safety pages.',
        'intro': 'These pages surface DUI-related public records so readers can quickly move from a broad pattern into the exact county records behind it.',
        'terms': ['dui', 'driving under the influence', 'impaired driving'],
        'faq_name': 'What does the DUI activity page track?',
        'faq_answer': 'It tracks visible Montana Blotter records that reference DUI or driving-under-the-influence activity in the indexed archive.',
    },
    'warrant-related-arrests': {
        'slug': 'warrant-related-arrests',
        'label': 'Warrant-Related Arrests',
        'short_label': 'Warrants',
        'hero_label': 'Montana Warrant Pattern Page',
        'description': 'Find recent records that reference warrant service, warrant arrests, or active warrant activity in county-level public records.',
        'title_statewide': 'Montana Warrant-Related Arrests',
        'title_county': '{county} County Warrant-Related Arrests',
        'meta_statewide': 'Browse Montana warrant-related arrest records with top counties, recent entries, and direct links into county warrant and arrest resources.',
        'meta_county': 'Browse warrant-related arrests in {county} County with recent records, county warrant resources, and linked public safety coverage.',
        'intro': 'These pages are built for readers who want a narrower path into warrant-linked activity than a general arrest log provides.',
        'terms': ['warrant', 'warrant arrest', 'arrest warrant', 'bench warrant', 'served warrant'],
        'faq_name': 'Does a warrant-related arrest page replace the official warrant list?',
        'faq_answer': 'No. It summarizes visible public records that mention warrant activity. Official county warrant lists and court records remain the final source.',
    },
    'domestic-disturbance': {
        'slug': 'domestic-disturbance',
        'label': 'Domestic Disturbance',
        'short_label': 'Domestic',
        'hero_label': 'Montana Domestic Disturbance Pattern Page',
        'description': 'Follow domestic disturbance and partner-or-family-violence related records across Montana counties and local archives.',
        'title_statewide': 'Montana Domestic Disturbance Activity',
        'title_county': '{county} County Domestic Disturbance Activity',
        'meta_statewide': 'Track domestic disturbance-related public records in Montana with top counties, recent entries, and linked county archive pages.',
        'meta_county': 'Track domestic disturbance activity in {county} County with recent records, related reports, and direct links into county-level archives.',
        'intro': 'These pages group domestic-disturbance-related records into a dedicated local archive for readers following recurring family or household call patterns.',
        'terms': ['domestic', 'domestic violence', 'partner/family', 'partner family', 'family member assault', 'pfma'],
        'faq_name': 'What counts as domestic disturbance activity here?',
        'faq_answer': 'It includes visible records that reference domestic disturbances, domestic violence, partner or family-related assault language, or PFMA-related terminology in the indexed archive.',
    },
}

# Apply DB migrations at startup
from init_db import migrate as _migrate
_migrate()
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'

# File upload configuration
ALLOWED_EXTENSIONS = {'pdf'}
app.config['UPLOAD_FOLDER'] = config.UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = config.MAX_UPLOAD_MB * 1024 * 1024

_ADMIN_CSRF_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
_SAFE_URL_SCHEMES = {'http', 'https', 'mailto', 'tel'}
_ALLOWED_MARKDOWN_TAGS = {
    'a', 'p', 'br', 'hr',
    'strong', 'em', 'blockquote',
    'ul', 'ol', 'li',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'code', 'pre',
}
_ALLOWED_MARKDOWN_ATTRS = {
    'a': {'href', 'title', 'target', 'rel'},
    'code': {'class'},
    'pre': {'class'},
}


def _csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


class _SafeHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def _append_start_tag(self, tag: str, attrs):
        allowed = _ALLOWED_MARKDOWN_ATTRS.get(tag, set())
        cleaned = {}
        for key, value in attrs:
            if key not in allowed or value is None:
                continue
            attr_val = value.strip()
            if key == 'href':
                parsed = urlparse(attr_val)
                scheme = parsed.scheme.lower()
                if scheme and scheme not in _SAFE_URL_SCHEMES:
                    continue
                if attr_val.lower().startswith(('javascript:', 'data:', 'vbscript:')):
                    continue
            if key == 'target' and attr_val not in {'_blank', '_self'}:
                continue
            cleaned[key] = attr_val

        if tag == 'a' and cleaned.get('target') == '_blank':
            rel_tokens = set((cleaned.get('rel') or '').split())
            rel_tokens.update({'noopener', 'noreferrer'})
            cleaned['rel'] = ' '.join(sorted(rel_tokens))

        attrs_html = ''.join(
            f' {name}="{escape(val, quote=True)}"'
            for name, val in cleaned.items()
        )
        self.parts.append(f'<{tag}{attrs_html}>')

    def handle_starttag(self, tag, attrs):
        if tag in _ALLOWED_MARKDOWN_TAGS:
            self._append_start_tag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _ALLOWED_MARKDOWN_TAGS and tag != 'br' and tag != 'hr':
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        self.parts.append(escape(data))

    def handle_entityref(self, name):
        self.parts.append(f'&{name};')

    def handle_charref(self, name):
        self.parts.append(f'&#{name};')

    def get_html(self):
        return ''.join(self.parts)


def _sanitize_html(html_text: str) -> str:
    sanitizer = _SafeHTMLSanitizer()
    sanitizer.feed(html_text or '')
    sanitizer.close()
    return sanitizer.get_html()


@app.before_request
def enforce_admin_csrf():
    if request.method not in _ADMIN_CSRF_METHODS:
        return
    if not request.path.startswith('/admin'):
        return

    session_token = session.get('_csrf_token')
    submitted_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not session_token or not submitted_token or not hmac.compare_digest(session_token, submitted_token):
        wants_json = bool(
            request.is_json
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.accept_mimetypes.best == 'application/json'
        )
        if wants_json:
            return jsonify({'ok': False, 'error': 'csrf_validation_failed'}), 400

        flash('Security token validation failed. Refresh the page and try again.', 'error')
        if current_user.is_authenticated:
            return redirect(request.referrer or url_for('admin_dashboard'))
        return redirect(url_for('admin_login'))


@app.after_request
def apply_security_headers(response):
    if request.path.startswith('/admin'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'

    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', config.X_FRAME_OPTIONS)
    response.headers.setdefault('Referrer-Policy', config.REFERRER_POLICY)
    if config.CONTENT_SECURITY_POLICY:
        response.headers.setdefault('Content-Security-Policy', config.CONTENT_SECURITY_POLICY)
    if request.path.startswith('/api/') and request.method in {'GET', 'HEAD', 'OPTIONS'}:
        allow_origin = (getattr(config, 'API_CORS_ALLOW_ORIGIN', '*') or '*').strip()
        response.headers.setdefault('Access-Control-Allow-Origin', allow_origin)
        response.headers.setdefault('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        response.headers.setdefault('Access-Control-Allow-Headers', 'Content-Type')
    return response


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': _csrf_token}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.template_filter('to_iso_date')
def to_iso_date(date_str):
    """Convert MM/DD/YY or MM/DD/YYYY to YYYY-MM-DD for share URLs."""
    for fmt in ('%m/%d/%y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(date_str or '', fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            pass
    return date_str or ''

def get_db():
    timeout_seconds = float(getattr(config, 'DB_TIMEOUT_SECONDS', 30))
    busy_timeout_ms = int(getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000))
    conn = sqlite3.connect(config.DB_PATH, timeout=timeout_seconds)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f'PRAGMA busy_timeout = {busy_timeout_ms}')
    conn.row_factory = sqlite3.Row
    return conn


def _client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return (request.remote_addr or '').strip()


def _parse_sqlite_timestamp(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _login_rate_limited(conn, username: str, ip_address: str):
    window = f'-{config.ADMIN_LOGIN_WINDOW_MINUTES} minutes'
    row = conn.execute(
        '''
        SELECT COUNT(*) AS failures, MAX(created_at) AS last_failure
        FROM auth_login_attempts
        WHERE success = 0
          AND created_at >= datetime('now', ?)
          AND (username = ? OR ip_address = ?)
        ''',
        (window, username, ip_address),
    ).fetchone()

    failures = int(row['failures'] or 0)
    if failures < config.ADMIN_LOGIN_MAX_ATTEMPTS:
        return False, 0

    last_failure = _parse_sqlite_timestamp(row['last_failure'])
    if not last_failure:
        return True, config.ADMIN_LOGIN_LOCKOUT_MINUTES * 60

    unlock_time = last_failure + timedelta(minutes=config.ADMIN_LOGIN_LOCKOUT_MINUTES)
    remaining = int((unlock_time - datetime.utcnow()).total_seconds())
    if remaining > 0:
        return True, remaining
    return False, 0


def _record_login_attempt(conn, username: str, ip_address: str, success: bool):
    conn.execute(
        '''
        INSERT INTO auth_login_attempts (username, ip_address, success)
        VALUES (?, ?, ?)
        ''',
        (username, ip_address, 1 if success else 0),
    )
    conn.commit()


def _county_slug_for_name(name):
    if not name:
        return None
    target = name.strip().lower()
    for slug, county in COUNTY_DATA.items():
        if county['name'].strip().lower() == target:
            return slug
    return None


def _city_slug_for_name(name):
    if not name:
        return None
    target = name.strip().lower()
    for slug, city in CITY_DATA.items():
        if city['name'].strip().lower() == target:
            return slug
    return None


def _summary_lines(summary):
    return [line.strip() for line in (summary or '').split('\n') if line.strip()]


def _safe_json_loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _donations_enabled():
    return bool(getattr(config, 'DONATIONS_ENABLED', False))


def _donation_currency():
    return (getattr(config, 'DONATION_CURRENCY', 'usd') or 'usd').strip().lower() or 'usd'


def _donation_min_cents():
    try:
        value = int(getattr(config, 'DONATION_MIN_CENTS', 500))
    except (TypeError, ValueError):
        value = 500
    return max(100, value)


def _donation_max_cents():
    return max(_donation_min_cents(), 500000)


def _allowed_donation_amounts():
    raw = getattr(config, 'DONATION_SUGGESTED_AMOUNTS_CENTS', (500, 1500, 2500, 5000))
    amounts = []
    for value in raw:
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if _donation_min_cents() <= amount <= _donation_max_cents():
            amounts.append(amount)
    if not amounts:
        amounts = [amount for amount in (500, 1500, 2500, 5000) if amount >= _donation_min_cents()]
    return tuple(sorted(set(amounts)))


def _stripe_keys():
    return {
        'secret_key': (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip(),
        'publishable_key': (getattr(config, 'STRIPE_PUBLISHABLE_KEY', '') or '').strip(),
        'webhook_secret': (getattr(config, 'STRIPE_WEBHOOK_SECRET', '') or '').strip(),
    }


def _stripe_ready_for_checkout():
    keys = _stripe_keys()
    return bool(stripe and keys['secret_key'] and keys['publishable_key'])


def _stripe_ready_for_webhooks():
    keys = _stripe_keys()
    return bool(stripe and keys['secret_key'] and keys['webhook_secret'])


def _donation_email_hash(email):
    normalized = (email or '').strip().lower()
    if not normalized:
        return ''
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _bail_ad_packages():
    return [
        {
            'id': 'featured_bondsman_banner',
            'name': 'Featured Bondsman',
            'price_monthly_cents': 45000,
            'price_annual_cents': 540000,
            'county_slots': 0,
            'badge': 'Top Banner Placement',
            'short_description': 'Premium header placement on all arrest feeds.',
            'full_description': 'Secure the most prominent real estate on MontanaBlotter.com. This 970x250 header banner keeps your agency first in view for visitors checking the latest arrests, including mobile click-to-call support.',
            'features': [
                '970x250 premium header inventory',
                'Top-of-feed visibility across arrest pages',
                'Mobile-first click-to-call CTA',
            ],
        },
        {
            'id': 'emergency_call_sidebar',
            'name': 'Emergency Call Sidebar',
            'price_monthly_cents': 30000,
            'price_annual_cents': 360000,
            'county_slots': 0,
            'badge': 'Sticky Sidebar Placement',
            'short_description': 'Persistent sidebar placement that stays visible as users scroll.',
            'full_description': 'Stay top of mind on individual arrest records. This 300x600 sticky unit remains visible during scroll and is optimized for mobile tap-to-call conversion.',
            'features': [
                '300x600 sticky sidebar inventory',
                'Persistent exposure while readers scroll',
                'High-intent call conversion placement',
            ],
        },
        {
            'id': 'exclusive_county_sponsorship',
            'name': 'Exclusive County Sponsorship',
            'price_monthly_cents': 15000,
            'price_annual_cents': 180000,
            'county_slots': 1,
            'badge': 'Exclusive County Placement',
            'price_label_monthly': '$150-$350/month (tiered by county)',
            'short_description': 'Dedicated sponsorship of a specific Montana County feed.',
            'full_description': 'Own the local conversation in one county feed with exclusive sponsorship placement above county arrests. One agency per county, with hyper-local reach to residents searching in their community.',
            'pricing_model': 'county_tiered',
            'features': [
                'One county feed sponsorship slot',
                'County-level exclusive share of voice',
                'Localized contact and branding placement',
            ],
        },
        {
            'id': 'gold_bond_bundle',
            'name': 'The Gold Bond Bundle',
            'price_monthly_cents': 65000,
            'price_annual_cents': 780000,
            'county_slots': 2,
            'badge': 'Top Banner + Sidebar + 2 Counties',
            'short_description': 'The ultimate visibility package: Top Banner + Sidebar + 2 Counties.',
            'full_description': 'Dominate MontanaBlotter with multi-touch placement. Includes Featured Bondsman top banner, Emergency Call sticky sidebar, and exclusive sponsorship in two counties of your choice, with bundled pricing built in.',
            'features': [
                'Featured Bondsman top banner placement',
                'Emergency Call sticky sidebar placement',
                'Exclusive sponsorship in two county feeds',
                'Includes 15% bundled discount value',
            ],
        },
        {
            'id': 'silver_link',
            'name': 'The Silver Link',
            'price_monthly_cents': 35000,
            'price_annual_cents': 350000,
            'county_slots': 1,
            'badge': 'Sidebar + one county feed',
            'legacy': True,
            'active': False,
            'features': [
                'Emergency Call sticky sidebar ad placement',
                'Sponsored link placement in one county feed',
                'Mobile-first tap-to-call call-to-action',
            ],
        },
        {
            'id': 'gold_bond',
            'name': 'The Gold Bond',
            'price_monthly_cents': 65000,
            'price_annual_cents': 650000,
            'county_slots': 2,
            'badge': 'Top banner + sidebar + 2 counties',
            'legacy': True,
            'active': False,
            'features': [
                'Featured Bondsman top banner placement',
                'Emergency Call sticky sidebar placement',
                'Sponsored coverage in two county feeds',
            ],
        },
        {
            'id': 'state_power',
            'name': 'The State Power',
            'price_monthly_cents': 150000,
            'price_annual_cents': 1500000,
            'county_slots': len(COUNTY_DATA),
            'all_counties': True,
            'badge': 'Statewide takeover package',
            'legacy': True,
            'active': False,
            'features': [
                'Top banner placement on all pages',
                'Emergency Call sticky sidebar placement',
                'County coverage across all Montana counties',
            ],
        },
    ]


def _bail_ad_public_packages():
    return [pkg for pkg in _bail_ad_packages() if pkg.get('active', True)]


def _bail_ad_package_aliases():
    return {
        'starter': 'exclusive_county_sponsorship',
        'growth': 'gold_bond_bundle',
        'dominance': 'gold_bond_bundle',
        'featured': 'featured_bondsman_banner',
        'sidebar': 'emergency_call_sidebar',
        'county': 'exclusive_county_sponsorship',
        'gold_bundle': 'gold_bond_bundle',
    }


def _normalize_bail_ad_package_id(raw_value):
    token = (raw_value or '').strip().lower()
    if not token:
        return ''
    return _bail_ad_package_aliases().get(token, token)


def _bail_ad_package_lookup():
    lookup = {pkg['id']: pkg for pkg in _bail_ad_packages()}
    for legacy_id, package_id in _bail_ad_package_aliases().items():
        package = lookup.get(package_id)
        if package:
            lookup[legacy_id] = package
    return lookup


def _bail_ad_addons():
    return [
        {
            'id': 'in_feed_integration',
            'name': 'In-Feed Integration',
            'description': 'Sponsored in-feed placement every 5th or 10th arrest entry.',
            'price_monthly_cents': 20000,
            'price_annual_cents': 200000,
        },
    ]


def _bail_ad_addon_lookup():
    return {addon['id']: addon for addon in _bail_ad_addons()}


def _parse_addon_ids(raw_values):
    lookup = _bail_ad_addon_lookup()
    out = []
    seen = set()
    for raw in raw_values or []:
        token = (raw or '').strip().lower()
        if token in lookup and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _parse_budget_cents(raw_value):
    token = (raw_value or '').strip().replace('$', '').replace(',', '')
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    cents = int(round(value * 100))
    if cents <= 0:
        return None
    return min(cents, 100000000)


def _bail_ad_checkout_ready():
    return _stripe_ready_for_checkout()


def _bail_ad_allowed_asset(filename):
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in {'png', 'jpg', 'jpeg', 'webp', 'gif'}


def _parse_county_targets(raw_value):
    raw = (raw_value or '').replace('\n', ',').replace(';', ',')
    county_lookup = {county['name'].lower(): county['name'] for county in COUNTY_DATA.values()}
    slug_lookup = {slug.lower(): county['name'] for slug, county in COUNTY_DATA.items()}
    parsed = []
    seen = set()
    for token in raw.split(','):
        value = token.strip()
        if not value:
            continue
        key = value.lower()
        normalized = county_lookup.get(key) or slug_lookup.get(key) or value[:64]
        slug_key = normalized.lower()
        if slug_key in seen:
            continue
        seen.add(slug_key)
        parsed.append(normalized)
        if len(parsed) >= max(12, len(COUNTY_DATA)):
            break
    return parsed


def _all_bail_counties():
    counties = []
    seen = set()
    for county in getattr(config, 'MONTANA_COUNTIES', []) or []:
        name = (county or '').strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        counties.append(name)
    for county in COUNTY_DATA.values():
        name = (county.get('name') or '').strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        counties.append(name)
    return sorted(counties)


def _normalize_bail_county(raw_value):
    value = (raw_value or '').strip()
    if not value:
        return ''
    lower_map = {county.lower(): county for county in _all_bail_counties()}
    slug_map = {_slugify_key(county): county for county in _all_bail_counties()}
    token = value.lower()
    if token in lower_map:
        return lower_map[token]
    if token in slug_map:
        return slug_map[token]
    slug = _slugify_key(value)
    if slug in slug_map:
        return slug_map[slug]
    return value[:80]


def _format_phone_for_tel(raw_phone):
    token = ''.join(ch for ch in (raw_phone or '') if ch.isdigit())
    if len(token) == 10:
        token = f'1{token}'
    if len(token) < 11:
        return ''
    return f'+{token}'


def _bail_help_contact(default_phone=''):
    phone = (getattr(config, 'BAIL_HELP_PHONE', '') or '').strip()
    sms_number = (getattr(config, 'BAIL_HELP_SMS', '') or '').strip()
    chat_url = (getattr(config, 'BAIL_HELP_CHAT_URL', '') or '').strip()

    if not phone:
        phone = (default_phone or '').strip()
    if not sms_number:
        sms_number = phone

    tel_href = _format_phone_for_tel(phone)
    sms_href = _format_phone_for_tel(sms_number)
    return {
        'phone': phone,
        'phone_display': phone or 'Call',
        'tel_href': f'tel:{tel_href}' if tel_href else '',
        'sms_href': f'sms:{sms_href}' if sms_href else '',
        'chat_url': chat_url,
    }


def _ensure_bail_consumer_lead_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_consumer_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            county TEXT NOT NULL,
            jail_facility TEXT,
            callback_preference TEXT,
            notes TEXT,
            source TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            routed_order_ids TEXT,
            routed_business_names TEXT,
            routed_emails TEXT,
            routed_phones TEXT,
            ip_hash TEXT,
            referrer TEXT,
            review_notes TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_created ON bail_consumer_leads(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_status ON bail_consumer_leads(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_county ON bail_consumer_leads(county)')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_consumer_lead_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            event_type TEXT NOT NULL,
            county TEXT,
            source TEXT,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES bail_consumer_leads(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_created ON bail_consumer_lead_events(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_type ON bail_consumer_lead_events(event_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_county ON bail_consumer_lead_events(county)')


def _record_bail_consumer_event(conn, event_type, county='', source='', lead_id=None):
    safe_event = (event_type or '').strip().lower()[:40]
    if not safe_event:
        return
    safe_county = _normalize_bail_county(county)[:80]
    safe_source = (source or '').strip()[:80]
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    conn.execute(
        '''
        INSERT INTO bail_consumer_lead_events (lead_id, event_type, county, source, ip_hash, referrer)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            int(lead_id) if lead_id else None,
            safe_event,
            safe_county,
            safe_source,
            ip_hash,
            referrer,
        ),
    )


def _bail_lead_notify_recipients():
    recipients = []
    configured = getattr(config, 'BAIL_LEAD_NOTIFY_EMAILS', ()) or ()
    if isinstance(configured, str):
        configured = [part.strip() for part in configured.split(',') if part.strip()]
    for entry in configured:
        email = (entry or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)
    if not recipients:
        fallback = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
        if fallback and '@' in fallback:
            recipients.append(fallback)
    return recipients


def _send_bail_lead_notification_email(to_emails, subject, body):
    recipients = []
    for value in to_emails or []:
        email = (value or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)
    if not recipients:
        return False

    smtp_user = (getattr(config, 'SMTP_USER', '') or '').strip()
    smtp_password = (getattr(config, 'SMTP_PASSWORD', '') or '').strip()
    smtp_server = (getattr(config, 'SMTP_SERVER', '') or '').strip()
    smtp_port = int(getattr(config, 'SMTP_PORT', 587) or 587)
    if not (smtp_user and smtp_password and smtp_server):
        return False

    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = ', '.join(recipients)
    try:
        smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(smtp_user, recipients, msg.as_string())
        smtp.quit()
        return True
    except Exception:
        return False


def _post_bail_lead_webhook(payload):
    webhook_url = (getattr(config, 'BAIL_LEAD_WEBHOOK_URL', '') or '').strip()
    if not webhook_url:
        return False
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=8):
            return True
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def _active_bail_ad_listings(conn):
    rows = conn.execute(
        '''
        SELECT
            bail_ad_orders.id,
            bail_ad_orders.business_name,
            bail_ad_orders.phone,
            bail_ad_orders.email,
            bail_ad_orders.website_url,
            bail_ad_orders.county_targets,
            bail_ad_orders.package_id,
            bail_ad_orders.status,
            bail_ad_creatives.headline,
            bail_ad_creatives.body_copy,
            bail_ad_creatives.cta_text,
            bail_ad_creatives.target_url,
            bail_ad_creatives.logo_path
        FROM bail_ad_orders
        LEFT JOIN bail_ad_creatives ON bail_ad_creatives.order_id = bail_ad_orders.id
        WHERE bail_ad_orders.status IN ('active', 'active_pending_creative_review')
          AND (bail_ad_creatives.status = 'approved' OR bail_ad_creatives.status IS NULL)
        ORDER BY datetime(bail_ad_orders.paid_at) DESC, datetime(bail_ad_orders.created_at) DESC
        '''
    ).fetchall()

    listings = []
    for row in rows:
        county_list = _bail_ad_county_list(row['county_targets'])
        phone_value = (row['phone'] or '').strip()
        phone_token = _format_phone_for_tel(phone_value)
        listings.append({
            'id': row['id'],
            'business_name': row['business_name'],
            'phone': phone_value,
            'phone_href': f"tel:{phone_token}" if phone_token else '',
            'sms_href': f"sms:{phone_token}" if phone_token else '',
            'email': row['email'],
            'website_url': row['website_url'],
            'counties': county_list,
            'package_id': row['package_id'],
            'status': row['status'],
            'headline': row['headline'] or f"{row['business_name']} Bail Bonds",
            'body_copy': row['body_copy'] or 'Licensed local bail bond support available.',
            'cta_text': row['cta_text'] or 'Contact Now',
            'target_url': row['target_url'] or row['website_url'] or '',
            'logo_path': row['logo_path'] or '',
        })
    return listings


def _bail_county_sections(listings, selected_county=''):
    selected = _normalize_bail_county(selected_county)
    by_county = {}
    for listing in listings:
        counties = listing.get('counties') or ['Statewide']
        for county in counties:
            normalized_county = _normalize_bail_county(county) or county
            if selected and normalized_county != selected:
                continue
            by_county.setdefault(normalized_county, []).append(listing)
    return [{'county': county, 'listings': values} for county, values in sorted(by_county.items())]


def _bail_lead_routing_targets(listings, county):
    normalized_county = _normalize_bail_county(county)
    targets = []
    for listing in listings:
        target_counties = listing.get('counties') or []
        if target_counties and normalized_county and normalized_county not in target_counties:
            continue
        targets.append(listing)
    return targets[:3]


def _bail_advertiser_attribution_30d(conn, limit=120):
    calls_by_order = {
        int(row['order_id']): int(row['calls'] or 0)
        for row in conn.execute(
            '''
            SELECT order_id, COUNT(*) AS calls
            FROM bail_ad_events
            WHERE order_id IS NOT NULL
              AND event_type IN ('call', 'lead')
              AND created_at >= date('now', '-30 days')
            GROUP BY order_id
            '''
        ).fetchall()
        if row['order_id'] is not None
    }
    texts_by_order = {
        int(row['order_id']): int(row['texts'] or 0)
        for row in conn.execute(
            '''
            SELECT order_id, COUNT(*) AS texts
            FROM bail_ad_events
            WHERE order_id IS NOT NULL
              AND event_type = 'text'
              AND created_at >= date('now', '-30 days')
            GROUP BY order_id
            '''
        ).fetchall()
        if row['order_id'] is not None
    }

    routed_by_order = {}
    for row in conn.execute(
        '''
        SELECT routed_order_ids, status
        FROM bail_consumer_leads
        WHERE created_at >= date('now', '-30 days')
        '''
    ).fetchall():
        order_ids = []
        for token in (row['routed_order_ids'] or '').split(','):
            clean = token.strip()
            if not clean:
                continue
            try:
                value = int(clean)
            except ValueError:
                continue
            if value > 0:
                order_ids.append(value)
        for order_id in sorted(set(order_ids)):
            stats_bucket = routed_by_order.setdefault(order_id, {'routed': 0, 'qualified': 0, 'booked': 0})
            stats_bucket['routed'] += 1
            if (row['status'] or '').strip().lower() in {'qualified', 'booked'}:
                stats_bucket['qualified'] += 1
            if (row['status'] or '').strip().lower() == 'booked':
                stats_bucket['booked'] += 1

    pipeline_order_ids = set(calls_by_order.keys()) | set(texts_by_order.keys()) | set(routed_by_order.keys())
    order_lookup = {}
    if pipeline_order_ids:
        placeholders = ','.join('?' for _ in sorted(pipeline_order_ids))
        for row in conn.execute(
            f'''
            SELECT id, business_name, package_id, status, county_targets
            FROM bail_ad_orders
            WHERE id IN ({placeholders})
            ''',
            tuple(sorted(pipeline_order_ids)),
        ).fetchall():
            order_lookup[int(row['id'])] = dict(row)

    out = []
    for order_id in sorted(pipeline_order_ids):
        order_info = order_lookup.get(order_id) or {}
        routed = int((routed_by_order.get(order_id) or {}).get('routed') or 0)
        qualified = int((routed_by_order.get(order_id) or {}).get('qualified') or 0)
        booked = int((routed_by_order.get(order_id) or {}).get('booked') or 0)
        calls = int(calls_by_order.get(order_id, 0) or 0)
        texts = int(texts_by_order.get(order_id, 0) or 0)
        out.append({
            'order_id': order_id,
            'business_name': order_info.get('business_name') or f'Order #{order_id}',
            'package_id': order_info.get('package_id') or '',
            'status': order_info.get('status') or '',
            'county_targets': order_info.get('county_targets') or '',
            'calls': calls,
            'texts': texts,
            'routed_leads': routed,
            'qualified_leads': qualified,
            'booked_bonds': booked,
            'qualified_rate_pct': (qualified / routed * 100.0) if routed else 0.0,
            'booked_rate_pct': (booked / qualified * 100.0) if qualified else 0.0,
        })
    out.sort(
        key=lambda item: (
            item['booked_bonds'],
            item['qualified_leads'],
            item['routed_leads'],
            item['calls'],
            item['texts'],
        ),
        reverse=True,
    )
    return out[:max(1, int(limit or 120))]


_BAIL_OUTREACH_STATUSES = {
    'new',
    'queued',
    'contacted',
    'replied',
    'meeting_scheduled',
    'closed_won',
    'closed_lost',
    'do_not_contact',
}


def _crm_phone_token(raw_phone):
    digits = ''.join(ch for ch in (raw_phone or '') if ch.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def _bail_agency_dedupe_key(agency_name, email, phone):
    agency_token = _slugify_key(agency_name)[:80]
    email_token = (email or '').strip().lower()[:160]
    phone_token = _crm_phone_token(phone)
    if not agency_token:
        return ''
    return f'{agency_token}|{email_token}|{phone_token}'


def _ensure_bail_agency_outreach_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_agency_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL UNIQUE,
            agency_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            counties TEXT,
            source TEXT,
            outreach_status TEXT NOT NULL DEFAULT 'new',
            last_contacted_at TEXT,
            next_follow_up_at TEXT,
            owner TEXT,
            email_subject_template TEXT,
            email_body_template TEXT,
            call_script_template TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_status ON bail_agency_outreach(outreach_status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_followup ON bail_agency_outreach(next_follow_up_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_name ON bail_agency_outreach(agency_name)')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_agency_email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER,
            agency_name TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            email_kind TEXT NOT NULL,
            subject TEXT,
            body_preview TEXT,
            sent_by TEXT,
            send_status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES bail_agency_outreach(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_created ON bail_agency_email_logs(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_agency ON bail_agency_email_logs(agency_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_status ON bail_agency_email_logs(send_status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_kind ON bail_agency_email_logs(email_kind)')


def _log_bail_agency_email(
    conn,
    agency_id,
    agency_name,
    recipient_email,
    email_kind,
    subject,
    body_preview,
    sent_by,
    send_status,
    error_message='',
):
    conn.execute(
        '''
        INSERT INTO bail_agency_email_logs (
            agency_id, agency_name, recipient_email, email_kind, subject, body_preview, sent_by, send_status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            int(agency_id) if agency_id else None,
            (agency_name or '').strip()[:160],
            (recipient_email or '').strip().lower()[:160],
            (email_kind or '').strip().lower()[:32],
            (subject or '').strip()[:500],
            (body_preview or '').strip()[:1200],
            (sent_by or '').strip()[:120],
            (send_status or '').strip().lower()[:32],
            (error_message or '').strip()[:500],
        ),
    )


def _seed_bail_agency_outreach(conn):
    rows = conn.execute(
        '''
        SELECT business_name AS agency_name, contact_name, email, phone, counties_served AS counties, source
        FROM bail_ad_inquiries
        WHERE business_name IS NOT NULL AND business_name != ''
        UNION ALL
        SELECT business_name AS agency_name, contact_name, email, phone, county_targets AS counties, source
        FROM bail_ad_orders
        WHERE business_name IS NOT NULL AND business_name != ''
        '''
    ).fetchall()

    for row in rows:
        agency_name = (row['agency_name'] or '').strip()[:160]
        if not agency_name:
            continue
        contact_name = (row['contact_name'] or '').strip()[:120]
        email = (row['email'] or '').strip().lower()[:160]
        phone = (row['phone'] or '').strip()[:40]
        counties = (row['counties'] or '').strip()[:500]
        source = (row['source'] or '').strip()[:80]
        dedupe_key = _bail_agency_dedupe_key(agency_name, email, phone)
        if not dedupe_key:
            continue

        conn.execute(
            '''
            INSERT INTO bail_agency_outreach (
                dedupe_key, agency_name, contact_name, email, phone, counties, source, outreach_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT(dedupe_key) DO UPDATE SET
                contact_name = CASE
                    WHEN (bail_agency_outreach.contact_name IS NULL OR bail_agency_outreach.contact_name = '')
                         AND excluded.contact_name != '' THEN excluded.contact_name
                    ELSE bail_agency_outreach.contact_name
                END,
                email = CASE
                    WHEN (bail_agency_outreach.email IS NULL OR bail_agency_outreach.email = '')
                         AND excluded.email != '' THEN excluded.email
                    ELSE bail_agency_outreach.email
                END,
                phone = CASE
                    WHEN (bail_agency_outreach.phone IS NULL OR bail_agency_outreach.phone = '')
                         AND excluded.phone != '' THEN excluded.phone
                    ELSE bail_agency_outreach.phone
                END,
                counties = CASE
                    WHEN (bail_agency_outreach.counties IS NULL OR bail_agency_outreach.counties = '')
                         AND excluded.counties != '' THEN excluded.counties
                    ELSE bail_agency_outreach.counties
                END,
                source = CASE
                    WHEN (bail_agency_outreach.source IS NULL OR bail_agency_outreach.source = '')
                         AND excluded.source != '' THEN excluded.source
                    ELSE bail_agency_outreach.source
                END,
                updated_at = datetime('now')
            ''',
            (dedupe_key, agency_name, contact_name, email, phone, counties, source),
        )


def _bail_agency_default_templates(agency):
    agency_name = (agency.get('agency_name') or '').strip() or 'Your Agency'
    counties = (agency.get('counties') or '').strip() or 'your target counties'
    subject = f'Quick lead growth plan for {agency_name}'
    body = (
        f"Hi {{contact_name_or_team}},\n\n"
        f"I run growth partnerships for Montana Blotter. We already have high-intent county traffic around {counties}, "
        f"and I wanted to share a simple 30-day plan for {agency_name}.\n\n"
        f"Plan focus:\n"
        f"- More qualified inbound calls from your target counties\n"
        f"- Better speed-to-lead using call/text routing\n"
        f"- Clear weekly reporting on qualified leads and booked bonds\n\n"
        f"If useful, I can send a 10-minute breakdown specific to your coverage area.\n\n"
        f"Thanks,\n"
        f"{{sender_name}}"
    )
    script = (
        f"Hi {{contact_name_or_team}}, this is {{sender_name}} from Montana Blotter.\n"
        f"We help bail bond agencies increase qualified county-level calls.\n"
        f"Quick question: are you currently looking to improve lead quality, volume, or both?\n\n"
        f"If both, I can share a 30-day plan for {agency_name} in {counties}.\n"
        f"It takes 10 minutes to review."
    )
    return {
        'subject': subject,
        'email_body': body,
        'call_script': script,
    }


def _render_bail_template(template_text, context):
    rendered = template_text or ''
    for key, value in context.items():
        rendered = rendered.replace('{{' + key + '}}', value or '')
    return rendered


def _bail_agency_rendered_templates(agency):
    defaults = _bail_agency_default_templates(agency)
    subject_template = (agency.get('email_subject_template') or '').strip() or defaults['subject']
    email_template = (agency.get('email_body_template') or '').strip() or defaults['email_body']
    script_template = (agency.get('call_script_template') or '').strip() or defaults['call_script']
    context = {
        'agency_name': (agency.get('agency_name') or '').strip(),
        'contact_name': (agency.get('contact_name') or '').strip(),
        'contact_name_or_team': (agency.get('contact_name') or '').strip() or 'team',
        'counties': (agency.get('counties') or '').strip() or 'your target counties',
        'sender_name': 'Montana Blotter Team',
        'today_iso': datetime.utcnow().strftime('%Y-%m-%d'),
    }
    return {
        'subject_template': subject_template,
        'email_template': email_template,
        'script_template': script_template,
        'subject_preview': _render_bail_template(subject_template, context),
        'email_preview': _render_bail_template(email_template, context),
        'script_preview': _render_bail_template(script_template, context),
    }


def _default_bail_test_email():
    username_value = (getattr(current_user, 'username', '') or '').strip().lower()
    if username_value and '@' in username_value:
        return username_value
    notify_recipients = _bail_lead_notify_recipients()
    if notify_recipients:
        return notify_recipients[0]
    smtp_user = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
    if smtp_user and '@' in smtp_user:
        return smtp_user
    return ''


def _exclusive_county_tier_monthly_cents(county_name=''):
    county_key = (county_name or '').strip().lower()
    premium_counties = {'yellowstone', 'missoula', 'gallatin'}
    metro_counties = {'cascade', 'flathead', 'lewis and clark', 'lewis & clark'}
    if county_key in premium_counties:
        return 35000
    if county_key in metro_counties:
        return 25000
    return 15000


def _bail_ad_price_cents(package_id, billing_cycle, county_targets=None):
    package = _bail_ad_package_lookup().get(_normalize_bail_ad_package_id(package_id))
    if not package:
        return None

    monthly_cents = int(package.get('price_monthly_cents') or 0)
    if package.get('pricing_model') == 'county_tiered':
        primary_county = ''
        if isinstance(county_targets, (list, tuple)) and county_targets:
            primary_county = county_targets[0]
        elif isinstance(county_targets, str):
            parsed = _parse_county_targets(county_targets)
            primary_county = parsed[0] if parsed else ''
        monthly_cents = _exclusive_county_tier_monthly_cents(primary_county)

    if billing_cycle == 'annual':
        annual_cents = int(package.get('price_annual_cents') or 0)
        if package.get('pricing_model') == 'county_tiered':
            annual_cents = monthly_cents * 12
        return annual_cents or monthly_cents * 12
    return monthly_cents


def _bail_ad_addon_total_cents(addon_ids, billing_cycle):
    lookup = _bail_ad_addon_lookup()
    total = 0
    for addon_id in addon_ids or []:
        addon = lookup.get(addon_id)
        if not addon:
            continue
        if billing_cycle == 'annual':
            total += int(addon.get('price_annual_cents') or 0) or int(addon['price_monthly_cents']) * 10
        else:
            total += int(addon['price_monthly_cents'])
    return total


def _bail_ad_county_list(value):
    raw = (value or '').replace('\n', ',').replace(';', ',')
    out = []
    seen = set()
    for part in raw.split(','):
        token = part.strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token[:64])
    return out


def _upsert_bail_ad_slot_assignments(conn, order_id, county_targets, slot_count):
    if not order_id or slot_count <= 0:
        return 0
    targets = _parse_county_targets(county_targets)
    if not targets:
        return 0

    created = 0
    for county_name in targets[:slot_count]:
        existing = conn.execute(
            'SELECT id FROM bail_ad_slots WHERE order_id = ? AND county = ? LIMIT 1',
            (order_id, county_name),
        ).fetchone()
        if existing:
            conn.execute(
                '''
                UPDATE bail_ad_slots
                SET status = 'active', starts_at = COALESCE(starts_at, datetime('now')), updated_at = datetime('now')
                WHERE id = ?
                ''',
                (existing['id'],),
            )
            continue
        conn.execute(
            '''
            INSERT INTO bail_ad_slots (order_id, county, slot_type, status, starts_at)
            VALUES (?, ?, 'county_feature', 'active', datetime('now'))
            ''',
            (order_id, county_name),
        )
        created += 1
    return created


def _apply_stripe_bail_ad_event(conn, event):
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}
    if (metadata.get('flow') or '').strip() != 'bail_ad':
        return

    if event_type not in {'checkout.session.completed', 'checkout.session.async_payment_succeeded', 'checkout.session.expired', 'checkout.session.async_payment_failed'}:
        return

    session_id = (data_object.get('id') or '').strip()
    if not session_id:
        return

    package_id = _normalize_bail_ad_package_id(metadata.get('package_id'))
    billing_cycle = (metadata.get('billing_cycle') or 'monthly').strip().lower()
    if billing_cycle not in {'monthly', 'annual'}:
        billing_cycle = 'monthly'
    package = _bail_ad_package_lookup().get(package_id)
    if not package:
        return

    mapped_status = {
        'checkout.session.completed': 'active',
        'checkout.session.async_payment_succeeded': 'active',
        'checkout.session.expired': 'canceled',
        'checkout.session.async_payment_failed': 'payment_failed',
    }[event_type]

    raw_county_targets = metadata.get('county_targets') or ''
    if package.get('all_counties') and (raw_county_targets or '').strip().lower() in {'all', 'all_counties', 'statewide'}:
        county_target_values = sorted({county['name'] for county in COUNTY_DATA.values()})
    else:
        county_target_values = _parse_county_targets(raw_county_targets)
    amount_cents = int(data_object.get('amount_total') or 0)
    if amount_cents <= 0:
        amount_cents = _bail_ad_price_cents(package_id, billing_cycle, county_target_values) or 0
    currency = (data_object.get('currency') or 'usd').lower()
    business_name = (metadata.get('business_name') or '').strip()[:120]
    contact_name = (metadata.get('contact_name') or '').strip()[:120]
    email = (metadata.get('email') or '').strip().lower()[:160]
    phone = (metadata.get('phone') or '').strip()[:40]
    website_url = (metadata.get('website_url') or '').strip()[:300]
    license_number = (metadata.get('license_number') or '').strip()[:80]
    county_targets = ', '.join(county_target_values)
    source = (metadata.get('source') or 'bail_ad_checkout').strip()[:80]
    add_on_ids = ','.join(_parse_addon_ids((metadata.get('add_on_ids') or '').split(',')))
    onboarding_token = (metadata.get('onboarding_token') or '').strip()[:64]
    provider_subscription_id = data_object.get('subscription')
    provider_customer_id = data_object.get('customer')

    existing = conn.execute(
        '''
        SELECT id, onboarding_token
        FROM bail_ad_orders
        WHERE provider_session_id = ?
        LIMIT 1
        ''',
        (session_id,),
    ).fetchone()
    if existing and not onboarding_token:
        onboarding_token = existing['onboarding_token'] or ''
    if not onboarding_token:
        onboarding_token = secrets.token_urlsafe(24)

    conn.execute(
        '''
        INSERT INTO bail_ad_orders (
            business_name, contact_name, email, phone, website_url, license_number,
            county_targets, package_id, billing_cycle, amount_cents, currency, source,
            add_on_ids, status, provider, provider_session_id, provider_subscription_id, provider_customer_id,
            onboarding_token, paid_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stripe', ?, ?, ?, ?, ?)
        ON CONFLICT(provider_session_id) DO UPDATE SET
            business_name = excluded.business_name,
            contact_name = excluded.contact_name,
            email = excluded.email,
            phone = excluded.phone,
            website_url = excluded.website_url,
            license_number = excluded.license_number,
            county_targets = excluded.county_targets,
            package_id = excluded.package_id,
            billing_cycle = excluded.billing_cycle,
            amount_cents = CASE WHEN excluded.amount_cents > 0 THEN excluded.amount_cents ELSE bail_ad_orders.amount_cents END,
            currency = excluded.currency,
            source = excluded.source,
            add_on_ids = excluded.add_on_ids,
            status = excluded.status,
            provider_subscription_id = COALESCE(excluded.provider_subscription_id, bail_ad_orders.provider_subscription_id),
            provider_customer_id = COALESCE(excluded.provider_customer_id, bail_ad_orders.provider_customer_id),
            onboarding_token = CASE WHEN excluded.onboarding_token != '' THEN excluded.onboarding_token ELSE bail_ad_orders.onboarding_token END,
            paid_at = CASE WHEN excluded.paid_at IS NOT NULL THEN excluded.paid_at ELSE bail_ad_orders.paid_at END,
            updated_at = datetime('now')
        ''',
        (
            business_name,
            contact_name,
            email,
            phone,
            website_url,
            license_number,
            county_targets,
            package_id,
            billing_cycle,
            amount_cents,
            currency,
            source,
            add_on_ids,
            mapped_status,
            session_id,
            provider_subscription_id,
            provider_customer_id,
            onboarding_token,
            datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if mapped_status == 'active' else None,
        ),
    )

    order_row = conn.execute(
        'SELECT id FROM bail_ad_orders WHERE provider_session_id = ? LIMIT 1',
        (session_id,),
    ).fetchone()
    if mapped_status == 'active' and order_row:
        _upsert_bail_ad_slot_assignments(
            conn,
            order_row['id'],
            county_targets,
            int(package.get('county_slots') or 0),
        )


def _donation_launch_snapshot():
    snapshot = {
        'schema_ready': True,
        'donations_enabled': _donations_enabled(),
        'stripe_checkout_ready': _stripe_ready_for_checkout(),
        'stripe_webhook_ready': _stripe_ready_for_webhooks(),
        'webhook_events_24h': 0,
        'webhook_errors_24h': 0,
        'stale_webhook_events_10m': 0,
        'last_webhook_received_at': None,
        'last_webhook_processed_at': None,
        'launch_ready': False,
    }

    conn = get_db()
    try:
        conn.execute('SELECT 1 FROM donations LIMIT 1').fetchone()
        conn.execute('SELECT 1 FROM payment_webhook_events LIMIT 1').fetchone()

        row = conn.execute(
            '''
            SELECT
                COUNT(*) AS webhook_events_24h,
                COALESCE(SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END), 0) AS webhook_errors_24h,
                COALESCE(SUM(CASE WHEN processed = 0 AND created_at <= datetime('now', '-10 minutes') THEN 1 ELSE 0 END), 0) AS stale_webhook_events_10m,
                MAX(created_at) AS last_webhook_received_at,
                MAX(CASE WHEN processed = 1 AND (error IS NULL OR error = '') THEN processed_at END) AS last_webhook_processed_at
            FROM payment_webhook_events
            WHERE created_at >= datetime('now', '-24 hours')
            '''
        ).fetchone()
        if row:
            snapshot.update(dict(row))
    except sqlite3.OperationalError:
        snapshot['schema_ready'] = False
    finally:
        conn.close()

    snapshot['launch_ready'] = bool(
        snapshot['schema_ready']
        and snapshot['donations_enabled']
        and snapshot['stripe_checkout_ready']
        and snapshot['stripe_webhook_ready']
        and int(snapshot['stale_webhook_events_10m'] or 0) == 0
    )
    return snapshot


def _apply_stripe_event(conn, event, event_source='/webhooks/stripe', event_ip_hash='', event_referrer=''):
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}
    if (metadata.get('flow') or '').strip() == 'bail_ad':
        return
    if not event_type:
        return

    if event_type in {'checkout.session.completed', 'checkout.session.async_payment_succeeded', 'checkout.session.expired', 'checkout.session.async_payment_failed'}:
        session_id = (data_object.get('id') or '').strip()
        if session_id:
            stripe_mode = (data_object.get('mode') or '').strip()
            mode = 'monthly' if stripe_mode == 'subscription' else 'one_time'
            mapped_status = {
                'checkout.session.completed': 'succeeded',
                'checkout.session.async_payment_succeeded': 'succeeded',
                'checkout.session.expired': 'canceled',
                'checkout.session.async_payment_failed': 'failed',
            }[event_type]
            amount_cents = int(data_object.get('amount_total') or 0)
            currency = (data_object.get('currency') or _donation_currency()).lower()
            metadata = data_object.get('metadata') or {}
            source = (metadata.get('source') or '').strip()[:80]
            donor_name = (metadata.get('donor_name') or '').strip()[:120]
            customer_details = data_object.get('customer_details') or {}
            email_hash = _donation_email_hash(customer_details.get('email') or '')
            payment_intent_id = data_object.get('payment_intent')
            subscription_id = data_object.get('subscription')

            existing = conn.execute(
                'SELECT amount_cents FROM donations WHERE provider_session_id = ?',
                (session_id,),
            ).fetchone()
            if amount_cents <= 0 and existing:
                amount_cents = int(existing['amount_cents'] or 0)

            conn.execute(
                '''
                INSERT INTO donations (
                    provider, mode, status, amount_cents, currency, email_hash, donor_name, source,
                    provider_session_id, provider_payment_intent_id, provider_subscription_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_session_id) DO UPDATE SET
                    mode = excluded.mode,
                    status = excluded.status,
                    amount_cents = CASE
                        WHEN excluded.amount_cents > 0 THEN excluded.amount_cents
                        ELSE donations.amount_cents
                    END,
                    currency = excluded.currency,
                    email_hash = CASE
                        WHEN excluded.email_hash != '' THEN excluded.email_hash
                        ELSE donations.email_hash
                    END,
                    donor_name = CASE
                        WHEN excluded.donor_name != '' THEN excluded.donor_name
                        ELSE donations.donor_name
                    END,
                    source = CASE
                        WHEN excluded.source != '' THEN excluded.source
                        ELSE donations.source
                    END,
                    provider_payment_intent_id = COALESCE(excluded.provider_payment_intent_id, donations.provider_payment_intent_id),
                    provider_subscription_id = COALESCE(excluded.provider_subscription_id, donations.provider_subscription_id),
                    updated_at = datetime('now')
                ''',
                (
                    'stripe',
                    mode,
                    mapped_status,
                    amount_cents,
                    currency,
                    email_hash,
                    donor_name,
                    source,
                    session_id,
                    payment_intent_id,
                    subscription_id,
                ),
            )

            if mapped_status in {'succeeded', 'failed', 'canceled'}:
                conn.execute(
                    '''
                    INSERT INTO donation_events (
                        event_type, source, page_path, ip_hash, referrer, amount_cents
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        'checkout_success' if mapped_status == 'succeeded' else 'checkout_cancel',
                        source,
                        (event_source or '/webhooks/stripe')[:255],
                        (event_ip_hash or '')[:64],
                        (event_referrer or '')[:500],
                        amount_cents if amount_cents > 0 else None,
                    ),
                )

    elif event_type == 'charge.refunded':
        payment_intent_id = (data_object.get('payment_intent') or '').strip()
        if payment_intent_id:
            conn.execute(
                '''
                UPDATE donations
                SET status = 'refunded', updated_at = datetime('now')
                WHERE provider = 'stripe' AND provider_payment_intent_id = ?
                ''',
                (payment_intent_id,),
            )


def _iso_lastmod(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(value[:19], fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
    return None


def _build_like_clause(columns, terms):
    clauses = []
    params = []
    for term in terms:
        like_value = f'%{term}%'
        clauses.append('(' + ' OR '.join(f'{column} LIKE ?' for column in columns) + ')')
        params.extend([like_value] * len(columns))
    return ' OR '.join(clauses), params


def _pattern_clause(pattern_slug: str, alias: str = 'records'):
    pattern = PATTERN_DEFINITIONS.get(pattern_slug)
    if not pattern:
        raise KeyError(pattern_slug)
    columns = [
        f'LOWER(COALESCE({alias}.incident_type, \'\'))',
        f'LOWER(COALESCE({alias}.incident, \'\'))',
        f'LOWER(COALESCE({alias}.details, \'\'))',
    ]
    clauses = []
    params = []
    for term in pattern['terms']:
        clauses.append('(' + ' OR '.join(f'{column} LIKE ?' for column in columns) + ')')
        params.extend([f'%{term.lower()}%'] * len(columns))
    return '(' + ' OR '.join(clauses) + ')', params


def _pattern_links_for_county(county_slug: str):
    return [
        {
            'label': pattern['label'],
            'short_label': pattern['short_label'],
            'href': f"/patterns/{pattern['slug']}/{county_slug}",
        }
        for pattern in PATTERN_DEFINITIONS.values()
    ]


def _annotate_posts_for_pattern(conn, posts):
    return _annotate_posts_with_confidence(conn, posts)


def _top_pattern_pages(conn, limit=6):
    candidates = []
    for pattern in PATTERN_DEFINITIONS.values():
        where_sql, params = _pattern_clause(pattern['slug'], 'records')
        rows = conn.execute(
            f'''
            SELECT records.county, COUNT(*) AS record_count, MAX(records.date) AS last_seen
            FROM records
            WHERE {where_sql}
              AND records.county IS NOT NULL
              AND records.county != ''
            GROUP BY records.county
            ORDER BY record_count DESC, records.county ASC
            LIMIT 4
            ''',
            params,
        ).fetchall()
        for row in rows:
            county_slug = _county_slug_for_name(row['county'])
            if not county_slug:
                continue
            candidates.append({
                'pattern_slug': pattern['slug'],
                'pattern_label': pattern['label'],
                'pattern_short_label': pattern['short_label'],
                'county': row['county'],
                'county_slug': county_slug,
                'record_count': row['record_count'],
                'last_seen': row['last_seen'],
                'href': f"/patterns/{pattern['slug']}/{county_slug}",
            })

    candidates.sort(
        key=lambda item: (-item['record_count'], item['pattern_label'], item['county'])
    )
    return candidates[:limit]


def _related_pattern_pages_for_post(records, county_slug=None):
    matched_pages = []
    for pattern in PATTERN_DEFINITIONS.values():
        hit_count = 0
        for record in records:
            haystack_parts = [
                record['incident_type'] if 'incident_type' in record.keys() else '',
                record['incident'] if 'incident' in record.keys() else '',
                record['details'] if 'details' in record.keys() else '',
            ]
            haystack = ' '.join(part for part in haystack_parts if part).lower()
            if any(term.lower() in haystack for term in pattern['terms']):
                hit_count += 1

        if hit_count == 0:
            continue

        href = (
            f"/patterns/{pattern['slug']}/{county_slug}"
            if county_slug else
            f"/patterns/{pattern['slug']}"
        )
        matched_pages.append({
            'label': pattern['label'],
            'short_label': pattern['short_label'],
            'description': pattern['description'],
            'hit_count': hit_count,
            'href': href,
        })

    matched_pages.sort(key=lambda item: (-item['hit_count'], item['label']))
    return matched_pages


def _pattern_target_meta(target_path):
    path = (target_path or '').strip()
    if not path:
        return None
    parsed = urlparse(path)
    clean_path = (parsed.path or path).strip()
    parts = [segment for segment in clean_path.split('/') if segment]
    if len(parts) < 2 or parts[0] != 'patterns':
        return None
    pattern_slug = parts[1]
    if pattern_slug not in PATTERN_DEFINITIONS:
        return None
    county_slug = parts[2] if len(parts) > 2 else None
    return {
        'pattern_slug': pattern_slug,
        'county_slug': county_slug,
        'target_path': clean_path,
    }


def _pattern_page_context(conn, pattern_slug: str, county=None):
    pattern = PATTERN_DEFINITIONS.get(pattern_slug)
    if not pattern:
        return None

    record_where, record_params = _pattern_clause(pattern_slug, 'records')
    where_clauses = [record_where]
    where_params = list(record_params)

    county_name = None
    county_slug = None
    if county:
        county_name = county['name']
        county_slug = county['slug']
        where_clauses.append('records.county = ?')
        where_params.append(county_name)

    where_sql = ' AND '.join(where_clauses)

    total_records = conn.execute(
        f'SELECT COUNT(*) FROM records WHERE {where_sql}',
        where_params,
    ).fetchone()[0]

    last_seen_row = conn.execute(
        f'SELECT MAX(date) AS last_seen FROM records WHERE {where_sql}',
        where_params,
    ).fetchone()
    last_seen = last_seen_row['last_seen'] if last_seen_row else None

    top_incidents = conn.execute(
        f'''
        SELECT COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_type,
               COUNT(*) AS count
        FROM records
        WHERE {where_sql}
        GROUP BY COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident')
        ORDER BY count DESC, incident_type ASC
        LIMIT 8
        ''',
        where_params,
    ).fetchall()

    top_counties = []
    if not county:
        top_counties = conn.execute(
            f'''
            SELECT records.county, COUNT(*) AS count
            FROM records
            WHERE {where_sql}
              AND records.county IS NOT NULL
              AND records.county != ''
            GROUP BY records.county
            ORDER BY count DESC, records.county ASC
            LIMIT 8
            ''',
            where_params,
        ).fetchall()

    recent_records = conn.execute(
        f'''
        SELECT
            records.id,
            records.date,
            records.time,
            COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_label,
            records.location,
            posts.id AS post_id
        FROM records
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE {where_sql}
        ORDER BY records.date DESC, records.time DESC, records.id DESC
        LIMIT 12
        ''',
        where_params,
    ).fetchall()
    recent_records = _annotate_recent_records(conn, recent_records)

    post_params = list(record_params)
    post_sql = f'''
        SELECT DISTINCT posts.*, blotters.county AS blotter_county
        FROM posts
        JOIN records ON records.blotter_id = posts.blotter_id
        JOIN blotters ON blotters.id = posts.blotter_id
        WHERE {record_where}
    '''
    if county_name:
        post_sql += ' AND posts.county = ?'
        post_params.append(county_name)
    post_sql += ' ORDER BY posts.incident_date DESC, posts.created_at DESC LIMIT 8'
    related_posts = conn.execute(post_sql, post_params).fetchall()
    related_posts = _annotate_posts_for_pattern(conn, related_posts)

    sample_counties = []
    if top_counties:
        for row in top_counties:
            slug = _county_slug_for_name(row['county'])
            if slug:
                sample_counties.append({
                    'name': row['county'],
                    'slug': slug,
                    'count': row['count'],
                })

    title = pattern['title_county'].format(county=county_name) if county_name else pattern['title_statewide']
    meta_description = pattern['meta_county'].format(county=county_name) if county_name else pattern['meta_statewide']
    canonical_url = (
        f'{BASE_URL}/patterns/{pattern_slug}/{county_slug}'
        if county_slug else
        f'{BASE_URL}/patterns/{pattern_slug}'
    )

    return {
        'pattern': pattern,
        'county': county,
        'county_name': county_name,
        'county_slug': county_slug,
        'title': title,
        'meta_description': meta_description,
        'canonical_url': canonical_url,
        'total_records': total_records,
        'last_seen': last_seen,
        'top_incidents': top_incidents,
        'top_counties': sample_counties,
        'recent_records': recent_records,
        'related_posts': related_posts,
        'pattern_links': _pattern_links_for_county(county_slug) if county_slug else [],
    }


def _source_channel_meta(post):
    source_key = post['source_type'] or post['blotter_source_type'] or 'pdf'
    labels = {
        'imap_pdf': ('Email PDF', 'Delivered to records@montanablotter.com as a PDF attachment.'),
        'imap_text': ('Email Body', 'Delivered to records@montanablotter.com as text in the email body.'),
        'local_pdf': ('Manual Upload', 'Uploaded manually through the Montana Blotter admin panel.'),
        'crimemapping': ('CrimeMapping', 'Imported from a public CrimeMapping incident feed.'),
        'pdf': ('Imported PDF', 'Imported from a PDF batch in the blotter pipeline.'),
        'text': ('Text Blotter', 'Imported from a plain-text blotter body.'),
    }
    label, description = labels.get(source_key, ('Imported Source', 'Imported into the blotter pipeline.'))
    return {
        'key': source_key,
        'label': label,
        'description': description,
        'received_at': post['source_received_at'] or post['upload_date'],
        'sender': post['source_sender'],
        'subject': post['source_subject'],
        'filename': post['source_filename'] or post['blotter_filename'],
        'extraction_method': post['extraction_method'],
        'warnings': _safe_json_loads(post['extraction_warnings'], []),
    }


def _parse_quality(records, county, extraction_method=None):
    total = len(records)
    if total == 0:
        return {
            'label': 'Unavailable',
            'score': 0,
            'detail': 'No source records were available for this post.',
        }

    unknown_incident = sum(
        1 for record in records
        if not (record['incident_type'] or '').strip()
        or (record['incident_type'] or '').strip().lower() == 'unknown'
    )
    unknown_location = sum(
        1 for record in records
        if not (record['location'] or '').strip()
        or (record['location'] or '').strip().lower() == 'unknown'
    )
    missing_time = sum(1 for record in records if not (record['time'] or '').strip())

    score = 100
    score -= round((unknown_incident / total) * 38)
    score -= round((unknown_location / total) * 28)
    score -= round((missing_time / total) * 14)
    if (county or '').strip().lower() == 'unknown':
        score -= 18
    if extraction_method == 'email_html':
        score -= 10
    score = max(0, min(100, score))

    if score >= 82:
        label = 'High'
    elif score >= 60:
        label = 'Medium'
    else:
        label = 'Low'

    return {
        'label': label,
        'score': score,
        'detail': (
            f"{total - unknown_incident}/{total} incident labels, "
            f"{total - unknown_location}/{total} locations, and "
            f"{total - missing_time}/{total} timestamps parsed cleanly."
        ),
    }


def _infer_summary_method(post, event_details=None):
    details = event_details or {}
    method = details.get('method')
    provider = details.get('provider')
    if method == 'ai_generated':
        provider_label = 'OpenAI' if provider == 'openai' else 'Anthropic' if provider == 'anthropic' else 'LLM'
        return {
            'label': f'{provider_label} summary',
            'detail': 'The summary text was generated from the indexed incidents using the publishing model pipeline.',
        }
    if method == 'skipped_duplicate_post':
        overlap_ratio = details.get('overlap_ratio')
        overlap_pct = f"{round(float(overlap_ratio) * 100)}%" if overlap_ratio is not None else 'high'
        return {
            'label': 'Duplicate post suppressed',
            'detail': f'A near-identical post already existed for this incident set ({overlap_pct} overlap).',
        }

    summary = (post['summary'] or '').strip().lower()
    if 'responded to the following incidents:' in summary:
        return {
            'label': 'Fallback digest',
            'detail': 'Published from the built-in incident formatter without an LLM rewrite.',
        }

    return {
        'label': 'AI-generated summary',
        'detail': 'Published from the structured summarizer path.',
    }


def _cross_source_overlap(conn, post, records):
    keys = incident_key_set(records, county=post['county'])
    if not keys or not post['incident_date'] or not post['county']:
        return {'label': 'Not enough data', 'detail': 'This post does not have enough structured data for overlap scoring.'}

    candidates = conn.execute(
        '''
        SELECT id, title, blotter_id
        FROM posts
        WHERE county = ? AND incident_date = ? AND id != ?
        ORDER BY created_at DESC
        LIMIT 8
        ''',
        (post['county'], post['incident_date'], post['id']),
    ).fetchall()

    best_match = None
    best_overlap = 0.0
    for candidate in candidates:
        sibling_records = conn.execute(
            '''
            SELECT cfs_number, date, time,
                   COALESCE(incident_type, incident, '') AS incident_type,
                   COALESCE(location, '') AS location,
                   COALESCE(details, '') AS details,
                   county
            FROM records
            WHERE blotter_id = ?
            ''',
            (candidate['blotter_id'],),
        ).fetchall()
        sibling_keys = incident_key_set(sibling_records, county=post['county'])
        if not sibling_keys:
            continue
        overlap = len(keys & sibling_keys) / max(len(keys), 1)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = candidate

    if best_overlap >= 0.7 and best_match:
        return {
            'label': 'Potential overlap',
            'detail': f"About {round(best_overlap * 100)}% of this incident set overlaps with post #{best_match['id']}.",
            'status': 'warn',
            'matched_post_id': best_match['id'],
        }
    if best_overlap >= 0.3 and best_match:
        return {
            'label': 'Low overlap',
            'detail': f"Some incident signatures overlap with post #{best_match['id']}, but not enough to treat it as a duplicate.",
            'status': 'ok',
            'matched_post_id': best_match['id'],
        }
    return {
        'label': 'Passed',
        'detail': 'No materially similar public post was found for the same county and incident date.',
        'status': 'ok',
    }


def _build_provenance_card(conn, post, records):
    source_meta = _source_channel_meta(post)
    ingestion_job = None
    pipeline_events = []
    event_map = {}

    if post['source_document_id']:
        ingestion_job = conn.execute(
            '''
            SELECT id, status, retry_count, last_error, started_at, finished_at
            FROM ingestion_jobs
            WHERE source_document_id = ?
            ''',
            (post['source_document_id'],),
        ).fetchone()
        if ingestion_job:
            pipeline_events = conn.execute(
                '''
                SELECT stage, status, details_json, created_at
                FROM pipeline_events
                WHERE ingestion_job_id = ?
                ORDER BY id
                ''',
                (ingestion_job['id'],),
            ).fetchall()
            for event in pipeline_events:
                event_map[event['stage']] = {
                    'status': event['status'],
                    'details': _safe_json_loads(event['details_json'], {}),
                    'created_at': event['created_at'],
                }

    parse_quality = _parse_quality(records, post['county'], source_meta['extraction_method'])
    summary_meta = _infer_summary_method(post, event_map.get('summary_method', {}).get('details'))
    duplicate_meta = _cross_source_overlap(conn, post, records)

    normalize_details = event_map.get('normalize', {}).get('details', {})
    skipped_duplicates = normalize_details.get('duplicate_incidents_skipped')
    if skipped_duplicates is not None and duplicate_meta.get('status') != 'warn':
        duplicate_meta = {
            'label': 'Passed',
            'detail': (
                f"{skipped_duplicates} incident(s) matched earlier records and were skipped before publication."
                if skipped_duplicates
                else 'No incoming incidents matched an earlier source record during publication.'
            ),
            'status': 'ok',
        }

    audit_details = event_map.get('audit', {}).get('details', {})
    flagged_count = int(audit_details.get('flagged', 0) or 0)

    confidence_score = parse_quality['score']
    source_key = source_meta['key']
    if source_key in ('imap_pdf', 'local_pdf', 'crimemapping'):
        confidence_score += 8
    elif source_key in ('imap_text', 'text'):
        confidence_score += 2
    if flagged_count == 0:
        confidence_score += 5
    if duplicate_meta.get('status') == 'warn':
        confidence_score -= 20
    if ingestion_job and ingestion_job['retry_count']:
        confidence_score -= min(10, ingestion_job['retry_count'] * 3)
    if (post['county'] or '').strip().lower() == 'unknown':
        confidence_score -= 10
    confidence_score = max(0, min(100, confidence_score))

    if confidence_score >= 82:
        confidence_label = 'High confidence'
    elif confidence_score >= 60:
        confidence_label = 'Medium confidence'
    else:
        confidence_label = 'Limited confidence'

    return {
        'confidence_label': confidence_label,
        'confidence_score': confidence_score,
        'source': source_meta,
        'parse_quality': parse_quality,
        'duplicate_checks': duplicate_meta,
        'summary_method': summary_meta,
        'audit_flagged_count': flagged_count,
        'ingestion_status': ingestion_job['status'] if ingestion_job else 'published',
        'retry_count': ingestion_job['retry_count'] if ingestion_job else 0,
        'last_error': ingestion_job['last_error'] if ingestion_job else None,
        'stages': event_map,
    }


def _build_record_provenance_card(conn, record, blotter_records):
    record_context = {
        'id': record['post_id'] or record['id'],
        'county': record['county'],
        'incident_date': record['incident_date'] or record['date'],
        'summary': None,
        'source_type': record['source_type'],
        'blotter_source_type': record['blotter_source_type'],
        'source_sender': record['source_sender'],
        'source_subject': record['source_subject'],
        'source_received_at': record['source_received_at'],
        'source_filename': record['source_filename'],
        'blotter_filename': record['blotter_filename'],
        'upload_date': record['upload_date'],
        'source_document_id': record['source_document_id'],
        'extraction_method': record['extraction_method'],
        'extraction_warnings': record['extraction_warnings'],
    }
    card = _build_provenance_card(conn, record_context, blotter_records)
    if record['post_title']:
        card['summary_method']['detail'] += f" Linked report: {record['post_title']}."
    return card


def _confidence_badge_meta(card):
    score = card['confidence_score']
    if score >= 82:
        tone = 'high'
        bg_class = 'bg-emerald-100'
        text_class = 'text-emerald-700'
    elif score >= 60:
        tone = 'medium'
        bg_class = 'bg-amber-100'
        text_class = 'text-amber-700'
    else:
        tone = 'limited'
        bg_class = 'bg-rose-100'
        text_class = 'text-rose-700'
    return {
        'tone': tone,
        'label': card['confidence_label'],
        'score': score,
        'source_label': card['source']['label'],
        'bg_class': bg_class,
        'text_class': text_class,
    }


def _annotate_recent_records(conn, recent_records):
    if not recent_records:
        return []

    record_ids = [int(record['id']) for record in recent_records]
    placeholders = ','.join('?' for _ in record_ids)
    detail_rows = conn.execute(
        f'''
        SELECT records.id,
               records.blotter_id,
               records.county,
               records.date,
               records.time,
               blotters.filename AS blotter_filename,
               blotters.upload_date,
               blotters.source_type AS blotter_source_type,
               blotters.source_document_id,
               posts.id AS post_id,
               posts.title AS post_title,
               posts.incident_date,
               source_documents.source_type,
               source_documents.source_sender,
               source_documents.source_subject,
               source_documents.source_received_at,
               source_documents.filename AS source_filename,
               source_documents.extraction_method,
               source_documents.extraction_warnings
        FROM records
        LEFT JOIN blotters ON blotters.id = records.blotter_id
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        LEFT JOIN source_documents ON source_documents.id = blotters.source_document_id
        WHERE records.id IN ({placeholders})
        ''',
        record_ids,
    ).fetchall()
    detail_map = {int(row['id']): row for row in detail_rows}

    blotter_ids = sorted({int(row['blotter_id']) for row in detail_rows if row['blotter_id'] is not None})
    blotter_cache = {}
    if blotter_ids:
        placeholders = ','.join('?' for _ in blotter_ids)
        batch_rows = conn.execute(
            f'''
            SELECT blotter_id,
                   cfs_number, date, time,
                   COALESCE(incident_type, incident, '') AS incident_type,
                   COALESCE(location, '') AS location,
                   COALESCE(details, '') AS details,
                   county
            FROM records
            WHERE blotter_id IN ({placeholders})
            ORDER BY date, time, id
            ''',
            blotter_ids,
        ).fetchall()
        for row in batch_rows:
            blotter_cache.setdefault(int(row['blotter_id']), []).append(row)

    enriched = []
    for record in recent_records:
        item = dict(record)
        detail = detail_map.get(int(record['id']))
        if detail:
            card = _build_record_provenance_card(
                conn,
                detail,
                blotter_cache.get(int(detail['blotter_id']), []),
            )
            item['confidence_badge'] = _confidence_badge_meta(card)
        else:
            item['confidence_badge'] = {
                'tone': 'limited',
                'label': 'Limited confidence',
                'score': 0,
                'source_label': 'Unknown source',
                'bg_class': 'bg-rose-100',
                'text_class': 'text-rose-700',
            }
        enriched.append(item)
    return enriched


def _annotate_posts_with_confidence(conn, posts):
    if not posts:
        return []

    post_ids = [int(post['id']) for post in posts]
    placeholders = ','.join('?' for _ in post_ids)
    detail_rows = conn.execute(
        f'''
        SELECT posts.*,
               blotters.filename AS blotter_filename,
               blotters.file_path,
               blotters.upload_date,
               blotters.incident_count,
               blotters.source_type AS blotter_source_type,
               blotters.source_document_id,
               source_documents.source_type,
               source_documents.source_sender,
               source_documents.source_subject,
               source_documents.source_received_at,
               source_documents.filename AS source_filename,
               source_documents.extraction_method,
               source_documents.extraction_warnings
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        LEFT JOIN source_documents ON source_documents.id = blotters.source_document_id
        WHERE posts.id IN ({placeholders})
        ''',
        post_ids,
    ).fetchall()
    detail_map = {int(row['id']): row for row in detail_rows}

    blotter_ids = sorted({int(row['blotter_id']) for row in detail_rows if row['blotter_id'] is not None})
    blotter_cache = {}
    if blotter_ids:
        placeholders = ','.join('?' for _ in blotter_ids)
        batch_rows = conn.execute(
            f'''
            SELECT blotter_id,
                   cfs_number, date, time,
                   COALESCE(incident_type, incident, '') AS incident_type,
                   COALESCE(location, '') AS location,
                   COALESCE(details, '') AS details,
                   county
            FROM records
            WHERE blotter_id IN ({placeholders})
            ORDER BY date, time, id
            ''',
            blotter_ids,
        ).fetchall()
        for row in batch_rows:
            blotter_cache.setdefault(int(row['blotter_id']), []).append(row)

    enriched = []
    for post in posts:
        item = dict(post)
        detail = detail_map.get(int(post['id']))
        if detail:
            card = _build_provenance_card(
                conn,
                detail,
                blotter_cache.get(int(detail['blotter_id']), []),
            )
            item['confidence_badge'] = _confidence_badge_meta(card)
        else:
            item['confidence_badge'] = {
                'tone': 'limited',
                'label': 'Limited confidence',
                'score': 0,
                'source_label': 'Unknown source',
                'bg_class': 'bg-rose-100',
                'text_class': 'text-rose-700',
            }
        enriched.append(item)
    return enriched


def _latest_weekly_digest(conn):
    return conn.execute(
        """
        SELECT id, title, slug, excerpt, author, created_at
        FROM blog_posts
        WHERE published = 1
          AND slug LIKE 'montana-weekly-county-digest-%'
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()


def _city_directory_listing(conn):
    cities = []
    for city in CITY_DATA.values():
        where_sql, params = _build_like_clause(
            ['posts.city', 'posts.agency_name'],
            city['search_terms'],
        )
        rec_where_sql, rec_params = _build_like_clause(
            ['records.location'],
            city['search_terms'],
        )

        post_count = conn.execute(
            f'SELECT COUNT(*) FROM posts WHERE {where_sql}',
            params,
        ).fetchone()[0]
        last_row = conn.execute(
            f'SELECT incident_date FROM posts WHERE {where_sql} ORDER BY incident_date DESC LIMIT 1',
            params,
        ).fetchone()
        record_count = conn.execute(
            f'SELECT COUNT(*) FROM records WHERE {rec_where_sql}',
            rec_params,
        ).fetchone()[0]

        cities.append({
            **city,
            'post_count': post_count,
            'record_count': record_count,
            'last_report': last_row['incident_date'] if last_row else None,
        })

    cities.sort(key=lambda city: (-city['post_count'], city['name']))
    return cities


def _featured_city_pages(cities, limit=None):
    city_lookup = {city['slug']: city for city in cities}
    featured = [
        city_lookup[slug]
        for slug in NEW_CITY_SLUGS
        if slug in city_lookup
    ]
    featured.sort(
        key=lambda city: (-city['record_count'], -city['post_count'], city['name'])
    )
    if limit is not None:
        return featured[:limit]
    return featured


def _parse_record_date(value):
    for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime((value or '').strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _weekly_snapshot(conn, window_days=7):
    raw_dates = {}
    for row in conn.execute(
        """
        SELECT DISTINCT date
        FROM records
        WHERE date IS NOT NULL AND TRIM(date) != ''
        """
    ).fetchall():
        normalized = _parse_record_date(row['date'])
        if normalized is None:
            continue
        raw_dates.setdefault(normalized, set()).add(row['date'])

    if not raw_dates:
        return None

    end_date = max(raw_dates)
    start_date = end_date - timedelta(days=window_days - 1)
    window_raw_dates = sorted(
        raw
        for normalized, originals in raw_dates.items()
        if start_date <= normalized <= end_date
        for raw in originals
    )
    if not window_raw_dates:
        return None

    placeholders = ','.join('?' for _ in window_raw_dates)
    total_records = conn.execute(
        f'SELECT COUNT(*) FROM records WHERE date IN ({placeholders})',
        window_raw_dates,
    ).fetchone()[0]

    top_counties = []
    for row in conn.execute(
        f"""
        SELECT COALESCE(NULLIF(county, ''), 'Unknown') AS county, COUNT(*) AS count
        FROM records
        WHERE date IN ({placeholders})
        GROUP BY COALESCE(NULLIF(county, ''), 'Unknown')
        ORDER BY count DESC, county ASC
        LIMIT 5
        """,
        window_raw_dates,
    ).fetchall():
        top_counties.append({
            'name': row['county'],
            'count': row['count'],
            'slug': _county_slug_for_name(row['county']),
        })

    top_incidents = [
        {'name': row['incident_type'] or 'Incident', 'count': row['count']}
        for row in conn.execute(
            f"""
            SELECT COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident') AS incident_type,
                   COUNT(*) AS count
            FROM records
            WHERE date IN ({placeholders})
            GROUP BY COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident')
            ORDER BY count DESC, incident_type ASC
            LIMIT 6
            """,
            window_raw_dates,
        ).fetchall()
    ]

    return {
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'total_records': total_records,
        'top_counties': top_counties,
        'top_incidents': top_incidents,
    }


@app.context_processor
def inject_public_nav():
    public_primary_nav_items = [
        {'id': 'home', 'href': '/', 'label': 'Home'},
        {'id': 'arrests', 'href': '/arrests', 'label': 'Arrests'},
        {'id': 'counties', 'href': '/counties', 'label': 'Counties'},
        {'id': 'jail_rosters', 'href': '/jail-rosters', 'label': 'Jail Rosters'},
        {'id': 'advertise', 'href': '/advertise/bail-bonds', 'label': 'Advertise'},
    ]
    public_secondary_nav_items = []
    public_footer_items = [
        {'href': '/', 'label': 'Home'},
        {'href': '/arrests', 'label': 'Arrests'},
        {'href': '/counties', 'label': 'Counties'},
        {'href': '/jail-rosters', 'label': 'Jail Rosters'},
        {'href': '/advertise/bail-bonds', 'label': 'Advertise'},
        {'href': '/subscribe', 'label': 'Subscribe'},
        {'href': '/terms-of-use', 'label': 'Terms'},
        {'href': '/privacy', 'label': 'Privacy'},
    ]
    footer_featured_city_items = []
    return {
        'public_primary_nav_items': public_primary_nav_items,
        'public_secondary_nav_items': public_secondary_nav_items,
        'public_footer_items': public_footer_items,
        'footer_featured_city_items': footer_featured_city_items,
        'current_year': datetime.now().year,
    }

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    res = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if res:
        return User(res['id'], res['username'])
    return None

# ==========================================
# PAGE VIEW TRACKING
# ==========================================

@app.before_request
def track_page_view():
    """Log public page views for visitor analytics (admin routes and static files excluded)."""
    if request.path.startswith('/admin') or request.path.startswith('/static'):
        return
    if request.path in (
        '/api/pattern-click',
        '/api/subscribe-event',
        '/api/donate-event',
        '/api/donate/create-checkout-session',
        '/webhooks/stripe',
        '/favicon.ico',
        '/feed.xml',
        '/robots.txt',
        '/sitemap.xml',
        '/sitemap-static.xml',
        '/sitemap-locations.xml',
        '/sitemap-posts.xml',
        '/sitemap-blog.xml',
    ):
        return
    ip_hash = hashlib.sha256((request.remote_addr or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    try:
        conn = get_db()
        conn.execute(
            'INSERT INTO page_views (path, ip_hash, referrer) VALUES (?, ?, ?)',
            (request.path, ip_hash, referrer)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Never break the site for analytics


def _record_subscribe_event(event_type, source='', page_path='', email=''):
    event_type = (event_type or '').strip()[:40]
    if not event_type:
        return
    safe_source = (source or '').strip()[:80]
    safe_path = (page_path or request.path or '')[:255]
    referrer = (request.referrer or '')[:500]
    ip_hash = hashlib.sha256((request.remote_addr or '').encode()).hexdigest()[:16]
    email_hash = ''
    if email:
        email_hash = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO subscribe_events (
                event_type, source, page_path, ip_hash, referrer, email_hash
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (event_type, safe_source, safe_path, ip_hash, referrer, email_hash),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _record_donation_event(event_type, source='', page_path='', amount_cents=None):
    event_type = (event_type or '').strip()[:40]
    if not event_type:
        return

    safe_source = (source or '').strip()[:80]
    safe_path = (page_path or request.path or '')[:255]
    referrer = (request.referrer or '')[:500]
    ip_hash = hashlib.sha256((request.remote_addr or '').encode()).hexdigest()[:16]
    amount_value = None
    if amount_cents is not None:
        try:
            parsed = int(amount_cents)
            if 0 < parsed <= _donation_max_cents():
                amount_value = parsed
        except (TypeError, ValueError):
            amount_value = None

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO donation_events (
                event_type, source, page_path, ip_hash, referrer, amount_cents
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (event_type, safe_source, safe_path, ip_hash, referrer, amount_value),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


@app.route('/api/pattern-click', methods=['POST'])
def track_pattern_click():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    placement = (payload.get('placement') or '').strip()[:80]
    meta = _pattern_target_meta(payload.get('target_path') or '')
    if not placement or not meta:
        return ('', 204)

    ip_hash = hashlib.sha256((request.remote_addr or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    source_path = (payload.get('source_path') or request.headers.get('X-Source-Path') or '')[:255]

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO pattern_clicks (
                pattern_slug, county_slug, target_path, placement, source_path, ip_hash, referrer
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                meta['pattern_slug'],
                meta['county_slug'],
                meta['target_path'],
                placement,
                source_path,
                ip_hash,
                referrer,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return ('', 204)


@app.route('/api/subscribe-event', methods=['POST'])
def track_subscribe_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip()
    if event_type not in {'cta_click', 'form_submit'}:
        return ('', 204)

    source = payload.get('source') or ''
    page_path = payload.get('page_path') or request.path
    _record_subscribe_event(event_type, source=source, page_path=page_path)
    return ('', 204)


@app.route('/api/donate-event', methods=['POST'])
def track_donate_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip()
    if event_type not in {'donate_view', 'cta_click', 'checkout_start', 'checkout_success', 'checkout_cancel'}:
        return ('', 204)

    source = payload.get('source') or ''
    page_path = payload.get('page_path') or request.path
    amount_cents = payload.get('amount_cents')
    _record_donation_event(event_type, source=source, page_path=page_path, amount_cents=amount_cents)
    return ('', 204)


@app.route('/api/bail-ads/event', methods=['POST'])
def track_bail_ad_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip().lower()
    if event_type not in {'impression', 'click', 'lead', 'call', 'text'}:
        return ('', 204)

    try:
        order_id = int(payload.get('order_id') or 0)
    except (TypeError, ValueError):
        order_id = 0
    try:
        slot_id = int(payload.get('slot_id') or 0)
    except (TypeError, ValueError):
        slot_id = 0
    county = (payload.get('county') or '').strip()[:80]
    source = (payload.get('source') or '').strip()[:80]
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO bail_ad_events (order_id, slot_id, event_type, county, source, ip_hash, referrer)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                order_id if order_id > 0 else None,
                slot_id if slot_id > 0 else None,
                event_type,
                county,
                source,
                ip_hash,
                referrer,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return ('', 204)


@app.route('/api/bail-leads/event', methods=['POST'])
def track_bail_consumer_event():
    payload = request.get_json(silent=True)
    if payload is None:
        raw = request.get_data(as_text=True)
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            payload = {}

    event_type = (payload.get('event_type') or '').strip().lower()
    if event_type not in {'directory_view', 'form_view', 'form_submit', 'call_click', 'text_click', 'chat_click'}:
        return ('', 204)

    county = _normalize_bail_county((payload.get('county') or '').strip()[:80])
    source = (payload.get('source') or '').strip()[:80]
    conn = None
    try:
        conn = get_db()
        _ensure_bail_consumer_lead_schema(conn)
        _record_bail_consumer_event(conn, event_type, county=county, source=source)
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    return ('', 204)


# ==========================================
# PUBLIC ROUTES (No Login Required)
# ==========================================

@app.route('/')
def index():
    """Public homepage — daily activity reports with calendar filter"""
    county       = request.args.get('county', '')
    city         = request.args.get('city', '')
    agency_type  = request.args.get('agency_type', '')
    agency       = request.args.get('agency', '')   # specific agency_name
    search_query = request.args.get('q', '')
    date_filter  = request.args.get('date', '')     # expects YYYY-MM-DD
    status_filter = request.args.get('status', '')  # active | pending | resolved
    page         = max(1, request.args.get('page', 1, type=int))
    per_page     = 10

    conn = get_db()

    # Convert YYYY-MM-DD date_filter → MM/DD/YY for DB match
    date_sql_val = ''
    if date_filter:
        try:
            dt = datetime.strptime(date_filter, '%Y-%m-%d')
            date_sql_val = dt.strftime('%m/%d/%y')
        except ValueError:
            date_filter = ''

    sql = """
        SELECT posts.*, blotters.county AS blotter_county, blotters.file_path AS file_path
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        WHERE 1=1
    """
    params = []

    if county:
        sql += " AND posts.county = ?"
        params.append(county)
    if city:
        sql += " AND posts.city LIKE ?"
        params.append(f'%{city}%')
    if agency_type:
        sql += " AND posts.agency_type = ?"
        params.append(agency_type)
    if agency:
        sql += " AND posts.agency_name = ?"
        params.append(agency)
    if search_query:
        st = f'%{search_query}%'
        sql += " AND (posts.title LIKE ? OR posts.summary LIKE ?)"
        params.extend([st, st])
    if date_sql_val:
        sql += " AND posts.incident_date = ?"
        params.append(date_sql_val)
    if status_filter in ('active', 'pending', 'resolved'):
        sql += " AND COALESCE(posts.case_status, 'pending') = ?"
        params.append(status_filter)

    count_sql = f"SELECT COUNT(*) FROM ({sql}) AS post_listing"
    total = conn.execute(count_sql, params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    sql += " ORDER BY posts.incident_date DESC, posts.created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    posts = conn.execute(sql, params).fetchall()
    posts = _annotate_posts_with_confidence(conn, posts)

    # Filter dropdowns
    counties = [r['county'] for r in conn.execute(
        'SELECT DISTINCT county FROM posts ORDER BY county').fetchall()]
    cities = [r['city'] for r in conn.execute(
        "SELECT DISTINCT city FROM posts WHERE city != '' ORDER BY city").fetchall()]

    # Agency directory: each agency with last report date and count
    agencies = conn.execute("""
        SELECT agency_name, agency_type,
               MAX(incident_date) AS last_report,
               COUNT(*) AS report_count
        FROM posts
        WHERE agency_name IS NOT NULL AND agency_name != ''
        GROUP BY agency_name
        ORDER BY last_report DESC
    """).fetchall()

    # Calendar: all dates that have at least one post, normalised to YYYY-MM-DD
    dates_with_posts = []
    for row in conn.execute(
            'SELECT DISTINCT incident_date FROM posts '
            'WHERE incident_date IS NOT NULL AND incident_date != "" '
            'ORDER BY incident_date').fetchall():
        try:
            d = datetime.strptime(row[0], '%m/%d/%y').strftime('%Y-%m-%d')
            dates_with_posts.append(d)
        except ValueError:
            pass

    total_records = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]

    post_stats = {
        row['county']: row
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS post_count, MAX(incident_date) AS last_report
            FROM posts
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }
    record_stats = {
        row['county']: row['record_count']
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS record_count
            FROM records
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }
    top_counties = []
    for county_data in COUNTY_DATA.values():
        stats = post_stats.get(county_data['name'])
        record_count = record_stats.get(county_data['name'], 0)
        post_count = stats['post_count'] if stats else 0
        if not record_count and not post_count:
            continue
        top_counties.append({
            **county_data,
            'record_count': record_count,
            'post_count': post_count,
            'last_report': stats['last_report'] if stats else None,
        })
    top_counties.sort(
        key=lambda item: (-item['record_count'], -item['post_count'], item['name'])
    )
    top_counties = top_counties[:5]
    city_directory_listing = _city_directory_listing(conn)
    featured_new_cities = _featured_city_pages(city_directory_listing)
    latest_weekly_digest = _latest_weekly_digest(conn)
    weekly_snapshot = _weekly_snapshot(conn)
    top_pattern_pages = _top_pattern_pages(conn)

    # Leaderboard: most active agencies this week vs last week
    this_week_rows = conn.execute("""
        SELECT COALESCE(county, 'Unknown') AS county,
               COUNT(*) AS cnt
        FROM records
        WHERE created_at >= datetime('now', '-7 days')
        GROUP BY county ORDER BY cnt DESC LIMIT 6
    """).fetchall()
    prev_week_map = {r['county']: r['cnt'] for r in conn.execute("""
        SELECT COALESCE(county, 'Unknown') AS county, COUNT(*) AS cnt
        FROM records
        WHERE created_at >= datetime('now', '-14 days')
          AND created_at < datetime('now', '-7 days')
        GROUP BY county
    """).fetchall()}
    leaderboard = []
    for r in this_week_rows:
        prev = prev_week_map.get(r['county'], 0)
        trend = 'up' if r['cnt'] > prev else ('down' if r['cnt'] < prev else 'same')
        leaderboard.append({'county': r['county'], 'count': r['cnt'],
                            'prev': prev, 'trend': trend})

    conn.close()

    return render_template('index.html',
                           posts=posts,
                           total=total,
                           total_pages=total_pages,
                           page=page,
                           counties=counties,
                           cities=cities,
                           agencies=agencies,
                           county=county,
                           city=city,
                           agency_type=agency_type,
                           agency=agency,
                           q=search_query,
                           date_filter=date_filter,
                           status_filter=status_filter,
                           dates_with_posts=dates_with_posts,
                           total_records=total_records,
                           top_counties=top_counties,
                           featured_new_cities=featured_new_cities,
                           latest_weekly_digest=latest_weekly_digest,
                           weekly_snapshot=weekly_snapshot,
                           top_pattern_pages=top_pattern_pages,
                           leaderboard=leaderboard,
                           current_year=datetime.now().year)


@app.route('/feed.xml')
def rss_feed():
    """Atom feed of the 20 most recent daily activity reports."""
    conn = get_db()
    posts = conn.execute("""
        SELECT posts.*, blotters.county AS blotter_county
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        ORDER BY posts.incident_date DESC, posts.created_at DESC
        LIMIT 20
    """).fetchall()
    conn.close()

    # Build RFC-3339 timestamps
    def to_rfc3339(date_str, created_at):
        for fmt in ('%m/%d/%y', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str, fmt).strftime('%Y-%m-%dT00:00:00Z')
            except (ValueError, TypeError):
                pass
        try:
            return datetime.strptime(created_at[:19], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%SZ')
        except (ValueError, TypeError):
            pass
        return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    items = []
    for p in posts:
        pub = to_rfc3339(p['incident_date'], p['created_at'])
        summary_snippet = (p['summary'] or '')[:300].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        title = (p['title'] or 'Daily Activity Report').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        agency = (p['agency_name'] or 'Montana Blotter').replace('&', '&amp;')
        link = f"{BASE_URL}/post/{p['id']}"
        items.append(f"""  <entry>
    <title>{title}</title>
    <link href="{link}"/>
    <id>{link}</id>
    <updated>{pub}</updated>
    <author><name>{agency}</name></author>
    <summary type="text">{summary_snippet}</summary>
  </entry>""")

    updated = to_rfc3339(posts[0]['incident_date'], posts[0]['created_at']) if posts else datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Montana Blotter — Daily Activity Reports</title>
  <subtitle>AI-summarized police blotters from Montana law enforcement agencies</subtitle>
  <link href="{BASE_URL}/feed.xml" rel="self"/>
  <link href="{BASE_URL}/"/>
  <id>{BASE_URL}/feed.xml</id>
  <updated>{updated}</updated>
{chr(10).join(items)}
</feed>"""

    return Response(xml, mimetype='application/atom+xml')


def _render_urlset(urls):
    xml_items = []
    for loc, lastmod in urls:
        if lastmod:
            xml_items.append(
                f"<url><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod></url>"
            )
        else:
            xml_items.append(f"<url><loc>{escape(loc)}</loc></url>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + ''.join(xml_items) +
        '</urlset>'
    )
    return Response(xml, mimetype='application/xml')


def _sitemap_static_urls():
    return [
        (f'{BASE_URL}/', None),
        (f'{BASE_URL}/counties', None),
        (f'{BASE_URL}/cities', None),
        (f'{BASE_URL}/patterns', None),
        (f'{BASE_URL}/trends', None),
        (f'{BASE_URL}/posts', None),
        (f'{BASE_URL}/arrests', None),
        (f'{BASE_URL}/jail-rosters', None),
        (f'{BASE_URL}/bail-bonds', None),
        (f'{BASE_URL}/donate', None),
        (f'{BASE_URL}/advertise/bail-bonds', None),
        (f'{BASE_URL}/subscribe', None),
        (f'{BASE_URL}/terms-of-use', None),
        (f'{BASE_URL}/privacy', None),
        (f'{BASE_URL}/developers/api', None),
        (f'{BASE_URL}/laws', None),
        (f'{BASE_URL}/blog', None),
        (f'{BASE_URL}/warrants', None),
        (f'{BASE_URL}/feed.xml', None),
    ]


@app.route('/robots.txt')
def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        f"Sitemap: {BASE_URL}/sitemap.xml",
        "",
    ])
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_index():
    """Sitemap index for public archive sections."""
    conn = get_db()
    post_lastmod_row = conn.execute(
        'SELECT MAX(created_at) AS lastmod FROM posts'
    ).fetchone()
    blog_lastmod_row = conn.execute(
        'SELECT MAX(COALESCE(updated_at, created_at)) AS lastmod FROM blog_posts WHERE published = 1'
    ).fetchone()
    conn.close()

    sections = [
        ('static', None),
        ('locations', None),
        ('patterns', None),
        ('posts', _iso_lastmod(post_lastmod_row['lastmod']) if post_lastmod_row else None),
        ('blog', _iso_lastmod(blog_lastmod_row['lastmod']) if blog_lastmod_row else None),
    ]
    items = []
    for name, lastmod in sections:
        loc = f'{BASE_URL}/sitemap-{name}.xml'
        if lastmod:
            items.append(f"<sitemap><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod></sitemap>")
        else:
            items.append(f"<sitemap><loc>{escape(loc)}</loc></sitemap>")

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + ''.join(items) +
        '</sitemapindex>'
    )
    return Response(xml, mimetype='application/xml')


@app.route('/sitemap-static.xml')
def sitemap_static():
    return _render_urlset(_sitemap_static_urls())


@app.route('/sitemap-locations.xml')
def sitemap_locations():
    urls = []
    for county in COUNTY_DATA.values():
        urls.append((f"{BASE_URL}/county/{county['slug']}", None))
    for county_name in _all_bail_counties():
        urls.append((f"{BASE_URL}/bail-bonds/{_slugify_key(county_name)}", None))
    for city in CITY_DATA.values():
        urls.append((f"{BASE_URL}/city/{city['slug']}", None))
    for county in COUNTY_DATA.values():
        urls.append((f"{BASE_URL}/warrants/{county['slug']}", None))
    return _render_urlset(urls)


@app.route('/sitemap-posts.xml')
def sitemap_posts():
    conn = get_db()
    rows = conn.execute(
        'SELECT id, created_at FROM posts ORDER BY created_at DESC'
    ).fetchall()
    conn.close()
    urls = [(f"{BASE_URL}/post/{row['id']}", _iso_lastmod(row['created_at'])) for row in rows]
    return _render_urlset(urls)


@app.route('/sitemap-patterns.xml')
def sitemap_patterns():
    conn = get_db()
    urls = [(f"{BASE_URL}/patterns", None)]
    for pattern in PATTERN_DEFINITIONS.values():
        urls.append((f"{BASE_URL}/patterns/{pattern['slug']}", None))
        clause, params = _pattern_clause(pattern['slug'], 'records')
        rows = conn.execute(
            f'''
            SELECT records.county, COUNT(*) AS count
            FROM records
            WHERE {clause}
              AND records.county IS NOT NULL
              AND records.county != ''
            GROUP BY records.county
            ORDER BY count DESC, records.county ASC
            ''',
            params,
        ).fetchall()
        for row in rows:
            county_slug = _county_slug_for_name(row['county'])
            if county_slug:
                urls.append((f"{BASE_URL}/patterns/{pattern['slug']}/{county_slug}", None))
    conn.close()
    return _render_urlset(urls)


@app.route('/sitemap-blog.xml')
def sitemap_blog():
    conn = get_db()
    rows = conn.execute(
        'SELECT slug, COALESCE(updated_at, created_at) AS updated_at FROM blog_posts WHERE published = 1 ORDER BY COALESCE(updated_at, created_at) DESC'
    ).fetchall()
    conn.close()
    urls = [(f"{BASE_URL}/blog/{row['slug']}", _iso_lastmod(row['updated_at'])) for row in rows]
    return _render_urlset(urls)


@app.route('/arrests')
def arrests():
    """Dedicated arrest log — records where an arrest was made."""
    county = request.args.get('county', '')
    search_query = request.args.get('q', '')
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 25

    conn = get_db()

    arrest_filter = """(
        LOWER(COALESCE(records.details, '')) LIKE '%arrest%'
        OR LOWER(COALESCE(records.incident_type, '')) LIKE '%arrest%'
        OR LOWER(COALESCE(records.incident, '')) LIKE '%arrest%'
    )"""

    sql = f"""
        SELECT records.*,
               COALESCE(blotters.filename, '') AS filename
        FROM records
        LEFT JOIN blotters ON records.blotter_id = blotters.id
        WHERE {arrest_filter}
    """
    params = []

    if county:
        sql += " AND records.county = ?"
        params.append(county)
    if search_query:
        st = f'%{search_query}%'
        sql += " AND (records.incident_type LIKE ? OR records.details LIKE ? OR records.location LIKE ?)"
        params.extend([st, st, st])

    total = conn.execute(
        sql.replace("SELECT records.*,\n               COALESCE(blotters.filename, '') AS filename", "SELECT COUNT(*)"),
        params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    sql += " ORDER BY records.created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    records = conn.execute(sql, params).fetchall()

    counties = [r['county'] for r in conn.execute(
        'SELECT DISTINCT county FROM records ORDER BY county').fetchall()]

    conn.close()
    return render_template('arrests.html',
                           records=records, total=total,
                           total_pages=total_pages, page=page,
                           counties=counties, county=county,
                           q=search_query,
                           current_year=datetime.now().year)


@app.route('/donate')
def donate():
    keys = _stripe_keys()
    source = (request.args.get('source') or '').strip()[:80]
    source = source or 'donate_page'
    return render_template(
        'donate.html',
        donations_enabled=_donations_enabled(),
        stripe_ready=_stripe_ready_for_checkout(),
        stripe_publishable_key=keys['publishable_key'],
        suggested_amounts_cents=_allowed_donation_amounts(),
        donation_min_cents=_donation_min_cents(),
        donation_max_cents=_donation_max_cents(),
        donation_currency=_donation_currency(),
        donate_source=source,
        active_nav='donate',
        current_year=datetime.now().year,
    )


@app.route('/api/donate/create-checkout-session', methods=['POST'])
def donate_create_checkout_session():
    if not _donations_enabled():
        return jsonify({'error': 'Donations are currently unavailable'}), 503
    if not _stripe_ready_for_checkout():
        return jsonify({'error': 'Payment provider is not configured'}), 503

    payload = request.get_json(silent=True)
    if payload is None:
        payload = request.form.to_dict() if request.form else {}

    mode = (payload.get('mode') or 'one_time').strip().lower()
    if mode not in {'one_time', 'monthly'}:
        return jsonify({'error': 'Invalid donation mode'}), 400

    try:
        amount_cents = int(payload.get('amount_cents'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid donation amount'}), 400

    min_cents = _donation_min_cents()
    max_cents = _donation_max_cents()
    if amount_cents < min_cents or amount_cents > max_cents:
        return jsonify({'error': 'Donation amount out of allowed range'}), 400

    source = (payload.get('source') or 'donate_page').strip()[:80]
    donor_name = (payload.get('name') or '').strip()[:120]
    email = (payload.get('email') or '').strip().lower()
    if email and '@' not in email:
        email = ''

    currency = _donation_currency()
    stripe_keys = _stripe_keys()
    stripe.api_key = stripe_keys['secret_key']

    line_item = {
        'price_data': {
            'currency': currency,
            'product_data': {'name': 'Montana Blotter Donation'},
            'unit_amount': amount_cents,
        },
        'quantity': 1,
    }
    if mode == 'monthly':
        line_item['price_data']['recurring'] = {'interval': 'month'}

    checkout_params = {
        'mode': 'subscription' if mode == 'monthly' else 'payment',
        'line_items': [line_item],
        'success_url': f'{BASE_URL}/donate/success?session_id={{CHECKOUT_SESSION_ID}}',
        'cancel_url': f'{BASE_URL}/donate/cancel',
        'billing_address_collection': 'auto',
        'allow_promotion_codes': True,
        'metadata': {
            'source': source,
            'mode': mode,
            'amount_cents': str(amount_cents),
            'donor_name': donor_name,
        },
    }
    if email:
        checkout_params['customer_email'] = email

    try:
        checkout_session = stripe.checkout.Session.create(**checkout_params)
    except Exception:
        return jsonify({'error': 'Unable to start secure checkout'}), 502

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO donations (
                provider, mode, status, amount_cents, currency, email_hash, donor_name,
                source, provider_session_id, provider_payment_intent_id, provider_subscription_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_session_id) DO UPDATE SET
                mode = excluded.mode,
                status = excluded.status,
                amount_cents = excluded.amount_cents,
                currency = excluded.currency,
                email_hash = excluded.email_hash,
                donor_name = excluded.donor_name,
                source = excluded.source,
                provider_payment_intent_id = excluded.provider_payment_intent_id,
                provider_subscription_id = excluded.provider_subscription_id,
                updated_at = datetime('now')
            ''',
            (
                'stripe',
                mode,
                'pending',
                amount_cents,
                currency,
                _donation_email_hash(email),
                donor_name,
                source,
                checkout_session.get('id'),
                checkout_session.get('payment_intent'),
                checkout_session.get('subscription'),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    _record_donation_event('checkout_start', source=source, page_path='/donate', amount_cents=amount_cents)
    return jsonify({
        'checkout_url': checkout_session.get('url'),
        'session_id': checkout_session.get('id'),
    })


@app.route('/webhooks/stripe', methods=['POST'])
def stripe_webhook():
    if not _stripe_ready_for_webhooks():
        return ('', 503)

    payload = request.get_data(cache=False)
    signature = request.headers.get('Stripe-Signature', '')
    keys = _stripe_keys()
    stripe.api_key = keys['secret_key']

    try:
        event = stripe.Webhook.construct_event(payload, signature, keys['webhook_secret'])
    except Exception:
        return ('', 400)

    event_id = (event.get('id') or '').strip()
    event_type = (event.get('type') or '').strip()
    if not event_id or not event_type:
        return ('', 400)

    payload_text = payload.decode('utf-8', errors='replace')
    conn = get_db()
    try:
        conn.execute(
            '''
            INSERT INTO payment_webhook_events (provider, event_id, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            ''',
            ('stripe', event_id, event_type, payload_text),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return ('', 200)

    webhook_ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    webhook_referrer = (request.referrer or '')[:500]
    try:
        _apply_stripe_bail_ad_event(conn, event)
        _apply_stripe_event(
            conn,
            event,
            event_source='/webhooks/stripe',
            event_ip_hash=webhook_ip_hash,
            event_referrer=webhook_referrer,
        )

        conn.execute(
            '''
            UPDATE payment_webhook_events
            SET processed = 1, processed_at = datetime('now'), error = NULL
            WHERE event_id = ?
            ''',
            (event_id,),
        )
        conn.commit()
    except Exception as exc:
        conn.execute(
            '''
            UPDATE payment_webhook_events
            SET processed = 0, error = ?, processed_at = datetime('now')
            WHERE event_id = ?
            ''',
            (str(exc)[:500], event_id),
        )
        conn.commit()
        conn.close()
        return ('', 500)

    conn.close()
    return ('', 200)


@app.route('/donate/success')
def donate_success():
    session_id = (request.args.get('session_id') or '').strip()
    donation = None
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or (getattr(config, 'EMAIL_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )

    if session_id:
        conn = get_db()
        row = conn.execute(
            '''
            SELECT
                mode,
                status,
                amount_cents,
                currency,
                source,
                provider_payment_intent_id,
                provider_subscription_id,
                created_at
            FROM donations
            WHERE provider = 'stripe' AND provider_session_id = ?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (session_id,),
        ).fetchone()
        conn.close()
        donation = dict(row) if row else None

    return render_template(
        'donate_success.html',
        donation=donation,
        session_id=session_id,
        support_email=support_email,
        active_nav='donate',
        current_year=datetime.now().year,
    )


@app.route('/donate/cancel')
def donate_cancel():
    return render_template(
        'donate_cancel.html',
        active_nav='donate',
        current_year=datetime.now().year,
    )


@app.route('/subscribe', methods=['GET', 'POST'])
def subscribe():
    """Public email digest subscription."""
    import secrets

    conn = get_db()
    all_counties = [r['county'] for r in conn.execute(
        'SELECT DISTINCT county FROM posts ORDER BY county').fetchall()]
    source = (request.values.get('source') or '').strip()[:80]
    source = source or 'subscribe_page'

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        selected = request.form.getlist('counties')  # empty list = all counties
        _record_subscribe_event('form_submit', source=source, page_path=request.path, email=email)

        if not email or '@' not in email:
            _record_subscribe_event('invalid_email', source=source, page_path=request.path, email=email)
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   source=source,
                                   error='Please enter a valid email address.',
                                   current_year=datetime.now().year)

        token = secrets.token_urlsafe(32)
        counties_str = ','.join(selected)

        try:
            conn.execute(
                'INSERT INTO subscribers (email, counties, token) VALUES (?, ?, ?)',
                (email, counties_str, token))
            conn.commit()
            _record_subscribe_event('subscribe_success', source=source, page_path=request.path, email=email)
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   source=source,
                                   success=True, email=email,
                                   current_year=datetime.now().year)
        except Exception:
            # Email already subscribed — update preferences
            conn.execute(
                'UPDATE subscribers SET counties=?, active=1 WHERE email=?',
                (counties_str, email))
            conn.commit()
            _record_subscribe_event('subscribe_update', source=source, page_path=request.path, email=email)
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   source=source,
                                   success=True, email=email, updated=True,
                                   current_year=datetime.now().year)

    conn.close()
    return render_template('subscribe.html', counties=all_counties,
                           source=source,
                           current_year=datetime.now().year)


@app.route('/unsubscribe')
def unsubscribe():
    """Unsubscribe via token link in digest emails."""
    token = request.args.get('token', '')
    conn = get_db()
    row = conn.execute('SELECT email FROM subscribers WHERE token=?', (token,)).fetchone()
    if row:
        conn.execute('UPDATE subscribers SET active=0 WHERE token=?', (token,))
        conn.commit()
        email = row['email']
        conn.close()
        return render_template('subscribe.html', counties=[], unsubscribed=True, email=email,
                               current_year=datetime.now().year)
    conn.close()
    return render_template('subscribe.html', counties=[],
                           error='Invalid or expired unsubscribe link.',
                           current_year=datetime.now().year)


# ==========================================
# BLOG — PUBLIC
# ==========================================

def _slugify(text):
    import re as _re
    text = text.lower().strip()
    text = _re.sub(r'[^\w\s-]', '', text)
    text = _re.sub(r'[\s_-]+', '-', text)
    return text[:80]


@app.template_filter('markdown')
def render_markdown(text):
    import markdown as _md
    # Escape raw HTML first, then sanitize generated Markdown HTML.
    safe_source = escape(text or '')
    rendered = _md.markdown(safe_source, extensions=['extra', 'nl2br'])
    return _sanitize_html(rendered)


@app.route('/jail-rosters')
def jail_rosters():
    return render_template('jail_rosters.html', current_year=datetime.now().year)


@app.route('/counties')
def counties_directory():
    conn = get_db()
    post_stats = {
        row['county']: row
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS post_count, MAX(incident_date) AS last_report
            FROM posts
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }
    record_stats = {
        row['county']: row['record_count']
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS record_count
            FROM records
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }
    conn.close()

    counties = []
    for county in COUNTY_DATA.values():
        county_cities = [
            city for city in CITY_DATA.values()
            if city.get('county_slug') == county['slug']
        ]
        stats = post_stats.get(county['name'])
        counties.append({
            **county,
            'post_count': stats['post_count'] if stats else 0,
            'record_count': record_stats.get(county['name'], 0),
            'last_report': stats['last_report'] if stats else None,
            'city_count': len(county_cities),
            'county_cities': county_cities,
        })

    counties.sort(key=lambda county: (-county['post_count'], county['name']))
    return render_template(
        'counties.html',
        counties=counties,
        current_year=datetime.now().year,
    )


# ==========================================
# COUNTY PAGES
# ==========================================

COUNTY_DATA = {
    'yellowstone': {
        'slug': 'yellowstone',
        'name': 'Yellowstone',
        'seat': 'Billings',
        'phone': '406-256-2929',
        'roster_url': 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp',
        'warrant_url': 'https://www.yellowstonecountymt.gov/justicecourt/JCWarrants.asp',
        'description': (
            "Yellowstone County is Montana's most populous county, home to Billings — the state's largest city. "
            "Law enforcement is handled by the Yellowstone County Sheriff's Office, the Billings Police Department, "
            "and the Laurel Police Department, among others. The county seat, Billings, sits at the junction of "
            "Interstate 90 and I-94, making it a key transit corridor that law enforcement monitors closely for "
            "drug trafficking and vehicle crime. The Yellowstone County Detention Facility is located in Billings "
            "and publishes a live inmate roster online. The county also operates a dedicated cold case unit."
        ),
        'agencies': [
            {'name': 'Yellowstone County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-256-2929'},
            {'name': 'Billings Police Department', 'type': 'police', 'phone': '406-657-8460'},
            {'name': 'Laurel Police Department', 'type': 'police', 'phone': '406-628-4040'},
        ],
        'neighbors': [
            {'name': 'Carbon', 'slug': 'carbon'},
            {'name': 'Stillwater', 'slug': 'stillwater'},
            {'name': 'Golden Valley', 'slug': 'golden-valley'},
            {'name': 'Musselshell', 'slug': 'musselshell'},
            {'name': 'Treasure', 'slug': 'treasure'},
            {'name': 'Big Horn', 'slug': 'big-horn'},
        ],
    },
    'gallatin': {
        'slug': 'gallatin',
        'name': 'Gallatin',
        'seat': 'Bozeman',
        'phone': '406-582-2100',
        'roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates',
        'warrant_url': None,
        'description': (
            "Gallatin County, home to Bozeman and Montana State University, is one of the fastest-growing counties "
            "in the United States. The Gallatin County Sheriff's Office patrols the unincorporated areas of the county, "
            "while the Bozeman Police Department covers the city. West Yellowstone, at the north entrance to Yellowstone "
            "National Park, is also in Gallatin County and sees significant seasonal traffic and related law enforcement "
            "activity. The county has seen a rise in property crime and traffic incidents in step with its rapid population "
            "growth, and drug enforcement has become an increasing priority as meth and fentanyl distribution networks "
            "have expanded into the Bozeman area."
        ),
        'agencies': [
            {'name': 'Gallatin County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-582-2100'},
            {'name': 'Bozeman Police Department', 'type': 'police', 'phone': '406-582-2000'},
            {'name': 'West Yellowstone Police Department', 'type': 'police', 'phone': '406-646-7600'},
        ],
        'neighbors': [
            {'name': 'Park', 'slug': 'park'},
            {'name': 'Meagher', 'slug': 'meagher'},
            {'name': 'Broadwater', 'slug': 'broadwater'},
            {'name': 'Jefferson', 'slug': 'jefferson'},
            {'name': 'Madison', 'slug': 'madison'},
        ],
    },
    'hill': {
        'slug': 'hill',
        'name': 'Hill',
        'seat': 'Havre',
        'phone': '406-265-5481',
        'roster_url': 'https://vinelink.vineapps.com/state/mt',
        'warrant_url': None,
        'description': (
            "Hill County sits along Montana's Hi-Line near the Canadian border and is anchored by Havre, the county "
            "seat and largest city. The Hill County Sheriff's Office works alongside the Havre Police Department to "
            "cover a large rural footprint, cross-border highway traffic, and the regional hub around Montana State "
            "University-Northern. Montana Blotter receives regular Havre Police activity, making Hill County one of "
            "the most visible county archives on the site today."
        ),
        'agencies': [
            {'name': 'Hill County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-265-5481'},
            {'name': 'Havre Police Department', 'type': 'police', 'phone': '406-265-4397'},
        ],
        'neighbors': [
            {'name': 'Liberty', 'slug': 'liberty'},
            {'name': 'Chouteau', 'slug': 'chouteau'},
            {'name': 'Blaine', 'slug': 'blaine'},
            {'name': 'Phillips', 'slug': 'phillips'},
        ],
    },
    'carbon': {
        'slug': 'carbon',
        'name': 'Carbon',
        'seat': 'Red Lodge',
        'phone': '406-446-1234',
        'roster_url': 'https://carbonmt.gov/sheriff/',
        'warrant_url': None,
        'description': (
            "Carbon County covers Red Lodge, Bridger, Joliet, and the Beartooth foothills in south-central Montana. "
            "The Carbon County Sheriff's Office works with Red Lodge Police and nearby local agencies across a region "
            "that blends rural highway traffic, tourism, and mountain recreation. Montana Blotter already carries a "
            "small amount of Carbon County activity through CrimeMapping-connected agency coverage, and this county "
            "page creates a stable landing point as that archive grows."
        ),
        'agencies': [
            {'name': 'Carbon County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-446-1234'},
            {'name': 'Red Lodge Police Department', 'type': 'police', 'phone': '406-446-1234'},
            {'name': 'Bridger Police Department', 'type': 'police', 'phone': '406-662-3676'},
        ],
        'neighbors': [
            {'name': 'Yellowstone', 'slug': 'yellowstone'},
            {'name': 'Stillwater', 'slug': 'stillwater'},
            {'name': 'Big Horn', 'slug': 'big-horn'},
            {'name': 'Park', 'slug': 'park'},
            {'name': 'Sweet Grass', 'slug': 'sweet-grass'},
        ],
    },
    'missoula': {
        'slug': 'missoula',
        'name': 'Missoula',
        'seat': 'Missoula',
        'phone': '406-258-4780',
        'roster_url': 'https://webapps.missoulacounty.us/jailroster/Inmates',
        'warrant_url': None,
        'description': (
            "Missoula County is home to the University of Montana and the city of Missoula, western Montana's largest "
            "urban center. The Missoula County Sheriff's Office and Missoula Police Department are the primary law "
            "enforcement agencies. In 2024, the Missoula Police Department reported a 9.9% decrease in felony violent "
            "crime, including 36 robberies, 357 assaults, and 2 homicides — though theft and disorderly conduct "
            "increased. The county also has an active drug task force that seized nearly 40,000 dosage units of "
            "fentanyl in 2024. The Missoula County Detention Center publishes a real-time inmate roster updated "
            "throughout the day."
        ),
        'agencies': [
            {'name': 'Missoula County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-258-4780'},
            {'name': 'Missoula Police Department', 'type': 'police', 'phone': '406-552-6300'},
        ],
        'neighbors': [
            {'name': 'Ravalli', 'slug': 'ravalli'},
            {'name': 'Mineral', 'slug': 'mineral'},
            {'name': 'Powell', 'slug': 'powell'},
            {'name': 'Lake', 'slug': 'lake'},
            {'name': 'Sanders', 'slug': 'sanders'},
            {'name': 'Granite', 'slug': 'granite'},
        ],
    },
    'cascade': {
        'slug': 'cascade',
        'name': 'Cascade',
        'seat': 'Great Falls',
        'phone': '406-454-6840',
        'roster_url': 'https://www.cascadecountymt.gov/314/Inmate-Roster',
        'warrant_url': 'https://greatfallsmt.net/municipalcourt/warrants-list',
        'description': (
            "Cascade County is home to Great Falls, Montana's third-largest city and the county seat. The county is "
            "served by the Cascade County Sheriff's Office and the Great Falls Police Department. Great Falls sits "
            "along the Missouri River and is a regional hub for north-central Montana. Malmstrom Air Force Base, "
            "located within the city, brings a substantial military population to the area. The Great Falls Municipal "
            "Court publishes an active warrant list online. Cascade County Detention Center maintains a public inmate "
            "roster with current bookings."
        ),
        'agencies': [
            {'name': 'Cascade County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-454-6840'},
            {'name': 'Great Falls Police Department', 'type': 'police', 'phone': '406-455-8500'},
        ],
        'neighbors': [
            {'name': 'Chouteau', 'slug': 'chouteau'},
            {'name': 'Judith Basin', 'slug': 'judith-basin'},
            {'name': 'Meagher', 'slug': 'meagher'},
            {'name': 'Lewis and Clark', 'slug': 'lewis-and-clark'},
            {'name': 'Teton', 'slug': 'teton'},
        ],
    },
    'flathead': {
        'slug': 'flathead',
        'name': 'Flathead',
        'seat': 'Kalispell',
        'phone': '406-758-5610',
        'roster_url': 'https://apps.flathead.mt.gov/jailroster/',
        'warrant_url': 'https://apps.flathead.mt.gov/warrants/warrants_list.php',
        'description': (
            "Flathead County is located in northwest Montana and includes Kalispell, Whitefish, and Columbia Falls. "
            "It borders Glacier National Park to the east and Flathead Lake — the largest natural freshwater lake west "
            "of the Mississippi — to the south. The Flathead County Sheriff's Office and Kalispell Police Department "
            "are the primary agencies. The county's outdoor recreation economy and proximity to the Canadian border "
            "create unique law enforcement challenges. The Flathead Beacon newspaper publishes a detailed daily police "
            "blotter. Flathead County offers a public warrant list and live inmate roster online."
        ),
        'agencies': [
            {'name': 'Flathead County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-758-5610'},
            {'name': 'Kalispell Police Department', 'type': 'police', 'phone': '406-758-7780'},
            {'name': 'Whitefish Police Department', 'type': 'police', 'phone': '406-863-2420'},
            {'name': 'Columbia Falls Police Department', 'type': 'police', 'phone': '406-892-2222'},
        ],
        'neighbors': [
            {'name': 'Lake', 'slug': 'lake'},
            {'name': 'Lincoln', 'slug': 'lincoln'},
            {'name': 'Sanders', 'slug': 'sanders'},
            {'name': 'Glacier', 'slug': 'glacier'},
            {'name': 'Pondera', 'slug': 'pondera'},
        ],
    },
    'lewis-and-clark': {
        'slug': 'lewis-and-clark',
        'name': 'Lewis and Clark',
        'seat': 'Helena',
        'phone': '406-447-8270',
        'roster_url': 'https://www.lccountymt.gov/Sheriff/Detention-Center',
        'warrant_url': 'https://www.helenamt.gov/Departments/Municipal-Court/Arrest-Warrants-Defendants-in-Custody',
        'description': (
            "Lewis and Clark County is the home of Helena, Montana's state capital. As the seat of state government, "
            "the county hosts the Montana Legislature, the Governor's office, and numerous state agencies. The Lewis "
            "and Clark County Sheriff's Office and Helena Police Department are the primary law enforcement agencies. "
            "The Helena Municipal Court publishes a list of active arrest warrants and defendants in custody online. "
            "Montana Blotter currently receives daily activity reports directly from the Helena Police Department, "
            "making Lewis and Clark County one of our most consistently covered areas."
        ),
        'agencies': [
            {'name': 'Lewis and Clark County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-447-8270'},
            {'name': 'Helena Police Department', 'type': 'police', 'phone': '406-442-3233'},
            {'name': 'East Helena Police Department', 'type': 'police', 'phone': '406-227-8222'},
        ],
        'neighbors': [
            {'name': 'Cascade', 'slug': 'cascade'},
            {'name': 'Meagher', 'slug': 'meagher'},
            {'name': 'Broadwater', 'slug': 'broadwater'},
            {'name': 'Jefferson', 'slug': 'jefferson'},
            {'name': 'Powell', 'slug': 'powell'},
            {'name': 'Teton', 'slug': 'teton'},
        ],
    },
    'silver-bow': {
        'slug': 'silver-bow',
        'name': 'Silver Bow',
        'seat': 'Butte',
        'phone': '406-497-1120',
        'roster_url': 'https://co.silverbow.mt.us/3274/Detention-Center',
        'warrant_url': None,
        'description': (
            "Silver Bow County is a consolidated city-county government — the City and County of Butte-Silver Bow — "
            "making it one of the few such unified governments in the western United States. Butte has a rich mining "
            "history and is home to the Berkeley Pit Superfund site. The Butte-Silver Bow Law Enforcement Division "
            "handles both municipal and county law enforcement functions. The Silver Bow County Detention Center "
            "maintains inmate booking information. Butte is located at the junction of I-90 and I-15, making it a "
            "significant node in Montana's highway network and a transit point for drug trafficking routes."
        ),
        'agencies': [
            {'name': 'Butte-Silver Bow Law Enforcement', 'type': 'police', 'phone': '406-497-1120'},
        ],
        'neighbors': [
            {'name': 'Deer Lodge', 'slug': 'deer-lodge'},
            {'name': 'Jefferson', 'slug': 'jefferson'},
            {'name': 'Beaverhead', 'slug': 'beaverhead'},
            {'name': 'Madison', 'slug': 'madison'},
            {'name': 'Granite', 'slug': 'granite'},
        ],
    },
}


def _ensure_county_shells():
    for county_name in config.MONTANA_COUNTIES:
        slug = _slugify_key(county_name)
        if slug in COUNTY_DATA:
            continue

        directory = COUNTY_DIRECTORY.get(slug, {})
        roster_url = directory.get('roster_url')
        phone = directory.get('phone')
        roster_sentence = (
            "The county publishes an online jail roster that readers can use as a starting point for inmate status checks."
            if roster_url else
            "The county does not appear to publish a public online jail roster, so readers may need to rely on VINELink or direct sheriff contact for detention information."
        )
        COUNTY_DATA[slug] = {
            'slug': slug,
            'name': county_name,
            'seat': None,
            'phone': phone,
            'roster_url': roster_url,
            'warrant_url': None,
            'description': (
                f"{county_name} County is part of Montana Blotter's statewide public records expansion. "
                f"This county landing page is designed to connect readers to local arrest coverage, jail roster resources, warrant guidance, and any agency reporting that becomes available over time. "
                f"{roster_sentence}"
            ),
            'agencies': [
                {
                    'name': f"{county_name} County Sheriff's Office",
                    'type': 'sheriff',
                    'phone': phone,
                },
            ],
            'neighbors': [],
        }


_ensure_county_shells()


@app.route('/county/<slug>')
def county_page(slug):
    county = COUNTY_DATA.get(slug)
    if not county:
        return render_template('404.html'), 404

    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10

    conn = get_db()

    count_row = conn.execute(
        'SELECT COUNT(*) FROM posts WHERE county = ?', (county['name'],)
    ).fetchone()
    post_count = count_row[0] if count_row else 0
    total_pages = max(1, (post_count + per_page - 1) // per_page)

    posts = conn.execute(
        """SELECT posts.*, blotters.county AS blotter_county
           FROM posts
           JOIN blotters ON posts.blotter_id = blotters.id
           WHERE posts.county = ?
           ORDER BY posts.incident_date DESC, posts.created_at DESC
           LIMIT ? OFFSET ?""",
        (county['name'], per_page, (page - 1) * per_page)
    ).fetchall()

    record_count = conn.execute(
        'SELECT COUNT(*) FROM records WHERE county = ?', (county['name'],)
    ).fetchone()[0]

    top_incidents = conn.execute(
        """
        SELECT COALESCE(NULLIF(incident_type, ''), 'Other') AS incident_type,
               COUNT(*) AS count
        FROM records
        WHERE county = ?
        GROUP BY COALESCE(NULLIF(incident_type, ''), 'Other')
        ORDER BY count DESC, incident_type ASC
        LIMIT 8
        """,
        (county['name'],)
    ).fetchall()

    recent_records = conn.execute(
        """
        SELECT
            records.id,
            records.date,
            records.time,
            COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_label,
            records.location,
            posts.id AS post_id
        FROM records
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE records.county = ?
        ORDER BY records.date DESC, records.time DESC, records.id DESC
        LIMIT 8
        """,
        (county['name'],)
    ).fetchall()
    recent_records = _annotate_recent_records(conn, recent_records)

    agency_coverage = conn.execute(
        """
        SELECT COALESCE(NULLIF(agency_name, ''), 'Unknown agency') AS agency_name,
               COUNT(*) AS report_count
        FROM posts
        WHERE county = ?
        GROUP BY COALESCE(NULLIF(agency_name, ''), 'Unknown agency')
        ORDER BY report_count DESC, agency_name ASC
        LIMIT 8
        """,
        (county['name'],)
    ).fetchall()

    last_row = conn.execute(
        'SELECT incident_date FROM posts WHERE county = ? ORDER BY incident_date DESC LIMIT 1',
        (county['name'],)
    ).fetchone()
    last_report = last_row['incident_date'] if last_row else None
    latest_weekly_digest = _latest_weekly_digest(conn)

    conn.close()

    county_cities = [
        city for city in CITY_DATA.values()
        if city.get('county_slug') == county['slug']
    ]
    linked_neighbors = [
        COUNTY_DATA[neighbor['slug']]
        for neighbor in county.get('neighbors', [])
        if neighbor.get('slug') in COUNTY_DATA
    ]

    return render_template(
        'county_page.html',
        county=county,
        posts=posts,
        post_count=post_count,
        record_count=record_count,
        top_incidents=top_incidents,
        recent_records=recent_records,
        agency_coverage=agency_coverage,
        county_cities=county_cities,
        pattern_links=_pattern_links_for_county(county['slug']),
        linked_neighbors=linked_neighbors,
        last_report=last_report,
        latest_weekly_digest=latest_weekly_digest,
        page=page,
        total_pages=total_pages,
        current_year=datetime.now().year,
    )


# ==========================================
# CITY PAGES
# ==========================================

CITY_DATA = {
    'billings': {
        'slug': 'billings',
        'name': 'Billings',
        'county': 'Yellowstone',
        'county_slug': 'yellowstone',
        'county_roster_url': 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp',
        'pd_name': 'Billings Police Department',
        'pd_phone': '406-657-8460',
        'pd_url': 'https://www.billingsmt.gov/2564/Police-Department',
        'pd_records_url': 'https://www.billingsmt.gov/2874/Warrants',
        'warrant_url': 'https://www.billingsmt.gov/2874/Warrants',
        'municipal_court_url': None,
        'search_terms': ['Billings', 'Billings Police'],
        'description': (
            "The Billings Police Department serves Montana's largest city, with a population of around 120,000. "
            "Billings sits at the crossroads of Interstate 90 and I-94 in Yellowstone County, making it a major "
            "commercial and transportation hub for the region. The department operates multiple divisions including "
            "patrol, investigations, traffic, and a dedicated drug task force that works alongside county and federal "
            "agencies to combat meth and fentanyl distribution in the Yellowstone Valley. The Billings Police "
            "Department publishes regular crime statistics and participates in the national Project Safe Neighborhoods "
            "initiative."
        ),
        'nearby': [
            {'name': 'Laurel', 'slug': 'laurel'},
            {'name': 'Hardin', 'slug': 'hardin'},
        ],
    },
    'missoula': {
        'slug': 'missoula',
        'name': 'Missoula',
        'county': 'Missoula',
        'county_slug': 'missoula',
        'county_roster_url': 'https://webapps.missoulacounty.us/jailroster/Inmates',
        'pd_name': 'Missoula Police Department',
        'pd_phone': '406-552-6300',
        'pd_url': 'https://www.ci.missoula.mt.us/212/Police-Department',
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': 'https://www.ci.missoula.mt.us/335/Crime-Activity',
        'search_terms': ['Missoula', 'Missoula Police'],
        'description': (
            "The Missoula Police Department serves a city of approximately 75,000 people in western Montana, home "
            "to the University of Montana. In 2024, the department reported a 9.9% decrease in felony violent crime, "
            "with 36 robberies, 357 assaults, and 2 homicides. Theft and disorderly conduct increased during the "
            "same period. The MPD's drug task force seized nearly 40,000 dosage units of fentanyl in 2024. The "
            "department releases an annual crime report and posts crime activity data online. Missoula Police "
            "work closely with the Missoula County Sheriff's Office and the University of Montana Police."
        ),
        'nearby': [
            {'name': 'Lolo', 'slug': 'lolo'},
        ],
    },
    'bozeman': {
        'slug': 'bozeman',
        'name': 'Bozeman',
        'county': 'Gallatin',
        'county_slug': 'gallatin',
        'county_roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates',
        'pd_name': 'Bozeman Police Department',
        'pd_phone': '406-582-2000',
        'pd_url': 'https://www.bozeman.net/departments/police',
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': 'https://www.bozeman.net/departments/municipal-court',
        'search_terms': ['Bozeman', 'Bozeman Police'],
        'description': (
            "The Bozeman Police Department serves one of the fastest-growing cities in the United States. Bozeman's "
            "population has grown dramatically over the past decade, driven by an influx of remote workers, tech "
            "companies, and outdoor recreation tourism around Yellowstone National Park. In 2025, the BPD reported "
            "a 60% increase in traffic violations — a direct consequence of the city's rapid growth and increased "
            "vehicle traffic. The department works closely with the Gallatin County Sheriff's Office and Montana "
            "State University Police. Property crime and drug-related offenses have grown alongside the city's "
            "expanding population."
        ),
        'nearby': [
            {'name': 'Belgrade', 'slug': 'belgrade'},
            {'name': 'Manhattan', 'slug': 'manhattan'},
        ],
    },
    'great-falls': {
        'slug': 'great-falls',
        'name': 'Great Falls',
        'county': 'Cascade',
        'county_slug': 'cascade',
        'county_roster_url': 'https://www.cascadecountymt.gov/314/Inmate-Roster',
        'pd_name': 'Great Falls Police Department',
        'pd_phone': '406-455-8500',
        'pd_url': 'https://greatfallsmt.net/police',
        'pd_records_url': None,
        'warrant_url': 'https://greatfallsmt.net/municipalcourt/warrants-list',
        'municipal_court_url': 'https://greatfallsmt.net/municipalcourt',
        'search_terms': ['Great Falls', 'Great Falls Police'],
        'description': (
            "The Great Falls Police Department serves Montana's third-largest city, situated along the Missouri River "
            "in Cascade County. The city is home to Malmstrom Air Force Base, a significant presence that affects "
            "the local population and law enforcement dynamics. Great Falls serves as the commercial and medical hub "
            "for a large swath of north-central Montana. The Great Falls Municipal Court publishes an active online "
            "warrant list. The GFPD works alongside the Cascade County Sheriff's Office and Malmstrom's security "
            "forces. The department has a strong community policing focus and participates in regional drug task "
            "force operations."
        ),
        'nearby': [
            {'name': 'Havre', 'slug': 'havre'},
        ],
    },
    'helena': {
        'slug': 'helena',
        'name': 'Helena',
        'county': 'Lewis and Clark',
        'county_slug': 'lewis-and-clark',
        'county_roster_url': 'https://www.lccountymt.gov/Sheriff/Detention-Center',
        'pd_name': 'Helena Police Department',
        'pd_phone': '406-442-3233',
        'pd_url': 'https://www.helenamt.gov/Departments/Police',
        'pd_records_url': None,
        'warrant_url': 'https://www.helenamt.gov/Departments/Municipal-Court/Arrest-Warrants-Defendants-in-Custody',
        'municipal_court_url': 'https://www.helenamt.gov/Departments/Municipal-Court',
        'search_terms': ['Helena', 'Helena Police'],
        'description': (
            "The Helena Police Department serves Montana's state capital, a city of approximately 33,000 people in "
            "Lewis and Clark County. As the seat of state government, Helena is home to the Montana Legislature, "
            "the Governor's office, the Montana Supreme Court, and numerous state agencies. Montana Blotter currently "
            "receives daily activity reports directly from the Helena Police Department, making it one of our most "
            "consistently covered agencies. The HPD's media log is published each weekday and includes all calls for "
            "service, arrests, and notable incidents. The Helena Municipal Court publishes active arrest warrants "
            "and current defendants in custody online."
        ),
        'nearby': [
            {'name': 'East Helena', 'slug': 'east-helena'},
            {'name': 'Havre', 'slug': 'havre'},
        ],
    },
    'kalispell': {
        'slug': 'kalispell',
        'name': 'Kalispell',
        'county': 'Flathead',
        'county_slug': 'flathead',
        'county_roster_url': 'https://apps.flathead.mt.gov/jailroster/',
        'pd_name': 'Kalispell Police Department',
        'pd_phone': '406-758-7780',
        'pd_url': 'https://kalispell.com/police',
        'pd_records_url': None,
        'warrant_url': 'https://apps.flathead.mt.gov/warrants/warrants_list.php',
        'municipal_court_url': None,
        'search_terms': ['Kalispell', 'Kalispell Police'],
        'description': (
            "The Kalispell Police Department serves the county seat of Flathead County in northwest Montana. "
            "Kalispell is the largest city in the region and the commercial hub for the Flathead Valley, which "
            "includes Whitefish and Columbia Falls. The city's proximity to Glacier National Park and Flathead Lake "
            "draws significant seasonal tourism. The KPD works closely with the Flathead County Sheriff's Office, "
            "which maintains both a public jail roster and a public warrant list online. The Flathead Beacon "
            "newspaper provides detailed daily police blotter coverage for the Flathead Valley."
        ),
        'nearby': [
            {'name': 'Whitefish', 'slug': 'whitefish'},
            {'name': 'Columbia Falls', 'slug': 'columbia-falls'},
        ],
    },
    'butte': {
        'slug': 'butte',
        'name': 'Butte',
        'county': 'Silver Bow',
        'county_slug': 'silver-bow',
        'county_roster_url': 'https://co.silverbow.mt.us/3274/Detention-Center',
        'pd_name': 'Butte-Silver Bow Law Enforcement',
        'pd_phone': '406-497-1120',
        'pd_url': 'https://co.silverbow.mt.us/192/Law-Enforcement',
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Butte', 'Butte-Silver Bow', 'Butte Silver Bow'],
        'description': (
            "Butte-Silver Bow Law Enforcement is the unified law enforcement division of the consolidated "
            "City and County of Butte-Silver Bow — one of the few city-county government consolidations in the "
            "western United States. Butte has a storied mining history and is home to the Berkeley Pit Superfund "
            "site. The city sits at the junction of I-90 and I-15, key corridors for both commerce and, "
            "historically, drug trafficking routes. The consolidated government structure means a single law "
            "enforcement agency handles both municipal and county functions, and the Silver Bow County Detention "
            "Center handles all local inmate booking."
        ),
        'nearby': [
            {'name': 'Anaconda', 'slug': 'anaconda'},
            {'name': 'Deer Lodge', 'slug': 'deer-lodge'},
        ],
    },
    'havre': {
        'slug': 'havre',
        'name': 'Havre',
        'county': 'Hill',
        'county_slug': 'hill',
        'county_roster_url': None,
        'pd_name': 'Havre Police Department',
        'pd_phone': '406-265-4397',
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Havre', 'Havre Police', 'HAVRE POLICE'],
        'description': (
            "The Havre Police Department serves Hill County's largest city, located in north-central Montana near "
            "the Canadian border. Havre is home to Montana State University–Northern and serves as a regional hub "
            "for the Hi-Line communities along Highway 2. The city's proximity to the border creates unique law "
            "enforcement challenges including drug smuggling and human trafficking concerns. Montana Blotter "
            "currently receives daily activity reports directly from the Havre Police Department, making it one of "
            "our most consistently covered agencies on the Hi-Line."
        ),
        'nearby': [
            {'name': 'Great Falls', 'slug': 'great-falls'},
        ],
    },
    'laurel': {
        'slug': 'laurel',
        'name': 'Laurel',
        'county': 'Yellowstone',
        'county_slug': 'yellowstone',
        'county_roster_url': 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp',
        'pd_name': 'Laurel Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Laurel', 'Laurel Police'],
        'description': (
            "Laurel sits west of Billings in Yellowstone County and functions as part of the larger Billings metro corridor. "
            "This city page gives Montana Blotter a dedicated landing page for Laurel-area reports and for readers who want a narrower search than the full Yellowstone County archive."
        ),
        'nearby': [
            {'name': 'Billings', 'slug': 'billings'},
            {'name': 'Red Lodge', 'slug': 'red-lodge'},
        ],
    },
    'hardin': {
        'slug': 'hardin',
        'name': 'Hardin',
        'county': 'Big Horn',
        'county_slug': 'big-horn',
        'county_roster_url': 'https://www.bighorncountymt.gov/239/Detention',
        'pd_name': 'Hardin Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Hardin', 'Hardin Police'],
        'description': (
            "Hardin is the county seat of Big Horn County and a high-interest local search term for southeastern Montana. "
            "This page is built to capture city-level police blotter intent while still linking back to county records and detention resources."
        ),
        'nearby': [
            {'name': 'Billings', 'slug': 'billings'},
            {'name': 'Laurel', 'slug': 'laurel'},
        ],
    },
    'belgrade': {
        'slug': 'belgrade',
        'name': 'Belgrade',
        'county': 'Gallatin',
        'county_slug': 'gallatin',
        'county_roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates',
        'pd_name': 'Belgrade Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Belgrade', 'Belgrade Police'],
        'description': (
            "Belgrade is one of the fastest-growing communities in Gallatin County and often gets grouped into broader Bozeman coverage. "
            "This city page gives Belgrade its own entry point for blotter and arrest-related search traffic."
        ),
        'nearby': [
            {'name': 'Bozeman', 'slug': 'bozeman'},
            {'name': 'Manhattan', 'slug': 'manhattan'},
        ],
    },
    'manhattan': {
        'slug': 'manhattan',
        'name': 'Manhattan',
        'county': 'Gallatin',
        'county_slug': 'gallatin',
        'county_roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates',
        'pd_name': 'Manhattan Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Manhattan', 'Manhattan Police'],
        'description': (
            "Manhattan gives Montana Blotter a city-level page for another Gallatin County community that is usually overshadowed by Bozeman. "
            "That makes it useful for long-tail search intent and local internal linking."
        ),
        'nearby': [
            {'name': 'Belgrade', 'slug': 'belgrade'},
            {'name': 'Bozeman', 'slug': 'bozeman'},
        ],
    },
    'whitefish': {
        'slug': 'whitefish',
        'name': 'Whitefish',
        'county': 'Flathead',
        'county_slug': 'flathead',
        'county_roster_url': 'https://apps.flathead.mt.gov/jailroster/',
        'pd_name': 'Whitefish Police Department',
        'pd_phone': '406-863-2420',
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Whitefish', 'Whitefish Police'],
        'description': (
            "Whitefish is one of northwest Montana's highest-interest local search terms thanks to tourism, seasonal traffic, and regional nightlife. "
            "This page gives Whitefish its own blotter landing page separate from the broader Flathead County archive."
        ),
        'nearby': [
            {'name': 'Kalispell', 'slug': 'kalispell'},
            {'name': 'Columbia Falls', 'slug': 'columbia-falls'},
        ],
    },
    'columbia-falls': {
        'slug': 'columbia-falls',
        'name': 'Columbia Falls',
        'county': 'Flathead',
        'county_slug': 'flathead',
        'county_roster_url': 'https://apps.flathead.mt.gov/jailroster/',
        'pd_name': 'Columbia Falls Police Department',
        'pd_phone': '406-892-2222',
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Columbia Falls', 'Columbia Falls Police'],
        'description': (
            "Columbia Falls is another Flathead Valley city that benefits from a dedicated page because it captures more specific local-intent traffic than county-level coverage alone."
        ),
        'nearby': [
            {'name': 'Whitefish', 'slug': 'whitefish'},
            {'name': 'Kalispell', 'slug': 'kalispell'},
        ],
    },
    'east-helena': {
        'slug': 'east-helena',
        'name': 'East Helena',
        'county': 'Lewis and Clark',
        'county_slug': 'lewis-and-clark',
        'county_roster_url': 'https://www.lccountymt.gov/Sheriff/Detention-Center',
        'pd_name': 'East Helena Police Department',
        'pd_phone': '406-227-8222',
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['East Helena', 'East Helena Police'],
        'description': (
            "East Helena gives the Lewis and Clark archive an additional city-level landing page for readers looking beyond Helena proper."
        ),
        'nearby': [
            {'name': 'Helena', 'slug': 'helena'},
            {'name': 'Havre', 'slug': 'havre'},
        ],
    },
    'anaconda': {
        'slug': 'anaconda',
        'name': 'Anaconda',
        'county': 'Deer Lodge',
        'county_slug': 'deer-lodge',
        'county_roster_url': None,
        'pd_name': 'Anaconda-Deer Lodge Law Enforcement Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Anaconda', 'Anaconda Police', 'Anaconda-Deer Lodge'],
        'description': (
            "Anaconda is a recognizable western Montana city and a natural city-level expansion for Montana Blotter's location coverage, especially for users searching outside Butte-Silver Bow."
        ),
        'nearby': [
            {'name': 'Butte', 'slug': 'butte'},
        ],
    },
    'red-lodge': {
        'slug': 'red-lodge',
        'name': 'Red Lodge',
        'county': 'Carbon',
        'county_slug': 'carbon',
        'county_roster_url': 'https://carbonmt.gov/sheriff/',
        'pd_name': 'Red Lodge Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Red Lodge', 'Red Lodge Police'],
        'description': (
            "Red Lodge adds a mountain-tourism city to the Montana Blotter city inventory and creates a focused landing page for Carbon County readers."
        ),
        'nearby': [
            {'name': 'Laurel', 'slug': 'laurel'},
            {'name': 'Billings', 'slug': 'billings'},
        ],
    },
    'livingston': {
        'slug': 'livingston',
        'name': 'Livingston',
        'county': 'Park',
        'county_slug': 'park',
        'county_roster_url': 'https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/',
        'pd_name': 'Livingston Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Livingston', 'Livingston Police'],
        'description': (
            "Livingston is a strong next-step city page for south-central Montana and supports city-level long-tail SEO around Park County public safety searches."
        ),
        'nearby': [
            {'name': 'Bozeman', 'slug': 'bozeman'},
            {'name': 'Red Lodge', 'slug': 'red-lodge'},
        ],
    },
}


@app.route('/cities')
def cities_directory():
    conn = get_db()
    cities = _city_directory_listing(conn)
    featured_new_cities = _featured_city_pages(cities)
    conn.close()
    return render_template(
        'cities.html',
        cities=cities,
        featured_new_cities=featured_new_cities,
        current_year=datetime.now().year,
    )


@app.route('/city/<slug>')
def city_page(slug):
    city = CITY_DATA.get(slug)
    if not city:
        return render_template('404.html'), 404

    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10

    terms = city['search_terms']
    where_sql, params_count = _build_like_clause(
        ['posts.city', 'posts.agency_name'],
        terms,
    )

    conn = get_db()

    count_row = conn.execute(
        f'SELECT COUNT(*) FROM posts WHERE {where_sql}', params_count
    ).fetchone()
    post_count = count_row[0] if count_row else 0
    total_pages = max(1, (post_count + per_page - 1) // per_page)

    params_fetch = params_count + [per_page, (page - 1) * per_page]
    posts = conn.execute(
        f"""SELECT posts.*, blotters.county AS blotter_county
            FROM posts
            JOIN blotters ON posts.blotter_id = blotters.id
            WHERE {where_sql}
            ORDER BY posts.incident_date DESC, posts.created_at DESC
            LIMIT ? OFFSET ?""",
        params_fetch
    ).fetchall()

    rec_where_sql, rec_params = _build_like_clause(['records.location'], terms)
    record_count = conn.execute(
        f'SELECT COUNT(*) FROM records WHERE {rec_where_sql}',
        rec_params
    ).fetchone()[0]

    top_incidents = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(incident_type, ''), 'Other') AS incident_type,
               COUNT(*) AS count
        FROM records
        WHERE {rec_where_sql}
        GROUP BY COALESCE(NULLIF(incident_type, ''), 'Other')
        ORDER BY count DESC, incident_type ASC
        LIMIT 8
        """,
        rec_params
    ).fetchall()

    recent_records = conn.execute(
        f"""
        SELECT
            records.id,
            records.date,
            records.time,
            COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_label,
            records.location,
            posts.id AS post_id
        FROM records
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE {rec_where_sql}
        ORDER BY records.date DESC, records.time DESC, records.id DESC
        LIMIT 8
        """,
        rec_params
    ).fetchall()
    recent_records = _annotate_recent_records(conn, recent_records)

    agency_coverage = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(agency_name, ''), 'Unknown agency') AS agency_name,
               COUNT(*) AS report_count
        FROM posts
        WHERE {where_sql}
        GROUP BY COALESCE(NULLIF(agency_name, ''), 'Unknown agency')
        ORDER BY report_count DESC, agency_name ASC
        LIMIT 8
        """,
        params_count
    ).fetchall()

    last_row = conn.execute(
        f'SELECT incident_date FROM posts WHERE {where_sql} ORDER BY incident_date DESC LIMIT 1',
        params_count
    ).fetchone()
    last_report = last_row['incident_date'] if last_row else None

    conn.close()

    linked_nearby = [
        CITY_DATA[nearby['slug']]
        for nearby in city.get('nearby', [])
        if nearby.get('slug') in CITY_DATA
    ]

    return render_template(
        'city_page.html',
        city=city,
        posts=posts,
        post_count=post_count,
        record_count=record_count,
        top_incidents=top_incidents,
        recent_records=recent_records,
        agency_coverage=agency_coverage,
        pattern_links=_pattern_links_for_county(city['county_slug']),
        linked_nearby=linked_nearby,
        last_report=last_report,
        page=page,
        total_pages=total_pages,
        current_year=datetime.now().year,
    )


# ==========================================
# PATTERN PAGES
# ==========================================

@app.route('/patterns')
def patterns_hub():
    conn = get_db()
    pattern_cards = []
    for pattern in PATTERN_DEFINITIONS.values():
        clause, params = _pattern_clause(pattern['slug'], 'records')
        total_records = conn.execute(
            f'SELECT COUNT(*) FROM records WHERE {clause}',
            params,
        ).fetchone()[0]
        top_counties = []
        for row in conn.execute(
            f'''
            SELECT records.county, COUNT(*) AS count
            FROM records
            WHERE {clause}
              AND records.county IS NOT NULL
              AND records.county != ''
            GROUP BY records.county
            ORDER BY count DESC, records.county ASC
            LIMIT 5
            ''',
            params,
        ).fetchall():
            county_slug = _county_slug_for_name(row['county'])
            if county_slug:
                top_counties.append({
                    'name': row['county'],
                    'slug': county_slug,
                    'count': row['count'],
                })
        pattern_cards.append({
            **pattern,
            'total_records': total_records,
            'top_counties': top_counties,
        })
    latest_weekly_digest = _latest_weekly_digest(conn)
    conn.close()
    return render_template(
        'patterns_hub.html',
        pattern_cards=pattern_cards,
        latest_weekly_digest=latest_weekly_digest,
        current_year=datetime.now().year,
    )


@app.route('/patterns/<pattern_slug>')
@app.route('/patterns/<pattern_slug>/<county_slug>')
def pattern_page(pattern_slug, county_slug=None):
    pattern = PATTERN_DEFINITIONS.get(pattern_slug)
    if not pattern:
        return render_template('404.html'), 404

    county = None
    if county_slug:
        county = COUNTY_DATA.get(county_slug)
        if not county:
            return render_template('404.html'), 404

    conn = get_db()
    context = _pattern_page_context(conn, pattern_slug, county=county)
    conn.close()
    if context is None:
        return render_template('404.html'), 404

    return render_template(
        'pattern_page.html',
        active_nav='patterns',
        **context,
        current_year=datetime.now().year,
    )


# ==========================================
# WARRANT PAGES
# ==========================================

# Slim list of counties shown on warrant pages — reuses COUNTY_DATA
_WARRANT_COUNTIES = [
    'yellowstone', 'hill', 'gallatin', 'missoula', 'cascade', 'flathead',
    'lewis-and-clark', 'silver-bow',
]


@app.route('/warrants')
def warrants_hub():
    counties = [COUNTY_DATA[s] for s in _WARRANT_COUNTIES if s in COUNTY_DATA]
    return render_template(
        'warrants_hub.html',
        counties=counties,
        current_year=datetime.now().year,
    )


@app.route('/warrants/<slug>')
def warrant_county(slug):
    county = COUNTY_DATA.get(slug)
    if not county:
        return render_template('404.html'), 404
    all_counties = [COUNTY_DATA[s] for s in _WARRANT_COUNTIES if s in COUNTY_DATA]
    return render_template(
        'warrant_county.html',
        county=county,
        all_counties=all_counties,
        current_year=datetime.now().year,
    )


@app.route('/laws')
def montana_laws():
    return render_template('laws.html', current_year=datetime.now().year)


@app.route('/bail-bonds')
def bail_bonds_directory():
    return _render_bail_bonds_directory()


def _render_bail_bonds_directory(selected_county=''):
    normalized_county = _normalize_bail_county(selected_county)
    county_slug = _slugify_key(normalized_county) if normalized_county else ''
    lead_submitted = request.args.get('lead_submitted') == '1'
    lead_error = (request.args.get('lead_error') or '').strip()[:80]

    conn = get_db()
    try:
        _ensure_bail_consumer_lead_schema(conn)
        listings = _active_bail_ad_listings(conn)
    except sqlite3.OperationalError:
        listings = []
    finally:
        conn.close()

    county_sections = _bail_county_sections(listings, normalized_county)
    visible_listings = []
    for section in county_sections:
        visible_listings.extend(section['listings'])
    if not normalized_county:
        visible_listings = listings

    default_help_phone = next((listing['phone'] for listing in visible_listings if listing.get('phone')), '')
    help_contact = _bail_help_contact(default_phone=default_help_phone)
    county_links = [
        {'name': county_name, 'slug': _slugify_key(county_name)}
        for county_name in _all_bail_counties()
    ]

    if normalized_county:
        page_title = f'{normalized_county} Bail Bonds Directory'
        meta_description = f'Find licensed bail bond agents serving {normalized_county} County with click-to-call, text support, and rapid intake.'
        canonical_url = f'{BASE_URL}/bail-bonds/{county_slug}'
    else:
        page_title = 'Bail Bonds Directory'
        meta_description = 'County-level bail bonds directory for Montana Blotter partner advertisers.'
        canonical_url = f'{BASE_URL}/bail-bonds'

    return render_template(
        'bail_bonds_directory.html',
        listings=visible_listings,
        county_sections=county_sections,
        selected_county=normalized_county,
        selected_county_slug=county_slug,
        county_links=county_links,
        lead_submitted=lead_submitted,
        lead_error=lead_error,
        lead_form={
            'county': normalized_county or '',
            'callback_preference': 'call_now',
            'source': (request.args.get('source') or 'directory').strip()[:80],
        },
        help_contact=help_contact,
        page_title=page_title,
        meta_description=meta_description,
        canonical_url=canonical_url,
        og_title=f'{page_title} | Montana Blotter',
        og_description=meta_description,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@app.route('/bail-bonds/<county_slug>')
def bail_bonds_directory_county(county_slug):
    normalized_county = _normalize_bail_county(county_slug)
    if not normalized_county or _slugify_key(normalized_county) != _slugify_key(county_slug):
        return render_template('404.html'), 404
    return _render_bail_bonds_directory(selected_county=normalized_county)


@app.route('/bail-bonds/intake', methods=['POST'])
def bail_bonds_intake():
    return_path = (request.form.get('return_path') or '').strip()
    if not return_path.startswith('/bail-bonds') or return_path.startswith('//'):
        return_path = '/bail-bonds'

    def _redirect_with_flag(flag_key, flag_value='1'):
        separator = '&' if '?' in return_path else '?'
        return redirect(f'{return_path}{separator}{flag_key}={flag_value}')

    full_name = (request.form.get('full_name') or '').strip()[:120]
    phone = (request.form.get('phone') or '').strip()[:40]
    email = (request.form.get('email') or '').strip().lower()[:160]
    county = _normalize_bail_county((request.form.get('county') or '').strip()[:80])
    jail_facility = (request.form.get('jail_facility') or '').strip()[:120]
    callback_preference = (request.form.get('callback_preference') or 'call_now').strip().lower()[:32]
    notes = (request.form.get('notes') or '').strip()[:1200]
    source = (request.form.get('source') or 'directory_form').strip()[:80]
    honeypot = (request.form.get('fax_number') or '').strip()

    if honeypot:
        return _redirect_with_flag('lead_submitted')

    if not full_name or not phone or not county:
        return _redirect_with_flag('lead_error', 'missing_required')

    if callback_preference not in {'call_now', 'text_me', 'email_me', 'call_later'}:
        callback_preference = 'call_now'

    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]

    conn = get_db()
    try:
        _ensure_bail_consumer_lead_schema(conn)
        listings = _active_bail_ad_listings(conn)
        routed_targets = _bail_lead_routing_targets(listings, county)

        routed_order_ids = ','.join(str(item['id']) for item in routed_targets)
        routed_business_names = ', '.join(item['business_name'] for item in routed_targets)
        routed_emails = ','.join(
            sorted({(item.get('email') or '').strip().lower() for item in routed_targets if item.get('email')})
        )
        routed_phones = ', '.join(
            sorted({(item.get('phone') or '').strip() for item in routed_targets if item.get('phone')})
        )

        cursor = conn.execute(
            '''
            INSERT INTO bail_consumer_leads (
                full_name, phone, email, county, jail_facility, callback_preference, notes, source,
                routed_order_ids, routed_business_names, routed_emails, routed_phones, ip_hash, referrer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                full_name,
                phone,
                email,
                county,
                jail_facility,
                callback_preference,
                notes,
                source,
                routed_order_ids,
                routed_business_names,
                routed_emails,
                routed_phones,
                ip_hash,
                referrer,
            ),
        )
        lead_id = int(cursor.lastrowid or 0)
        _record_bail_consumer_event(conn, 'form_submit', county=county, source=source, lead_id=lead_id)
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
        return _redirect_with_flag('lead_error', 'schema')
    finally:
        conn.close()

    recipients = _bail_lead_notify_recipients()
    for email_value in (routed_emails or '').split(','):
        clean = (email_value or '').strip().lower()
        if clean and '@' in clean and clean not in recipients:
            recipients.append(clean)

    subject = f'New Bail Lead · {county} · {full_name}'
    body = (
        f'Lead ID: {lead_id}\n'
        f'Name: {full_name}\n'
        f'Phone: {phone}\n'
        f'Email: {email or "-"}\n'
        f'County: {county}\n'
        f'Jail Facility: {jail_facility or "-"}\n'
        f'Callback Preference: {callback_preference}\n'
        f'Source: {source}\n'
        f'Routed Advertisers: {routed_business_names or "-"}\n'
        f'Notes: {notes or "-"}\n'
    )
    _send_bail_lead_notification_email(recipients, subject, body)
    _post_bail_lead_webhook({
        'lead_id': lead_id,
        'county': county,
        'name': full_name,
        'phone': phone,
        'email': email,
        'jail_facility': jail_facility,
        'callback_preference': callback_preference,
        'source': source,
        'routed_order_ids': routed_order_ids,
        'routed_business_names': routed_business_names,
        'created_at': datetime.utcnow().isoformat() + 'Z',
    })
    return _redirect_with_flag('lead_submitted')


@app.route('/advertise')
def advertise_redirect():
    return redirect(url_for('advertise_bail_bonds'))


@app.route('/advertise/bail-bonds', methods=['GET', 'POST'])
def advertise_bail_bonds():
    package_options = _bail_ad_public_packages()
    package_ids = {pkg['id'] for pkg in package_options}

    form_data = {
        'business_name': '',
        'contact_name': '',
        'email': '',
        'phone': '',
        'website_url': '',
        'license_number': '',
        'counties_served': '',
        'package_interest': '',
        'monthly_budget': '',
        'message': '',
    }
    errors = []
    submitted = request.args.get('submitted') == '1'

    if request.method == 'POST':
        form_data = {
            'business_name': (request.form.get('business_name') or '').strip()[:120],
            'contact_name': (request.form.get('contact_name') or '').strip()[:120],
            'email': (request.form.get('email') or '').strip().lower()[:160],
            'phone': (request.form.get('phone') or '').strip()[:40],
            'website_url': (request.form.get('website_url') or '').strip()[:300],
            'license_number': (request.form.get('license_number') or '').strip()[:80],
            'counties_served': (request.form.get('counties_served') or '').strip()[:500],
            'package_interest': _normalize_bail_ad_package_id((request.form.get('package_interest') or '').strip()[:32]),
            'monthly_budget': (request.form.get('monthly_budget') or '').strip()[:32],
            'message': (request.form.get('message') or '').strip()[:1200],
        }

        if not form_data['business_name']:
            errors.append('Business name is required.')
        if not form_data['contact_name']:
            errors.append('Contact name is required.')
        if not form_data['email'] or '@' not in form_data['email']:
            errors.append('A valid email is required.')
        if not form_data['phone']:
            errors.append('Phone number is required.')
        if not form_data['license_number']:
            errors.append('State license number is required.')
        if not form_data['counties_served']:
            errors.append('Please list at least one county served.')
        if form_data['package_interest'] and form_data['package_interest'] not in package_ids:
            errors.append('Selected package is invalid.')
        if request.form.get('policy_ack') != 'yes':
            errors.append('You must confirm the advertising policy.')

        budget_cents = _parse_budget_cents(form_data['monthly_budget'])
        source = (request.form.get('source') or request.args.get('source') or 'bail_ad_page').strip()[:80]
        if not errors:
            ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
            referrer = (request.referrer or '')[:500]
            conn = get_db()
            conn.execute(
                '''
                INSERT INTO bail_ad_inquiries (
                    business_name, contact_name, email, phone, website_url,
                    license_number, counties_served, package_interest,
                    monthly_budget_cents, message, source, status, ip_hash, referrer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    form_data['business_name'],
                    form_data['contact_name'],
                    form_data['email'],
                    form_data['phone'],
                    form_data['website_url'],
                    form_data['license_number'],
                    form_data['counties_served'],
                    form_data['package_interest'],
                    budget_cents,
                    form_data['message'],
                    source,
                    'pending',
                    ip_hash,
                    referrer,
                ),
            )
            conn.commit()
            conn.close()
            return redirect(url_for('advertise_bail_bonds', submitted='1'))

    return render_template(
        'advertise_bail_bonds.html',
        package_options=package_options,
        form_data=form_data,
        form_errors=errors,
        submitted=submitted,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@app.route('/advertise/bail-bonds/checkout', methods=['GET', 'POST'])
def advertise_bail_bonds_checkout():
    package_map = _bail_ad_package_lookup()
    package_options = _bail_ad_public_packages()
    package_ids = {pkg['id'] for pkg in package_options}
    addon_options = _bail_ad_addons()
    addon_lookup = _bail_ad_addon_lookup()
    if not _bail_ad_checkout_ready():
        return render_template(
            'advertise_bail_checkout.html',
            package_options=package_options,
            addon_options=addon_options,
            addon_lookup=addon_lookup,
            form_data={},
            form_errors=['Secure checkout is not configured yet. Please contact support.'],
            checkout_ready=False,
            current_year=datetime.now().year,
            active_nav='advertise',
        ), 503

    prefill_package = _normalize_bail_ad_package_id(request.values.get('package'))
    if prefill_package not in package_ids:
        prefill_package = ''

    form_data = {
        'business_name': (request.values.get('business_name') or '').strip()[:120],
        'contact_name': (request.values.get('contact_name') or '').strip()[:120],
        'email': (request.values.get('email') or '').strip().lower()[:160],
        'phone': (request.values.get('phone') or '').strip()[:40],
        'website_url': (request.values.get('website_url') or '').strip()[:300],
        'license_number': (request.values.get('license_number') or '').strip()[:80],
        'county_targets': (request.values.get('county_targets') or '').strip()[:500],
        'package_id': prefill_package,
        'billing_cycle': 'monthly',
        'source': (request.args.get('source') or 'bail_ad_checkout').strip()[:80],
        'add_on_ids': [],
    }
    errors = []

    if request.method == 'POST':
        form_data = {
            'business_name': (request.form.get('business_name') or '').strip()[:120],
            'contact_name': (request.form.get('contact_name') or '').strip()[:120],
            'email': (request.form.get('email') or '').strip().lower()[:160],
            'phone': (request.form.get('phone') or '').strip()[:40],
            'website_url': (request.form.get('website_url') or '').strip()[:300],
            'license_number': (request.form.get('license_number') or '').strip()[:80],
            'county_targets': (request.form.get('county_targets') or '').strip()[:500],
            'package_id': _normalize_bail_ad_package_id((request.form.get('package_id') or '').strip()[:32]),
            'billing_cycle': (request.form.get('billing_cycle') or 'monthly').strip().lower()[:16],
            'source': (request.form.get('source') or 'bail_ad_checkout').strip()[:80],
            'add_on_ids': _parse_addon_ids(request.form.getlist('add_on_ids')),
        }
        if not form_data['business_name']:
            errors.append('Business name is required.')
        if not form_data['contact_name']:
            errors.append('Contact name is required.')
        if '@' not in form_data['email']:
            errors.append('Valid contact email is required.')
        if not form_data['phone']:
            errors.append('Phone number is required.')
        if not form_data['license_number']:
            errors.append('License number is required.')
        if form_data['package_id'] not in package_ids:
            errors.append('Please select a valid package.')
        if form_data['billing_cycle'] not in {'monthly', 'annual'}:
            errors.append('Billing cycle is invalid.')

        selected_package = package_map.get(form_data['package_id']) if form_data['package_id'] in package_ids else None
        parsed_counties = _parse_county_targets(form_data['county_targets'])
        if selected_package:
            package_id = selected_package.get('id')
            slot_count = int(selected_package.get('county_slots') or 0)
            if package_id == 'exclusive_county_sponsorship' and len(parsed_counties) != 1:
                errors.append('Exclusive County Sponsorship requires exactly one county target.')
            elif package_id == 'gold_bond_bundle':
                if len(parsed_counties) < 2:
                    errors.append('The Gold Bond Bundle requires exactly two county targets.')
                elif len(parsed_counties) > 2:
                    errors.append('The Gold Bond Bundle includes two county targets. Please select two.')
            elif slot_count > 0 and len(parsed_counties) < slot_count:
                errors.append(f"Please provide at least {slot_count} county target{'s' if slot_count != 1 else ''}.")

        if request.form.get('policy_ack') != 'yes':
            errors.append('Advertising policy acknowledgement is required.')
        if request.form.get('terms_ack') != 'yes':
            errors.append('You must accept billing terms to continue.')

        if not errors:
            package = package_map[form_data['package_id']]
            base_amount_cents = _bail_ad_price_cents(
                form_data['package_id'],
                form_data['billing_cycle'],
                parsed_counties,
            ) or 0
            addon_amount_cents = _bail_ad_addon_total_cents(form_data['add_on_ids'], form_data['billing_cycle'])
            amount_cents = base_amount_cents + addon_amount_cents
            if amount_cents <= 0:
                errors.append('Unable to price selected package.')
            else:
                stripe_keys = _stripe_keys()
                stripe.api_key = stripe_keys['secret_key']
                onboarding_token = secrets.token_urlsafe(24)
                interval = 'year' if form_data['billing_cycle'] == 'annual' else 'month'
                metadata_county_targets = (
                    'all_counties'
                    if package.get('all_counties')
                    else ','.join(parsed_counties)
                )
                checkout_payload = {
                    'mode': 'subscription',
                    'line_items': [{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {
                                'name': f"Montana Blotter Bail Ad - {package['name']}",
                            },
                            'unit_amount': amount_cents,
                            'recurring': {'interval': interval},
                        },
                        'quantity': 1,
                    }],
                    'success_url': f'{BASE_URL}/advertise/bail-bonds/checkout/success?session_id={{CHECKOUT_SESSION_ID}}',
                    'cancel_url': f'{BASE_URL}/advertise/bail-bonds/checkout/cancel',
                    'customer_email': form_data['email'],
                    'allow_promotion_codes': False,
                    'billing_address_collection': 'auto',
                    'metadata': {
                        'flow': 'bail_ad',
                        'package_id': form_data['package_id'],
                        'billing_cycle': form_data['billing_cycle'],
                        'business_name': form_data['business_name'],
                        'contact_name': form_data['contact_name'],
                        'email': form_data['email'],
                        'phone': form_data['phone'],
                        'website_url': form_data['website_url'],
                        'license_number': form_data['license_number'],
                        'county_targets': metadata_county_targets,
                        'source': form_data['source'],
                        'add_on_ids': ','.join(form_data['add_on_ids']),
                        'onboarding_token': onboarding_token,
                    },
                }
                try:
                    checkout_session = stripe.checkout.Session.create(**checkout_payload)
                except Exception:
                    errors.append('Unable to start secure checkout right now. Please try again.')
                    checkout_session = None

                if checkout_session:
                    conn = get_db()
                    conn.execute(
                        '''
                        INSERT INTO bail_ad_orders (
                            business_name, contact_name, email, phone, website_url, license_number,
                            county_targets, package_id, billing_cycle, amount_cents, currency, source,
                            add_on_ids, status, provider, provider_session_id, provider_subscription_id, provider_customer_id,
                            onboarding_token
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'usd', ?, ?, 'checkout_pending', 'stripe', ?, ?, ?, ?)
                        ON CONFLICT(provider_session_id) DO UPDATE SET
                            business_name = excluded.business_name,
                            contact_name = excluded.contact_name,
                            email = excluded.email,
                            phone = excluded.phone,
                            website_url = excluded.website_url,
                            license_number = excluded.license_number,
                            county_targets = excluded.county_targets,
                            package_id = excluded.package_id,
                            billing_cycle = excluded.billing_cycle,
                            amount_cents = excluded.amount_cents,
                            source = excluded.source,
                            add_on_ids = excluded.add_on_ids,
                            onboarding_token = excluded.onboarding_token,
                            updated_at = datetime('now')
                        ''',
                        (
                            form_data['business_name'],
                            form_data['contact_name'],
                            form_data['email'],
                            form_data['phone'],
                            form_data['website_url'],
                            form_data['license_number'],
                            ', '.join(parsed_counties),
                            form_data['package_id'],
                            form_data['billing_cycle'],
                            amount_cents,
                            form_data['source'],
                            ','.join(form_data['add_on_ids']),
                            checkout_session.get('id'),
                            checkout_session.get('subscription'),
                            checkout_session.get('customer'),
                            onboarding_token,
                        ),
                    )
                    conn.commit()
                    conn.close()
                    return redirect(checkout_session.get('url'))

    selected_package = package_map.get(form_data.get('package_id') or '')
    return render_template(
        'advertise_bail_checkout.html',
        package_options=package_options,
        addon_options=addon_options,
        addon_lookup=addon_lookup,
        selected_package=selected_package,
        form_data=form_data,
        form_errors=errors,
        checkout_ready=True,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@app.route('/advertise/bail-bonds/checkout/success')
def advertise_bail_checkout_success():
    session_id = (request.args.get('session_id') or '').strip()
    order = None
    package_map = _bail_ad_package_lookup()
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or (getattr(config, 'EMAIL_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )
    if session_id:
        conn = get_db()
        row = conn.execute(
            '''
            SELECT
                id,
                business_name,
                package_id,
                billing_cycle,
                amount_cents,
                currency,
                status,
                onboarding_token,
                county_targets,
                add_on_ids,
                paid_at,
                created_at
            FROM bail_ad_orders
            WHERE provider_session_id = ?
            ORDER BY id DESC
            LIMIT 1
            ''',
            (session_id,),
        ).fetchone()
        conn.close()
        if row:
            order = dict(row)
            package = package_map.get(order.get('package_id') or '')
            order['package_name'] = (package.get('name') if package else '') or (order.get('package_id') or '').replace('_', ' ').title()
    return render_template(
        'advertise_bail_checkout_success.html',
        order=order,
        session_id=session_id,
        support_email=support_email,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@app.route('/advertise/bail-bonds/checkout/cancel')
def advertise_bail_checkout_cancel():
    return render_template(
        'advertise_bail_checkout_cancel.html',
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@app.route('/advertise/bail-bonds/onboarding/<token>', methods=['GET', 'POST'])
def advertise_bail_onboarding(token):
    safe_token = (token or '').strip()[:128]
    conn = get_db()
    row = conn.execute(
        '''
        SELECT id, business_name, package_id, billing_cycle, status, county_targets, onboarding_token
        FROM bail_ad_orders
        WHERE onboarding_token = ?
        LIMIT 1
        ''',
        (safe_token,),
    ).fetchone()
    if not row:
        conn.close()
        return render_template('404.html'), 404

    order = dict(row)
    package = _bail_ad_package_lookup().get(order.get('package_id') or '')
    order['package_name'] = (package.get('name') if package else '') or (order.get('package_id') or '').replace('_', ' ').title()
    creative_row = conn.execute(
        '''
        SELECT id, headline, body_copy, cta_text, target_url, logo_path, status, review_notes, created_at, updated_at
        FROM bail_ad_creatives
        WHERE order_id = ?
        LIMIT 1
        ''',
        (order['id'],),
    ).fetchone()
    creative = dict(creative_row) if creative_row else None

    form_data = {
        'headline': (creative.get('headline') if creative else '') or '',
        'body_copy': (creative.get('body_copy') if creative else '') or '',
        'cta_text': (creative.get('cta_text') if creative else '') or '',
        'target_url': (creative.get('target_url') if creative else '') or '',
    }
    errors = []
    submitted = request.args.get('submitted') == '1'

    if request.method == 'POST':
        form_data = {
            'headline': (request.form.get('headline') or '').strip()[:120],
            'body_copy': (request.form.get('body_copy') or '').strip()[:800],
            'cta_text': (request.form.get('cta_text') or '').strip()[:50],
            'target_url': (request.form.get('target_url') or '').strip()[:300],
        }
        if not form_data['headline']:
            errors.append('Headline is required.')
        if not form_data['body_copy']:
            errors.append('Body copy is required.')
        if not form_data['target_url']:
            errors.append('Target URL is required.')

        logo_file = request.files.get('logo_file')
        logo_path = (creative.get('logo_path') if creative else '') or ''
        if logo_file and logo_file.filename:
            if not _bail_ad_allowed_asset(logo_file.filename):
                errors.append('Logo file must be PNG, JPG, JPEG, WEBP, or GIF.')
            else:
                file_name = secure_filename(logo_file.filename)
                token_prefix = safe_token[:12]
                storage_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{token_prefix}_{file_name}"
                ads_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'bail_ads')
                os.makedirs(ads_dir, exist_ok=True)
                abs_path = os.path.join(ads_dir, storage_name)
                logo_file.save(abs_path)
                logo_path = f"/uploads/bail_ads/{storage_name}"

        if not errors:
            conn.execute(
                '''
                INSERT INTO bail_ad_creatives (
                    order_id, headline, body_copy, cta_text, target_url, logo_path, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(order_id) DO UPDATE SET
                    headline = excluded.headline,
                    body_copy = excluded.body_copy,
                    cta_text = excluded.cta_text,
                    target_url = excluded.target_url,
                    logo_path = CASE WHEN excluded.logo_path != '' THEN excluded.logo_path ELSE bail_ad_creatives.logo_path END,
                    status = 'pending',
                    review_notes = NULL,
                    reviewed_by = NULL,
                    reviewed_at = NULL,
                    updated_at = datetime('now')
                ''',
                (
                    order['id'],
                    form_data['headline'],
                    form_data['body_copy'],
                    form_data['cta_text'],
                    form_data['target_url'],
                    logo_path,
                ),
            )
            conn.execute(
                '''
                UPDATE bail_ad_orders
                SET status = CASE
                        WHEN status = 'active' THEN 'active_pending_creative_review'
                        ELSE status
                    END,
                    updated_at = datetime('now')
                WHERE id = ?
                ''',
                (order['id'],),
            )
            conn.commit()
            conn.close()
            return redirect(url_for('advertise_bail_onboarding', token=safe_token, submitted='1'))

    conn.commit()
    conn.close()
    return render_template(
        'advertise_bail_onboarding.html',
        order=order,
        creative=creative,
        form_data=form_data,
        form_errors=errors,
        submitted=submitted,
        active_nav='advertise',
        current_year=datetime.now().year,
    )


@app.route('/developers/api')
@app.route('/api/docs')
def developers_api():
    return render_template(
        'api_docs.html',
        base_url=BASE_URL.rstrip('/'),
        active_nav='api',
        current_year=datetime.now().year,
    )


@app.route('/terms')
def terms():
    return redirect(url_for('terms_of_use'), code=301)


@app.route('/terms-of-use')
def terms_of_use():
    return render_template('terms_of_use.html', current_year=datetime.now().year)


@app.route('/privacy')
def privacy():
    return render_template('privacy.html', current_year=datetime.now().year)


@app.route('/trends')
def trends():
    conn = get_db()
    weekly_snapshot = _weekly_snapshot(conn)
    latest_weekly_digest = _latest_weekly_digest(conn)
    conn.close()
    return render_template(
        'trends.html',
        weekly_snapshot=weekly_snapshot,
        latest_weekly_digest=latest_weekly_digest,
        current_year=datetime.now().year,
    )


@app.route('/blog')
def blog():
    conn = get_db()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10
    latest_weekly_digest = _latest_weekly_digest(conn)
    total = conn.execute(
        'SELECT COUNT(*) FROM blog_posts WHERE published=1').fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    posts = conn.execute(
        'SELECT * FROM blog_posts WHERE published=1 ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (per_page, (page - 1) * per_page)).fetchall()
    conn.close()
    return render_template('blog.html', posts=posts, total=total,
                           page=page, total_pages=total_pages,
                           latest_weekly_digest=latest_weekly_digest,
                           current_year=datetime.now().year)


@app.route('/blog/<slug>')
def blog_post(slug):
    conn = get_db()
    post = conn.execute(
        'SELECT * FROM blog_posts WHERE slug=? AND published=1', (slug,)).fetchone()
    conn.close()
    if not post:
        return render_template('404.html'), 404
    return render_template('blog_post.html', post=post,
                           current_year=datetime.now().year)


# ==========================================
# BLOG — ADMIN
# ==========================================

@app.route('/admin/blog')
@login_required
def admin_blog():
    conn = get_db()
    posts = conn.execute(
        'SELECT * FROM blog_posts ORDER BY created_at DESC').fetchall()
    conn.close()
    return render_template('admin_blog.html', posts=posts)


@app.route('/admin/blog/new', methods=['GET', 'POST'])
@login_required
def admin_blog_new():
    if request.method == 'POST':
        title   = request.form.get('title', '').strip()
        slug    = request.form.get('slug', '').strip() or _slugify(title)
        body    = request.form.get('body', '').strip()
        excerpt = request.form.get('excerpt', '').strip()
        author  = request.form.get('author', 'Montana Blotter').strip()
        published = 1 if request.form.get('published') else 0
        if not title or not body:
            flash('Title and body are required.', 'error')
            return render_template('admin_blog_edit.html', post=None,
                                   form=request.form)
        conn = get_db()
        try:
            conn.execute(
                'INSERT INTO blog_posts (title, slug, body, excerpt, author, published) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (title, slug, body, excerpt, author, published))
            conn.commit()
            flash('Post published!' if published else 'Post saved as draft.', 'success')
            return redirect(url_for('admin_blog'))
        except Exception as e:
            flash(f'Error: {e}', 'error')
        finally:
            conn.close()
    return render_template('admin_blog_edit.html', post=None, form={})


@app.route('/admin/blog/<int:post_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_blog_edit(post_id):
    conn = get_db()
    post = conn.execute('SELECT * FROM blog_posts WHERE id=?', (post_id,)).fetchone()
    if not post:
        conn.close()
        return redirect(url_for('admin_blog'))
    if request.method == 'POST':
        title     = request.form.get('title', '').strip()
        slug      = request.form.get('slug', '').strip() or _slugify(title)
        body      = request.form.get('body', '').strip()
        excerpt   = request.form.get('excerpt', '').strip()
        author    = request.form.get('author', 'Montana Blotter').strip()
        published = 1 if request.form.get('published') else 0
        conn.execute(
            'UPDATE blog_posts SET title=?, slug=?, body=?, excerpt=?, author=?, '
            'published=?, updated_at=datetime("now") WHERE id=?',
            (title, slug, body, excerpt, author, published, post_id))
        conn.commit()
        conn.close()
        flash('Post updated.', 'success')
        return redirect(url_for('admin_blog'))
    conn.close()
    return render_template('admin_blog_edit.html', post=post, form=post)


@app.route('/admin/blog/<int:post_id>/delete', methods=['POST'])
@login_required
def admin_blog_delete(post_id):
    conn = get_db()
    conn.execute('DELETE FROM blog_posts WHERE id=?', (post_id,))
    conn.commit()
    conn.close()
    flash('Post deleted.', 'success')
    return redirect(url_for('admin_blog'))


@app.route('/record/<int:record_id>')
def view_record(record_id):
    """Public view of individual record"""
    conn = get_db()
    
    record = conn.execute('''
        SELECT records.*,
               blotters.filename AS blotter_filename,
               blotters.file_path,
               blotters.upload_date,
               blotters.source_type AS blotter_source_type,
               blotters.source_document_id,
               posts.id AS post_id,
               posts.title AS post_title,
               posts.agency_name,
               posts.incident_date,
               source_documents.source_type,
               source_documents.source_sender,
               source_documents.source_subject,
               source_documents.source_received_at,
               source_documents.filename AS source_filename,
               source_documents.extraction_method,
               source_documents.extraction_warnings
        FROM records
        LEFT JOIN blotters ON records.blotter_id = blotters.id
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        LEFT JOIN source_documents ON source_documents.id = blotters.source_document_id
        WHERE records.id = ?
    ''', (record_id,)).fetchone()
    
    if not record:
        flash('Record not found')
        conn.close()
        return redirect(url_for('index'))
    
    # Get command logs
    logs = conn.execute('''
        SELECT * FROM command_logs
        WHERE record_id = ?
        ORDER BY timestamp
    ''', (record_id,)).fetchall()

    sibling_records = conn.execute(
        '''
        SELECT id, time, incident_type, location
        FROM records
        WHERE blotter_id = ? AND id != ?
        ORDER BY date, time, id
        LIMIT 6
        ''',
        (record['blotter_id'], record_id)
    ).fetchall()

    blotter_records = conn.execute(
        '''
        SELECT cfs_number, date, time,
               COALESCE(incident_type, incident, '') AS incident_type,
               COALESCE(location, '') AS location,
               COALESCE(details, '') AS details,
               county
        FROM records
        WHERE blotter_id = ?
        ORDER BY date, time, id
        ''',
        (record['blotter_id'],)
    ).fetchall()

    provenance_card = _build_record_provenance_card(conn, record, blotter_records)
    
    conn.close()

    source_pdf_name = None
    if record['file_path']:
        source_pdf_name = os.path.basename(record['file_path'])

    return render_template(
        'record_detail.html',
        record=record,
        logs=logs,
        sibling_records=sibling_records,
        provenance_card=provenance_card,
        county_slug=_county_slug_for_name(record['county']),
        source_pdf_name=source_pdf_name,
        current_year=datetime.now().year,
    )


@app.route('/post/<int:post_id>')
def view_post(post_id):
    """Public view of a generated daily activity post with source traceability."""
    conn = get_db()
    post = conn.execute(
        '''
        SELECT posts.*,
               blotters.filename AS blotter_filename,
               blotters.file_path,
               blotters.upload_date,
               blotters.incident_count,
               blotters.source_type AS blotter_source_type,
               blotters.source_document_id,
               source_documents.source_type,
               source_documents.source_sender,
               source_documents.source_subject,
               source_documents.source_received_at,
               source_documents.filename AS source_filename,
               source_documents.extraction_method,
               source_documents.extraction_warnings
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        LEFT JOIN source_documents ON source_documents.id = blotters.source_document_id
        WHERE posts.id = ?
        ''',
        (post_id,),
    ).fetchone()

    if not post:
        conn.close()
        return render_template('404.html'), 404

    records = conn.execute(
        '''
        SELECT id, cfs_number, date, time, incident_type, location, details, officer
        FROM records
        WHERE blotter_id = ?
        ORDER BY date, time, id
        ''',
        (post['blotter_id'],),
    ).fetchall()

    related_posts = conn.execute(
        '''
        SELECT id, title, incident_date, agency_name
        FROM posts
        WHERE county = ? AND id != ?
        ORDER BY incident_date DESC, created_at DESC
        LIMIT 4
        ''',
        (post['county'], post_id),
    ).fetchall()
    provenance_card = _build_provenance_card(conn, post, records)
    county_slug = _county_slug_for_name(post['county'])
    related_pattern_pages = _related_pattern_pages_for_post(records, county_slug=county_slug)
    conn.close()

    city_slug = _city_slug_for_name(post['city'])
    source_pdf_name = None
    if post['file_path']:
        source_pdf_name = os.path.basename(post['file_path'])
    total_incident_count = max(len(records), post['incident_count'] or 0)

    return render_template(
        'post_detail.html',
        post=post,
        records=records,
        total_incident_count=total_incident_count,
        related_posts=related_posts,
        related_pattern_pages=related_pattern_pages,
        provenance_card=provenance_card,
        summary_lines=_summary_lines(post['summary']),
        county_slug=county_slug,
        city_slug=city_slug,
        source_pdf_name=source_pdf_name,
        current_year=datetime.now().year,
    )

@app.route('/posts')
def posts():
    """Public posts page with AI-summarized incidents"""
    county = request.args.get('county', '')
    city = request.args.get('city', '')
    agency_type = request.args.get('agency_type', '')
    q = request.args.get('q', '')
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 20

    conn = get_db()

    # Build filter query
    sql = """
        SELECT posts.*, blotters.county as blotter_county, blotters.file_path AS file_path
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        WHERE 1=1
    """
    params = []

    if county:
        sql += " AND posts.county = ?"
        params.append(county)
    if city:
        sql += " AND posts.city LIKE ?"
        params.append(f'%{city}%')
    if agency_type:
        sql += " AND posts.agency_type = ?"
        params.append(agency_type)
    if q:
        sql += " AND (posts.title LIKE ? OR posts.summary LIKE ?)"
        term = f'%{q}%'
        params.extend([term, term])

    # Total count
    count_sql = f"SELECT COUNT(*) FROM ({sql})"
    total = conn.execute(count_sql, params).fetchone()[0]

    sql += " ORDER BY posts.incident_date DESC, posts.created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    post_rows = conn.execute(sql, params).fetchall()

    # Dropdown options
    counties = [r['county'] for r in conn.execute(
        'SELECT DISTINCT county FROM posts ORDER BY county').fetchall()]
    cities = [r['city'] for r in conn.execute(
        "SELECT DISTINCT city FROM posts WHERE city != '' ORDER BY city").fetchall()]

    conn.close()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        'posts.html',
        posts=post_rows,
        total=total,
        page=page,
        total_pages=total_pages,
        counties=counties,
        cities=cities,
        county=county,
        city=city,
        agency_type=agency_type,
        q=q,
    )


# ==========================================
# ADMIN ROUTES (Login Required)
# ==========================================

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        ip_address = _client_ip()

        conn = get_db()
        is_limited, retry_seconds = _login_rate_limited(conn, username, ip_address)
        if is_limited:
            conn.close()
            retry_minutes = max(1, (retry_seconds + 59) // 60)
            flash(f'Too many login attempts. Try again in about {retry_minutes} minute(s).')
            return render_template('admin_login.html'), 429

        user_row = conn.execute(
            'SELECT * FROM users WHERE username = ?',
            (username,),
        ).fetchone()
        is_valid = bool(user_row and bcrypt.check_password_hash(user_row['password'], password))
        _record_login_attempt(conn, username, ip_address, is_valid)
        conn.close()

        if is_valid:
            session.clear()
            session['_csrf_token'] = secrets.token_urlsafe(32)
            login_user(User(user_row['id'], user_row['username']))
            return redirect(url_for('admin_dashboard'))
        
        flash('Invalid credentials')
    
    return render_template('admin_login.html')

@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
    session.clear()
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin_dashboard():
    """Admin dashboard with stats and management"""
    
    conn = get_db()
    
    # Get statistics
    total_records = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    total_blotters = conn.execute('SELECT COUNT(*) FROM blotters').fetchone()[0]
    total_counties = conn.execute('SELECT COUNT(DISTINCT county) FROM records').fetchone()[0]
    failed_ingestions = conn.execute(
        "SELECT COUNT(*) FROM ingestion_jobs WHERE status = 'failed'"
    ).fetchone()[0]
    
    # Get recent blotters
    recent_blotters = conn.execute('''
        SELECT * FROM blotters 
        ORDER BY upload_date DESC 
        LIMIT 10
    ''').fetchall()
    
    # Get county breakdown
    county_stats = conn.execute('''
        SELECT county, COUNT(*) as count 
        FROM records 
        GROUP BY county 
        ORDER BY count DESC
    ''').fetchall()
    
    conn.close()
    
    return render_template('admin_dashboard.html',
                         total_records=total_records,
                         total_blotters=total_blotters,
                         total_counties=total_counties,
                         failed_ingestions=failed_ingestions,
                         recent_blotters=recent_blotters,
                         county_stats=county_stats)


@app.route('/admin/ingestion')
@login_required
def admin_ingestion():
    """Inspect failed and recent ingestion jobs."""
    status_filter = request.args.get('status', 'failed')
    if status_filter not in ('failed', 'published', 'all'):
        status_filter = 'failed'

    conn = get_db()
    where_clause = ''
    params = []
    if status_filter != 'all':
        where_clause = 'WHERE ij.status = ?'
        params.append(status_filter)

    jobs = conn.execute(
        f'''
        SELECT
            ij.id,
            ij.status,
            ij.retry_count,
            ij.last_error,
            ij.started_at,
            ij.finished_at,
            sd.id AS source_document_id,
            sd.source_type,
            sd.source_sender,
            sd.source_subject,
            sd.source_received_at,
            sd.filename AS source_filename,
            sd.storage_path,
            sd.raw_text,
            b.id AS blotter_id,
            b.filename AS blotter_filename,
            b.county AS blotter_county,
            EXISTS(SELECT 1 FROM posts p WHERE p.blotter_id = b.id) AS has_post,
            (
                SELECT pe.stage
                FROM pipeline_events pe
                WHERE pe.ingestion_job_id = ij.id
                ORDER BY pe.id DESC
                LIMIT 1
            ) AS latest_stage,
            (
                SELECT pe.status
                FROM pipeline_events pe
                WHERE pe.ingestion_job_id = ij.id
                ORDER BY pe.id DESC
                LIMIT 1
            ) AS latest_stage_status,
            (
                SELECT pe.details_json
                FROM pipeline_events pe
                WHERE pe.ingestion_job_id = ij.id
                ORDER BY pe.id DESC
                LIMIT 1
            ) AS latest_details_json
        FROM ingestion_jobs ij
        JOIN source_documents sd ON sd.id = ij.source_document_id
        LEFT JOIN blotters b ON b.source_document_id = sd.id
        {where_clause}
        ORDER BY
            CASE ij.status WHEN 'failed' THEN 0 WHEN 'received' THEN 1 WHEN 'published' THEN 2 ELSE 3 END,
            COALESCE(ij.finished_at, ij.started_at) DESC
        LIMIT 100
        ''',
        params,
    ).fetchall()

    counts = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS published_count,
            COUNT(*) AS total_count
        FROM ingestion_jobs
        """
    ).fetchone()
    conn.close()

    parsed_jobs = []
    for job in jobs:
        details = {}
        if job['latest_details_json']:
            try:
                details = json.loads(job['latest_details_json'])
            except json.JSONDecodeError:
                details = {'raw': job['latest_details_json']}
        job_dict = dict(job)
        job_dict['latest_details'] = details
        job_dict['source_excerpt'] = ((job['raw_text'] or '')[:180] + '...') if job['raw_text'] and len(job['raw_text']) > 180 else (job['raw_text'] or '')
        parsed_jobs.append(job_dict)

    return render_template(
        'admin_ingestion.html',
        jobs=parsed_jobs,
        status_filter=status_filter,
        failed_count=counts['failed_count'] or 0,
        published_count=counts['published_count'] or 0,
        total_count=counts['total_count'] or 0,
    )


@app.route('/admin/ingestion/<int:job_id>/retry', methods=['POST'])
@login_required
def admin_retry_ingestion(job_id):
    """Retry a failed ingestion job from its stored source document."""
    conn = get_db()
    job = conn.execute(
        '''
        SELECT
            ij.id,
            ij.source_document_id,
            ij.status,
            sd.source_type,
            sd.source_sender,
            sd.storage_path,
            sd.raw_text
        FROM ingestion_jobs ij
        JOIN source_documents sd ON sd.id = ij.source_document_id
        WHERE ij.id = ?
        ''',
        (job_id,),
    ).fetchone()
    conn.close()

    if not job:
        flash('Ingestion job not found.')
        return redirect(url_for('admin_ingestion'))

    from pipeline_state import log_pipeline_event, set_ingestion_job_status
    from processor import process_new_blotter, process_text_blotter

    try:
        set_ingestion_job_status(job_id, 'received', last_error=None, finished=False)
        log_pipeline_event(job_id, 'retry', 'ok', {'message': 'manual-retry-started'})

        if job['source_type'] in ('imap_pdf', 'local_pdf'):
            storage_path = job['storage_path']
            if not storage_path or not os.path.exists(storage_path):
                raise FileNotFoundError('Stored PDF file is no longer available')
            blotter_id = process_new_blotter(
                storage_path,
                source_document_id=job['source_document_id'],
                ingestion_job_id=job_id,
            )
        elif job['source_type'] == 'imap_text':
            raw_text = job['raw_text']
            if not raw_text:
                raise ValueError('Stored email body is empty')
            blotter_id = process_text_blotter(
                raw_text,
                sender_email=job['source_sender'],
                source_document_id=job['source_document_id'],
                ingestion_job_id=job_id,
            )
        else:
            raise ValueError(f"Unsupported source type: {job['source_type']}")

        flash(f'Retried ingestion job #{job_id}. Blotter #{blotter_id} processed.')
    except Exception as e:
        log_pipeline_event(job_id, 'retry', 'error', {'error': str(e)})
        set_ingestion_job_status(job_id, 'failed', last_error=str(e), finished=True)
        flash(f'Retry failed for job #{job_id}: {e}')

    return redirect(url_for('admin_ingestion'))

@app.route('/admin/upload', methods=['GET', 'POST'])
@login_required
def admin_upload():
    """Admin PDF upload"""
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected')
            return redirect(request.url)
        
        file = request.files['file']
        county = request.form.get('county', '')
        
        if file.filename == '':
            flash('No file selected')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Process the PDF
            try:
                from processor import process_new_blotter
                batch_id = process_new_blotter(filepath, county if county else None)
                flash(f'✅ Successfully processed! Batch #{batch_id} with incidents added.')
                return redirect(url_for('admin_dashboard'))
            except Exception as e:
                flash(f'Error processing PDF: {str(e)}')
                return redirect(request.url)
        
        flash('Invalid file type. PDF only.')
        return redirect(request.url)
    
    # GET request - show upload form
    return render_template('admin_upload.html', counties=config.MONTANA_COUNTIES)

@app.route('/admin/blotters')
@login_required
def admin_blotters():
    """View and manage all blotters"""
    conn = get_db()
    blotters = conn.execute('SELECT * FROM blotters ORDER BY upload_date DESC').fetchall()
    # Fetch the post (id + case_status) associated with each blotter
    latest_fb_queue = {
        row['post_id']: {
            'fb_status': row['status'],
            'fb_queue_id': row['id'],
            'facebook_post_id': row['facebook_post_id'],
        }
        for row in conn.execute(
            '''
            SELECT q.id, q.post_id, q.status, q.facebook_post_id
            FROM facebook_post_queue q
            JOIN (
                SELECT post_id, MAX(id) AS latest_id
                FROM facebook_post_queue
                GROUP BY post_id
            ) latest ON latest.latest_id = q.id
            '''
        )
    }

    posts_map = {}
    for row in conn.execute('SELECT id, blotter_id, case_status FROM posts'):
        fb = latest_fb_queue.get(row['id'], {})
        posts_map[row['blotter_id']] = {
            'id': row['id'],
            'case_status': row['case_status'] or 'pending',
            'fb_status': fb.get('fb_status'),
            'fb_queue_id': fb.get('fb_queue_id'),
            'facebook_post_id': fb.get('facebook_post_id'),
        }
    conn.close()
    return render_template('admin_blotters.html', blotters=blotters, posts_map=posts_map)

@app.route('/admin/blotter/<int:blotter_id>/delete', methods=['POST'])
@login_required
def admin_delete_blotter(blotter_id):
    """Delete a blotter and its records"""
    conn = get_db()
    
    # Foreign-key cascades are enabled on this connection, but delete posts explicitly
    # to clean up legacy databases that may have been created without enforcement.
    conn.execute('DELETE FROM posts WHERE blotter_id = ?', (blotter_id,))
    conn.execute('DELETE FROM records WHERE blotter_id = ?', (blotter_id,))
    conn.execute('DELETE FROM blotters WHERE id = ?', (blotter_id,))
    
    conn.commit()
    conn.close()
    
    flash('Blotter deleted successfully')
    return redirect(url_for('admin_blotters'))

@app.route('/admin/post/<int:post_id>/redact', methods=['GET', 'POST'])
@login_required
def admin_redact_post(post_id):
    """PII Redaction Editor — highlight, black-bar, and save a sanitised post summary."""
    conn = get_db()
    post = conn.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        conn.close()
        flash('Post not found.')
        return redirect(url_for('admin_blotters'))

    if request.method == 'POST':
        redacted_summary = request.form.get('redacted_summary', '').strip()
        mark_clean       = request.form.get('mark_clean', '') == '1'
        new_status       = 'clean' if mark_clean else (post['audit_status'] or 'pending')
        conn.execute(
            'UPDATE posts SET summary = ?, audit_status = ? WHERE id = ?',
            (redacted_summary, new_status, post_id),
        )
        conn.commit()
        conn.close()
        flash('Post redacted and saved successfully.' if mark_clean
              else 'Draft saved — not yet marked clean.')
        return redirect(url_for('admin_redact_post', post_id=post_id))

    # Build PII spans from current summary
    from blotter_auditor import get_pii_spans
    summary    = post['summary'] or ''
    pii_spans  = get_pii_spans(summary)
    conn.close()
    return render_template('admin_redaction.html',
                           post=post,
                           pii_spans=pii_spans)


@app.route('/admin/post/<int:post_id>/status', methods=['POST'])
@login_required
def admin_update_post_status(post_id):
    """AJAX endpoint — cycle case_status for a post (active / pending / resolved)."""
    data = request.get_json(force=True) or {}
    new_status = data.get('status', 'pending')
    if new_status not in ('active', 'pending', 'resolved'):
        return jsonify({'ok': False, 'error': 'invalid status'}), 400
    conn = get_db()
    conn.execute('UPDATE posts SET case_status = ? WHERE id = ?', (new_status, post_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'status': new_status})


def _safe_redirect_target(raw_target: str):
    target = (raw_target or '').strip()
    if target.startswith('/') and not target.startswith('//'):
        return target
    return None


@app.route('/admin/facebook', methods=['GET', 'POST'])
@login_required
def admin_facebook():
    if request.method == 'POST':
        action = (request.form.get('action') or '').strip()
        next_target = _safe_redirect_target(request.form.get('next') or '')

        if action == 'save_settings':
            settings = save_facebook_settings({
                'page_id': request.form.get('page_id', ''),
                'access_token': request.form.get('access_token', ''),
                'base_url': request.form.get('base_url', ''),
                'template': request.form.get('template', ''),
                'enabled': request.form.get('enabled'),
                'auto_enqueue_enabled': request.form.get('auto_enqueue_enabled'),
                'auto_publish_enabled': request.form.get('auto_publish_enabled'),
                'max_per_run': request.form.get('max_per_run', '3'),
            })
            flash(
                f"Facebook settings saved. "
                f"{'Enabled' if settings['enabled'] else 'Disabled'} / "
                f"{'Auto-publish ON' if settings['auto_publish_enabled'] else 'Auto-publish OFF'}."
            )

        elif action == 'queue_post':
            post_id_raw = request.form.get('post_id', '0')
            try:
                post_id = int(post_id_raw)
            except ValueError:
                post_id = 0

            if post_id <= 0:
                flash('Invalid post ID.')
            else:
                result = queue_post(
                    post_id=post_id,
                    created_by_user_id=current_user.id,
                    enqueue_source='admin_manual',
                )
                if not result.get('ok'):
                    flash('Unable to queue post (not found).')
                elif result.get('created'):
                    flash(f'Queued post #{post_id} for Facebook.')
                elif result.get('requeued'):
                    flash(f'Re-queued post #{post_id} for Facebook retry.')
                else:
                    flash(f'Post #{post_id} already in queue ({result.get("status")}).')

        elif action == 'queue_recent':
            limit_raw = request.form.get('limit', '10')
            try:
                limit = int(limit_raw)
            except ValueError:
                limit = 10
            stats = queue_recent_posts(
                limit=limit,
                created_by_user_id=current_user.id,
                enqueue_source='admin_bulk_recent',
            )
            flash(
                f"Queue recent complete. "
                f"Created: {stats['created']}, Re-queued: {stats['requeued']}, Skipped: {stats['skipped']}."
            )

        elif action == 'publish_queue_item':
            queue_id_raw = request.form.get('queue_id', '0')
            try:
                queue_id = int(queue_id_raw)
            except ValueError:
                queue_id = 0
            if queue_id <= 0:
                flash('Invalid queue item.')
            else:
                result = publish_queue_item(queue_id)
                if result.get('ok'):
                    flash(f"Published queue item #{queue_id} to Facebook ({result.get('facebook_post_id')}).")
                else:
                    flash(f"Queue item #{queue_id} failed: {result.get('error')}.")

        elif action == 'retry_queue_item':
            queue_id_raw = request.form.get('queue_id', '0')
            try:
                queue_id = int(queue_id_raw)
            except ValueError:
                queue_id = 0
            if queue_id <= 0:
                flash('Invalid queue item.')
            else:
                conn = get_db()
                updated = conn.execute(
                    """
                    UPDATE facebook_post_queue
                    SET status = 'queued',
                        last_error = NULL,
                        scheduled_for = datetime('now'),
                        updated_at = datetime('now')
                    WHERE id = ? AND status IN ('failed', 'skipped')
                    """,
                    (queue_id,),
                ).rowcount
                conn.commit()
                conn.close()
                if updated:
                    flash(f'Queue item #{queue_id} moved back to queued.')
                else:
                    flash(f'Queue item #{queue_id} is not retryable.')

        elif action == 'run_publisher':
            max_items_raw = request.form.get('max_items', '')
            max_items = None
            if max_items_raw:
                try:
                    max_items = int(max_items_raw)
                except ValueError:
                    max_items = None
            stats = run_facebook_queue(max_items=max_items, manual_trigger=True)
            if stats.get('ok'):
                if stats.get('skipped_reason'):
                    flash(f"Publisher skipped: {stats['skipped_reason']}.")
                else:
                    flash(
                        f"Publisher run complete. "
                        f"Processed: {stats['processed']}, Posted: {stats['posted']}, Failed: {stats['failed']}."
                    )
            else:
                flash(f"Publisher failed: {stats.get('error', 'unknown error')}.")

        else:
            flash('Unknown Facebook action.')

        return redirect(next_target or url_for('admin_facebook'))

    conn = get_db()
    settings = load_facebook_settings(conn)
    token_preview = mask_token(settings.get('access_token', ''))

    latest_queue_by_post = {
        row['post_id']: row['status']
        for row in conn.execute(
            '''
            SELECT q.post_id, q.status
            FROM facebook_post_queue q
            JOIN (
                SELECT post_id, MAX(id) AS latest_id
                FROM facebook_post_queue
                GROUP BY post_id
            ) latest ON latest.latest_id = q.id
            '''
        ).fetchall()
    }

    recent_posts = []
    for row in conn.execute(
        """
        SELECT id, title, county, agency_name, incident_date, created_at
        FROM posts
        ORDER BY incident_date DESC, created_at DESC
        LIMIT 25
        """
    ).fetchall():
        item = dict(row)
        item['queue_status'] = latest_queue_by_post.get(row['id'])
        recent_posts.append(item)

    queue_rows = conn.execute(
        """
        SELECT
            q.id,
            q.post_id,
            q.status,
            q.scheduled_for,
            q.attempts,
            q.max_attempts,
            q.facebook_post_id,
            q.last_error,
            q.enqueue_source,
            q.created_at,
            q.posted_at,
            p.title,
            p.county,
            p.agency_name,
            p.incident_date
        FROM facebook_post_queue q
        LEFT JOIN posts p ON p.id = q.post_id
        ORDER BY
            CASE q.status
                WHEN 'processing' THEN 0
                WHEN 'queued' THEN 1
                WHEN 'failed' THEN 2
                WHEN 'posted' THEN 3
                ELSE 4
            END,
            datetime(COALESCE(q.scheduled_for, q.created_at)) ASC,
            q.id DESC
        LIMIT 120
        """
    ).fetchall()
    conn.close()

    return render_template(
        'admin_facebook.html',
        settings=settings,
        token_preview=token_preview,
        queue_rows=queue_rows,
        recent_posts=recent_posts,
    )


@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    """Admin settings - change password"""
    
    if request.method == 'POST':
        new_password = request.form.get('new_password')
        
        if new_password:
            conn = get_db()
            hashed_pw = bcrypt.generate_password_hash(new_password).decode('utf-8')
            conn.execute('UPDATE users SET password = ? WHERE id = ?', 
                        (hashed_pw, current_user.id))
            conn.commit()
            conn.close()
            
            flash('Password updated successfully')
            return redirect(url_for('admin_dashboard'))
    
    return render_template('admin_settings.html')

@app.route('/admin/emails', methods=['GET', 'POST'])
@login_required
def admin_emails():
    """Manage emails and send bulk emails to sheriffs"""
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'resend_bounced':
            from resend_bounced import run as resend_run
            resent, skipped = resend_run()
            flash(f'✅ Bounced emails processed — Resent: {resent} | Skipped: {skipped}')
            return redirect(url_for('admin_emails'))

        if action == 'send_to_sheriffs':
            # Get form data
            counties = request.form.getlist('counties')
            subject = request.form.get('subject', '')
            body = request.form.get('body', '')
            
            if not counties or not subject or not body:
                flash('Please select counties, provide subject and body')
                return redirect(url_for('admin_emails'))
            
            # Sheriffs email database (by county)
            # NOTE: Only entries with confirmed valid MX records are included.
            # The remaining ~50 counties need real addresses looked up from each
            # sheriff's official website — their domains do not have valid DNS MX
            # records and all sends will bounce. Add them here once verified.
            SHERIFFS_EMAILS = {
                'Beaverhead':      'sheriff@beaverheadcounty.gov',
                'Big Horn':        'bso@bighorncountymt.gov',
                'Blaine':          'bcsheriff@blainecounty-mt.gov',
                'Broadwater':      'records@co.broadwater.mt.us',
                'Carbon':          'carboncoso@co.carbon.mt.us',
                'Carter':          'ccsomontana@gmail.com',
                'Cascade':         'info@cascadecountysheriff.org',
                'Chouteau':        'sheriff@chouteaucounty.org',
                'Custer':          'ccso-records@co.custer.mt.us',
                # 'Daniels': TODO — email address could not be verified (URL pasted by mistake)
                'Dawson':          'dcsoadmin@dawsoncountymontana.com',
                'Deer Lodge':      'dlrecords@adlc.us',
                'Fallon':          'sheriff@falloncounty.net',
                'Fergus':          'fcso@co.fergus.mt.us',
                'Flathead':        'fcsorecords@flathead.mt.gov',
                'Gallatin':        'publicrecordsrequests@gallatin.mt.gov',
                'Garfield':        'garfieldcountysheriff@midrivers.com',
                'Glacier':         'sheriffadmin@glaciercountymt.org',
                'Golden Valley':   'gvso@itstriangle.com',
                'Granite':         'sheriff@granitecountymt.gov',
                'Hill':            'hillcosheriff@hillcounty.us',
                'Jefferson':       'tgrimsrud@jeffersoncounty-mt.gov',
                'Judith Basin':    'jbcso@jbcounty.org',
                'Lake':            'lcsorecords@lakemt.gov',
                'Lewis and Clark': 'records@lccountymt.gov',
                'Liberty':         'lcso@libertycountymt.gov',
                'Lincoln':         'lcsoadmin@libbymt.com',
                'Madison':         'mcso@madisoncountymt.gov',
                'McCone':          'mcconesheriff@midrivers.com',
                'Meagher':         'mcso@meagherco.net',
                'Mineral':         'records@co.mineral.mt.us',
                'Missoula':        'MCSOrecords@missoulacounty.us',
                'Musselshell':     'mcso@musselshellcounty.org',
                'Park':            'sheriffrecords@parkcounty.org',
                'Petroleum':       'petcoso@midrivers.com',
                'Phillips':        'sheriff@phillipscountymt.gov',
                'Pondera':         'brandy.egan@ponderacounty.org',
                'Powder River':    'prso@prcounty.com',
                'Powell':          'pcoso@powellcountymt.gov',
                'Prairie':         'klewis@prairiecounty.org',
                'Ravalli':         'rcso-records@rc.mt.gov',
                'Richland':        'rcso-records@richland.org',
                'Roosevelt':       'rcsosheriff@rooseveltcounty.org',
                'Rosebud':         'afulton@rosebudcountymt.com',
                'Sanders':         'sfielders@co.sanders.mt.us',
                'Sheridan':        'ljohnson@sheridancountymt.gov',
                'Silver Bow':      'bsbpolice@bsb.mt.gov',
                'Stillwater':      'carnold@stillwatercountymt.gov',
                'Sweet Grass':     'aronneberg@sgcountymt.gov',
                'Teton':           'tcso@tetoncountymt.gov',
                'Toole':           'tcsorecords@toolecountymt.gov',
                'Treasure':        'msears@treasurecountymt.gov',
                'Valley':          'tboyer@valleycountymt.gov',
                'Wheatland':       'wcdisp@wheatlandcomt.gov',
                'Wibaux':          'wibauxso@midrivers.com',
                'Yellowstone':     'SheriffRecords@yellowstonecountymt.gov',
            }

            POLICE_EMAILS = {
                'Billings PD':   'BPDRecords@billingsmt.gov',
                'Bozeman PD':    'bpdrecords@bozeman.net',
                'Great Falls PD':'gfpdrecords@greatfallsmt.net',
                'Helena PD':     'hpdrecords@helenamt.gov',
                'Kalispell PD':  'kpdrecords@kalispell.com',
                'Missoula PD':   'mpdrecords@ci.missoula.mt.us',
            }

            ALL_AGENCIES = {**SHERIFFS_EMAILS, **POLICE_EMAILS}

            # Load already-contacted agencies
            conn = get_db()
            already_emailed = {
                row[0] for row in conn.execute(
                    'SELECT DISTINCT agency_name FROM emailed_agencies'
                ).fetchall()
            }

            # Split selected agencies into new vs already contacted
            selected = [a for a in counties if a in ALL_AGENCIES]
            skip = [a for a in selected if a in already_emailed]
            to_send = [a for a in selected if a not in already_emailed]

            if not to_send:
                flash(f'All {len(skip)} selected agencies have already been contacted — no emails sent.')
                conn.close()
                return redirect(url_for('admin_emails'))

            # Send only to new agencies
            try:
                from email_worker import EmailWorker
                worker = EmailWorker()
                results = worker.send_bulk_emails(
                    [ALL_AGENCIES[a] for a in to_send], subject, body
                )

                # Log successful sends
                for agency in to_send:
                    email_addr = ALL_AGENCIES[agency]
                    if results.get(email_addr):
                        conn.execute(
                            'INSERT INTO emailed_agencies (agency_name, email_address, subject) VALUES (?, ?, ?)',
                            (agency, email_addr, subject)
                        )
                conn.commit()

                successful = sum(1 for v in results.values() if v)
                failed = len(results) - successful

                msg = f'✅ Emails sent! Success: {successful}/{len(to_send)}'
                if skip:
                    msg += f' | Skipped {len(skip)} already-contacted'
                flash(msg)
                if failed > 0:
                    flash(f'⚠️ Failed to send to {failed} recipients', 'warning')

            except Exception as e:
                flash(f'Error sending emails: {str(e)}')
            finally:
                conn.close()

            return redirect(url_for('admin_emails'))

    police_depts = ['Billings PD', 'Bozeman PD', 'Great Falls PD', 'Helena PD', 'Kalispell PD', 'Missoula PD']
    conn = get_db()
    already_emailed = {
        row[0] for row in conn.execute(
            'SELECT DISTINCT agency_name FROM emailed_agencies'
        ).fetchall()
    }
    conn.close()
    return render_template('admin_emails.html', counties=config.MONTANA_COUNTIES,
                           police_depts=police_depts, already_emailed=already_emailed)

@app.route('/admin/emails/template/<template_type>')
@login_required
def get_email_template(template_type):
    """Get a preset email template"""
    
    TEMPLATES = {
        'blotter_request': {
            'subject': 'Request for Law Enforcement Blotter Records - Montana Blotter Project',
            'body': '''Dear Sheriff,

We are writing to request law enforcement blotter records from your county as part of the Montana Blotter project, a public information initiative to make law enforcement activity more transparent and accessible to citizens.

The Montana Blotter aggregates public blotter information from sheriffs' offices across the state, allowing citizens to search and view recent law enforcement incidents in their area. This helps communities stay informed about public safety activities.

We would greatly appreciate it if your office could provide regular blotter updates (weekly or daily) via email. The appropriate format would be either:
- PDF documents with incident listings
- CSV/Excel spreadsheets with structured data
- Any other standard format your office uses

All information shared will be made publicly available and properly attributed to your department.

Thank you for your time and consideration. Please contact us if you have any questions about this initiative.

Best regards,
Montana Blotter Project
'''
        },
        'follow_up': {
            'subject': 'Follow-up: Law Enforcement Blotter Submission - Montana Blotter',
            'body': '''Dear Sheriff,

We hope you received our previous request regarding law enforcement blotter records for the Montana Blotter project. We have not yet received a response and wanted to follow up.

The Montana Blotter is a valuable public resource for citizens to stay informed about law enforcement activity in their communities. Your county's participation would be greatly appreciated.

If you have any questions or concerns about the project, please feel free to reach out. We are happy to discuss any data sharing arrangements or requirements your office may have.

Thank you,
Montana Blotter Project
'''
        }
    }
    
    if template_type not in TEMPLATES:
        return jsonify({'error': 'Template not found'}), 404
    
    return jsonify(TEMPLATES[template_type])

# ==========================================
# PUBLIC JSON API
# ==========================================

@app.route('/api/posts')
def api_posts():
    conn = get_db()
    page     = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    county      = request.args.get('county', '').strip()
    agency_type = request.args.get('agency_type', '').strip()
    date_from   = request.args.get('date_from', '').strip()
    date_to     = request.args.get('date_to', '').strip()
    search      = request.args.get('search', '').strip()
    ids_raw     = request.args.get('ids', '').strip()

    post_ids = []
    if ids_raw:
        for token in ids_raw.split(','):
            token = token.strip()
            if not token:
                continue
            try:
                value = int(token)
            except (TypeError, ValueError):
                continue
            if value > 0:
                post_ids.append(value)
        if len(post_ids) > 100:
            post_ids = post_ids[:100]
    post_ids = sorted(set(post_ids))

    where, params = [], []
    if post_ids:
        placeholders = ','.join('?' for _ in post_ids)
        where.append(f'id IN ({placeholders})')
        params.extend(post_ids)
    if county:
        where.append('county = ?'); params.append(county)
    if agency_type:
        where.append('agency_type = ?'); params.append(agency_type)
    if date_from:
        where.append('incident_date >= ?'); params.append(date_from)
    if date_to:
        where.append('incident_date <= ?'); params.append(date_to)
    if search:
        where.append('(title LIKE ? OR summary LIKE ?)'); params += [f'%{search}%', f'%{search}%']

    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    total = conn.execute(f'SELECT COUNT(*) FROM posts {clause}', params).fetchone()[0]
    rows  = conn.execute(
        f'SELECT id, title, summary, county, agency_name, agency_type, '
        f'incident_date, incident_type, created_at FROM posts {clause} '
        f'ORDER BY created_at DESC LIMIT ? OFFSET ?',
        params + [per_page, (page - 1) * per_page]
    ).fetchall()
    conn.close()
    posts = []
    for row in rows:
        item = dict(row)
        item['post_url'] = f"{BASE_URL}/post/{item['id']}"
        posts.append(item)

    return jsonify({
        'posts': posts,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (total + per_page - 1) // per_page),
        'filters': {
            'county': county or None,
            'agency_type': agency_type or None,
            'date_from': date_from or None,
            'date_to': date_to or None,
            'search': search or None,
            'ids': post_ids,
        },
    })


@app.route('/api/posts/<int:post_id>')
def api_post(post_id):
    conn = get_db()
    row = conn.execute(
        'SELECT id, title, summary, county, agency_name, agency_type, '
        'incident_date, incident_type, created_at FROM posts WHERE id = ?',
        (post_id,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    payload = dict(row)
    payload['post_url'] = f"{BASE_URL}/post/{post_id}"
    return jsonify(payload)


@app.route('/api/blog')
def api_blog_posts():
    conn = get_db()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))

    total = conn.execute(
        'SELECT COUNT(*) FROM blog_posts WHERE published = 1'
    ).fetchone()[0]
    rows = conn.execute(
        'SELECT id, title, slug, excerpt, author, created_at '
        'FROM blog_posts WHERE published = 1 '
        'ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (per_page, (page - 1) * per_page)
    ).fetchall()
    conn.close()

    return jsonify({
        'posts': [dict(r) for r in rows],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (total + per_page - 1) // per_page)
    })


@app.route('/api/blog/<slug>')
def api_blog_post(slug):
    conn = get_db()
    row = conn.execute(
        'SELECT id, title, slug, excerpt, body, author, created_at '
        'FROM blog_posts WHERE slug = ? AND published = 1',
        (slug,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))


@app.route('/api/counties')
def api_counties():
    conn = get_db()
    rows = conn.execute(
        'SELECT COALESCE(p.county, "Unknown") AS county, '
        'COUNT(DISTINCT p.id) AS post_count, '
        'COUNT(DISTINCT r.id) AS record_count '
        'FROM posts p LEFT JOIN records r ON r.county = p.county '
        'GROUP BY p.county ORDER BY post_count DESC'
    ).fetchall()
    conn.close()
    return jsonify({'counties': [dict(r) for r in rows]})


@app.route('/api/agencies')
def api_agencies():
    conn = get_db()
    rows = conn.execute(
        'SELECT agency_name, agency_type, county, COUNT(*) AS post_count '
        'FROM posts WHERE agency_name IS NOT NULL '
        'GROUP BY agency_name ORDER BY post_count DESC'
    ).fetchall()
    conn.close()
    return jsonify({'agencies': [dict(r) for r in rows]})


# ==========================================
# PUBLIC API - RECORDS
# ==========================================

@app.route('/api/records')
def api_records():
    """List individual incident records with filtering and pagination"""
    conn = get_db()
    page     = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    county        = request.args.get('county', '').strip()
    incident_type = request.args.get('incident_type', '').strip()
    date_from    = request.args.get('date_from', '').strip()
    date_to      = request.args.get('date_to', '').strip()
    search       = request.args.get('search', '').strip()

    where, params = [], []
    if county:
        where.append('county = ?'); params.append(county)
    if incident_type:
        where.append('incident_type = ?'); params.append(incident_type)
    if date_from:
        where.append('date >= ?'); params.append(date_from)
    if date_to:
        where.append('date <= ?'); params.append(date_to)
    if search:
        where.append('(details LIKE ? OR location LIKE ? OR officer LIKE ?)')
        term = f'%{search}%'
        params.extend([term, term, term])

    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    total = conn.execute(f'SELECT COUNT(*) FROM records {clause}', params).fetchone()[0]
    
    rows = conn.execute(
        f'SELECT id, blotter_id, cfs_number, date, time, incident_type, location, details, county, officer, created_at '
        f'FROM records {clause} ORDER BY date DESC, created_at DESC LIMIT ? OFFSET ?',
        params + [per_page, (page - 1) * per_page]
    ).fetchall()
    conn.close()
    
    return jsonify({
        'records': [dict(r) for r in rows],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (total + per_page - 1) // per_page)
    })


@app.route('/api/records/<int:record_id>')
def api_record(record_id):
    """Get a single record with its command logs"""
    conn = get_db()
    row = conn.execute(
        'SELECT * FROM records WHERE id = ?', (record_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    
    logs = conn.execute(
        'SELECT timestamp, officer, entry FROM command_logs WHERE record_id = ? ORDER BY timestamp',
        (record_id,)
    ).fetchall()
    conn.close()
    
    result = dict(row)
    result['command_logs'] = [dict(l) for l in logs]
    return jsonify(result)


# ==========================================
# PUBLIC API - BLOTTERS
# ==========================================

@app.route('/api/blotters')
def api_blotters():
    """List all blotters with pagination"""
    conn = get_db()
    page     = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(1, request.args.get('per_page', 20, type=int)))
    county = request.args.get('county', '').strip()

    where, params = [], []
    if county:
        where.append('county = ?'); params.append(county)

    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    total = conn.execute(f'SELECT COUNT(*) FROM blotters {clause}', params).fetchone()[0]
    
    rows = conn.execute(
        f'SELECT id, filename, county, upload_date, incident_count, source_type '
        f'FROM blotters {clause} ORDER BY upload_date DESC LIMIT ? OFFSET ?',
        params + [per_page, (page - 1) * per_page]
    ).fetchall()
    conn.close()
    
    return jsonify({
        'blotters': [dict(r) for r in rows],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (total + per_page - 1) // per_page)
    })


@app.route('/api/blotters/<int:blotter_id>')
def api_blotter(blotter_id):
    """Get a single blotter with its posts"""
    conn = get_db()
    row = conn.execute(
        'SELECT id, filename, county, upload_date, incident_count, source_type '
        'FROM blotters WHERE id = ?', (blotter_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Not found'}), 404
    
    posts = conn.execute(
        'SELECT id, title, summary, agency_name, agency_type, incident_date, incident_type '
        'FROM posts WHERE blotter_id = ?', (blotter_id,)
    ).fetchall()
    conn.close()
    
    result = dict(row)
    result['posts'] = [dict(p) for p in posts]
    return jsonify(result)


# ==========================================
# PUBLIC API - STATS
# ==========================================

@app.route('/api/stats')
def api_stats():
    """Get comprehensive database statistics"""
    conn = get_db()
    stats = {
        'total_records':    conn.execute('SELECT COUNT(*) FROM records').fetchone()[0],
        'total_posts':      conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0],
        'total_blotters':   conn.execute('SELECT COUNT(*) FROM blotters').fetchone()[0],
        'total_counties':   conn.execute('SELECT COUNT(DISTINCT county) FROM records WHERE county IS NOT NULL').fetchone()[0],
        'total_agencies':  conn.execute('SELECT COUNT(DISTINCT agency_name) FROM posts WHERE agency_name IS NOT NULL').fetchone()[0],
    }
    
    # Latest blotter
    latest = conn.execute(
        'SELECT county, upload_date FROM blotters ORDER BY upload_date DESC LIMIT 1'
    ).fetchone()
    if latest:
        stats['latest_blotter'] = dict(latest)
    
    # Date range
    date_range = conn.execute(
        'SELECT MIN(date) as earliest, MAX(date) as latest FROM records'
    ).fetchone()
    if date_range:
        stats['date_range'] = dict(date_range)
    
    # Top counties by incidents
    top_counties = conn.execute(
        'SELECT county, COUNT(*) as count FROM records WHERE county IS NOT NULL GROUP BY county ORDER BY count DESC LIMIT 10'
    ).fetchall()
    stats['top_counties'] = [dict(r) for r in top_counties]
    
    # Top incident types
    top_types = conn.execute(
        'SELECT incident_type, COUNT(*) as count FROM records WHERE incident_type IS NOT NULL AND incident_type != "" GROUP BY incident_type ORDER BY count DESC LIMIT 10'
    ).fetchall()
    stats['top_incident_types'] = [dict(r) for r in top_types]
    
    conn.close()
    return jsonify(stats)


# ==========================================
# ADMIN ANALYTICS

# ==========================================
# ADMIN ANALYTICS
# ==========================================

@app.route('/admin/donations')
@login_required
def admin_donations():
    launch_snapshot = _donation_launch_snapshot()
    conn = get_db()
    schema_ready = launch_snapshot['schema_ready']
    totals_all_time = {'gross_cents': 0, 'success_count': 0, 'avg_cents': 0}
    totals_mtd = {'gross_cents': 0, 'success_count': 0, 'avg_cents': 0}
    totals_ytd = {'gross_cents': 0, 'success_count': 0, 'avg_cents': 0}
    recurring_stats = {'active_subscriptions': 0, 'monthly_success_count': 0}
    funnel_30d = {
        'donate_view': 0,
        'checkout_start': 0,
        'checkout_success': 0,
        'checkout_cancel': 0,
        'start_rate_pct': 0.0,
        'completion_rate_pct': 0.0,
    }
    webhook_7d = {
        'total': 0,
        'processed': 0,
        'unprocessed': 0,
        'error_count': 0,
    }
    recent_donations = []
    recent_events = []
    recent_webhook_errors = []
    top_sources_30d = []

    try:
        total_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0) AS gross_cents,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS success_count,
                COALESCE(AVG(CASE WHEN status = 'succeeded' THEN amount_cents END), 0) AS avg_cents
            FROM donations
            '''
        ).fetchone()
        if total_row:
            totals_all_time = dict(total_row)

        mtd_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0) AS gross_cents,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS success_count,
                COALESCE(AVG(CASE WHEN status = 'succeeded' THEN amount_cents END), 0) AS avg_cents
            FROM donations
            WHERE created_at >= date('now', 'start of month')
            '''
        ).fetchone()
        if mtd_row:
            totals_mtd = dict(mtd_row)

        ytd_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN amount_cents ELSE 0 END), 0) AS gross_cents,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS success_count,
                COALESCE(AVG(CASE WHEN status = 'succeeded' THEN amount_cents END), 0) AS avg_cents
            FROM donations
            WHERE created_at >= date('now', 'start of year')
            '''
        ).fetchone()
        if ytd_row:
            totals_ytd = dict(ytd_row)

        recurring_row = conn.execute(
            '''
            SELECT
                COALESCE(COUNT(DISTINCT provider_subscription_id), 0) AS active_subscriptions,
                COALESCE(SUM(CASE WHEN status = 'succeeded' AND mode = 'monthly' THEN 1 ELSE 0 END), 0) AS monthly_success_count
            FROM donations
            WHERE provider_subscription_id IS NOT NULL AND provider_subscription_id != ''
            '''
        ).fetchone()
        if recurring_row:
            recurring_stats = dict(recurring_row)

        funnel_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN event_type = 'donate_view' THEN 1 ELSE 0 END), 0) AS donate_view,
                COALESCE(SUM(CASE WHEN event_type = 'checkout_start' THEN 1 ELSE 0 END), 0) AS checkout_start,
                COALESCE(SUM(CASE WHEN event_type = 'checkout_success' THEN 1 ELSE 0 END), 0) AS checkout_success,
                COALESCE(SUM(CASE WHEN event_type = 'checkout_cancel' THEN 1 ELSE 0 END), 0) AS checkout_cancel
            FROM donation_events
            WHERE created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        if funnel_row:
            funnel_30d.update(dict(funnel_row))
            donate_views = float(funnel_30d['donate_view'] or 0)
            starts = float(funnel_30d['checkout_start'] or 0)
            successes = float(funnel_30d['checkout_success'] or 0)
            funnel_30d['start_rate_pct'] = (starts / donate_views * 100.0) if donate_views else 0.0
            funnel_30d['completion_rate_pct'] = (successes / starts * 100.0) if starts else 0.0

        webhook_row = conn.execute(
            '''
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN processed = 1 THEN 1 ELSE 0 END), 0) AS processed,
                COALESCE(SUM(CASE WHEN processed = 0 THEN 1 ELSE 0 END), 0) AS unprocessed,
                COALESCE(SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END), 0) AS error_count
            FROM payment_webhook_events
            WHERE created_at >= date('now', '-7 days')
            '''
        ).fetchone()
        if webhook_row:
            webhook_7d = dict(webhook_row)

        recent_donations = conn.execute(
            '''
            SELECT provider, mode, status, amount_cents, currency, donor_name, source, created_at
            FROM donations
            ORDER BY datetime(created_at) DESC
            LIMIT 30
            '''
        ).fetchall()

        recent_events = conn.execute(
            '''
            SELECT event_type, source, page_path, amount_cents, created_at
            FROM donation_events
            ORDER BY datetime(created_at) DESC
            LIMIT 30
            '''
        ).fetchall()

        recent_webhook_errors = conn.execute(
            '''
            SELECT event_type, error, created_at
            FROM payment_webhook_events
            WHERE error IS NOT NULL AND error != ''
            ORDER BY datetime(created_at) DESC
            LIMIT 20
            '''
        ).fetchall()

        top_sources_30d = conn.execute(
            '''
            SELECT
                COALESCE(NULLIF(source, ''), '(direct)') AS source,
                COUNT(*) AS donation_count,
                COALESCE(SUM(amount_cents), 0) AS gross_cents
            FROM donations
            WHERE status = 'succeeded'
              AND created_at >= date('now', '-30 days')
            GROUP BY COALESCE(NULLIF(source, ''), '(direct)')
            ORDER BY gross_cents DESC, donation_count DESC, source ASC
            LIMIT 10
            '''
        ).fetchall()
    except sqlite3.OperationalError:
        schema_ready = False
    finally:
        conn.close()

    launch_snapshot['schema_ready'] = schema_ready
    launch_snapshot['launch_ready'] = bool(
        launch_snapshot['schema_ready']
        and launch_snapshot['donations_enabled']
        and launch_snapshot['stripe_checkout_ready']
        and launch_snapshot['stripe_webhook_ready']
        and int(launch_snapshot['stale_webhook_events_10m'] or 0) == 0
    )

    donations_enabled = launch_snapshot['donations_enabled']
    stripe_checkout_ready = launch_snapshot['stripe_checkout_ready']
    stripe_webhook_ready = launch_snapshot['stripe_webhook_ready']
    return render_template(
        'admin_donations.html',
        donations_enabled=donations_enabled,
        stripe_checkout_ready=stripe_checkout_ready,
        stripe_webhook_ready=stripe_webhook_ready,
        launch_snapshot=launch_snapshot,
        schema_ready=schema_ready,
        totals_all_time=totals_all_time,
        totals_mtd=totals_mtd,
        totals_ytd=totals_ytd,
        recurring_stats=recurring_stats,
        funnel_30d=funnel_30d,
        webhook_7d=webhook_7d,
        recent_donations=recent_donations,
        recent_events=recent_events,
        recent_webhook_errors=recent_webhook_errors,
        top_sources_30d=top_sources_30d,
    )


@app.route('/admin/donations/preflight')
@login_required
def admin_donations_preflight():
    return jsonify(_donation_launch_snapshot())


@app.route('/admin/donations/reconcile', methods=['POST'])
@login_required
def admin_donations_reconcile():
    try:
        requested_limit = int(request.form.get('limit', 100))
    except (TypeError, ValueError):
        requested_limit = 100
    limit = max(1, min(500, requested_limit))

    conn = get_db()
    succeeded = 0
    failed = 0
    try:
        rows = conn.execute(
            '''
            SELECT event_id, payload_json
            FROM payment_webhook_events
            WHERE provider = 'stripe' AND processed = 0
            ORDER BY datetime(created_at) ASC
            LIMIT ?
            ''',
            (limit,),
        ).fetchall()

        for row in rows:
            event_id = row['event_id']
            payload_text = row['payload_json'] or ''
            try:
                event = json.loads(payload_text)
                if not isinstance(event, dict):
                    raise ValueError('Webhook payload is not a JSON object')

                _apply_stripe_bail_ad_event(conn, event)
                _apply_stripe_event(
                    conn,
                    event,
                    event_source='/admin/donations/reconcile',
                    event_ip_hash='',
                    event_referrer='',
                )
                conn.execute(
                    '''
                    UPDATE payment_webhook_events
                    SET processed = 1, processed_at = datetime('now'), error = NULL
                    WHERE event_id = ?
                    ''',
                    (event_id,),
                )
                conn.commit()
                succeeded += 1
            except Exception as exc:
                conn.rollback()
                conn.execute(
                    '''
                    UPDATE payment_webhook_events
                    SET processed = 0, processed_at = datetime('now'), error = ?
                    WHERE event_id = ?
                    ''',
                    (str(exc)[:500], event_id),
                )
                conn.commit()
                failed += 1
    except sqlite3.OperationalError:
        conn.close()
        flash('Donation webhook tables are not available. Run migration first.', 'error')
        return redirect(url_for('admin_donations'))

    conn.close()
    flash(f'Reconciliation complete. Processed {succeeded} event(s), {failed} failed.', 'success' if failed == 0 else 'warning')
    return redirect(url_for('admin_donations'))


@app.route('/admin/donations/export.csv')
@login_required
def admin_donations_export():
    conn = get_db()
    try:
        rows = conn.execute(
            '''
            SELECT
                id, created_at, updated_at, provider, mode, status, amount_cents, currency,
                donor_name, source, provider_session_id, provider_payment_intent_id,
                provider_subscription_id, email_hash
            FROM donations
            ORDER BY datetime(created_at) DESC
            '''
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return Response('Donation tables are not available.\n', status=503, mimetype='text/plain')
    conn.close()

    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([
        'id',
        'created_at',
        'updated_at',
        'provider',
        'mode',
        'status',
        'amount_cents',
        'amount_usd',
        'currency',
        'donor_name',
        'source',
        'provider_session_id',
        'provider_payment_intent_id',
        'provider_subscription_id',
        'email_hash',
    ])
    for row in rows:
        amount_cents = int(row['amount_cents'] or 0)
        writer.writerow([
            row['id'],
            row['created_at'],
            row['updated_at'],
            row['provider'],
            row['mode'],
            row['status'],
            amount_cents,
            f'{amount_cents / 100:.2f}',
            (row['currency'] or '').upper(),
            row['donor_name'] or '',
            row['source'] or '',
            row['provider_session_id'] or '',
            row['provider_payment_intent_id'] or '',
            row['provider_subscription_id'] or '',
            row['email_hash'] or '',
        ])

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename=donations_export_{timestamp}.csv'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/admin/bail-ads')
@login_required
def admin_bail_ads():
    package_map = _bail_ad_package_lookup()
    stats = {
        'pending': 0,
        'in_review': 0,
        'approved': 0,
        'declined': 0,
        'total': 0,
    }
    order_stats = {
        'checkout_pending': 0,
        'active': 0,
        'active_pending_creative_review': 0,
        'payment_failed': 0,
        'canceled': 0,
        'total': 0,
    }
    inquiries = []
    orders = []
    creatives = []
    performance_30d = {
        'impressions': 0,
        'clicks': 0,
        'leads': 0,
        'ctr_pct': 0.0,
        'lead_rate_pct': 0.0,
    }
    county_performance_30d = []
    renewal_candidates = []
    upgrade_candidates = []
    consumer_pipeline_30d = {
        'calls': 0,
        'qualified_leads': 0,
        'booked_bonds': 0,
        'booked_from_qualified_pct': 0.0,
    }
    county_pipeline_30d = []
    consumer_leads = []
    advertiser_pipeline_30d = []
    schema_ready = True

    conn = get_db()
    try:
        stats_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
                COALESCE(SUM(CASE WHEN status = 'in_review' THEN 1 ELSE 0 END), 0) AS in_review,
                COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0) AS approved,
                COALESCE(SUM(CASE WHEN status = 'declined' THEN 1 ELSE 0 END), 0) AS declined,
                COUNT(*) AS total
            FROM bail_ad_inquiries
            '''
        ).fetchone()
        if stats_row:
            stats = dict(stats_row)

        inquiries = conn.execute(
            '''
            SELECT
                id, business_name, contact_name, email, phone, website_url, license_number,
                counties_served, package_interest, monthly_budget_cents, source, status,
                review_notes, reviewed_by, reviewed_at, created_at
            FROM bail_ad_inquiries
            ORDER BY datetime(created_at) DESC
            LIMIT 200
            '''
        ).fetchall()

        order_stats_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status = 'checkout_pending' THEN 1 ELSE 0 END), 0) AS checkout_pending,
                COALESCE(SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END), 0) AS active,
                COALESCE(SUM(CASE WHEN status = 'active_pending_creative_review' THEN 1 ELSE 0 END), 0) AS active_pending_creative_review,
                COALESCE(SUM(CASE WHEN status = 'payment_failed' THEN 1 ELSE 0 END), 0) AS payment_failed,
                COALESCE(SUM(CASE WHEN status = 'canceled' THEN 1 ELSE 0 END), 0) AS canceled,
                COUNT(*) AS total
            FROM bail_ad_orders
            '''
        ).fetchone()
        if order_stats_row:
            order_stats = dict(order_stats_row)

        orders = conn.execute(
            '''
            SELECT
                id, business_name, email, package_id, billing_cycle, amount_cents, currency,
                status, county_targets, add_on_ids, notes, provider_session_id, provider_subscription_id,
                onboarding_token, paid_at, created_at
            FROM bail_ad_orders
            ORDER BY datetime(created_at) DESC
            LIMIT 120
            '''
        ).fetchall()

        creatives = conn.execute(
            '''
            SELECT
                bail_ad_creatives.id,
                bail_ad_creatives.order_id,
                bail_ad_creatives.headline,
                bail_ad_creatives.target_url,
                bail_ad_creatives.logo_path,
                bail_ad_creatives.status,
                bail_ad_creatives.review_notes,
                bail_ad_creatives.reviewed_by,
                bail_ad_creatives.reviewed_at,
                bail_ad_creatives.updated_at,
                bail_ad_orders.business_name
            FROM bail_ad_creatives
            JOIN bail_ad_orders ON bail_ad_orders.id = bail_ad_creatives.order_id
            ORDER BY datetime(bail_ad_creatives.updated_at) DESC
            LIMIT 120
            '''
        ).fetchall()

        perf_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
                COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
                COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads
            FROM bail_ad_events
            WHERE created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        if perf_row:
            performance_30d.update(dict(perf_row))
            impressions = float(performance_30d['impressions'] or 0)
            clicks = float(performance_30d['clicks'] or 0)
            leads = float(performance_30d['leads'] or 0)
            performance_30d['ctr_pct'] = (clicks / impressions * 100.0) if impressions else 0.0
            performance_30d['lead_rate_pct'] = (leads / clicks * 100.0) if clicks else 0.0

        county_performance_30d = conn.execute(
            '''
            SELECT
                COALESCE(NULLIF(county, ''), '(unassigned)') AS county,
                COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
                COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
                COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads
            FROM bail_ad_events
            WHERE created_at >= date('now', '-30 days')
            GROUP BY COALESCE(NULLIF(county, ''), '(unassigned)')
            ORDER BY clicks DESC, impressions DESC, county ASC
            LIMIT 20
            '''
        ).fetchall()

        _ensure_bail_consumer_lead_schema(conn)
        consumer_totals_row = conn.execute(
            '''
            SELECT
                COALESCE(SUM(CASE WHEN status IN ('qualified', 'booked') THEN 1 ELSE 0 END), 0) AS qualified_leads,
                COALESCE(SUM(CASE WHEN status = 'booked' THEN 1 ELSE 0 END), 0) AS booked_bonds
            FROM bail_consumer_leads
            WHERE created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        calls_row = conn.execute(
            '''
            SELECT COUNT(*) AS calls
            FROM bail_ad_events
            WHERE event_type IN ('call', 'lead')
              AND created_at >= date('now', '-30 days')
            '''
        ).fetchone()
        consumer_pipeline_30d['calls'] = int((calls_row['calls'] if calls_row else 0) or 0)
        if consumer_totals_row:
            consumer_pipeline_30d['qualified_leads'] = int(consumer_totals_row['qualified_leads'] or 0)
            consumer_pipeline_30d['booked_bonds'] = int(consumer_totals_row['booked_bonds'] or 0)
        qualified_total = float(consumer_pipeline_30d['qualified_leads'] or 0)
        booked_total = float(consumer_pipeline_30d['booked_bonds'] or 0)
        consumer_pipeline_30d['booked_from_qualified_pct'] = (booked_total / qualified_total * 100.0) if qualified_total else 0.0

        calls_by_county = {
            (row['county'] or '(unassigned)'): int(row['calls'] or 0)
            for row in conn.execute(
                '''
                SELECT COALESCE(NULLIF(county, ''), '(unassigned)') AS county, COUNT(*) AS calls
                FROM bail_ad_events
                WHERE event_type IN ('call', 'lead')
                  AND created_at >= date('now', '-30 days')
                GROUP BY COALESCE(NULLIF(county, ''), '(unassigned)')
                '''
            ).fetchall()
        }
        leads_by_county = {
            (row['county'] or '(unassigned)'): dict(row)
            for row in conn.execute(
                '''
                SELECT
                    COALESCE(NULLIF(county, ''), '(unassigned)') AS county,
                    COALESCE(SUM(CASE WHEN status IN ('qualified', 'booked') THEN 1 ELSE 0 END), 0) AS qualified_leads,
                    COALESCE(SUM(CASE WHEN status = 'booked' THEN 1 ELSE 0 END), 0) AS booked_bonds
                FROM bail_consumer_leads
                WHERE created_at >= date('now', '-30 days')
                GROUP BY COALESCE(NULLIF(county, ''), '(unassigned)')
                '''
            ).fetchall()
        }
        county_keys = set(calls_by_county.keys()) | set(leads_by_county.keys())
        county_pipeline_30d = sorted(
            [
                {
                    'county': county_name,
                    'calls': int(calls_by_county.get(county_name, 0) or 0),
                    'qualified_leads': int((leads_by_county.get(county_name) or {}).get('qualified_leads') or 0),
                    'booked_bonds': int((leads_by_county.get(county_name) or {}).get('booked_bonds') or 0),
                }
                for county_name in county_keys
            ],
            key=lambda item: (item['booked_bonds'], item['qualified_leads'], item['calls'], item['county']),
            reverse=True,
        )[:24]

        consumer_leads = conn.execute(
            '''
            SELECT
                id,
                full_name,
                phone,
                email,
                county,
                jail_facility,
                callback_preference,
                source,
                status,
                routed_business_names,
                review_notes,
                reviewed_by,
                reviewed_at,
                created_at
            FROM bail_consumer_leads
            ORDER BY datetime(created_at) DESC
            LIMIT 120
            '''
        ).fetchall()

        advertiser_pipeline_30d = _bail_advertiser_attribution_30d(conn, limit=120)

        events_by_order = {
            row['order_id']: row
            for row in conn.execute(
                '''
                SELECT
                    order_id,
                    COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
                    COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
                    COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads
                FROM bail_ad_events
                WHERE order_id IS NOT NULL
                  AND created_at >= date('now', '-30 days')
                GROUP BY order_id
                '''
            ).fetchall()
        }

        now_utc = datetime.utcnow()
        package_map = _bail_ad_package_lookup()
        for order in orders:
            order_dict = dict(order)
            paid_at_raw = order_dict.get('paid_at') or order_dict.get('created_at')
            paid_at = _parse_sqlite_timestamp(paid_at_raw)
            if not paid_at:
                continue

            cycle = (order_dict.get('billing_cycle') or 'monthly').lower()
            renewal_days = 365 if cycle == 'annual' else 30
            next_renewal = paid_at + timedelta(days=renewal_days)
            days_to_renewal = int((next_renewal - now_utc).total_seconds() // 86400)

            if order_dict.get('status') in {'active', 'active_pending_creative_review'} and days_to_renewal <= 14:
                renewal_candidates.append({
                    'id': order_dict['id'],
                    'business_name': order_dict.get('business_name') or '',
                    'package_id': order_dict.get('package_id') or '',
                    'billing_cycle': cycle,
                    'days_to_renewal': days_to_renewal,
                    'next_renewal': next_renewal.strftime('%Y-%m-%d'),
                })

            metrics = events_by_order.get(order_dict['id']) or {'impressions': 0, 'clicks': 0, 'leads': 0}
            counties = _bail_ad_county_list(order_dict.get('county_targets') or '')
            package_id = (order_dict.get('package_id') or '').lower()
            click_count = int(metrics['clicks'] or 0)
            recommendation = ''
            if package_id in {'starter', 'silver_link', 'exclusive_county_sponsorship'} and click_count >= 15:
                recommendation = 'Upgrade to The Gold Bond Bundle for top banner + sidebar + 2 counties.'
            elif package_id in {'featured_bondsman_banner', 'emergency_call_sidebar'} and click_count >= 20:
                recommendation = 'Upgrade to The Gold Bond Bundle for multi-touch coverage across placements.'
            elif package_id in {'growth', 'gold_bond'} and click_count >= 25:
                recommendation = 'Migrate this account to The Gold Bond Bundle pricing framework.'
            elif package_id == 'gold_bond_bundle' and click_count >= 40:
                recommendation = 'Add one Exclusive County Sponsorship for deeper local saturation.'

            if recommendation:
                pkg = package_map.get(package_id) or {}
                upgrade_candidates.append({
                    'id': order_dict['id'],
                    'business_name': order_dict.get('business_name') or '',
                    'package_id': package_id,
                    'clicks': int(metrics['clicks'] or 0),
                    'impressions': int(metrics['impressions'] or 0),
                    'county_count': len(counties),
                    'county_slots': int(pkg.get('county_slots') or 0),
                    'recommendation': recommendation,
                })
    except sqlite3.OperationalError:
        schema_ready = False
    finally:
        conn.close()

    return render_template(
        'admin_bail_ads.html',
        schema_ready=schema_ready,
        stats=stats,
        order_stats=order_stats,
        inquiries=inquiries,
        orders=orders,
        creatives=creatives,
        performance_30d=performance_30d,
        county_performance_30d=county_performance_30d,
        renewal_candidates=renewal_candidates,
        upgrade_candidates=upgrade_candidates,
        consumer_pipeline_30d=consumer_pipeline_30d,
        county_pipeline_30d=county_pipeline_30d,
        consumer_leads=consumer_leads,
        advertiser_pipeline_30d=advertiser_pipeline_30d,
        package_map=package_map,
    )


@app.route('/admin/bail-ads/agencies')
@login_required
def admin_bail_agency_cms():
    q = (request.args.get('q') or '').strip()[:120]
    status_filter = (request.args.get('status') or 'all').strip().lower()
    if status_filter not in _BAIL_OUTREACH_STATUSES and status_filter != 'all':
        status_filter = 'all'

    agencies = []
    email_logs = []
    status_counts = {status: 0 for status in sorted(_BAIL_OUTREACH_STATUSES)}
    total_count = 0
    conn = get_db()
    try:
        _ensure_bail_agency_outreach_schema(conn)
        _seed_bail_agency_outreach(conn)
        conn.commit()

        count_row = conn.execute(
            '''
            SELECT
                COUNT(*) AS total_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'new' THEN 1 ELSE 0 END), 0) AS new_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'queued' THEN 1 ELSE 0 END), 0) AS queued_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'contacted' THEN 1 ELSE 0 END), 0) AS contacted_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'replied' THEN 1 ELSE 0 END), 0) AS replied_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'meeting_scheduled' THEN 1 ELSE 0 END), 0) AS meeting_scheduled_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'closed_won' THEN 1 ELSE 0 END), 0) AS closed_won_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'closed_lost' THEN 1 ELSE 0 END), 0) AS closed_lost_count,
                COALESCE(SUM(CASE WHEN outreach_status = 'do_not_contact' THEN 1 ELSE 0 END), 0) AS do_not_contact_count
            FROM bail_agency_outreach
            '''
        ).fetchone()
        if count_row:
            total_count = int(count_row['total_count'] or 0)
            for key in status_counts:
                status_counts[key] = int(count_row[f'{key}_count'] or 0)

        clauses = []
        params = []
        if status_filter != 'all':
            clauses.append('outreach_status = ?')
            params.append(status_filter)
        if q:
            like = f'%{q}%'
            clauses.append(
                '(agency_name LIKE ? OR contact_name LIKE ? OR email LIKE ? OR phone LIKE ? OR counties LIKE ? OR notes LIKE ?)'
            )
            params.extend([like, like, like, like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        rows = conn.execute(
            f'''
            SELECT
                id, agency_name, contact_name, email, phone, counties, source, outreach_status,
                last_contacted_at, next_follow_up_at, owner,
                email_subject_template, email_body_template, call_script_template, notes,
                created_at, updated_at
            FROM bail_agency_outreach
            {where_sql}
            ORDER BY
                CASE WHEN next_follow_up_at IS NULL OR next_follow_up_at = '' THEN 1 ELSE 0 END ASC,
                date(next_follow_up_at) ASC,
                datetime(updated_at) DESC
            LIMIT 400
            ''',
            tuple(params),
        ).fetchall()
        for row in rows:
            agency = dict(row)
            agency.update(_bail_agency_rendered_templates(agency))
            agencies.append(agency)

        email_logs = conn.execute(
            '''
            SELECT
                id,
                agency_id,
                agency_name,
                recipient_email,
                email_kind,
                subject,
                sent_by,
                send_status,
                error_message,
                created_at
            FROM bail_agency_email_logs
            ORDER BY datetime(created_at) DESC
            LIMIT 120
            '''
        ).fetchall()
    except sqlite3.OperationalError:
        flash('Bail agency CMS table is not available. Run migration first.', 'error')
    finally:
        conn.close()

    return render_template(
        'admin_bail_agency_cms.html',
        agencies=agencies,
        total_count=total_count,
        status_counts=status_counts,
        status_filter=status_filter,
        q=q,
        outreach_statuses=sorted(_BAIL_OUTREACH_STATUSES),
        default_test_email=_default_bail_test_email(),
        email_logs=email_logs,
    )


@app.route('/admin/bail-ads/agencies/create', methods=['POST'])
@login_required
def admin_bail_agency_cms_create():
    agency_name = (request.form.get('agency_name') or '').strip()[:160]
    contact_name = (request.form.get('contact_name') or '').strip()[:120]
    email = (request.form.get('email') or '').strip().lower()[:160]
    phone = (request.form.get('phone') or '').strip()[:40]
    counties = (request.form.get('counties') or '').strip()[:500]
    source = (request.form.get('source') or 'manual').strip()[:80]
    owner = (request.form.get('owner') or '').strip()[:120]

    if not agency_name:
        flash('Agency name is required.', 'warning')
        return redirect(url_for('admin_bail_agency_cms'))

    dedupe_key = _bail_agency_dedupe_key(agency_name, email, phone)
    if not dedupe_key:
        flash('Unable to create agency record.', 'error')
        return redirect(url_for('admin_bail_agency_cms'))

    conn = get_db()
    try:
        _ensure_bail_agency_outreach_schema(conn)
        conn.execute(
            '''
            INSERT INTO bail_agency_outreach (
                dedupe_key, agency_name, contact_name, email, phone, counties, source, owner, outreach_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT(dedupe_key) DO UPDATE SET
                agency_name = excluded.agency_name,
                contact_name = CASE WHEN excluded.contact_name != '' THEN excluded.contact_name ELSE bail_agency_outreach.contact_name END,
                email = CASE WHEN excluded.email != '' THEN excluded.email ELSE bail_agency_outreach.email END,
                phone = CASE WHEN excluded.phone != '' THEN excluded.phone ELSE bail_agency_outreach.phone END,
                counties = CASE WHEN excluded.counties != '' THEN excluded.counties ELSE bail_agency_outreach.counties END,
                source = CASE WHEN excluded.source != '' THEN excluded.source ELSE bail_agency_outreach.source END,
                owner = CASE WHEN excluded.owner != '' THEN excluded.owner ELSE bail_agency_outreach.owner END,
                updated_at = datetime('now')
            ''',
            (dedupe_key, agency_name, contact_name, email, phone, counties, source, owner),
        )
        conn.commit()
        flash(f'Agency record saved for {agency_name}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail agency CMS table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_bail_agency_cms'))


@app.route('/admin/bail-ads/agencies/<int:agency_id>/update', methods=['POST'])
@login_required
def admin_bail_agency_cms_update(agency_id):
    action = (request.form.get('action') or 'save').strip().lower()
    agency_name = (request.form.get('agency_name') or '').strip()[:160]
    contact_name = (request.form.get('contact_name') or '').strip()[:120]
    email = (request.form.get('email') or '').strip().lower()[:160]
    phone = (request.form.get('phone') or '').strip()[:40]
    counties = (request.form.get('counties') or '').strip()[:500]
    source = (request.form.get('source') or '').strip()[:80]
    outreach_status = (request.form.get('outreach_status') or '').strip().lower()
    if outreach_status not in _BAIL_OUTREACH_STATUSES:
        outreach_status = 'new'
    last_contacted_at = (request.form.get('last_contacted_at') or '').strip()[:32]
    next_follow_up_at = (request.form.get('next_follow_up_at') or '').strip()[:32]
    owner = (request.form.get('owner') or '').strip()[:120]
    email_subject_template = (request.form.get('email_subject_template') or '').strip()[:500]
    email_body_template = (request.form.get('email_body_template') or '').strip()[:4000]
    call_script_template = (request.form.get('call_script_template') or '').strip()[:3000]
    notes = (request.form.get('notes') or '').strip()[:3000]
    test_email = (request.form.get('test_email') or '').strip().lower()[:160]
    actor = (getattr(current_user, 'username', '') or 'admin').strip()[:120]

    if action == 'contacted_now':
        last_contacted_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        if outreach_status == 'new':
            outreach_status = 'contacted'

    dedupe_key = _bail_agency_dedupe_key(agency_name, email, phone)
    if not agency_name or not dedupe_key:
        flash('Agency name is required for updates.', 'warning')
        return redirect(url_for('admin_bail_agency_cms'))

    conn = get_db()
    try:
        _ensure_bail_agency_outreach_schema(conn)
        result = conn.execute(
            '''
            UPDATE bail_agency_outreach
            SET dedupe_key = ?, agency_name = ?, contact_name = ?, email = ?, phone = ?, counties = ?, source = ?,
                outreach_status = ?, last_contacted_at = ?, next_follow_up_at = ?, owner = ?,
                email_subject_template = ?, email_body_template = ?, call_script_template = ?, notes = ?,
                updated_at = datetime('now')
            WHERE id = ?
            ''',
            (
                dedupe_key,
                agency_name,
                contact_name,
                email,
                phone,
                counties,
                source,
                outreach_status,
                last_contacted_at,
                next_follow_up_at,
                owner,
                email_subject_template,
                email_body_template,
                call_script_template,
                notes,
                agency_id,
            ),
        )
        conn.commit()
        if result.rowcount <= 0:
            flash('Agency record not found.', 'warning')
        elif action == 'send_email':
            if not email or '@' not in email:
                _log_bail_agency_email(
                    conn=conn,
                    agency_id=agency_id,
                    agency_name=agency_name,
                    recipient_email=email or '',
                    email_kind='live',
                    subject=email_subject_template,
                    body_preview=email_body_template,
                    sent_by=actor,
                    send_status='skipped',
                    error_message='invalid_recipient_email',
                )
                conn.commit()
                flash(f'{agency_name} saved, but no valid email address is set.', 'warning')
            else:
                agency_payload = {
                    'agency_name': agency_name,
                    'contact_name': contact_name,
                    'counties': counties,
                    'email_subject_template': email_subject_template,
                    'email_body_template': email_body_template,
                    'call_script_template': call_script_template,
                }
                rendered = _bail_agency_rendered_templates(agency_payload)
                sent = _send_bail_lead_notification_email(
                    [email],
                    rendered['subject_preview'],
                    rendered['email_preview'],
                )
                _log_bail_agency_email(
                    conn=conn,
                    agency_id=agency_id,
                    agency_name=agency_name,
                    recipient_email=email,
                    email_kind='live',
                    subject=rendered['subject_preview'],
                    body_preview=rendered['email_preview'],
                    sent_by=actor,
                    send_status='sent' if sent else 'failed',
                    error_message='' if sent else 'smtp_send_failed',
                )
                conn.commit()
                if sent:
                    sent_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    next_status = outreach_status
                    if next_status in {'new', 'queued'}:
                        next_status = 'contacted'
                    conn.execute(
                        '''
                        UPDATE bail_agency_outreach
                        SET outreach_status = ?, last_contacted_at = ?, updated_at = datetime('now')
                        WHERE id = ?
                        ''',
                        (next_status, sent_at, agency_id),
                    )
                    conn.commit()
                    flash(f'Email sent to {agency_name} at {email}.', 'success')
                else:
                    flash(f'{agency_name} saved, but email send failed. Check SMTP settings.', 'warning')
        elif action == 'send_test_email':
            target_email = test_email
            if not target_email or '@' not in target_email:
                target_email = _default_bail_test_email()
            if not target_email or '@' not in target_email:
                _log_bail_agency_email(
                    conn=conn,
                    agency_id=agency_id,
                    agency_name=agency_name,
                    recipient_email=test_email or '',
                    email_kind='test',
                    subject=email_subject_template,
                    body_preview=email_body_template,
                    sent_by=actor,
                    send_status='skipped',
                    error_message='invalid_test_recipient_email',
                )
                conn.commit()
                flash('Agency saved, but no valid test recipient email is configured.', 'warning')
            else:
                agency_payload = {
                    'agency_name': agency_name,
                    'contact_name': contact_name,
                    'counties': counties,
                    'email_subject_template': email_subject_template,
                    'email_body_template': email_body_template,
                    'call_script_template': call_script_template,
                }
                rendered = _bail_agency_rendered_templates(agency_payload)
                subject = f"[TEST] {rendered['subject_preview']}"
                body = (
                    f"Test send for agency outreach template.\n"
                    f"Agency: {agency_name}\n"
                    f"Timestamp (UTC): {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"{rendered['email_preview']}"
                )
                sent = _send_bail_lead_notification_email([target_email], subject, body)
                _log_bail_agency_email(
                    conn=conn,
                    agency_id=agency_id,
                    agency_name=agency_name,
                    recipient_email=target_email,
                    email_kind='test',
                    subject=subject,
                    body_preview=body,
                    sent_by=actor,
                    send_status='sent' if sent else 'failed',
                    error_message='' if sent else 'smtp_send_failed',
                )
                conn.commit()
                if sent:
                    flash(f'Test email sent to {target_email} for {agency_name}.', 'success')
                else:
                    flash(f'{agency_name} saved, but test email send failed. Check SMTP settings.', 'warning')
        elif action == 'contacted_now':
            flash(f'{agency_name} marked as contacted.', 'success')
        else:
            flash(f'{agency_name} updated.', 'success')
    except sqlite3.IntegrityError:
        flash('Another agency already uses that dedupe key (name/email/phone combination).', 'error')
    except sqlite3.OperationalError:
        flash('Bail agency CMS table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_bail_agency_cms'))


@app.route('/admin/bail-ads/attribution/export.csv')
@login_required
def admin_bail_ads_attribution_export():
    conn = get_db()
    try:
        _ensure_bail_consumer_lead_schema(conn)
        rows = _bail_advertiser_attribution_30d(conn, limit=10000)
    except sqlite3.OperationalError:
        conn.close()
        return Response('Attribution tables are not available.\n', status=503, mimetype='text/plain')
    conn.close()

    package_map = _bail_ad_package_lookup()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([
        'order_id',
        'business_name',
        'package_id',
        'package_name',
        'order_status',
        'county_targets',
        'calls_30d',
        'texts_30d',
        'routed_leads_30d',
        'qualified_leads_30d',
        'booked_bonds_30d',
        'qualified_rate_pct',
        'booked_rate_pct',
    ])
    for row in rows:
        package = package_map.get(row.get('package_id') or '')
        package_name = (package.get('name') if package else '') or (row.get('package_id') or '').replace('_', ' ').title()
        writer.writerow([
            row.get('order_id') or '',
            row.get('business_name') or '',
            row.get('package_id') or '',
            package_name,
            row.get('status') or '',
            row.get('county_targets') or '',
            int(row.get('calls') or 0),
            int(row.get('texts') or 0),
            int(row.get('routed_leads') or 0),
            int(row.get('qualified_leads') or 0),
            int(row.get('booked_bonds') or 0),
            f"{float(row.get('qualified_rate_pct') or 0.0):.2f}",
            f"{float(row.get('booked_rate_pct') or 0.0):.2f}",
        ])

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    response = Response(output.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = f'attachment; filename=bail_ads_attribution_30d_{timestamp}.csv'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/admin/bail-ads/<int:inquiry_id>/status', methods=['POST'])
@login_required
def admin_bail_ads_update_status(inquiry_id):
    next_status = (request.form.get('status') or '').strip().lower()
    review_notes = (request.form.get('review_notes') or '').strip()[:1200]
    if next_status not in {'pending', 'in_review', 'approved', 'declined', 'archived'}:
        flash('Invalid bail ad status.', 'error')
        return redirect(url_for('admin_bail_ads'))

    reviewer = getattr(current_user, 'username', '') or 'admin'
    conn = get_db()
    try:
        result = conn.execute(
            '''
            UPDATE bail_ad_inquiries
            SET status = ?, review_notes = ?, reviewed_by = ?, reviewed_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ?
            ''',
            (next_status, review_notes, reviewer, inquiry_id),
        )
        conn.commit()
        if result.rowcount <= 0:
            flash('Inquiry not found.', 'error')
        else:
            flash(f'Inquiry #{inquiry_id} updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad inquiry table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_bail_ads'))


@app.route('/admin/bail-ads/creatives/<int:creative_id>/status', methods=['POST'])
@login_required
def admin_bail_ads_creative_status(creative_id):
    next_status = (request.form.get('status') or '').strip().lower()
    review_notes = (request.form.get('review_notes') or '').strip()[:1200]
    if next_status not in {'pending', 'approved', 'rejected'}:
        flash('Invalid creative status.', 'error')
        return redirect(url_for('admin_bail_ads'))

    reviewer = getattr(current_user, 'username', '') or 'admin'
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT order_id FROM bail_ad_creatives WHERE id = ? LIMIT 1',
            (creative_id,),
        ).fetchone()
        if not row:
            flash('Creative record not found.', 'error')
            conn.close()
            return redirect(url_for('admin_bail_ads'))

        conn.execute(
            '''
            UPDATE bail_ad_creatives
            SET status = ?, review_notes = ?, reviewed_by = ?, reviewed_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ?
            ''',
            (next_status, review_notes, reviewer, creative_id),
        )
        if next_status == 'approved':
            conn.execute(
                '''
                UPDATE bail_ad_orders
                SET status = CASE
                        WHEN status IN ('active_pending_creative_review', 'checkout_pending') THEN 'active'
                        ELSE status
                    END,
                    updated_at = datetime('now')
                WHERE id = ?
                ''',
                (row['order_id'],),
            )
        conn.commit()
        flash(f'Creative #{creative_id} updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad creative table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_bail_ads'))


@app.route('/admin/bail-ads/orders/<int:order_id>/status', methods=['POST'])
@login_required
def admin_bail_ads_order_status(order_id):
    next_status = (request.form.get('status') or '').strip().lower()
    notes = (request.form.get('notes') or '').strip()[:1200]
    allowed_statuses = {'checkout_pending', 'active', 'active_pending_creative_review', 'payment_failed', 'canceled', 'paused'}
    if next_status not in allowed_statuses:
        flash('Invalid order status.', 'error')
        return redirect(url_for('admin_bail_ads'))

    conn = get_db()
    try:
        result = conn.execute(
            '''
            UPDATE bail_ad_orders
            SET status = ?, notes = ?, updated_at = datetime('now')
            WHERE id = ?
            ''',
            (next_status, notes, order_id),
        )
        conn.commit()
        if result.rowcount <= 0:
            flash('Order not found.', 'error')
        else:
            flash(f'Order #{order_id} updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad order table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_bail_ads'))


@app.route('/admin/bail-ads/orders/bulk-status', methods=['POST'])
@login_required
def admin_bail_ads_bulk_order_status():
    next_status = (request.form.get('status') or '').strip().lower()
    notes = (request.form.get('notes') or '').strip()[:1200]
    allowed_statuses = {'checkout_pending', 'active', 'active_pending_creative_review', 'payment_failed', 'canceled', 'paused'}
    if next_status not in allowed_statuses:
        flash('Invalid order status.', 'error')
        return redirect(url_for('admin_bail_ads'))

    order_ids = []
    for raw_id in request.form.getlist('order_ids'):
        try:
            order_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if order_id > 0:
            order_ids.append(order_id)
    order_ids = sorted(set(order_ids))
    if not order_ids:
        flash('Select at least one order first.', 'warning')
        return redirect(url_for('admin_bail_ads'))

    placeholders = ','.join('?' for _ in order_ids)
    conn = get_db()
    try:
        if notes:
            result = conn.execute(
                f'''
                UPDATE bail_ad_orders
                SET status = ?, notes = ?, updated_at = datetime('now')
                WHERE id IN ({placeholders})
                ''',
                tuple([next_status, notes] + order_ids),
            )
        else:
            result = conn.execute(
                f'''
                UPDATE bail_ad_orders
                SET status = ?, updated_at = datetime('now')
                WHERE id IN ({placeholders})
                ''',
                tuple([next_status] + order_ids),
            )
        conn.commit()
        changed_count = int(result.rowcount or 0)
        if changed_count <= 0:
            flash('No matching orders were found.', 'warning')
        elif changed_count == 1:
            flash(f'1 order updated to {next_status}.', 'success')
        else:
            flash(f'{changed_count} orders updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail ad order table is not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_bail_ads'))


@app.route('/admin/bail-ads/leads/<int:lead_id>/status', methods=['POST'])
@login_required
def admin_bail_consumer_lead_status(lead_id):
    next_status = (request.form.get('status') or '').strip().lower()
    review_notes = (request.form.get('review_notes') or '').strip()[:1200]
    allowed_statuses = {'new', 'contacted', 'qualified', 'booked', 'unqualified', 'archived'}
    if next_status not in allowed_statuses:
        flash('Invalid lead status.', 'error')
        return redirect(url_for('admin_bail_ads'))

    reviewer = getattr(current_user, 'username', '') or 'admin'
    conn = get_db()
    try:
        _ensure_bail_consumer_lead_schema(conn)
        result = conn.execute(
            '''
            UPDATE bail_consumer_leads
            SET status = ?, review_notes = ?, reviewed_by = ?, reviewed_at = datetime('now'), updated_at = datetime('now')
            WHERE id = ?
            ''',
            (next_status, review_notes, reviewer, lead_id),
        )
        conn.commit()
        if result.rowcount <= 0:
            flash('Lead not found.', 'warning')
        else:
            flash(f'Lead #{lead_id} updated to {next_status}.', 'success')
    except sqlite3.OperationalError:
        flash('Bail consumer lead tables are not available. Run migration first.', 'error')
    finally:
        conn.close()
    return redirect(url_for('admin_bail_ads'))


@app.route('/admin/analytics')
@login_required
def admin_analytics():
    conn = get_db()

    # Incidents per day — last 30 days
    daily_rows = conn.execute(
        "SELECT date(created_at) AS day, COUNT(*) AS cnt FROM records "
        "WHERE created_at >= date('now', '-30 days') "
        "GROUP BY day ORDER BY day"
    ).fetchall()
    daily_labels = [r['day'] for r in daily_rows]
    daily_counts = [r['cnt'] for r in daily_rows]

    # Top 10 incident types
    type_rows = conn.execute(
        "SELECT COALESCE(incident_type, 'Unknown') AS itype, COUNT(*) AS cnt "
        "FROM records WHERE incident_type IS NOT NULL AND incident_type != '' "
        "GROUP BY itype ORDER BY cnt DESC LIMIT 10"
    ).fetchall()
    type_labels = [r['itype'] for r in type_rows]
    type_counts = [r['cnt'] for r in type_rows]

    # Agency type breakdown
    agency_rows = conn.execute(
        "SELECT COALESCE(agency_type, 'other') AS atype, COUNT(*) AS cnt "
        "FROM posts GROUP BY atype"
    ).fetchall()
    agency_labels = [r['atype'].title() for r in agency_rows]
    agency_counts = [r['cnt'] for r in agency_rows]

    # Top 10 counties — this month vs last month
    county_this = {r['county']: r['cnt'] for r in conn.execute(
        "SELECT COALESCE(county, 'Unknown') AS county, COUNT(*) AS cnt FROM records "
        "WHERE created_at >= date('now', 'start of month') "
        "GROUP BY county ORDER BY cnt DESC LIMIT 10"
    ).fetchall()}
    county_last = {r['county']: r['cnt'] for r in conn.execute(
        "SELECT COALESCE(county, 'Unknown') AS county, COUNT(*) AS cnt FROM records "
        "WHERE created_at >= date('now', 'start of month', '-1 month') "
        "AND created_at < date('now', 'start of month') "
        "GROUP BY county ORDER BY cnt DESC LIMIT 10"
    ).fetchall()}
    county_labels = sorted(set(list(county_this.keys()) + list(county_last.keys())))[:10]
    county_this_vals = [county_this.get(c, 0) for c in county_labels]
    county_last_vals = [county_last.get(c, 0) for c in county_labels]

    # Blotters received per month — last 12 months
    blotter_rows = conn.execute(
        "SELECT strftime('%Y-%m', upload_date) AS mo, COUNT(*) AS cnt "
        "FROM blotters GROUP BY mo ORDER BY mo DESC LIMIT 12"
    ).fetchall()
    blotter_labels = [r['mo'] for r in reversed(blotter_rows)]
    blotter_counts = [r['cnt'] for r in reversed(blotter_rows)]

    pattern_clicks_30d = conn.execute(
        "SELECT COUNT(*) FROM pattern_clicks WHERE created_at >= date('now', '-30 days')"
    ).fetchone()[0]
    pattern_clicks_homepage = conn.execute(
        "SELECT COUNT(*) FROM pattern_clicks WHERE placement = 'homepage_pattern_promos' AND created_at >= date('now', '-30 days')"
    ).fetchone()[0]
    pattern_clicks_post = conn.execute(
        "SELECT COUNT(*) FROM pattern_clicks WHERE placement = 'post_related_patterns' AND created_at >= date('now', '-30 days')"
    ).fetchone()[0]
    pattern_click_rows = conn.execute(
        '''
        SELECT placement, COUNT(*) AS cnt
        FROM pattern_clicks
        WHERE created_at >= date('now', '-30 days')
        GROUP BY placement
        ORDER BY cnt DESC, placement ASC
        '''
    ).fetchall()
    pattern_click_labels = [row['placement'].replace('_', ' ').title() for row in pattern_click_rows]
    pattern_click_counts = [row['cnt'] for row in pattern_click_rows]
    pattern_click_targets = conn.execute(
        '''
        SELECT target_path, placement, COUNT(*) AS cnt
        FROM pattern_clicks
        WHERE created_at >= date('now', '-30 days')
        GROUP BY target_path, placement
        ORDER BY cnt DESC, target_path ASC
        LIMIT 10
        '''
    ).fetchall()
    pattern_click_patterns = conn.execute(
        '''
        SELECT pattern_slug, county_slug, COUNT(*) AS cnt
        FROM pattern_clicks
        WHERE created_at >= date('now', '-30 days')
        GROUP BY pattern_slug, county_slug
        ORDER BY cnt DESC, pattern_slug ASC, county_slug ASC
        LIMIT 10
        '''
    ).fetchall()
    homepage_page_views_30d = conn.execute(
        "SELECT COUNT(*) FROM page_views WHERE path = '/' AND created_at >= date('now', '-30 days')"
    ).fetchone()[0]
    post_page_views_30d = conn.execute(
        "SELECT COUNT(*) FROM page_views WHERE path LIKE '/post/%' AND created_at >= date('now', '-30 days')"
    ).fetchone()[0]
    homepage_pattern_ctr = (
        (pattern_clicks_homepage / homepage_page_views_30d * 100) if homepage_page_views_30d else 0
    )
    post_pattern_ctr = (
        (pattern_clicks_post / post_page_views_30d * 100) if post_page_views_30d else 0
    )

    conn.close()
    return render_template('admin_analytics.html',
        daily_labels=daily_labels, daily_counts=daily_counts,
        type_labels=type_labels, type_counts=type_counts,
        agency_labels=agency_labels, agency_counts=agency_counts,
        county_labels=county_labels, county_this=county_this_vals, county_last=county_last_vals,
        blotter_labels=blotter_labels, blotter_counts=blotter_counts,
        pattern_clicks_30d=pattern_clicks_30d,
        pattern_clicks_homepage=pattern_clicks_homepage,
        pattern_clicks_post=pattern_clicks_post,
        pattern_click_labels=pattern_click_labels,
        pattern_click_counts=pattern_click_counts,
        pattern_click_targets=pattern_click_targets,
        pattern_click_patterns=pattern_click_patterns,
        homepage_page_views_30d=homepage_page_views_30d,
        post_page_views_30d=post_page_views_30d,
        homepage_pattern_ctr=homepage_pattern_ctr,
        post_pattern_ctr=post_pattern_ctr,
    )


# ==========================================
# VISITOR ANALYTICS
# ==========================================

@app.route('/admin/visitors')
@login_required
def admin_visitors():
    conn = get_db()

    # Summary counts
    today     = conn.execute("SELECT COUNT(*) FROM page_views WHERE date(created_at)=date('now')").fetchone()[0]
    this_week = conn.execute("SELECT COUNT(*) FROM page_views WHERE created_at >= date('now','-7 days')").fetchone()[0]
    this_month= conn.execute("SELECT COUNT(*) FROM page_views WHERE created_at >= date('now','start of month')").fetchone()[0]
    all_time  = conn.execute("SELECT COUNT(*) FROM page_views").fetchone()[0]

    # Unique visitors (by ip_hash) — last 30 days
    unique_today     = conn.execute("SELECT COUNT(DISTINCT ip_hash) FROM page_views WHERE date(created_at)=date('now')").fetchone()[0]
    unique_week      = conn.execute("SELECT COUNT(DISTINCT ip_hash) FROM page_views WHERE created_at >= date('now','-7 days')").fetchone()[0]
    unique_month     = conn.execute("SELECT COUNT(DISTINCT ip_hash) FROM page_views WHERE created_at >= date('now','start of month')").fetchone()[0]

    # Views per day — last 30 days
    daily_rows = conn.execute(
        "SELECT date(created_at) AS day, COUNT(*) AS cnt FROM page_views "
        "WHERE created_at >= date('now','-30 days') GROUP BY day ORDER BY day"
    ).fetchall()
    daily_labels = [r['day'] for r in daily_rows]
    daily_counts = [r['cnt'] for r in daily_rows]

    # Top 10 pages
    top_pages = conn.execute(
        "SELECT path, COUNT(*) AS cnt FROM page_views "
        "WHERE created_at >= date('now','-30 days') "
        "GROUP BY path ORDER BY cnt DESC LIMIT 10"
    ).fetchall()

    # Top 10 referrers (exclude empty/direct)
    top_referrers = conn.execute(
        "SELECT referrer, COUNT(*) AS cnt FROM page_views "
        "WHERE referrer != '' AND created_at >= date('now','-30 days') "
        "GROUP BY referrer ORDER BY cnt DESC LIMIT 10"
    ).fetchall()

    conn.close()
    return render_template('admin_visitors.html',
        today=today, this_week=this_week, this_month=this_month, all_time=all_time,
        unique_today=unique_today, unique_week=unique_week, unique_month=unique_month,
        daily_labels=daily_labels, daily_counts=daily_counts,
        top_pages=top_pages, top_referrers=top_referrers,
    )


# ==========================================
# ERROR HANDLERS
# ==========================================

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serve files from the uploads directory"""
    return send_from_directory(config.UPLOAD_DIR, filename)


@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

if __name__ == "__main__":
    # Ensure directories exist
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    os.makedirs(config.RECORDS_DIR, exist_ok=True)
    
    # Run on port 5000
    app.run(host='0.0.0.0', port=5000, debug=config.DEBUG)
