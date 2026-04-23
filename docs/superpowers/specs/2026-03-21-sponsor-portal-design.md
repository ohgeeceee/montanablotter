# Sponsor Portal Design
**Date:** 2026-03-21
**Project:** MontanaBlotter.com
**Status:** Approved by user — spec under review

---

## Overview

Build a self-serve sponsor portal for bail bond companies (and other local advertisers) who purchase ad placements in the Weekly Safety Snapshot. Sponsors sign up, pay via Stripe, manage their business listing, and view real-time stats — all without admin involvement after payment.

---

## Goals

- Give paying sponsors a professional account experience with real stats
- Automate the full signup → payment → activation → email flow
- Track ad impressions and clicks per sponsor per week
- Allow sponsors to self-manage their listing (name, tagline, logo, counties, billing)
- Surface sponsor management to admin without adding manual work

---

## Architecture

### New Files

| File | Purpose |
|---|---|
| `sponsor_portal.py` | Flask blueprint — all `/sponsors/*` routes |
| `sponsor_mailer.py` | Welcome, weekly stats, and payment-failed emails |

### Modified Files

| File | Change |
|---|---|
| `app.py` | Register sponsor blueprint; extend existing `/webhooks/stripe` handler to branch on sponsor events |
| `weekly_snapshot.py` | Query active sponsor from DB per county instead of `--sponsor` CLI flag. The `--sponsor` CLI flag is **kept** as a manual override (e.g. dry-run testing) — if provided it takes precedence over the DB lookup. The existing crontab entry (`--county Cascade`, no `--sponsor`) requires no change. |
| `init_db.py` | Migration adds `sponsors`, `sponsor_impressions`, `sponsor_clicks` tables |
| `crontab.txt` | Add Monday 7:45am weekly stats mailer job |
| `script_watchdog.py` | Add `sponsor_mailer.log` to monitored log list |
| `config.py` | Add `STRIPE_SPONSOR_PRICE_ID` |

### No New Webhook File

The existing `/webhooks/stripe` handler in `app.py` (around line 8029) already processes Stripe lifecycle events and writes to `payment_webhook_events` for idempotency. The sponsor-specific logic will be added as a branch inside `_apply_stripe_bail_ad_event()` or a new sibling function `_apply_stripe_sponsor_event()` called from the same handler. This avoids duplicate event delivery and reuses the existing idempotency check.

---

## Config Additions

Add to `config.py`:
```python
STRIPE_SPONSOR_PRICE_ID = "price_..."   # monthly subscription product price ID
```

Existing keys already in `config.py`: `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`.

---

## Data Model

### `sponsors`
```sql
CREATE TABLE sponsors (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  email                   TEXT NOT NULL UNIQUE,
  password_hash           TEXT NOT NULL,
  business_name           TEXT NOT NULL,
  phone                   TEXT,
  website                 TEXT,
  tagline                 TEXT,                    -- one-sentence pitch shown in snapshots
  logo_path               TEXT,                    -- relative path to uploaded file
  counties                TEXT DEFAULT '[]',       -- JSON array e.g. ["Cascade","Yellowstone"]
  plan_status             TEXT DEFAULT 'pending',  -- pending|active|paused|cancelled
  stripe_customer_id      TEXT,
  stripe_subscription_id  TEXT,
  login_token             TEXT,                    -- for password reset / magic links
  login_token_expires_at  TEXT,                    -- ISO datetime; token invalid after this
  created_at              TEXT DEFAULT (datetime('now')),
  updated_at              TEXT DEFAULT (datetime('now'))  -- no trigger; every UPDATE statement must include updated_at = datetime('now')
);
```

### `sponsor_impressions`
```sql
CREATE TABLE sponsor_impressions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  sponsor_id    INTEGER NOT NULL REFERENCES sponsors(id) ON DELETE CASCADE,
  snapshot_slug TEXT NOT NULL,   -- blog_posts.slug of the snapshot
  county        TEXT NOT NULL,
  appeared_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_sponsor_impressions_sponsor_date ON sponsor_impressions(sponsor_id, appeared_at);
```

### `sponsor_clicks`
```sql
CREATE TABLE sponsor_clicks (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  sponsor_id     INTEGER NOT NULL REFERENCES sponsors(id) ON DELETE CASCADE,
  snapshot_slug  TEXT,           -- slug of snapshot that generated the click (if known)
  county         TEXT,           -- county of the snapshot that generated the click
  referrer_url   TEXT,
  clicked_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX idx_sponsor_clicks_sponsor_date ON sponsor_clicks(sponsor_id, clicked_at);
```

---

## Routes

### Public
| Method | Path | Description |
|---|---|---|
| GET | `/sponsors/login` | Login form |
| POST | `/sponsors/login` | Authenticate sponsor |
| GET | `/sponsors/logout` | Clear session |
| GET | `/sponsors/register` | Signup form (name, email, county selection) |
| POST | `/sponsors/register` | Create pending account → redirect to Stripe Checkout |
| GET | `/sponsors/welcome` | Post-payment landing page |

### Sponsor (login required)
| Method | Path | Description |
|---|---|---|
| GET | `/sponsors/dashboard` | Stats: impressions this week, clicks this week, CTR, 8-week bar chart (impressions + clicks per week), list of snapshots featured in |
| GET | `/sponsors/settings` | Edit business name, phone, website, tagline, counties, logo |
| POST | `/sponsors/settings` | Save settings changes |
| GET | `/sponsors/billing` | Redirect to Stripe Customer Portal |
| GET | `/sponsors/out/<id>` | Click tracker → log to `sponsor_clicks` → redirect to sponsor website |

### Admin (existing `/admin`, login required)
| Method | Path | Description |
|---|---|---|
| GET | `/admin/sponsors` | List all sponsors, plan status, impression/click totals |
| POST | `/admin/sponsors/<id>/pause` | Override plan status to paused |
| POST | `/admin/sponsors/<id>/approve` | Manually activate (comped accounts) |

### Stripe (extended, not new)
| Method | Path | Description |
|---|---|---|
| POST | `/webhooks/stripe` | Existing handler — extended to handle sponsor events |

**Stripe webhook events handled (sponsor branch):**
- `checkout.session.completed` → set `plan_status='active'`, send welcome email
- `customer.subscription.updated` → sync status
- `customer.subscription.deleted` → set `plan_status='cancelled'`
- `invoice.payment_failed` → set `plan_status='paused'`, send payment-failed email

All events checked against `payment_webhook_events` (existing idempotency table) before processing.

---

## CSRF Protection

The `sponsor_portal` blueprint registers a `before_request` hook that enforces a CSRF token on all `POST` requests, using the same `_csrf_token` session mechanism already in use on the admin blueprint. The token is injected into every sponsor form via a template helper. This mirrors the existing `enforce_admin_csrf()` pattern at app.py line 901.

---

## Email Flows

All sponsor emails use the existing Gmail SMTP credentials (`config.SMTP_USER`, `config.SMTP_PASSWORD`, `config.SMTP_SERVER`, `config.SMTP_PORT`), same as `morning_briefing.py`. Sender address is `config.SMTP_USER`.

### 1. Welcome Email
- **Trigger:** Stripe `checkout.session.completed`
- **Subject:** `Your MontanaBlotter.com ad account is live`
- **Content:** Login URL, county targeting summary, link to dashboard

### 2. Weekly Stats Email
- **Trigger:** Cron — every Monday 7:45am
- **Subject:** `Your MontanaBlotter ad stats — week of [date]`
- **Content:** Impressions this week, clicks this week, CTR, snapshots they appeared in, dashboard link
- **Audience:** `plan_status='active'` sponsors only

### 3. Payment Failed Email
- **Trigger:** Stripe `invoice.payment_failed`
- **Subject:** `Action required: MontanaBlotter ad payment failed`
- **Content:** Stripe billing portal link to update card
- **Effect:** Sets `plan_status='paused'`

---

## Sponsor Ad Integration

`weekly_snapshot.py` is modified to:
1. Query `sponsors` for the **earliest-created** active sponsor targeting the snapshot's county using `json_each` for reliable JSON array matching: `SELECT s.* FROM sponsors s, json_each(s.counties) j WHERE s.plan_status='active' AND j.value = ? ORDER BY s.created_at ASC LIMIT 1`. Do NOT use `LIKE '%county%'` — it is ambiguous for multi-word county names and partial matches.
2. If found, pull `business_name`, `tagline`, `website` and render sponsor block
3. Log a row to `sponsor_impressions` with the snapshot slug + county
4. Render sponsor website link as `/sponsors/out/<id>` (click tracker), passing `snapshot_slug` and `county` as query params so they can be recorded on click

If no active sponsor targets the county, snapshot renders without a sponsor block.

**Multi-county tiebreak rule:** One active sponsor per county per snapshot, selected by earliest `created_at`. First-come, first-served. Future v2 can add priority/bidding.

---

## Security

- Sponsor session stored in Flask session cookie under a separate session key (`sponsor_id`) from the admin session (`user_id`)
- No privilege crossover between sponsor and admin roles
- Stripe webhook validates `Stripe-Signature` header (existing pattern reused)
- Logo uploads: jpg/png/gif/webp only; **2 MB max enforced at route level** (not via global `MAX_CONTENT_LENGTH`, which is set for PDF uploads)
- Passwords hashed with `werkzeug.security.generate_password_hash`
- Password reset tokens expire after 1 hour (`login_token_expires_at`)
- CSRF enforced on all sponsor POST routes via blueprint `before_request` hook

---

## Crontab Addition

```
# Weekly sponsor stats email — every Monday at 7:45am (after snapshot at 7:40am)
45 7 * * 1 /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name sponsor_stats_mailer --log /root/montanablotter/sponsor_mailer.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/sponsor_mailer.py --weekly-stats
```

`sponsor_mailer.log` must also be added to `script_watchdog.py`'s monitored log list with `max_age_hours=168` (7 days — matches the weekly cadence, same as `weekly_county_digest`) so the watchdog alerts if the Monday mailer stops running.

---

## Out of Scope (v1)

- Multi-tier pricing plans (flat monthly rate only)
- Sponsor-facing invoice history (Stripe Customer Portal covers this)
- Admin approval gate before payment (payment itself activates the account)
- Ad formats beyond the snapshot sponsor block (banners, sidebar, etc.)
- Multiple active sponsors per county (first-come-first-served for now)
