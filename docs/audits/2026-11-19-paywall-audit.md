# Paywall Audit — 2026-11-19

**Auditor:** Kimi (chat session, read-only via GitHub API)
**Spec:** `HANDOFF-paywall.md` (2026-05-21)
**Method:** static code review of `main` (routes, decorators, templates, tests). No runtime verification — items marked ✅ are code-verified, not load-tested.

## Verdict: the paywall shipped and largely works. Four owner decisions + one live bypass remain.

### Acceptance criteria

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | `/alerts` loads without 500 | ✅ Fixed | `app.py:10108` imports `services.alerts.dispatcher` inside the route; no top-level `alert_dispatcher` import remains |
| 2 | `/pricing` page with tiers + Stripe Checkout | ✅ Exists (caveats A–C below) | `templates/pricing.html`; route served by `blueprints/bail_bond_ads.py` (`/pricing`), checkout at `/checkout/<plus\|pro>?interval=` in the same blueprint; price IDs in `blueprints/payments.py` (created 2026-08-05); test: `tests/test_pricing_page.py` |
| 3 | Checkout creates subscription, updates `public_users` | ✅ Implemented | `payments.py` `/webhooks/stripe` → idempotent `payment_webhook_events` log → `app._apply_stripe_event` → `_apply_subscription_stripe_event` (`app.py:2644`) sets `subscriber_plan`, `subscription_status`, `stripe_subscription_id` |
| 4 | Anonymous preview limit 3/day, 5/week rolling | ✅ Matches spec exactly | `services/monetization/paywall.py` `PREVIEW_LIMITS = {day: 3, week: 5}`; `preview_views` table in `page_views.db`; anonymous identity = session id + IP hash |
| 5 | Logged-in free users: same limit | ✅ | `viewer_type='public_user'` tracked per `public_users.id` |
| 6 | Paid users ungated | ✅ | `user_has_access('plus')` bypass; statuses `active`/`trialing`/`past_due` honored (grace during Stripe retries) |
| 7 | Jail bookings gated identically | ✅ | `/booking/<id>` uses `preview_allowed(resource_type='booking')` (`blueprints/detention.py`); `/jail-roster-search` has 3-row preview cap; `/api/jail-roster-search` returns 403 JSON when exhausted |
| 8 | Crime map: pins free, popups paid | ⚠️ Divergent (Decision 2) | `templates/crime_map.html` popups show type/date/location/county to everyone, ungated; the "View full details" link lands on gated `/record/<id>`. `/api/geo/incidents` is anonymous with a generous quota |
| 9 | Webhook cancel/renew/upgrade/downgrade | ✅ | `customer.subscription.updated/deleted`, `invoice.paid`, `invoice.payment_failed` all handled (`app.py:2737+`, `3058+`); cancellation clears columns + records `subscription_canceled_at`; `services/monetization/dunning.py` owns payment-failure notifications |
| 10 | Free alert signups still work | ✅ (code-level) | Import fixed; dispatcher functions present. Recommend one runtime signup test |
| 11 | nginx unchanged | N/A | Docs-only audit; ops verifies at deploy |

### Prior audit follow-ups (docs/audits/2026-07-18-site-health-audit.md)

- **H1 — `/api/v1/warrants` anonymous full records: FIXED.** Now gated by `user_has_warrant_access()` with a 401 JSON (`blueprints/api.py:2108-2128`).
- **H2 — `/record/<id>/source` pre-redaction PDF bypass: STILL OPEN.** `app.py:10467` serves the source-PDF viewer with no `preview_allowed()`/subscription check. Iterable IDs → unlimited free full documents. **This is the one live paywall bypass.**

## Decisions needed from the owner

1. **Tier names & prices have three conflicting sources of truth.**
   - HANDOFF spec (2026-05): Scout / Insider $7.99/mo, $69.99/yr / Professional $14.99/mo, $129.99/yr
   - `paywall.py` docstring: Plus $5.99/mo, $59/yr / Pro $19.99/mo, $199/yr
   - **Live pricing page: Member / Blotter Plus $7.99/mo, $79/yr / Blotter Pro $26/mo, $260/yr**
   - Presumably the live page is current; confirm, then fix the `paywall.py` docstring and HANDOFF so agents stop quoting dead prices.

2. **Crime map popups:** spec said popups require paid; implementation shows summary popups free and gates at `/record/<id>`. Current behavior is arguably better for SEO/engagement. Keep (update spec) or gate popups?

3. **Warrant access scope conflict.** `CLAUDE.md`/`AGENTS.md` say Insider/Professional do NOT include warrants (separate add-on). But current `paywall.py` has `WARRANT_PLANS = {'plus','pro','warrant_access'}` and remaps `warrant_access → plus` — so every Plus/Pro subscriber currently gets warrant pages. Intentional fold-in or bug?

4. **`/record/<id>/source` bypass:** gate it with `preview_allowed()` (one-line pattern from `/record/<id>`) or intentionally leave open?

## Smaller issues (no decision needed, fix when touched)

- **Duplicate `/pricing` route.** Defined in both `blueprints/bail_bond_ads.py` (registered `app.py:14841`, wins) and `app.py:15306` (shadowed dead code, and it doesn't pass `is_logged_in`/`already_subscribed`). Delete the app.py copy.
- **`already_subscribed` logic bug:** the blueprint computes it as `user_has_warrant_access()` — a Plus subscriber without warrant access won't see the "already subscribed" banner. Should check `user_has_access('plus')`.
- **Monetization-critical routes live in `bail_bond_ads.py`** (`/pricing`, `/checkout/<plan>`). Works, but confusing; move to `blueprints/payments.py` when convenient.
- **Feature matrix unused:** `has_feature()` is never called outside `paywall.py`; the advertised "7-day / 12-month / full archive" history limits and saved-search caps are not enforced through it. Audit each pricing-page promise before relying on it.
- **Stale codemap:** `docs/CODEMAPS/monetization.md` references `subscription_gate()` — doesn't exist (renamed `gated_detail`/`require_subscription`, both currently unused).
- **Stripe price IDs hardcoded in `payments.py`** rather than env vars as the handoff proposed (`MB_*_PRICE_ID`). Price IDs aren't secrets, so this is fine, but env-based config would ease test/prod splits.
