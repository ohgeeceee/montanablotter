# Session: Black Friday 2026 campaign

- **Date:** 2026-11-26
- **Agent:** kimi-code
- **Scope:** subscription campaign, Stripe checkout, pricing/landing UI, advertising drafts

## Goal

Create a Black Friday offer that attracts new customers and a review-ready advertising package.

## Done

- Added a scheduled 50% first-year offer for annual Plus ($39.50) and Pro ($130), November 27–30.
- Added `/black-friday`, pricing sale states, shared banner, social graphic, analytics sources, and registration-to-checkout links.
- Enforced new-customer and annual-only eligibility. Discounted Stripe failures do not fall back to full-price Payment Links.
- Added campaign tests and fixed the checkout route's missing `_plan_price_ids` import.
- Prepared launch/rollback and advertising drafts under `agent-queue/dev/` and `agent-queue/growth/`.

## Not done / blockers

- No live Stripe coupon/promotion code, deployment, email, social post, or ad purchase; these require separate owner approval.
- `npm run build:css` could not run because the local `tailwindcss` executable is not installed. No dependency was installed. Campaign templates were checked against the existing generated stylesheet and adjusted to use available utilities.

## State changes

- Local source and documentation only. No service restart, DB migration, production env change, deployment, or outbound communication.

## Handoff notes

- Start with `agent-queue/dev/2026-11-26-black-friday-2026/README.md` for Stripe setup, launch verification, measurement, and rollback.
- Advertising copy is in `agent-queue/growth/2026-11-26-black-friday-2026/README.md` and remains draft-only.
- Campaign decision recorded in `memory-bank/decisions.md`.

## Launch follow-up

- Owner approved live Stripe setup and deployment with "Lets do it" on 2026-11-26; advertising remains draft-only with no approved spend.
- Found that legacy live Stripe prices did not match owner-confirmed display pricing. Scoped the existing correct $79/$260 annual price IDs to campaign checkout; regular checkout behavior is unchanged and the mismatch remains an open issue.
- Created and validated the live one-time 50% coupon and `BLACKFRIDAY50` promotion code with product and first-time-customer restrictions. Direct live Checkout Sessions verified $39.50 Plus and $130 Pro totals; both were expired unpaid with no subscriptions or redemptions.
- Focused isolated release: 19 tests plus 5 subtests passed. Full isolated release: 1,343 tests plus 28 subtests passed, with the existing order-dependent `test_server_error_repair` failure; the same test passes alone and alongside the campaign tests. An unmodified full-suite baseline is being used to confirm the pre-existing failure.
- Production auto-deploy currently skips its dirty tracked working tree. Verified the four pre-existing Havre/email patches reapply cleanly on the campaign release; preserve them during deployment and compare their diff hash afterward.

## Deployment result

- The unmodified baseline confirmed the same single full-suite failure (`test_server_error_repair`): 1,332 passed there versus 1,343 passed on the campaign release, so the campaign introduced no new full-suite failure. Three additional failures in the mixed working-tree run belonged to unrelated untracked jail-digest work.
- Deployed commit `376d973d`, then commit `8addc38a` after live Pro verification exposed intermittent Checkout retrieval of the new price ID. Final campaign checkouts use inline annual `price_data`; regular checkout is unchanged.
- Preserved the four pre-existing production Havre/email patches through both deployments using tracked-file stashes. Normalized patch hashes matched after restoration, and `mtblotter.service` returned active/HTTP 200 on `8addc38a`.
- Final deployed route-driven live Stripe checks passed for Plus ($39.50) and Pro ($130), including campaign metadata. Six total verification sessions were expired; none was paid or created a subscription, and the promotion had zero redemptions at handoff.
- Public homepage and social image return 200. Before the scheduled start, `/black-friday` correctly redirects to `/pricing`; the offer becomes visible at 2026-11-27 07:00 UTC and ends at 2026-12-01 07:00 UTC.
- Advertising was not published and no ad spend was made. The standard checkout's pre-existing display/Stripe price mismatch remains open and is documented in `memory-bank/gotchas.md`.
