# Montana Blotter — Mobile UX, PWA & Alerting Design
**Date:** 2026-03-20
**Status:** Approved
**Scope:** Navigation overhaul, mobile bottom tab bar, PWA shell, page consolidation, email alerting gaps

---

## Goals

1. Make the site feel app-like on mobile (high % of traffic is mobile)
2. Reduce click-fatigue by eliminating low-traffic secondary pages from the nav
3. Enable "Add to Home Screen" on iOS/Android via PWA manifest
4. Provide offline fallback via service worker
5. Close email alerting gaps so every script failure triggers an admin notification

---

## Section 1 — Mobile Bottom Tab Bar

### What
A `<nav>` fixed to the bottom of the viewport, visible **only on mobile** (`md:hidden`). Replaces the current hamburger menu + dropdown on small screens.

### Tabs (5)
| Tab | Icon | Route |
|-----|------|-------|
| Home | house SVG | `/` |
| Arrests | badge SVG | `/arrests` |
| Counties | map-pin SVG | `/counties` |
| Bookings | clipboard SVG | `/jail-bookings` |
| More | grid/menu SVG | Opens slide-up drawer |

### "More" Drawer
A half-height slide-up `<dialog>` containing:
- Courts, Meetings, Detention, Bail Bonds, Case Journeys
- Subscribe / Log In / Account
- Standards, Corrections, Terms, Privacy (as modal triggers — see Section 2)

### Implementation Details
- Added once to `templates/public_page_base.html`, just before `</body>`
- Active tab driven by `active_nav` Jinja2 variable (already used by top nav)
- SVG icons inline — no Font Awesome dependency for tab bar
- Remove `#pub-mobile-btn` hamburger and `#pub-mobile-menu` dropdown from header on mobile breakpoints (keep on desktop `md:` and above)
- Main content gets `pb-20` bottom padding via a Jinja block to prevent last card being hidden behind tab bar
- ~30 lines of vanilla JS for drawer open/close; lives inline in base template

---

## Section 2 — Page Consolidation (Footer Modals)

### Pages Being Consolidated
All four keep their existing routes alive as 200 responses (SEO preserved).

| Page | New UX | Notes |
|------|--------|-------|
| `/standards` | `<dialog>` modal, triggered from footer link | ~300 words editorial standards content |
| `/corrections` | `<dialog>` modal, triggered from footer link | Links to corrections email |
| `/laws` | `<dialog>` modal with searchable filter | Statute table pulled into modal; `/laws` route stays for SEO |
| `/guides` | Hub list merges into blog index with "Guides" category filter | `/guides/<slug>` routes stay for SEO |

### Implementation Details
- Footer links change from `href="/standards"` to `href="#modal-standards"` with a JS `preventDefault` + modal open
- Existing routes return the same content (no 301 redirects) — safe for search rankings
- Modals injected once in `public_page_base.html` (lazy-rendered; content loaded inline)
- Guides hub: add a `?category=guide` filter param to `/blog` route or a separate includes partial

---

## Section 3 — Progressive Web App (PWA) Shell

### New Files
| File | Purpose |
|------|---------|
| `static/manifest.json` | App identity, icons, display mode, theme color |
| `static/sw.js` | Service worker — network-first dynamic, cache-first static, offline fallback |
| `static/icons/icon-192.png` | PWA icon (maskable) |
| `static/icons/icon-512.png` | PWA icon (maskable) |
| `templates/offline.html` | Offline fallback page |

### Manifest Config
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
    { "src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable any" },
    { "src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable any" }
  ]
}
```

### Service Worker Strategy
- **Dynamic routes** (`/`, `/arrests`, `/counties`, etc.): Network-first, fall back to `/offline.html`
- **Static assets** (`/static/css`, `/static/js`, fonts): Cache-first with 30-day TTL
- **Reason for network-first on dynamic routes:** Staleness is the bigger risk on a news/public-safety site — users must see current data

### Flask Changes
- New route: `GET /manifest.json` → serves `static/manifest.json` with `Content-Type: application/manifest+json` and `Cache-Control: public, max-age=86400`
- Add to `public_page_base.html` `<head>`:
  ```html
  <link rel="manifest" href="/manifest.json">
  <meta name="theme-color" content="#D4892A">
  <script>if('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js');</script>
  ```

---

## Section 4 — Email Alerting Gaps

### Current State
`job_runner.py` already wraps all cron jobs and emails on state transitions (fail → ok, ok → fail). `alerting.py` is the canonical SMTP sender.

### Gap 1 — `backup_db.sh` not wrapped (CRITICAL)
**Fix:** Update `crontab.txt` — change the raw bash call to use `job_runner.py` wrapper:
```cron
# Before:
0 2 * * * /root/montanablotter/backup_db.sh >> /root/montanablotter/backup.log 2>&1

# After:
0 2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py \
  --name backup_db --log /root/montanablotter/backup.log --workdir /root/montanablotter \
  -- /root/montanablotter/backup_db.sh
```

### Gap 2 — `ingestion_alerts.py` has its own SMTP block
**Fix:** Replace inline `smtplib` block with `from alerting import send_plaintext_email`.

### Gap 3 — `morning_briefing.py` has its own SMTP block
**Fix:** Replace inline `smtplib` block with import from `alerting.py` for admin failure notifications only. (Subscriber delivery stays as-is — it sends HTML to subscribers, not admin alerts.)

### Out of Scope
- `email_worker.py`: Intentionally kept separate — sends HTML multipart to subscribers, not plain-text admin alerts
- `resend_bounced.py`: Low-frequency manual tool, low risk

---

## Files Changed Summary

| File | Change Type |
|------|-------------|
| `templates/public_page_base.html` | Add bottom tab bar, More drawer, PWA head tags, footer modal markup |
| `templates/offline.html` | New — PWA offline fallback |
| `static/manifest.json` | New |
| `static/sw.js` | New |
| `static/icons/icon-192.png` | New |
| `static/icons/icon-512.png` | New |
| `app.py` | Add `/manifest.json` route; update footer modal link hrefs; add `/blog?category=guide` support |
| `crontab.txt` | Wrap `backup_db.sh` in `job_runner.py` |
| `ingestion_alerts.py` | Replace inline SMTP with `alerting.py` import |
| `morning_briefing.py` | Replace admin-alert SMTP with `alerting.py` import |

---

## Out of Scope (Future Work)
- Splitting `app.py` (16k lines) into Flask blueprints — separate initiative
- Compiled Tailwind build (currently CDN) — performance improvement, separate PR
- Full SMTP consolidation in `email_worker.py` and `resend_bounced.py`
- PWA "Add to Home Screen" nudge banner
