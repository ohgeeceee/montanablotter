# Recovery Center Advertising Panel — Design Spec
**Date:** 2026-03-28
**Status:** Approved

## Overview

A self-serve advertising system for Montana recovery centers to purchase directory listings on MontanaBlotter. Modeled after the existing bail bonds advertising system (fork, not refactor). Recovery centers get a dedicated `/recovery-centers` directory page — no ads injected into arrest or court content.

---

## Packages & Pricing

Three flat tiers with monthly and annual billing (annual ~15% discount):

| Package | Monthly | Annual | Features |
|---|---|---|---|
| **Bronze** | $99/mo | $1,010/yr | Name, phone, website link — basic directory listing |
| **Silver** | $199/mo | $2,029/yr | + Logo, tagline, 200-char description, services list |
| **Gold** | $399/mo | $4,069/yr | + Featured top placement, photo, 500-char description, monthly impression stats |

Gold listings appear first on the directory page, Silver in the middle, Bronze at the bottom.

---

## Directory Page (`/recovery-centers`)

Public-facing page, SEO-targeted for Montana recovery searches:

- **Hero** — brief intro copy + "Advertise Your Center" CTA
- **Gold section** — featured cards: logo, photo, full description, services tags, phone + website CTA buttons
- **Silver section** — standard cards: logo, tagline, services, contact
- **Bronze section** — compact list rows: name, phone, website
- **Empty state** — "Be the first to list your center" CTA if no active advertisers
- Meta description generated following existing blotter_auditor SEO pattern (150–160 chars, Great Falls / Montana targeted)

---

## Checkout & Payment Flow

1. Advertiser visits `/advertise/recovery` — package cards with monthly/annual toggle
2. Selects package → Stripe Checkout session created → redirect to Stripe-hosted payment
3. Success → `/advertise/recovery/checkout/success?session_id=...` — confirmation + magic-link token emailed
4. Cancel → `/advertise/recovery/checkout/cancel` — returns to package page
5. Stripe webhook at `/webhooks/recovery-ads`:
   - `checkout.session.completed` → activates listing, sets status=active
   - `customer.subscription.deleted` → deactivates listing, sets status=cancelled
   - `invoice.payment_failed` → marks listing inactive pending payment

---

## Advertiser Control Panel (`/recovery-control-panel?token=...`)

Token-based access (magic link, no password account). Advertiser can:

- Edit listing: tagline, description, services list, city/county, website URL
- Upload logo (Silver+) and hero photo (Gold only)
- View monthly impression and click stats
- See subscription status, next billing date, link to Stripe customer portal (payment/cancel)

Token is a UUID generated at signup, stored in `recovery_ad_orders.token`.

---

## Admin Panel (`/admin/recovery-ads`)

New admin section at `/admin/recovery-ads`:

- **Orders list** — all signups with status badge, package tier, center name, email, signup date, MRR contribution
- **Activate/deactivate toggle** — manual suspension override (listings auto-activate on Stripe payment; admin can deactivate problem listings)
- **CMS** — edit any listing's content on behalf of advertiser (logo, description, services)
- **Stats overview** — total active advertisers, MRR by tier, recent signups

---

## Data Model

Two new tables added via `init_db.migrate()`:

### `recovery_ad_orders`
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
center_name TEXT NOT NULL,
contact_name TEXT,
email TEXT NOT NULL,
phone TEXT,
website TEXT,
package_id TEXT NOT NULL,          -- 'bronze' | 'silver' | 'gold'
billing_cycle TEXT NOT NULL,       -- 'monthly' | 'annual'
stripe_customer_id TEXT,
stripe_subscription_id TEXT,
stripe_session_id TEXT,
status TEXT DEFAULT 'pending',     -- 'pending' | 'active' | 'cancelled' | 'inactive'
token TEXT UNIQUE NOT NULL,
created_at TEXT DEFAULT (datetime('now')),
activated_at TEXT,
cancelled_at TEXT
```

### `recovery_ad_listings`
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
order_id INTEGER UNIQUE REFERENCES recovery_ad_orders(id),
tagline TEXT,
description TEXT,
services TEXT,                     -- JSON array of strings
city TEXT,
county TEXT,
logo_path TEXT,
photo_path TEXT,                   -- Gold only
impressions INTEGER DEFAULT 0,
clicks INTEGER DEFAULT 0,
updated_at TEXT DEFAULT (datetime('now'))
```

Impression tracking: incremented server-side on each `/recovery-centers` page load per active listing. Click tracking: incremented on outbound link clicks via a redirect route `/recovery-centers/click/<order_id>`.

---

## File Structure

New files to create:

```
app_recovery_ads.py                  # All route handlers and helpers (registered as Blueprint)
templates/
  advertise_recovery.html            # Public package/pricing page
  advertise_recovery_checkout.html   # (if needed pre-Stripe redirect)
  advertise_recovery_checkout_success.html
  advertise_recovery_checkout_cancel.html
  advertise_recovery_control_panel.html
  recovery_centers_directory.html    # Public /recovery-centers directory
  admin_recovery_ads.html            # Admin orders + stats
  admin_recovery_ads_cms.html        # Admin listing CMS editor
```

Blueprint registered in `app.py` via `app.register_blueprint(recovery_ads_bp)`.

---

## Architecture Notes

- **Fork, not refactor** — bail bonds system untouched, zero regression risk
- **Blueprint pattern** — `app_recovery_ads.py` registers its own routes, keeping `app.py` clean
- Stripe webhook secret stored in `config.py` as `RECOVERY_ADS_STRIPE_WEBHOOK_SECRET`
- Stripe Price IDs for all 6 SKUs (3 packages × 2 billing cycles) stored in `config.py`
- Logo/photo uploads stored in `static/recovery_logos/` and `static/recovery_photos/`
- File upload size limit: 2MB, accepted types: jpg/png/webp
