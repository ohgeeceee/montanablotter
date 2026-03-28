# Recovery Center Full Control Panel — Design Spec
**Date:** 2026-03-28
**Status:** Approved for implementation

---

## Overview

Expand the existing recovery center advertising feature with a full-featured advertiser portal (username/password login), six bonus features exclusive to Montana Blotter's position as a criminal justice news site, and enhanced admin tooling. Target audience: recovery center staff managing their listing, and site admins managing the directory.

---

## 1. Account System

### Advertiser Login
- New login page at `GET/POST /recovery-portal/login`
- Username + bcrypt-hashed password authentication
- Session stored as `recovery_advertiser_session` key in Flask session (separate from admin and public user sessions)
- Logout at `GET /recovery-portal/logout`
- The existing token link (`/recovery-control-panel/<token>`) continues to work as a magic-login fallback: auto-authenticates and redirects to `/recovery-portal/dashboard`
- `@recovery_login_required` decorator protects all portal routes

### Account Creation
- Admin creates advertiser accounts from the CMS page (`/admin/recovery-ads/cms/<order_id>`)
- Admin sets username + temporary password; system emails advertiser with login link
- Advertisers can change username/password from their Account tab
- One account per order (UNIQUE constraint on `order_id`)

---

## 2. Control Panel Structure

**Base URL:** `/recovery-portal/dashboard`

Sidebar navigation tabs:

| Tab | Route | Tiers |
|-----|-------|-------|
| Overview | `/recovery-portal/dashboard` | All |
| Edit Listing | `/recovery-portal/dashboard/listing` | All |
| Inquiries | `/recovery-portal/dashboard/inquiries` | Silver, Gold |
| Demand Signals | `/recovery-portal/dashboard/demand` | Gold |
| Reports | `/recovery-portal/dashboard/reports` | Silver, Gold |
| Account | `/recovery-portal/dashboard/account` | All |

**Overview tab shows:**
- Package tier + billing cycle + status
- Impressions and clicks (all tiers, not just Gold)
- Unread inquiry count badge (Silver/Gold)
- Quick link to view listing in directory
- Active badges displayed as pills

---

## 3. Database Schema

All changes made in `ensure_recovery_ad_schema()` in `init_db.py`.

### New Table: `recovery_ad_accounts`
```sql
CREATE TABLE IF NOT EXISTS recovery_ad_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER UNIQUE NOT NULL REFERENCES recovery_ad_orders(id),
    username TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
)
```

### New Table: `recovery_ad_inquiries`
```sql
CREATE TABLE IF NOT EXISTS recovery_ad_inquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES recovery_ad_orders(id),
    message TEXT NOT NULL,
    contact_hint TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    read_at TEXT,
    notified_at TEXT
)
CREATE INDEX idx_recovery_inquiries_order ON recovery_ad_inquiries(order_id)
CREATE INDEX idx_recovery_inquiries_read ON recovery_ad_inquiries(read_at)
```

### New Table: `recovery_ad_badges`
```sql
CREATE TABLE IF NOT EXISTS recovery_ad_badges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES recovery_ad_orders(id),
    badge_type TEXT NOT NULL,
    awarded_by_user_id INTEGER REFERENCES users(id),
    awarded_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(order_id, badge_type)
)
-- badge_type values: samhsa | state_licensed | sliding_scale | scholarship | 24_7_intake | peer_support
```

### New Table: `recovery_ad_copy_log`
```sql
CREATE TABLE IF NOT EXISTS recovery_ad_copy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES recovery_ad_orders(id),
    generated_tagline TEXT,
    generated_description TEXT,
    accepted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
```

### Altered Table: `recovery_ad_listings`
```sql
ALTER TABLE recovery_ad_listings ADD COLUMN crisis_cta_enabled INTEGER NOT NULL DEFAULT 0
ALTER TABLE recovery_ad_listings ADD COLUMN inquiry_notify_email TEXT
```

---

## 4. Feature Implementations

### A — Blotter Demand Signals (Gold only)

**Route:** `GET /recovery-portal/dashboard/demand`

Queries `records` table for charges matching a keyword list in the advertiser's `county` field over the last 12 weeks, aggregated by week:

**Keywords:** `DUI`, `OUI`, `possession`, `methamphetamine`, `meth`, `fentanyl`, `heroin`, `cocaine`, `alcohol`, `controlled substance`, `intoxicated`, `under the influence`

Returns:
- Week-by-week bar chart (12 weeks)
- Top 5 charge types ranked by frequency
- Comparison to prior 12-week period (up/down %)

Data cached daily in `app_settings` key `recovery_demand_cache_<county>` as JSON. Cache invalidated at midnight.

**Admin view:** `/admin/recovery-ads/demand` — statewide county-by-county demand table for the past 12 weeks. Used to identify underserved markets for sales outreach.

---

### B — Crisis-Moment CTA Injection (Gold only)

**Context processor** added to `app.py` (`inject_recovery_cta()`):

1. Only fires when `request.path` starts with `/post/`
2. Extracts `post_id` from path
3. Checks if any record in that post has a substance-related charge (same keyword list as A) — cached per post_id in `app_settings`
4. If yes: queries active Gold listing with `crisis_cta_enabled = 1` matching post's `county` field
5. Falls back to any active Gold listing statewide if no county match
6. Injects `recovery_cta` dict into template context (`None` if no match)

**`post_detail.html` addition:** A subtle card near the bottom of the post:
```
"Struggling with substance use? Help is available."
[Center Name] · [City] · [Phone]
[Tagline if set]
[Get Help Now →] (tracked click)
```

Admin toggle: `crisis_cta_enabled` checkbox in CMS per listing. Off by default — admin enables after verifying center is active and appropriate.

---

### C — Claude Copy Generator (Silver, Gold)

**Route:** `POST /recovery-portal/dashboard/listing/generate-copy`

**Input:** center_name, city, county, services (list)
**Model:** `claude-sonnet-4-6`
**System prompt:**
> "You are a compassionate writer helping addiction recovery centers communicate with families in crisis. Write clear, warm, non-clinical copy. Never make medical claims. Keep language accessible and hopeful."

**User prompt:**
> "Write a tagline (under 120 characters) and a description (under {limit} characters) for {center_name} in {city}, Montana. Services offered: {services}. The audience is families of people who have just been arrested on substance-related charges."

**Response:** JSON `{tagline: "...", description: "..."}`

The control panel form has a **"Generate with AI ✦"** button that fires an AJAX call, populates the tagline and description fields (editable before saving), and shows a "Generated — review before saving" notice. Each generation is logged to `recovery_ad_copy_log` with `accepted = 0`. When the advertiser saves, the most recent unaccepted log entry for that order is updated to `accepted = 1`.

Rate limit: max 5 generations per order per day (checked before calling Claude).

---

### D — Anonymous Inquiry Inbox (Silver, Gold)

**Public submission:** Small form on each Silver/Gold listing card in `/recovery-centers` directory:
- Textarea: "Send a private message to this center" (max 1000 chars)
- Optional contact hint: "How can they reach you? (optional)" (max 200 chars — no validation, completely free-form)
- Submit button: "Send Anonymously"
- POST to `/recovery-centers/<order_id>/inquire`
- No CAPTCHA (low abuse risk), rate-limited to 3 per IP per hour

**Storage:** Saved to `recovery_ad_inquiries`. No IP address stored. `notified_at` set when email is sent.

**Email notification:** Sent to `inquiry_notify_email` (or order's `email` field) via existing email infrastructure:
> Subject: "New private message for [Center Name] on Montana Blotter"
> Body: message text + contact hint + link to portal inbox

**Advertiser inbox tab:**
- List of messages, newest first
- Unread shown in amber, read in muted
- Click to expand full message
- "Mark as read" on open
- No reply functionality in-platform (center contacts family via their provided hint)

**Admin inquiry view:** `/admin/recovery-ads/inquiries` — all inquiries across all centers. Shows: center name, message length, timestamp, read status. Full message visible on click. For moderation only.

---

### E — Monthly AI Performance Report (Silver, Gold)

**Cron:** Runs 1st of each month at 8am MT via `email_worker.py` or a dedicated `recovery_report_worker.py`.

**For each active Silver/Gold order with a valid email:**

Gathers:
- Impressions this month vs prior month
- Clicks this month vs prior month
- Click-through rate
- Inquiry count this month (Silver/Gold)
- Top 3 charge types in their county this month (demand signals)
- Current listing completeness score (0–100%, based on filled fields)

Claude generates a ~200-word plain-English email section:
> "Here's how [Center Name] performed in [Month]..."
> One actionable suggestion based on lowest-scoring listing field or CTR.

Email sent via existing SMTP infrastructure. Subject: `"Your Montana Blotter Recovery Listing — [Month] Report"`

Logged to `scheduled_job_runs` table (already exists).

---

### F — Verified Trust Badges (All tiers)

**Badge types and display labels:**

| badge_type | Label | Icon |
|---|---|---|
| `samhsa` | SAMHSA Listed | 🏥 |
| `state_licensed` | MT State Licensed | ✓ |
| `sliding_scale` | Sliding Scale Fees | $ |
| `scholarship` | Scholarships Available | 🎓 |
| `24_7_intake` | 24/7 Intake | 📞 |
| `peer_support` | Peer Support | 🤝 |

**Self-attestation:** Advertisers can check boxes on their listing edit form. These show as "Pending verification" with a clock icon in the directory and portal until admin verifies.

**Admin verification:** Admin clicks verify/revoke per badge in the CMS. Verified badges get an `awarded_by_user_id` and `awarded_at` set.

**Display:** Badges appear as small colored pills on the directory listing (all tiers) and in the advertiser portal overview.

---

## 5. Admin Panel Additions

### Orders List (`/admin/recovery-ads`)
New columns added to the table:
- **Inquiries** — count of unread inquiries (shown in amber if > 0)
- **Account** — "✓ Login" or "— No login" with quick-create link
- **Badges** — small icon pills for each awarded badge

### CMS Page (`/admin/recovery-ads/cms/<order_id>`)
Three new sections:

**Badges section:** Six checkboxes. Verified = filled/colored. Pending (self-attested) = outlined. Admin can verify or revoke each.

**Account section:** Shows username and last login if account exists. If no account: form with username field, auto-filled email, and "Create Account & Email Advertiser" button that generates a random temp password and sends login instructions.

**Crisis CTA toggle:** Gold orders only. Toggle to enable/disable crisis injection for this listing. Off by default.

### New Admin Routes
- `POST /admin/recovery-ads/order/<id>/badge` — award/revoke badge
- `POST /admin/recovery-ads/order/<id>/create-account` — create advertiser login
- `POST /admin/recovery-ads/order/<id>/crisis-cta` — toggle crisis CTA
- `GET /admin/recovery-ads/inquiries` — all inquiries view
- `GET /admin/recovery-ads/demand` — statewide demand signals

---

## 6. New Files

| File | Purpose |
|------|---------|
| `blueprints/recovery_portal.py` | New Blueprint (`recovery_portal_bp`) — all `/recovery-portal/*` routes |
| `recovery_report_worker.py` | Monthly report generator + emailer |
| `templates/recovery_portal_base.html` | Base template for advertiser portal (extends `public_page_base.html`) |
| `templates/recovery_portal_login.html` | Login page |
| `templates/recovery_portal_overview.html` | Overview tab |
| `templates/recovery_portal_listing.html` | Edit listing tab (+ AI copy button) |
| `templates/recovery_portal_inquiries.html` | Inbox tab |
| `templates/recovery_portal_demand.html` | Demand signals tab |
| `templates/recovery_portal_reports.html` | Reports tab |
| `templates/recovery_portal_account.html` | Account settings tab |
| `templates/admin_recovery_inquiries.html` | Admin inquiries view |
| `templates/admin_recovery_demand.html` | Admin demand statewide view |

### Modified Files
| File | Change |
|------|--------|
| `init_db.py` | Add 4 new tables + 2 ALTER TABLE columns in `ensure_recovery_ad_schema()` |
| `blueprints/recovery_ads.py` | Add inquiry submission route, crisis CTA helper |
| `blueprints/admin/recovery_ads.py` | Add badge, account, CTA toggle, inquiries, demand routes |
| `blueprints/admin/__init__.py` | No change needed (admin routes already registered) |
| `app.py` | Register `recovery_portal_bp`, add `inject_recovery_cta` context processor |
| `templates/recovery_centers_directory.html` | Add inquiry form to Silver/Gold listing cards |
| `templates/post_detail.html` | Add `recovery_cta` block |

---

## 7. Security Notes

- Portal session is separate from admin and public user sessions — no privilege escalation possible
- Passwords hashed with `bcrypt` (via `werkzeug.security`)
- Inquiry form rate-limited to 3/hour per IP (using `app_settings` counter + timestamp, no extra dependencies)
- Claude API calls rate-limited to 5/order/day
- All portal routes check that the logged-in account's `order_id` matches the requested resource — no horizontal privilege escalation
- Crisis CTA requires explicit admin opt-in (`crisis_cta_enabled`) — centers can't self-activate

---

## 8. Out of Scope

- SMS notifications (inquiry alerts via text)
- Advertiser-to-family reply messaging
- Public review/rating system for centers
- Payment management (Stripe portal link could be added later)
