# Mobile UX, PWA & Alerting Implementation Plan

> **Status:** Completed. The two SMTP/alerting test files referenced below (`test_ingestion_alerts_smtp.py`, `test_morning_briefing_admin_alert.py`) were removed in commit 7aa2b07b when `alerting` was refactored under `services.alerts.legacy` — the assertions no longer match the codebase.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship a mobile bottom tab bar, PWA manifest + service worker, footer modals for 4 secondary pages, and close 3 email alerting gaps.

**Architecture:** All UI changes live in `templates/public_page_base.html` (single shared base). PWA assets go in `static/`. Alerting consolidation replaces duplicate `smtplib` blocks with calls to the existing `alerting.send_plaintext_email`. No new dependencies required.

**Tech Stack:** Flask 3.1, SQLite, Tailwind (CDN), vanilla JS, Python `unittest`, `python -m unittest` test runner.

---

## Task 1: Wrap `backup_db.sh` in `job_runner.py`

**Files:**
- Modify: `crontab.txt`
- Modify: `script_watchdog.py` (add job to monitored list)

**Step 1: Update the crontab entry**

Open `crontab.txt`. Find:
```
0 2 * * * /root/montanablotter/backup_db.sh >> /root/montanablotter/backup.log 2>&1
```
Replace with:
```
0 2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name backup_db --log /root/montanablotter/backup.log --workdir /root/montanablotter -- /root/montanablotter/backup_db.sh
```

**Step 2: Add `backup_db` to the watchdog's monitored jobs**

Open `script_watchdog.py`. Find the `JOBS` tuple. The last entry before the closing `)` looks like:
```python
MonitoredJob("ingestion_alerts", ROOT / "ingestion_alerts.log", 2, "every 30 minutes"),
```
Add after it:
```python
MonitoredJob("backup_db", ROOT / "backup.log", 26, "daily"),
```
(26 hours gives a 2-hour buffer over the 24-hour cadence.)

**Step 3: Verify the watchdog still parses cleanly**

```bash
cd /root/montanablotter && source venv/bin/activate
python script_watchdog.py --json | python -m json.tool | head -10
```
Expected: valid JSON with `"status": "ok"` or `"error"` — no `SyntaxError` or `ImportError`.

**Step 4: Reload crontab**

```bash
crontab /root/montanablotter/crontab.txt
crontab -l | grep backup_db
```
Expected: the new `job_runner.py` line appears.

**Step 5: Commit**

```bash
cd /root/montanablotter
git add crontab.txt script_watchdog.py
git commit -m "ops: wrap backup_db.sh in job_runner for email alerting on failure"
```

---

## Task 2: Consolidate SMTP in `ingestion_alerts.py`

**Files:**
- Modify: `ingestion_alerts.py`
- Test: `tests/test_ingestion_alerts_smtp.py` (new)

**Step 1: Write the failing test**

Create `tests/test_ingestion_alerts_smtp.py`:
```python
"""Verify ingestion_alerts uses alerting.send_plaintext_email, not its own SMTP."""
import importlib
import unittest


class IngestionAlertsSmtpTest(unittest.TestCase):
    def test_does_not_define_own_smtp_block(self):
        """ingestion_alerts must not contain a raw smtplib.SMTP() call."""
        import inspect
        import ingestion_alerts
        src = inspect.getsource(ingestion_alerts)
        self.assertNotIn(
            "smtplib.SMTP(",
            src,
            "ingestion_alerts should delegate to alerting.send_plaintext_email, "
            "not call smtplib.SMTP() directly",
        )

    def test_imports_alerting_send(self):
        """ingestion_alerts must import send_plaintext_email from alerting."""
        import inspect
        import ingestion_alerts
        src = inspect.getsource(ingestion_alerts)
        self.assertIn(
            "from alerting import",
            src,
            "ingestion_alerts should import from alerting module",
        )


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run the test — confirm it fails**

```bash
cd /root/montanablotter && source venv/bin/activate
python -m unittest tests/test_ingestion_alerts_smtp.py -v
```
Expected: `FAIL` — `smtplib.SMTP(` is found and `from alerting import` is absent.

**Step 3: Edit `ingestion_alerts.py`**

At the top of the file, remove:
```python
import smtplib
from email.mime.text import MIMEText
```

Add (after the existing imports):
```python
from alerting import collect_alert_recipients, send_plaintext_email as _send_alert_email
```

Scroll down to the `_send_plaintext_email` function (around line 124). Delete the entire function body:
```python
def _send_plaintext_email(recipients: Iterable[str], subject: str, body: str) -> bool:
    smtp_user = ...
    ...
    except Exception:
        return False
```

Now find every call to `_send_plaintext_email(` inside `ingestion_alerts.py` and replace with `_send_alert_email(`.

Also find `_collect_recipients(` calls — check if `ingestion_alerts` has its own `_collect_recipients`. If so, replace with `collect_alert_recipients(conn)` from `alerting`. (Check line ~90–122 for a local `_collect_recipients` function; if it exists, delete it and replace its call sites.)

**Step 4: Run the test — confirm it passes**

```bash
python -m unittest tests/test_ingestion_alerts_smtp.py -v
```
Expected: `OK`.

**Step 5: Smoke-test the module loads without error**

```bash
python -c "import ingestion_alerts; print('ok')"
```
Expected: `ok`

**Step 6: Commit**

```bash
git add ingestion_alerts.py tests/test_ingestion_alerts_smtp.py
git commit -m "refactor: consolidate ingestion_alerts SMTP into alerting module"
```

---

## Task 3: Consolidate admin failure notification in `morning_briefing.py`

**Files:**
- Modify: `morning_briefing.py`
- Test: `tests/test_morning_briefing_admin_alert.py` (new)

**Context:** `morning_briefing.send_email()` sends HTML multipart emails to subscribers — do NOT touch it. Only the admin failure notification inside `run_briefing()` (the bare `except Exception: print(...)` block around line 255–261) needs to be wired to `alerting`.

**Step 1: Write the failing test**

Create `tests/test_morning_briefing_admin_alert.py`:
```python
"""Verify morning_briefing admin failure path uses alerting, not raw smtplib."""
import inspect
import unittest


class MorningBriefingAdminAlertTest(unittest.TestCase):
    def test_imports_alerting_for_admin_failures(self):
        import morning_briefing
        src = inspect.getsource(morning_briefing)
        self.assertIn(
            "from alerting import",
            src,
            "morning_briefing should import alerting for admin failure notifications",
        )


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run — confirm it fails**

```bash
python -m unittest tests/test_morning_briefing_admin_alert.py -v
```
Expected: `FAIL`.

**Step 3: Edit `morning_briefing.py`**

Add near the top (after existing imports):
```python
from alerting import collect_alert_recipients, send_plaintext_email as _send_admin_alert
```

Find the admin briefing failure block inside `run_briefing()`:
```python
try:
    send_email(ADMIN_EMAIL, subject, html)
    print(f"Admin briefing sent ({len(posts)} posts)")
except Exception as e:
    print(f"Admin briefing failed: {e}")
```

Replace with:
```python
try:
    send_email(ADMIN_EMAIL, subject, html)
    print(f"Admin briefing sent ({len(posts)} posts)")
except Exception as e:
    print(f"Admin briefing failed: {e}")
    try:
        conn = get_db()
        recipients = collect_alert_recipients(conn)
        conn.close()
    except Exception:
        recipients = [ADMIN_EMAIL] if ADMIN_EMAIL else []
    _send_admin_alert(
        recipients,
        "[Montana Blotter] Morning briefing failed",
        f"morning_briefing.py failed to send the admin briefing.\n\nError: {e}",
    )
```

**Step 4: Run — confirm it passes**

```bash
python -m unittest tests/test_morning_briefing_admin_alert.py -v
```
Expected: `OK`.

**Step 5: Smoke-test**

```bash
python -c "import morning_briefing; print('ok')"
```
Expected: `ok`

**Step 6: Commit**

```bash
git add morning_briefing.py tests/test_morning_briefing_admin_alert.py
git commit -m "refactor: wire morning_briefing admin failure path through alerting module"
```

---

## Task 4: Flask route for `manifest.json` + PWA head tags

**Files:**
- Create: `static/manifest.json`
- Modify: `app.py` (add one route near the `robots.txt` route, ~line 7637)
- Modify: `templates/public_page_base.html` (add 3 lines to `<head>`)
- Test: `tests/test_manifest_route.py` (new)

**Step 1: Write the failing test**

Create `tests/test_manifest_route.py`:
```python
"""Verify /manifest.json route returns correct content and headers."""
import json
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class ManifestRouteTest(unittest.TestCase):
    def setUp(self):
        # Import lazily to avoid full app startup
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_manifest_returns_200(self):
        resp = self.client.get("/manifest.json")
        self.assertEqual(resp.status_code, 200)

    def test_manifest_content_type(self):
        resp = self.client.get("/manifest.json")
        self.assertIn("manifest", resp.content_type)

    def test_manifest_has_required_fields(self):
        resp = self.client.get("/manifest.json")
        data = json.loads(resp.data)
        for field in ("name", "short_name", "start_url", "display", "icons"):
            self.assertIn(field, data, f"manifest missing field: {field}")

    def test_manifest_display_is_standalone(self):
        resp = self.client.get("/manifest.json")
        data = json.loads(resp.data)
        self.assertEqual(data["display"], "standalone")

    def test_manifest_theme_color(self):
        resp = self.client.get("/manifest.json")
        data = json.loads(resp.data)
        self.assertEqual(data["theme_color"], "#D4892A")


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run — confirm it fails**

```bash
python -m unittest tests/test_manifest_route.py -v
```
Expected: `FAIL` with 404 on `/manifest.json`.

**Step 3: Create `static/manifest.json`**

```json
{
  "name": "Montana Blotter",
  "short_name": "MT Blotter",
  "description": "Daily public safety dispatch for Montana",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0D0C0B",
  "theme_color": "#D4892A",
  "icons": [
    {
      "src": "/static/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "maskable any"
    },
    {
      "src": "/static/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable any"
    }
  ]
}
```

**Step 4: Add the Flask route to `app.py`**

Find the `robots.txt` route (around line 7637):
```python
@app.route('/robots.txt')
```

Add the manifest route just before it:
```python
@app.route('/manifest.json')
def manifest_json():
    import json as _json
    from flask import current_app
    manifest_path = os.path.join(current_app.static_folder, 'manifest.json')
    with open(manifest_path, 'r', encoding='utf-8') as f:
        data = _json.load(f)
    response = jsonify(data)
    response.headers['Content-Type'] = 'application/manifest+json'
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response
```

**Step 5: Run — confirm tests pass**

```bash
python -m unittest tests/test_manifest_route.py -v
```
Expected: all 5 tests `OK`.

**Step 6: Add PWA head tags to `public_page_base.html`**

Find the closing `{% block extra_head %}{% endblock %}` line near the end of `<head>`. Add just before it:
```html
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#D4892A">
    <script>if ('serviceWorker' in navigator) { navigator.serviceWorker.register('/static/sw.js'); }</script>
```

**Step 7: Commit**

```bash
git add static/manifest.json app.py templates/public_page_base.html tests/test_manifest_route.py
git commit -m "feat: add PWA manifest route and head tags"
```

---

## Task 5: Generate PWA icons

**Files:**
- Create: `static/icons/icon-192.png`
- Create: `static/icons/icon-512.png`

**Step 1: Create the icons directory and generate placeholder icons**

```bash
mkdir -p /root/montanablotter/static/icons
```

Run this Python script once to generate amber star icons matching the brand:
```bash
python3 - <<'EOF'
from PIL import Image, ImageDraw
import math, os

def make_icon(size, path):
    img = Image.new("RGBA", (size, size), (13, 12, 11, 255))   # #0D0C0B background
    draw = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2
    r_outer = size * 0.38
    r_inner = size * 0.16
    points = []
    for i in range(10):
        angle = math.radians(-90 + i * 36)
        r = r_outer if i % 2 == 0 else r_inner
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=(212, 137, 42, 255))   # #D4892A amber
    img.save(path, "PNG")
    print(f"Written {path}")

make_icon(192, "/root/montanablotter/static/icons/icon-192.png")
make_icon(512, "/root/montanablotter/static/icons/icon-512.png")
EOF
```

> **Note:** If `PIL` is not available, install it first: `pip install pillow`. Alternatively, replace with any 192×192 and 512×512 PNG images with a `#D4892A` amber star on `#0D0C0B` background — they just need to exist for the manifest to resolve.

**Step 2: Verify files exist and are valid PNGs**

```bash
file /root/montanablotter/static/icons/icon-192.png
file /root/montanablotter/static/icons/icon-512.png
```
Expected: both report `PNG image data`.

**Step 3: Commit**

```bash
git add static/icons/
git commit -m "feat: add PWA icons (amber star, 192 and 512)"
```

---

## Task 6: Service worker and offline fallback page

**Files:**
- Create: `static/sw.js`
- Create: `templates/offline.html`
- Modify: `app.py` (add `/offline` route)

**Step 1: Create `templates/offline.html`**

```html
{% extends "public_page_base.html" %}
{% block title %}Offline — Montana Blotter{% endblock %}
{% block content %}
<div style="max-width:480px; margin:80px auto; padding:0 24px; text-align:center;">
    <div style="width:56px; height:56px; background:#D4892A; border-radius:8px; display:flex; align-items:center; justify-content:center; margin:0 auto 24px; color:#0D0C0B;">
        <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2"><line x1="1" y1="1" x2="23" y2="23"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55M5 12.55a10.94 10.94 0 0 1 5.17-2.39M10.71 5.05A16 16 0 0 1 22.56 9M1.42 9a15.91 15.91 0 0 1 4.7-2.88M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01"/></svg>
    </div>
    <h1 style="font-family:'Playfair Display',Georgia,serif; font-size:1.5rem; font-weight:900; color:#E8DFD0; margin:0 0 12px;">You're offline</h1>
    <p style="font-family:'IBM Plex Mono',monospace; font-size:0.75rem; color:#8A7F72; margin:0 0 32px; line-height:1.6;">
        Montana Blotter needs a network connection to load fresh reports.<br>
        Check your connection and try again.
    </p>
    <a href="/" style="font-family:'IBM Plex Mono',monospace; font-size:0.7rem; background:#D4892A; color:#0D0C0B; padding:10px 20px; border-radius:4px; text-decoration:none; font-weight:600; text-transform:uppercase; letter-spacing:0.08em;">Try Again</a>
</div>
{% endblock %}
```

**Step 2: Add `/offline` route to `app.py`**

Near the `robots.txt` route, add:
```python
@app.route('/offline')
def offline_page():
    return render_template('offline.html',
        page_title='Offline',
        meta_description='You are offline.',
        canonical_url=None,
    ), 200
```

**Step 3: Create `static/sw.js`**

```javascript
const CACHE_VERSION = 'v1';
const STATIC_CACHE = 'mb-static-' + CACHE_VERSION;
const OFFLINE_URL = '/offline';

const STATIC_ASSETS = [
  '/offline',
  '/static/manifest.json',
];

// Install: pre-cache the offline fallback page
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate: clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== STATIC_CACHE).map(k => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// Fetch: network-first for navigation, cache-first for static assets
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== location.origin) return;

  // Static assets: cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => cached || fetch(request).then(resp => {
        const clone = resp.clone();
        caches.open(STATIC_CACHE).then(cache => cache.put(request, clone));
        return resp;
      }))
    );
    return;
  }

  // Navigation requests: network-first, offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(OFFLINE_URL).then(fallback => fallback || new Response('Offline', { status: 503 }))
      )
    );
    return;
  }
});
```

**Step 4: Smoke-test the offline route**

```bash
cd /root/montanablotter && source venv/bin/activate
python -c "
from app import app
app.config['TESTING'] = True
c = app.test_client()
r = c.get('/offline')
print('status:', r.status_code)
assert r.status_code == 200
print('ok')
"
```
Expected: `status: 200` then `ok`.

**Step 5: Commit**

```bash
git add static/sw.js templates/offline.html app.py
git commit -m "feat: add service worker (network-first) and offline fallback page"
```

---

## Task 7: Mobile bottom tab bar

**Files:**
- Modify: `templates/public_page_base.html`

**Step 1: Hide the hamburger menu on mobile in the header**

Find in `public_page_base.html` the hamburger button line:
```html
<button id="pub-mobile-btn" class="md:hidden" ...>
```
The current hamburger + its dropdown (`#pub-mobile-menu`) are the old mobile nav. They stay in the DOM but are no longer needed on mobile once the tab bar is in place. Add `hidden` to the mobile menu div so it never shows by default (the tab bar "More" drawer replaces it):
```html
<div id="pub-mobile-menu" class="hidden px-4 pb-3 ...">
```
(Remove `md:hidden` — replace with just `hidden`. The JS below will open the "More" drawer instead.)

**Step 2: Add bottom tab bar + More drawer just before `</body>`**

Add this block at the very end of `public_page_base.html`, immediately before `</body>`:

```html
<!-- ═══════════════════════════════════════ MOBILE BOTTOM TAB BAR ═══ -->
<nav id="mb-tab-bar" class="md:hidden" style="position:fixed; bottom:0; left:0; right:0; z-index:60; background:#161513; border-top:1px solid #282420; display:flex; height:56px;">

  {% set tabs = [
    ('/', 'home', 'Home', '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>'),
    ('/arrests', 'arrests', 'Arrests', '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'),
    ('/counties', 'counties', 'Counties', '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>'),
    ('/jail-bookings', 'jail_bookings', 'Bookings', '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>'),
  ] %}

  {% for href, nav_id, label, svg_path in tabs %}
  {% set is_active = active_nav == nav_id or (nav_id == 'home' and request.path == '/') %}
  <a href="{{ href }}" style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3px; text-decoration:none; color:{{ '#D4892A' if is_active else '#5F564C' }}; font-family:'IBM Plex Mono',monospace; font-size:0.42rem; font-weight:600; text-transform:uppercase; letter-spacing:0.08em; transition:color 0.15s;">
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{{ svg_path | safe }}</svg>
    {{ label }}
  </a>
  {% endfor %}

  <!-- More tab -->
  <button id="mb-more-btn" onclick="document.getElementById('mb-more-drawer').showModal()" style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:3px; background:none; border:none; cursor:pointer; color:#5F564C; font-family:'IBM Plex Mono',monospace; font-size:0.42rem; font-weight:600; text-transform:uppercase; letter-spacing:0.08em;">
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
    More
  </button>
</nav>

<!-- ═══════════════════════════════════════ MORE DRAWER ═══ -->
<dialog id="mb-more-drawer" style="position:fixed; bottom:0; left:0; right:0; top:auto; margin:0; width:100%; max-height:65vh; background:#161513; border:none; border-top:1px solid #282420; border-radius:12px 12px 0 0; padding:0; overflow-y:auto; z-index:70;">
  <div style="padding:12px 16px 8px; display:flex; align-items:center; justify-content:space-between;">
    <span style="font-family:'IBM Plex Mono',monospace; font-size:0.55rem; color:#D4892A; text-transform:uppercase; letter-spacing:0.15em; font-weight:600;">More</span>
    <button onclick="document.getElementById('mb-more-drawer').close()" style="background:none; border:none; color:#5F564C; cursor:pointer; font-size:1.2rem; line-height:1;">✕</button>
  </div>
  <div style="padding:4px 0 72px;">
    {% set more_items = [
      ('/courts',          'Courts'),
      ('/meetings',        'Meetings'),
      ('/detention',       'Detention'),
      ('/bail-bonds',      'Bail Bonds'),
      ('/case-journeys',   'Case Journeys'),
      ('/subscribe',       'Subscribe'),
    ] %}
    {% for href, label in more_items %}
    <a href="{{ href }}" style="display:block; padding:13px 20px; font-family:'IBM Plex Mono',monospace; font-size:0.7rem; font-weight:600; color:#E8DFD0; text-decoration:none; border-bottom:1px solid #1E1C1A; text-transform:uppercase; letter-spacing:0.08em;">{{ label }}</a>
    {% endfor %}

    {% if public_user %}
    <a href="/account" style="display:block; padding:13px 20px; font-family:'IBM Plex Mono',monospace; font-size:0.7rem; font-weight:600; color:#A89880; text-decoration:none; border-bottom:1px solid #1E1C1A; text-transform:uppercase; letter-spacing:0.08em;">Account</a>
    <a href="/logout" style="display:block; padding:13px 20px; font-family:'IBM Plex Mono',monospace; font-size:0.7rem; font-weight:600; color:#C94A3A; text-decoration:none; text-transform:uppercase; letter-spacing:0.08em;">Log Out</a>
    {% else %}
    <a href="/login" style="display:block; padding:13px 20px; font-family:'IBM Plex Mono',monospace; font-size:0.7rem; font-weight:600; color:#A89880; text-decoration:none; border-bottom:1px solid #1E1C1A; text-transform:uppercase; letter-spacing:0.08em;">Log In</a>
    <a href="/register" style="display:block; padding:13px 20px; font-family:'IBM Plex Mono',monospace; font-size:0.7rem; font-weight:600; color:#D4892A; text-decoration:none; text-transform:uppercase; letter-spacing:0.08em;">Register</a>
    {% endif %}
  </div>
</dialog>

<script>
/* Close the More drawer when tapping outside it */
(function () {
  var drawer = document.getElementById('mb-more-drawer');
  if (!drawer) return;
  drawer.addEventListener('click', function (e) {
    var rect = drawer.getBoundingClientRect();
    if (e.clientY < rect.top) { drawer.close(); }
  });
})();
</script>
<!-- ═══════════════════════════════════════ END MOBILE NAV ═══ -->
```

**Step 3: Add bottom padding to public page content**

In `public_page_base.html`, find the main content wrapper. It uses `{% block content %}`. Wrap it so mobile gets `pb-20` (80px) to clear the tab bar. Find:
```html
<body style="background:#0D0C0B; ...">
```
Add a wrapping `<div>` around `{% block content %}` that adds bottom padding on mobile only:
```html
<div class="md:pb-0 pb-16">
  {% block content %}{% endblock %}
</div>
```

**Step 4: Smoke-test in browser / dev server**

```bash
cd /root/montanablotter && source venv/bin/activate
python app.py &
# Open http://localhost:5000 in browser, resize to mobile width
# Verify: bottom tab bar appears, More drawer opens/closes, tabs highlight correctly
kill %1
```

**Step 5: Commit**

```bash
git add templates/public_page_base.html
git commit -m "feat: add mobile bottom tab bar and More slide-up drawer"
```

---

## Task 8: Footer modals for /standards, /corrections, /laws

**Files:**
- Modify: `templates/public_page_base.html` (add modal markup + JS)
- Modify: `app.py` (update footer link injection if needed — check `inject_public_nav`)

**Step 1: Add modal markup to `public_page_base.html`**

Read the current `/standards`, `/corrections`, and `/laws` templates to extract their body content. Then add three `<dialog>` elements at the bottom of `public_page_base.html` (after the tab bar block, before `</body>`):

```html
<!-- ═══════════════════════════ FOOTER MODALS ═══ -->

<!-- Standards modal -->
<dialog id="modal-standards" style="max-width:600px; width:90%; background:#161513; border:1px solid #282420; border-radius:8px; color:#E8DFD0; padding:0;">
  <div style="padding:20px 24px 8px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #282420;">
    <span style="font-family:'Playfair Display',serif; font-size:1rem; font-weight:900;">Editorial Standards</span>
    <button onclick="document.getElementById('modal-standards').close()" style="background:none;border:none;color:#5F564C;cursor:pointer;font-size:1.2rem;">✕</button>
  </div>
  <div style="padding:20px 24px 24px; font-family:'Spectral',Georgia,serif; font-size:0.875rem; line-height:1.7; color:#C8BFB0; max-height:65vh; overflow-y:auto;">
    {% include "includes/standards_content.html" ignore missing %}
    {% if not "includes/standards_content.html" %}
    <p>Montana Blotter reports public records only. We do not editorialize on guilt or innocence. All arrests are allegations until proven in court. Full standards at <a href="/standards" style="color:#D4892A;">/standards</a>.</p>
    {% endif %}
  </div>
</dialog>

<!-- Corrections modal -->
<dialog id="modal-corrections" style="max-width:600px; width:90%; background:#161513; border:1px solid #282420; border-radius:8px; color:#E8DFD0; padding:0;">
  <div style="padding:20px 24px 8px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #282420;">
    <span style="font-family:'Playfair Display',serif; font-size:1rem; font-weight:900;">Corrections</span>
    <button onclick="document.getElementById('modal-corrections').close()" style="background:none;border:none;color:#5F564C;cursor:pointer;font-size:1.2rem;">✕</button>
  </div>
  <div style="padding:20px 24px 24px; font-family:'Spectral',Georgia,serif; font-size:0.875rem; line-height:1.7; color:#C8BFB0; max-height:65vh; overflow-y:auto;">
    {% include "includes/corrections_content.html" ignore missing %}
    {% if not "includes/corrections_content.html" %}
    <p>To request a correction, email <a href="mailto:corrections@montanablotter.com" style="color:#D4892A;">corrections@montanablotter.com</a> with the URL and description of the error. We aim to respond within 48 hours.</p>
    {% endif %}
  </div>
</dialog>

<!-- Laws modal -->
<dialog id="modal-laws" style="max-width:700px; width:92%; background:#161513; border:1px solid #282420; border-radius:8px; color:#E8DFD0; padding:0;">
  <div style="padding:20px 24px 8px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #282420;">
    <span style="font-family:'Playfair Display',serif; font-size:1rem; font-weight:900;">Montana Laws Reference</span>
    <button onclick="document.getElementById('modal-laws').close()" style="background:none;border:none;color:#5F564C;cursor:pointer;font-size:1.2rem;">✕</button>
  </div>
  <div style="padding:16px 24px 8px; border-bottom:1px solid #282420;">
    <input id="modal-laws-search" type="search" placeholder="Search statutes…" oninput="filterLawsModal(this.value)"
      style="width:100%; background:#0D0C0B; border:1px solid #282420; border-radius:4px; padding:8px 12px; color:#E8DFD0; font-family:'IBM Plex Mono',monospace; font-size:0.7rem;">
  </div>
  <div id="modal-laws-list" style="padding:8px 0 24px; max-height:60vh; overflow-y:auto;">
    <!-- Laws content loaded from /laws page via fetch on first open, or inline via include -->
    <p style="padding:16px 24px; font-family:'IBM Plex Mono',monospace; font-size:0.65rem; color:#5F564C;">
      Loading… or <a href="/laws" style="color:#D4892A;">view full page</a>.
    </p>
  </div>
</dialog>

<script>
/* Modal open via anchor hash */
(function () {
  var modalMap = { 'modal-standards': true, 'modal-corrections': true, 'modal-laws': true };
  function openFromHash() {
    var hash = location.hash.replace('#', '');
    if (modalMap[hash]) {
      var el = document.getElementById(hash);
      if (el && el.showModal) { el.showModal(); }
    }
  }
  openFromHash();
  window.addEventListener('hashchange', openFromHash);

  /* Close on backdrop click */
  ['modal-standards','modal-corrections','modal-laws'].forEach(function(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('click', function(e) {
      var r = el.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) {
        el.close();
      }
    });
  });
})();

function filterLawsModal(query) {
  var list = document.getElementById('modal-laws-list');
  if (!list) return;
  var rows = list.querySelectorAll('[data-law-row]');
  var q = query.toLowerCase();
  rows.forEach(function(row) {
    row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}
</script>
```

**Step 2: Update footer links in `inject_public_nav` (app.py)**

Find `public_footer_items` in `inject_public_nav` (~line 5742). Change:
```python
{'href': '/standards', 'label': 'Standards'},
{'href': '/corrections', 'label': 'Corrections'},
```
To:
```python
{'href': '#modal-standards', 'label': 'Standards'},
{'href': '#modal-corrections', 'label': 'Corrections'},
```

**Step 3: Smoke-test**

```bash
python -c "
from app import app
app.config['TESTING'] = True
c = app.test_client()
r = c.get('/standards')
print('/standards still 200:', r.status_code == 200)
r = c.get('/corrections')
print('/corrections still 200:', r.status_code == 200)
"
```
Expected: both `True` (existing routes untouched).

**Step 4: Commit**

```bash
git add templates/public_page_base.html app.py
git commit -m "feat: add footer modals for standards, corrections, and laws; keep existing routes"
```

---

## Task 9: Guides consolidation into blog

**Files:**
- Modify: `app.py` (add `?category=guide` support to `/blog` route)
- Modify: `templates/blog.html` (add category filter UI)

**Step 1: Find the `/blog` route in `app.py`**

```bash
grep -n "^@app.route('/blog')" /root/montanablotter/app.py
```

**Step 2: Add category filtering**

Inside the `/blog` route function, find the DB query that fetches blog posts. Add a `category` query param filter:
```python
category = request.args.get('category', '').strip().lower()
# ... existing query building ...
# Add to WHERE clause if category is set:
if category:
    query += " AND lower(category) = ?"
    params.append(category)
```
Pass `active_category=category` to the template context.

**Step 3: Update the blog template**

In `templates/blog.html`, add a filter row above the post list:
```html
<div style="margin-bottom:24px; display:flex; gap:8px; flex-wrap:wrap;">
  <a href="/blog" style="font-family:'IBM Plex Mono',monospace; font-size:0.6rem; font-weight:600; text-transform:uppercase; letter-spacing:0.08em; padding:5px 12px; border-radius:3px; text-decoration:none; {{ 'background:#D4892A; color:#0D0C0B;' if not active_category else 'color:#8A7F72; border:1px solid #282420;' }}">All</a>
  <a href="/blog?category=guide" style="font-family:'IBM Plex Mono',monospace; font-size:0.6rem; font-weight:600; text-transform:uppercase; letter-spacing:0.08em; padding:5px 12px; border-radius:3px; text-decoration:none; {{ 'background:#D4892A; color:#0D0C0B;' if active_category == 'guide' else 'color:#8A7F72; border:1px solid #282420;' }}">Guides</a>
</div>
```

**Step 4: Update `/guides` route to redirect to `/blog?category=guide`**

In `app.py`, find the `/guides` route. Add a redirect at the top of the function (preserving the old route for SEO via 301 is optional — use 200 here since guides are already indexed under `/guides`):
```python
@app.route('/guides')
def guides_hub():
    return redirect('/blog?category=guide', 301)
```
Individual `/guides/<slug>` routes stay unchanged.

**Step 5: Smoke-test**

```bash
python -c "
from app import app
app.config['TESTING'] = True
c = app.test_client()
r = c.get('/guides', follow_redirects=False)
print('/guides redirects:', r.status_code == 301)
r2 = c.get('/blog?category=guide')
print('/blog?category=guide 200:', r2.status_code == 200)
"
```

**Step 6: Commit**

```bash
git add app.py templates/blog.html
git commit -m "feat: merge /guides hub into /blog with category filter; redirect /guides → /blog?category=guide"
```

---

## Task 10: Final verification

**Step 1: Run all tests**

```bash
cd /root/montanablotter && source venv/bin/activate
python -m unittest discover -s tests -p "test_*.py" -v 2>&1 | tail -20
```
Expected: all existing tests still pass; new tests pass.

**Step 2: Check app starts cleanly**

```bash
python -c "from app import app; print('app loaded ok')"
```

**Step 3: Verify PWA checklist**

```bash
# manifest route
python -c "
from app import app; app.config['TESTING']=True; c=app.test_client()
r=c.get('/manifest.json')
print('manifest:', r.status_code, r.content_type)
# offline
r2=c.get('/offline')
print('offline:', r2.status_code)
# sw.js static
import os
print('sw.js exists:', os.path.exists('static/sw.js'))
print('icon-192 exists:', os.path.exists('static/icons/icon-192.png'))
"
```

**Step 4: Verify alerting**

```bash
python -m unittest tests/test_ingestion_alerts_smtp.py tests/test_morning_briefing_admin_alert.py tests/test_manifest_route.py -v
```
Expected: all green.

**Step 5: Final commit if any loose ends**

```bash
git status
# If clean, tag the release
git tag mobile-ux-pwa-v1
```

---

## Summary of Changes

| Task | Files Changed | Type |
|------|--------------|------|
| 1 | `crontab.txt`, `script_watchdog.py` | ops |
| 2 | `ingestion_alerts.py`, new test | refactor |
| 3 | `morning_briefing.py`, new test | refactor |
| 4 | `static/manifest.json`, `app.py`, `public_page_base.html`, new test | feat |
| 5 | `static/icons/*.png` | asset |
| 6 | `static/sw.js`, `templates/offline.html`, `app.py` | feat |
| 7 | `templates/public_page_base.html` | feat |
| 8 | `templates/public_page_base.html`, `app.py` | feat |
| 9 | `app.py`, `templates/blog.html` | feat |
| 10 | — | verify |
