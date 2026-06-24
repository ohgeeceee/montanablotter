# Monetization codemap

Covers subscription gating/paywall, bail ads, recovery-center ads, donations, and Stripe webhook handling.

---

## Module map

| Path | Purpose | Key entry points |
|------|---------|-----------------|
| `services/monetization/paywall.py` | Subscription plan hierarchy, preview-limit tracking, and gating decorators. | `PLAN_HIERARCHY` (~L25), `PREVIEW_LIMITS`, `connect_page_views()`, `subscription_gate()`, `require_subscription()` |
| `services/monetization/bondsman.py` | Bondsman "command center": subscription state, watchlists, alerts, cases, check-ins. | Schema columns (~L24-80), API routes (`/api/bondsman/*`; ~L474-501), `_subscriber_required()`, watchlist/case helpers |
| `services/monetization/bondsman_watch.py` | CLI wrapper that runs the watchlist match cycle. | `run_watchlist_match_cycle()` (20-line wrapper) |
| `services/monetization/recovery_renewals.py` | Deterministic renewal-date projection for recovery ad subscriptions. | `project_next_renewal()` (L65), `days_until_renewal()` (L101), `find_upcoming_renewals()` (L118) |
| `services/monetization/ad_metrics.py` | Unified metrics bridge across recovery ads (counters) and bail ads (event log). | `get_recovery_ad_metrics()` (L43), `get_bail_ad_metrics()` (L122), `get_advertiser_metrics()` (L183), `format_ctr()` (L287) |
| `blueprints/payments.py` | Stripe webhook, bail ad checkout, recovery ad checkout, donation/supporter checkouts, warrant/subscription checkouts. | `stripe_webhook()` (L357), advertise routes (L444+), checkout routes |
| `blueprints/recovery_ads.py` | Public recovery directory, checkout, Stripe handler, tokenized advertiser control panel. | `apply_stripe_recovery_ad_event()` (L144), `/recovery-centers` (L248), `/advertise/recovery/*`, `/recovery-control-panel/<token>` |
| `blueprints/admin/bail_ads.py` | Bail ad admin: orders, agencies, creatives, slots, leads, simulator, CSV export. | `/admin/bail-ads*` (14 routes; L23, 480, 492, 619, 669, 865, 890, 947, 979, 1030, 1075, 1098, 1157, 1200) |
| `blueprints/admin/recovery_ads.py` | Recovery ad admin: status, CMS listing editor. | `/admin/recovery-ads` (L17), `/admin/recovery-ads/order/<id>/status` (L83), `/admin/recovery-ads/cms/<id>` (L104) |
| `blueprints/admin/donations.py` | Donation reconciliation dashboard and export. | `/admin/donations` (L23), `/admin/donations/preflight`, `/admin/donations/reconcile`, `/admin/donations/export.csv` |
| `blueprints/api.py` | JS telemetry endpoints for bail ads, simulator, and donation funnels. | `/api/bail-ads/event` (L908), `/api/bail-ads/simulator-event` (L959), `/api/donate-event` (L887) |
| `app.py` | Core helpers for bail ad placement, simulator, subscription state, Stripe event application. | `_apply_stripe_event()` (~L4200), `_apply_stripe_bail_ad_event()` (~L3660), `_bail_ad_public_placements()`, `_active_bail_ad_listings()`, `_bail_ad_control_panel_context()` |
| `docs/SUPPORTER_TIER.md` | Supporter/donation tier design and ad-unlock grants. | reference |

---

## Data model

### Primary SQLite DB

- `public_users` — subscription fields added/ensured by `services/monetization/bondsman.py` (~L24-80):
  - `is_subscribed`, `subscriber_plan`, `stripe_subscription_id`, `subscription_status`, `subscription_activated_at`, `subscription_canceled_at`.
- Subscription/paywall:
  - No standalone subscription table; state lives on `public_users` plus Stripe as source of truth.
  - `payment_webhook_events` — raw Stripe webhook payload, event ID, type, processed flag, error.
  - `ad_unlock_grants` — one-time ad-unlock grants tied to supporter donations.
- Donations:
  - `donations` — one row per Stripe session/payment; tracked by `provider_session_id`.
  - `donation_events` — frontend telemetry rows (`donate_view`, `checkout_start`, `checkout_success`, etc.).
- Bail ads:
  - `bail_ad_orders` — advertiser/order header.
  - `bail_ad_slots` — purchased slot/booking rows.
  - `bail_ad_creatives` — creative asset/status records.
  - `bail_ad_events` — impression/click/lead/call/text events.
  - `bail_ad_simulator_events` — simulator UI analytics.
- Recovery ads:
  - `recovery_ad_orders` — order/subscription header.
  - `recovery_ad_listings` — directory listing content and `impressions`/`clicks` counters.
- Bondsman:
  - `bondsman_watchlists`, `bondsman_alerts`, `bondsman_cases` — watchlist matches and case tracking.
  - `jail_bookings.date_of_birth` — used by bondsman matching.

### Paywall tracking DB (`page_views.db`)

A separate SQLite file managed by `services/monetization/paywall.py`:

- `preview_views` — one row per gated page preview attempt.
  - `viewer_type` (`anonymous`/`public_user`/`admin`), `viewer_id`, `page_type`, timestamp.
  - Drives the `3/day` and `5/week` preview limits.

---

## Data flow

### Subscription / paywall

1. User requests a gated page (warrant records, full docket, disposition details, etc.).
2. `subscription_gate(min_plan='insider')` or `require_subscription(...)` resolves the viewer to `(viewer_type, viewer_id)`.
3. `PLAN_HIERARCHY` (~L25-37) compares the viewer's `subscriber_plan` rank to the page minimum (`scout`=0, `warrant_access`/`insider`=1, `professional`=2).
4. If no active plan and preview budget remains (`PREVIEW_LIMITS: 3/day, 5/week`), a row is inserted into `page_views.db.preview_views` and access is granted.
5. Otherwise the user is redirected to checkout (or a JSON paywall response is returned for API calls).
6. Checkout creates a Stripe Session/Subscription with metadata `flow=subscription` or `flow=warrant_access`.
7. Webhook `/webhooks/stripe` calls `app._apply_stripe_event()` → `_apply_subscription_stripe_event()` / `_apply_warrant_access_stripe_event()`, which set `public_users.is_subscribed`, `subscriber_plan`, `stripe_subscription_id`, `subscription_status`, etc.
8. Lifecycle events (`customer.subscription.updated`/`deleted`) keep `public_users` in sync; cancellation clears the subscription columns and records `subscription_canceled_at`.

### Bail ads

1. An advertiser picks a package and counties at the public checkout page (`/advertise/bail-bonds*`).
2. The backend creates a Stripe Checkout Session with `metadata.flow='bail_ad'` plus `package_id`, `billing_cycle`, `county_targets`, `add_on_ids`, simulator fields, etc.
3. On success, Stripe webhook calls `app._apply_stripe_bail_ad_event()` (~L3660), which upserts `bail_ad_orders` (status `active`/`canceled`/`payment_failed`) and calls `_upsert_bail_ad_slot_assignments()` to allocate slots.
4. Public pages use `app._active_bail_ad_listings()` and `app._bail_ad_public_placements()` to pick banner/sidebar/county sponsor ads.
5. Frontend JS posts to `/api/bail-ads/event`, writing `impression`, `click`, `lead`, `call`, `text` rows into `bail_ad_events`.
6. The advertiser uses the tokenized `/bail-control-panel/<onboarding_token>` to view metrics and upload creative assets.
7. Admins manage orders, agencies, creatives, slots, and leads at `/admin/bail-ads`.

### Recovery ads

1. `/advertise/recovery` shows Bronze/Silver/Gold packages defined in `blueprints/recovery_ads.py::_PACKAGES` (L46+).
2. `/advertise/recovery/checkout` creates a Stripe Session with `metadata.flow='recovery_ad'`.
3. Webhook calls `apply_stripe_recovery_ad_event()` (L144), which upserts `recovery_ad_orders` and inserts a matching `recovery_ad_listings` row on activation.
4. `/recovery-centers` joins active orders to listings, increments `impressions` for every page view and sorts Gold > Silver > Bronze.
5. `/recovery-centers/click/<order_id>` increments `clicks` and redirects to the advertiser's website.
6. `/recovery-control-panel/<token>` lets the center edit listing content.
7. Admins edit CMS content and change order status at `/admin/recovery-ads`.

### Donations / supporter tier

1. Donation pages create Stripe Sessions with `tier=supporter` or donor `source` metadata.
2. Webhook `_apply_stripe_event()` inserts/updates the `donations` row and `donation_events`.
3. `_apply_supporter_stripe_event()` may grant advertising unlocks per `docs/SUPPORTER_TIER.md`.
4. Frontend conversion events are also captured via `/api/donate-event`.
5. Admins reconcile and export at `/admin/donations`.

### Bondsman command center

1. A public user signs up for the bondsman subscription (Stripe flow sets `flow=subscription`/`source` matching bondsman).
2. Webhook sets `public_users.subscriber_plan='bondsman_pro'` and `is_subscribed=1`.
3. The user calls `/api/bondsman/bootstrap` to create initial account data.
4. Watchlists are stored in `bondsman_watchlists`.
5. Running `services/monetization/bondsman_watch.py` (or a future cron) calls `run_watchlist_match_cycle()`, comparing watchlists against `jail_bookings` and creating `bondsman_alerts` and `bondsman_cases`.
6. Check-ins and case updates are persisted through the `/api/bondsman/*` routes and protected by `_subscriber_required`.

---

## Cron schedule

There is **no cron entry** for subscription reconciliation, bail ads, recovery ads, bondsman watchlist matching, or donations. The only related schedule in `crontab.txt` is:

```
7-59/15 * * * * .../check_ads_health.py
```

which monitors ad-script metadata, not orders/payments.

If the bondsman watchlist matching or recovery renewal reminders need to run unattended, add cron entries that invoke:

- `services/monetization/bondsman_watch.py`
- a small wrapper around `services/monetization/recovery_renewals.py::find_upcoming_renewals()`

---

## Admin UIs

| URL base | File | What it does |
|----------|------|--------------|
| `/admin/bail-ads` | `blueprints/admin/bail_ads.py` | Orders, agencies, creatives, slots, lead status, simulator, SEO content, attribution export. |
| `/admin/recovery-ads` | `blueprints/admin/recovery_ads.py` | Recovery order status and CMS listing editor. |
| `/admin/donations` | `blueprints/admin/donations.py` | Donation list, preflight health, reconcile action, CSV export. |
| (subscription state) | `app.py` / `payments.py` | Public-user subscription flags updated via Stripe webhooks. |

---

## Gotchas / important notes

- **Two databases for payments**: gated-page previews live in `page_views.db` (created by `connect_page_views()`), while subscriptions/ads/donations live in the main app DB. Backups must cover both files.
- **Stripe webhook is single endpoint**: `blueprints/payments.py::stripe_webhook()` (L357) received all Stripe events, stores them in `payment_webhook_events`, then dispatches to `_apply_stripe_bail_ad_event()`, `apply_stripe_recovery_ad_event()`, and `_apply_stripe_event()`. Duplicate event IDs are handled by the unique index on `event_id`.
- **Metadata routing**: webhooks route by `metadata.flow` (`bail_ad`, `recovery_ad`, `subscription`, `warrant_access`, `disposition_api`) or `tier='supporter'`. Payment Link warrant checkouts may lack `flow` metadata; `_apply_stripe_event()` falls back to `client_reference_id` + subscription detection.
- **Plan ranks**: `scout=0 < warrant_access/insider=1 < professional=2`. The `require_subscription` and `subscription_gate` decorators check these ranks.
- **Preview limits are per viewer**: counted separately for anonymous sessions, public users, and admins.
- **Bail ad state is wide**: orders carry many runtime-added columns (`simulator_*`, `add_on_ids`, `county_targets`, etc.); schema migrations in `app.py` add missing columns at startup via `_ensure_bail_ad_simulator_order_columns()`.
- **Recovery renewals are projected**: `recovery_ad_orders` has no `renews_on` column. Use `recovery_renewals.py` to compute the next date from `activated_at` + `billing_cycle` (`monthly`=30, `annual`=365).
- **Metrics model mismatch**: `ad_metrics.py` bridges the two ad products — recovery uses counter columns, bail uses an event-log. `get_advertiser_metrics()` matches by business name/email case-insensitively.
- **Subscription lifecycle edge cases**: when a Stripe subscription is canceled, `public_users` is cleared and `subscription_canceled_at` is set, but the bondman dashboard does not automatically close `bondsman_cases`. Build a cleanup job if required.
- **No secrets in source**: all Stripe keys, webhook secrets, and ad credentials are read from `config.py`/env; never hardcode or log them.
