# Bail Bonds Advertising Program (3-Phase Setup)

## Phase 1: Foundation (Implemented)
- Public landing page at `/advertise/bail-bonds` with:
- package lineup (Featured Bondsman, Emergency Call Sidebar, Exclusive County Sponsorship, The Gold Bond Bundle)
- compliance/policy requirements
- application form for local bail bonds businesses
- Intake data persistence table: `bail_ad_inquiries`
- Admin review queue at `/admin/bail-ads` with status controls:
- `pending`
- `in_review`
- `approved`
- `declined`
- `archived`

## Phase 2: Purchase and Provisioning
- Stripe-backed checkout for approved advertisers.
- Monthly and annual billing options with contract acknowledgment.
- Creative asset upload + ad copy approval workflow.
- Auto-assignment engine for county slot inventory.

Implementation status:
- Implemented: checkout routes, Stripe metadata flow, webhook fulfillment into ad orders, onboarding token flow, creative upload and admin review queue.
- Implemented: county slot assignment on successful payment and public advertiser directory page (`/bail-bonds`).

## Phase 3: Value Expansion and Reporting
- Package add-on: in-feed integration ($200/month).
- Advertiser performance dashboard (impressions, clicks, CTR).
- Renewal pipeline and upgrade prompts.
- Lead-quality reporting by county and placement.

Implementation status:
- Implemented: event tracking endpoint (`/api/bail-ads/event`) for impressions/clicks/leads.
- Implemented: admin performance dashboard metrics in `/admin/bail-ads` including 30-day totals, county performance, renewal candidates, and upgrade recommendations.
