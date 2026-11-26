# Session: Standard Plus/Pro checkout pricing fix

- **Date:** 2026-11-26
- **Agent:** kimi-code
- **Scope:** New standard and Black Friday Plus/Pro Checkout Sessions; no existing-subscription changes

## Goal

Fix the mismatch between displayed standard prices and the legacy Stripe prices used by checkout and Payment Link fallbacks.

## Done

- Added one product/amount catalog in `blueprints/payments.py`: Plus $7.99/month and $79/year; Pro $26/month and $260/year.
- Standard and Black Friday checkouts now share inline recurring `price_data`. Removed all repository references to the four legacy price IDs and four outdated Payment Links.
- Standard Stripe failures now return to `/pricing` with an error rather than opening a mispriced fallback.
- Added regression coverage for every standard amount and interval, campaign pricing, and failure behavior.
- Focused tests: 26 passed plus 9 subtests. Full isolated suite: 1,344 passed plus 32 subtests, with only the previously confirmed baseline `test_server_error_repair` failure.
- Live pre-deployment Stripe sessions verified all four standard totals without discounts. Deployed route-driven checks verified all four totals and intervals plus Black Friday Plus at $39.50.
- All nine verification sessions were expired unpaid; none created a subscription.
- Deployed `52a6238a`; `mtblotter.service` is active and the public homepage returns 200. The four existing production Havre/email patches were preserved by normalized hash comparison.

## Not changed

- Existing Stripe subscriptions and their renewal prices were not modified.
- Warrant-access, supporter, donation, and legacy subscription routes were outside this Plus/Pro fix.
- Old Payment Links remain in Stripe for historical reference but have no repository/site references after this fix.

## Handoff

Implementation, verification, and rollback notes: `agent-queue/dev/2026-11-26-standard-checkout-pricing/`.
