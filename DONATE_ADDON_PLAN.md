# Montana Blotter Donate Add-On Blueprint

## 1. Executive Summary
This plan adds a professional donations capability to Montana Blotter with:

- `one-time` and `monthly` donations
- secure checkout (no card data handled by your server)
- webhook-verified payment confirmation
- admin reporting for finance visibility
- donor analytics and conversion tracking

Recommended implementation path:

1. `Stripe Checkout` as the primary payment path (fastest, lowest engineering risk, low PCI burden).
2. Optional `PayPal Donate` button as secondary method if you want PayPal-native donors.

## 2. Product Scope (v1)
### Public-facing
- New `/donate` landing page with:
- Mission/value statement
- Suggested amounts (`$5`, `$15`, `$25`, `$50`, custom)
- One-time vs monthly toggle
- Secure checkout CTAs
- Trust copy: secure payments, privacy, cancellation terms
- Confirmation pages:
- `/donate/success`
- `/donate/cancel`

### Admin-facing
- New `/admin/donations` page:
- Total donated (MTD, YTD, all-time)
- One-time vs recurring split
- Conversion funnel (`donate_view -> checkout_start -> success`)
- Recent donations table (no sensitive card data)
- Failed/abandoned webhook events for troubleshooting

### Analytics
- Add `donate_events` tracking similar to `subscribe_events`:
- `cta_click`
- `checkout_start`
- `checkout_success`
- `checkout_cancel`

## 3. Technical Architecture
### Payment model
- Public page calls backend `POST /api/donate/create-checkout-session`.
- Backend creates provider checkout session with server-side amount validation.
- User is redirected to hosted checkout.
- Provider sends webhook event to `/webhooks/stripe`.
- Server verifies signature, writes donation record, returns `2xx`.
- Success page reads session status from backend for confirmation UI.

### Why hosted checkout first
- Keeps card entry off your app.
- Faster implementation and safer compliance posture.
- Easy to support recurring donations.

## 4. Data Model
Add these tables in `init_db.py` migration:

### `donations`
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `provider TEXT NOT NULL` (`stripe`, optional `paypal`)
- `mode TEXT NOT NULL` (`one_time`, `monthly`)
- `status TEXT NOT NULL` (`pending`, `succeeded`, `failed`, `canceled`, `refunded`)
- `amount_cents INTEGER NOT NULL`
- `currency TEXT NOT NULL DEFAULT 'usd'`
- `email_hash TEXT`
- `donor_name TEXT`
- `message TEXT`
- `source TEXT`
- `provider_session_id TEXT UNIQUE`
- `provider_payment_intent_id TEXT UNIQUE`
- `provider_subscription_id TEXT`
- `created_at TEXT DEFAULT (datetime('now'))`
- `updated_at TEXT DEFAULT (datetime('now'))`

Indexes:
- `idx_donations_created`
- `idx_donations_status`
- `idx_donations_mode`

### `donation_events`
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `event_type TEXT NOT NULL`
- `source TEXT`
- `page_path TEXT`
- `ip_hash TEXT`
- `referrer TEXT`
- `amount_cents INTEGER`
- `created_at TEXT DEFAULT (datetime('now'))`

Indexes:
- `idx_donation_events_created`
- `idx_donation_events_type`
- `idx_donation_events_source`

### `payment_webhook_events`
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `provider TEXT NOT NULL`
- `event_id TEXT NOT NULL UNIQUE`
- `event_type TEXT NOT NULL`
- `payload_json TEXT NOT NULL`
- `processed INTEGER NOT NULL DEFAULT 0`
- `error TEXT`
- `created_at TEXT DEFAULT (datetime('now'))`
- `processed_at TEXT`

Indexes:
- `idx_webhook_events_provider_created`
- `idx_webhook_events_processed`

## 5. File-Level Implementation Map
### Backend
- `app.py`
- Add routes:
  - `GET /donate`
  - `POST /api/donate/create-checkout-session`
  - `POST /api/donate-event`
  - `POST /webhooks/stripe`
  - `GET /donate/success`
  - `GET /donate/cancel`
  - `GET /admin/donations` (`@login_required`)
- Add helper functions:
  - `_record_donate_event(...)`
  - `_allowed_donation_amounts()` and server-side validation
  - `_upsert_donation_from_webhook(...)` (idempotent)
- Add `/donate` to `public_primary_nav_items` or footer items and static sitemap URLs.

- `init_db.py`
- Add `CREATE TABLE IF NOT EXISTS` blocks for new donation tables and indexes.

- `config.py`
- Add env config:
  - `MB_DONATIONS_ENABLED`
  - `MB_STRIPE_SECRET_KEY`
  - `MB_STRIPE_PUBLISHABLE_KEY`
  - `MB_STRIPE_WEBHOOK_SECRET`
  - `MB_DONATION_MIN_CENTS` (default `500`)
  - `MB_DONATION_SUGGESTED_AMOUNTS` (csv)
  - `MB_DONATION_MONTHLY_PRICE_ID` (if using fixed recurring price)

- `requirements.txt`
- Add Stripe SDK package (`stripe`).

### Frontend templates
- New templates:
  - `templates/donate.html`
  - `templates/donate_success.html`
  - `templates/donate_cancel.html`
  - `templates/admin_donations.html`
- Update:
  - `templates/public_page_base.html` (top nav + mobile nav + footer link)
  - `templates/includes/homepage_masthead.html` (primary CTA placement)
  - `templates/index.html` (donate CTA block near subscribe conversion)
  - `templates/base.html` (admin nav: add Donations)

## 6. UX Standards (Professional)
### Donate page structure
1. Hero: mission + impact statement.
2. Amount selection tiles + custom amount input.
3. Frequency toggle (`One-time` / `Monthly`).
4. Secure payment button(s).
5. Microcopy:
- "Secure checkout powered by Stripe."
- "Cancel monthly support any time."
- "Donations are not tax-deductible unless explicitly stated."

### Visual treatment
- Match existing `Montana Blotter` design language (type, spacing, monochrome + amber).
- Keep CTA prominent but non-intrusive on content pages.
- Mobile-first: large tap targets and single-column amount selector.

## 7. Security, Compliance, and Risk Controls
### Required controls
- Verify webhook signatures using provider SDK.
- Use raw request body for signature verification (do not parse before verify).
- Enforce server-side amount validation.
- Never trust client-submitted price/amount metadata for settlement.
- Idempotency:
- unique constraint on webhook `event_id`
- unique provider session/payment IDs
- Log and alert on webhook failures.
- Keep cardholder data off your app to minimize PCI scope.

### Copy and legal hygiene
- Do not claim tax deductibility unless you have valid nonprofit status and documentation.
- Update Privacy Policy with donation processing details.
- Update Terms of Use with refund/cancellation policy.

## 8. Observability and Reporting
### Admin KPIs
- Gross donated MTD / YTD / all-time
- Number of successful donations
- Average donation amount
- Monthly retention count (for recurring)
- Drop-off rate between donate page and checkout completion

### Operational alerts
- Webhook failures > threshold/day
- High ratio of checkout starts to success
- Provider API errors

## 9. Rollout Plan (Phased)
### Phase 0: Prep (0.5 day)
- Add config vars and feature flag `MB_DONATIONS_ENABLED`.
- Add DB migrations and admin nav placeholder.

### Phase 1: Core launch (1-2 days)
- Build `/donate` + Stripe Checkout session creation.
- Implement webhook processing and donation persistence.
- Build success/cancel pages.
- Add header/footer CTAs.

### Phase 2: Admin & analytics (1 day)
- Build `/admin/donations` dashboard.
- Add donation event tracking and funnel metrics.

### Phase 3: Trust & optimization (0.5-1 day)
- A/B test CTA placement and copy.
- Add optional PayPal Donate button.
- Add donor impact language and social proof snippets.

### Phase 4: Launch operations (0.5 day)
- Add launch readiness snapshot in `/admin/donations`.
- Add machine-readable preflight endpoint for operations checks.
- Add runbook + CLI preflight validation before enabling feature flag.

### Phase 5: Revenue operations (0.5 day)
- Add admin CSV export for accounting reconciliation.
- Add admin webhook reprocessing control for failed/unprocessed events.
- Surface operations actions directly in the donations dashboard.

## 10. Acceptance Criteria
- A user can donate one-time successfully end-to-end.
- A user can start monthly donation successfully.
- Webhooks are signature-verified and idempotent.
- Donation appears in admin dashboard within 60 seconds.
- Failed webhook events are visible in admin logs/reporting.
- `/donate` appears in desktop + mobile navigation.
- Privacy/Terms links are visible on donate flow.

## 11. Recommended Launch Configuration
- Start with one-time + monthly.
- Suggested amounts: `5, 15, 25, 50`.
- Minimum donation: `$5`.
- Feature flag enabled only after webhook endpoint verified in live mode.

## 12. Source References (for implementation decisions)
- Stripe Checkout overview: https://docs.stripe.com/payments/checkout
- Stripe Checkout one-time vs subscription modes: https://docs.stripe.com/payments/checkout/how-checkout-works
- Stripe recurring donations pattern: https://docs.stripe.com/recurring-payments
- Stripe webhook signature verification: https://docs.stripe.com/webhooks/signatures
- PayPal Donate SDK overview: https://developer.paypal.com/sdk/donate/
- PayPal JS SDK reference: https://developer.paypal.com/sdk/js/
