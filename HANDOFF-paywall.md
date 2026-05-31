# Handoff: Paywall / Sneak Preview Feature

**From:** blotter-ops
**To:** blotter-dev
**Date:** 2026-05-21
**Status:** Ready to implement

---

## Current State (All Public)

All crime data is currently 100% public with zero authentication required:

- 25,899 incidents accessible at `/incident/<id>`
- 3,314 jail bookings accessible publicly
- 1,120 current bookings
- 471 new incidents in last 24h
- 89 new bookings in last 24h

There is NO gating logic anywhere on incident detail, booking detail, or bulk data views.

---

## Existing Infrastructure (Do Not Rebuild)

Good news: much of the subscription plumbing already exists.

### Database Schema Already Has

- `public_users` table with:
  - `subscriber_plan` (default `'free'`)
  - `stripe_subscription_id`
  - `subscription_status`
  - `subscription_activated_at`
  - `subscription_canceled_at`
- `users` table with `membership` (default `'free'`)
- `watchdog_subscriptions` table with `tier`, `stripe_customer_id`, `stripe_subscription_id`, `status`
- `payments` table with `stripe_id`
- `payment_webhook_events` table (Stripe webhook log)
- Various indexes already in place

### Auth Already Wired

- Flask-Login is imported and active (`current_user` available globally)
- `User` model has `subscriber_plan` field
- Login/logout/session logic already works

### Stripe Already Configured

Env vars in `.env`:
- `MB_STRIPE_PUBLISHABLE_KEY`
- `MB_STRIPE_SECRET_KEY`
- `MB_STRIPE_WEBHOOK_SECRET`
- `MB_DONATION_MONTHLY_PRICE_ID` (for donations, not subscriptions)

Stripe checkout already works for bail bond ads and donations.

### What Is Missing

- **Zero gating logic** on any data route
- **No `/pricing` page** (returns 404)
- **No Stripe Price IDs** for subscription tiers
- **No preview limit enforcement** (e.g., "3 free views then pay")
- **No paywall UI** (blur, upgrade CTAs, etc.)

---

## Bugs to Fix First

### 1. `/alerts` 500 Error (Blocks Free Signups)

**File:** `app.py` lines ~8893, 8947, 8961

**Problem:**
```python
from alert_dispatcher import subscribe_county_alert, subscribe_name_watch
```
This module does not exist at top level. The actual code is at `services/alerts/dispatcher.py`.

**Impact:**
- `/alerts` page crashes on load
- County alert signups broken
- Name watch signups broken

**Fix:** Change imports to:
```python
from services.alerts.dispatcher import subscribe_county_alert, subscribe_name_watch, cancel_alert_subscription, cancel_name_watch
```

---

## Feature Requirements

### 1. Preview / Teaser Limits

Confirmed product decisions:
- **Anonymous and logged-in free users:** 3 free detailed views per day, 5 per week (rolling windows; whichever limit is hit first triggers the paywall)
- After limit, show blurred/partial content with upgrade CTA
- Free tier still gets:
  - Headlines / list views (unlimited)
  - Basic search (unlimited)
  - Crime map pins and heatmap (no detail popups)
  - Free alerts signup (after /alerts bugfix)

Track free views in a new table or column. If user is logged in, track against `public_users.id`. If anonymous, track against session ID + IP hash (or just session cookie).

### 2. Subscription Tiers

Create Stripe Products + Prices for these tiers:

| Tier | Monthly | Annual | Access |
|------|---------|--------|--------|
| Scout (Free) | $0 | $0 | Headlines, basic search, 3 previews/day, 5/week, free alerts |
| Insider | $7.99/mo | $69.99/yr | Full incident details, full jail booking details, crime map detail popups, deeper search |
| Professional | $14.99/mo | $129.99/yr | Everything Insider gets + data exports, API access, priority alerts |

Add the resulting Stripe Price IDs to `.env`:
- `MB_INSIDER_MONTHLY_PRICE_ID`
- `MB_INSIDER_YEARLY_PRICE_ID`
- `MB_PRO_MONTHLY_PRICE_ID`
- `MB_PRO_YEARLY_PRICE_ID`

Also update the existing `public_users.subscriber_plan` enum/check to allow: `scout`, `insider`, `professional`.

### 3. Gating Logic

Add a decorator or helper like:
```python
def require_subscription(min_plan='insider'):
    ...
```

Plan hierarchy: scout < insider < professional

Apply gating to:
- `/incident/<int:record_id>` (full detail)
- Jail booking detail pages
- Crime map detail popups
- Any bulk data export or API endpoint

Keep fully free:
- Home page / recent headlines
- `/crime-map` (pins and heatmap only, no popups for free users)
- `/subscribe` (free alerts)
- `/pricing`

### 4. `/pricing` Page

Build a pricing page at `/pricing` showing tiers, feature comparison, and Stripe Checkout integration.

### 5. Webhook Handling

Stripe webhooks already have a handler. Ensure it updates:
- `public_users.subscription_status`
- `public_users.stripe_subscription_id`
- `public_users.subscriber_plan`

On `checkout.session.completed`, `invoice.paid`, `customer.subscription.deleted`.

---

## Data Notes

- **Database:** `/root/montanablotter/data/blotter.db` (~13 GB)
- **Backup:** `blotter.db.pre-paywall-20260521-045038.bak` (created by ops before handoff)
- **Page views tracked in separate DB:** `/root/montanablotter/data/page_views.db` (do not confuse with stale `page_views` table inside `blotter.db`)
- **Paid users currently:** 2 internal `users` with `membership != 'free'`, 0 paid `public_users`

---

## Acceptance Criteria

- [ ] `/alerts` loads without 500 (fix import path)
- [ ] `/pricing` exists and shows Scout / Insider / Professional tiers with feature comparison
- [ ] Stripe checkout creates subscription and updates `public_users.subscriber_plan`, `subscription_status`, `stripe_subscription_id`
- [ ] Anonymous users see preview limit enforced: 3/day, 5/week rolling windows
- [ ] Logged-in Scout users see same preview limit enforced
- [ ] Insider and Professional users see all content ungated
- [ ] Jail bookings gated identically to incidents
- [ ] Crime map shows pins/heatmap to all users; detail popups require Insider or higher
- [ ] Webhook correctly handles cancel / renew / upgrade / downgrade
- [ ] All existing free alert signups still work
- [ ] nginx config unchanged (ops will verify after deploy)

---

## Product Decisions (Confirmed by Owner)

1. **Preview limits:** 3 free detailed views per day, 5 per week (rolling windows, whichever hits first)
2. **Tier names & prices (recommended by ops):**
   - **Scout** (Free) -- $0 -- headlines, basic search, 3 previews/day, 5/week, free alerts
   - **Insider** -- $7.99/mo or $69.99/yr -- full incident details, full jail booking details, crime map detail popups, deeper search
   - **Professional** -- $14.99/mo or $129.99/yr -- everything Insider gets + data exports, API access, priority alerts
3. **Jail bookings:** Gated identically to incidents
4. **Crime map:** Partially gated -- free users see pins/heatmap but NOT detail popups; paid users see full detail popups
5. **Annual discount:** ~27% off (Insider $69.99/yr vs $95.88/yr monthly; Pro $129.99/yr vs $179.88/yr monthly)

---

**Ops Note:** I will reload nginx and verify SSL after any new routes are deployed. Ping me when the PR is ready for staging.
