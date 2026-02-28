"""
Montana Blotter - Simplified Free & Open Source Version
Public browse + Admin panel only (no memberships)
"""

import os
import sqlite3
import hashlib
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from datetime import datetime
import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Apply DB migrations at startup
from init_db import migrate as _migrate
_migrate()
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'admin_login'

# File upload configuration
ALLOWED_EXTENSIONS = {'pdf'}
app.config['UPLOAD_FOLDER'] = config.UPLOAD_DIR

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
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
    if request.path in ('/favicon.ico',):
        return
    ip_hash = hashlib.sha256((request.remote_addr or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    try:
        conn = sqlite3.connect(config.DB_PATH)
        conn.execute(
            'INSERT INTO page_views (path, ip_hash, referrer) VALUES (?, ?, ?)',
            (request.path, ip_hash, referrer)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Never break the site for analytics


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

    count_sql = sql.replace(
        "SELECT posts.*, blotters.county AS blotter_county", "SELECT COUNT(*)")
    total = conn.execute(count_sql, params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    sql += " ORDER BY posts.incident_date DESC, posts.created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    posts = conn.execute(sql, params).fetchall()

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
                           leaderboard=leaderboard)


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
        link = f"https://montanablotters.com/?date={pub[:10]}&amp;agency={agency}"
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
  <link href="https://montanablotters.com/feed.xml" rel="self"/>
  <link href="https://montanablotters.com/"/>
  <id>https://montanablotters.com/feed.xml</id>
  <updated>{updated}</updated>
{chr(10).join(items)}
</feed>"""

    from flask import Response
    return Response(xml, mimetype='application/atom+xml')


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
                           q=search_query)


@app.route('/subscribe', methods=['GET', 'POST'])
def subscribe():
    """Public email digest subscription."""
    import secrets

    conn = get_db()
    all_counties = [r['county'] for r in conn.execute(
        'SELECT DISTINCT county FROM posts ORDER BY county').fetchall()]

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        selected = request.form.getlist('counties')  # empty list = all counties

        if not email or '@' not in email:
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   error='Please enter a valid email address.')

        token = secrets.token_urlsafe(32)
        counties_str = ','.join(selected)

        try:
            conn.execute(
                'INSERT INTO subscribers (email, counties, token) VALUES (?, ?, ?)',
                (email, counties_str, token))
            conn.commit()
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   success=True, email=email)
        except Exception:
            # Email already subscribed — update preferences
            conn.execute(
                'UPDATE subscribers SET counties=?, active=1 WHERE email=?',
                (counties_str, email))
            conn.commit()
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   success=True, email=email, updated=True)

    conn.close()
    return render_template('subscribe.html', counties=all_counties)


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
        return render_template('subscribe.html', counties=[], unsubscribed=True, email=email)
    conn.close()
    return render_template('subscribe.html', counties=[], error='Invalid or expired unsubscribe link.')


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
    return _md.markdown(text or '', extensions=['extra', 'nl2br'])


@app.route('/jail-rosters')
def jail_rosters():
    return render_template('jail_rosters.html')


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

    last_row = conn.execute(
        'SELECT incident_date FROM posts WHERE county = ? ORDER BY incident_date DESC LIMIT 1',
        (county['name'],)
    ).fetchone()
    last_report = last_row['incident_date'] if last_row else None

    conn.close()

    return render_template(
        'county_page.html',
        county=county,
        posts=posts,
        post_count=post_count,
        record_count=record_count,
        last_report=last_report,
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
}


@app.route('/city/<slug>')
def city_page(slug):
    city = CITY_DATA.get(slug)
    if not city:
        return render_template('404.html'), 404

    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10

    # Build a LIKE-based OR clause across city and agency_name for each search term
    terms = city['search_terms']
    where_clauses = []
    params_count = []
    for term in terms:
        where_clauses.append('(posts.city LIKE ? OR posts.agency_name LIKE ?)')
        params_count.extend([f'%{term}%', f'%{term}%'])

    where_sql = ' OR '.join(where_clauses)

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

    # Count incidents from records table using same city terms
    rec_clauses = []
    rec_params = []
    for term in terms:
        rec_clauses.append('location LIKE ?')
        rec_params.append(f'%{term}%')
    record_count = conn.execute(
        f"SELECT COUNT(*) FROM records WHERE {' OR '.join(rec_clauses)}",
        rec_params
    ).fetchone()[0]

    last_row = conn.execute(
        f'SELECT incident_date FROM posts WHERE {where_sql} ORDER BY incident_date DESC LIMIT 1',
        params_count
    ).fetchone()
    last_report = last_row['incident_date'] if last_row else None

    conn.close()

    return render_template(
        'city_page.html',
        city=city,
        posts=posts,
        post_count=post_count,
        record_count=record_count,
        last_report=last_report,
        page=page,
        total_pages=total_pages,
        current_year=datetime.now().year,
    )


# ==========================================
# WARRANT PAGES
# ==========================================

# Slim list of counties shown on warrant pages — reuses COUNTY_DATA
_WARRANT_COUNTIES = [
    'yellowstone', 'gallatin', 'missoula', 'cascade', 'flathead',
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
    return render_template('laws.html')


@app.route('/blog')
def blog():
    conn = get_db()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10
    total = conn.execute(
        'SELECT COUNT(*) FROM blog_posts WHERE published=1').fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    posts = conn.execute(
        'SELECT * FROM blog_posts WHERE published=1 ORDER BY created_at DESC LIMIT ? OFFSET ?',
        (per_page, (page - 1) * per_page)).fetchall()
    conn.close()
    return render_template('blog.html', posts=posts, total=total,
                           page=page, total_pages=total_pages)


@app.route('/blog/<slug>')
def blog_post(slug):
    conn = get_db()
    post = conn.execute(
        'SELECT * FROM blog_posts WHERE slug=? AND published=1', (slug,)).fetchone()
    conn.close()
    if not post:
        return render_template('404.html'), 404
    return render_template('blog_post.html', post=post)


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
        SELECT records.*, blotters.filename
        FROM records
        LEFT JOIN blotters ON records.blotter_id = blotters.id
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
    
    conn.close()
    
    return render_template('record_detail.html', record=record, logs=logs)

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
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db()
        user_row = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user_row and bcrypt.check_password_hash(user_row['password'], password):
            login_user(User(user_row['id'], user_row['username']))
            return redirect(url_for('admin_dashboard'))
        
        flash('Invalid credentials')
    
    return render_template('admin_login.html')

@app.route('/admin/logout')
@login_required
def admin_logout():
    logout_user()
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
                         recent_blotters=recent_blotters,
                         county_stats=county_stats)

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
    posts_map = {}
    for row in conn.execute('SELECT id, blotter_id, case_status FROM posts'):
        posts_map[row['blotter_id']] = {'id': row['id'],
                                        'case_status': row['case_status'] or 'pending'}
    conn.close()
    return render_template('admin_blotters.html', blotters=blotters, posts_map=posts_map)

@app.route('/admin/blotter/<int:blotter_id>/delete', methods=['POST'])
@login_required
def admin_delete_blotter(blotter_id):
    """Delete a blotter and its records"""
    conn = get_db()
    
    # Delete associated command logs first (via CASCADE this happens automatically)
    # Delete records (CASCADE will handle command_logs)
    conn.execute('DELETE FROM records WHERE blotter_id = ?', (blotter_id,))
    # Delete blotter
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

    where, params = [], []
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
    return jsonify({
        'posts': [dict(r) for r in rows],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': max(1, (total + per_page - 1) // per_page)
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
        f'SELECT id, filename, county, upload_date, incident_count, source_type, file_path '
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
        'SELECT id, filename, county, upload_date, incident_count, source_type, file_path '
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

    conn.close()
    return render_template('admin_analytics.html',
        daily_labels=daily_labels, daily_counts=daily_counts,
        type_labels=type_labels, type_counts=type_counts,
        agency_labels=agency_labels, agency_counts=agency_counts,
        county_labels=county_labels, county_this=county_this_vals, county_last=county_last_vals,
        blotter_labels=blotter_labels, blotter_counts=blotter_counts,
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
    app.run(host='0.0.0.0', port=5000, debug=True)
