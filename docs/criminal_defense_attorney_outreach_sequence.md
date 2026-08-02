# Montana Blotter — Criminal Defense Attorney Outreach Sequence

> **Status:** Internal sales doc — 2026-07-30 rewrite. The previous version of
> this file pitched a "Felony Alert Sidebar" and a "24-Hour Hotline Header"
> placement that were never built and never shipped. This version matches the
> products that actually exist in code today: `/lawyers` Bronze / Silver /
> Gold directory listings plus the optional real-time arrest-alert add-on
> described below.

---

## What we are actually selling

Three packages on `/lawyers`, billed through Stripe Checkout. Annual billing
is approximately 15% below twelve monthly payments.

| Package | Price | Public page | In code |
| --- | --- | --- | --- |
| Bronze Listing | $149/mo · $1,520/yr | `/lawyers` (standard card) | `blueprints/lawyer_ads.py::_PACKAGES[0]` |
| Silver Featured | $299/mo · $3,050/yr | `/lawyers` (logo + pinned above Bronze) | `blueprints/lawyer_ads.py::_PACKAGES[1]` |
| Gold Priority | $599/mo · $6,110/yr | `/lawyers` (top, photo, tagline, priority lead routing) | `blueprints/lawyer_ads.py::_PACKAGES[2]` |

Every active advertiser also gets the **free `/attorneys` directory** entry
(opt-in) and is eligible for the **real-time arrest-alert add-on** wired in
`services/alerts/lawyer_arrest_alerts.py` and
`scripts/ops/lawyer_arrest_alerts_watcher.py`. The alert watcher emails a
new booking to every active advertiser in the matching county within minutes
of ingestion. Gold-tier advertisers are contacted first.

Per-county inventory is capped at 1 Gold / 2 Silver / 2 Bronze. The cap is
enforced both at the public Stripe webhook (`_county_capacity_blocked`) and
in admin manual entry. If a county fills, new orders land in
`status='capacity_blocked'` until a slot opens.

---

## Outreach sequence

### Day 1 — initial email

Subject: `Montana families searching for a defense attorney in [County]`

Hi [First Name],

I run Montana Blotter — the open public-records platform that indexes jail
rosters, court activity, warrants, and blotter reports from all 56 Montana
counties.

When someone is arrested or booked in [County], their family usually starts
searching within the hour. They search "[County] criminal defense attorney"
or "[County] jail roster" — and Montana Blotter is the page that ranks for
those searches because the records are the page.

We just opened a paid directory at `/lawyers` that puts a firm's name,
phone, and intake link directly on those pages. Listings are county-targeted
and tiered: Bronze, Silver Featured, and Gold Priority. Public intake
inquiries from those county pages route to every active advertiser in the
county, with Gold-tier firms notified first.

[Mock county page screenshot: link to the live `/lawyers/yellowstone` page
with a redacted example firm name for the screenshot]

If this is the right time, I can send a one-page sample report showing the
exact metrics the firm will receive each month. Reply "SEND REPORT" and
I'll get it to you today.

— Jon
Montana Blotter · advertising@montanablotter.com

P.S. — [County] currently has [N] active listings and [N] of [Cap] Gold
slots open. The Gold slot goes to whichever firm commits first.

### Day 3 — phone follow-up

Call the office. Ask who owns intake and paid marketing. Do not pitch the
receptionist for ten minutes. Confirm the firm actually serves [County] and
has working intake coverage. If yes, email the mock placement and the
sample monthly report. If no, remove from the list.

### Day 5 — sample report email

Subject: `Sample monthly report — [Firm Name] on /lawyers/[County]`

Hi [First Name],

Attached / linked: a one-page sample monthly report showing the exact
metrics the firm would receive.

What you'll see in the report:

- Directory impressions in [County] (deduped per visitor per day)
- Tap-to-call actions
- Website / target URL clicks
- Consumer intake leads delivered to your inbox
- Delivery failures (with the destination that bounced)
- Advertiser-reported contact / consultation / retained counts — the firm
  fills these in
- Cost per delivered lead
- Cost per consultation and retained matter when those numbers exist

The report is real data, not estimates. We will not promise case volume or
ROI before we have cohort data. After 90 days we can talk about the
conversion numbers we are actually seeing.

— Jon

### Day 10 — close

Subject: `Final check-in — [County] Gold slot`

Hi [First Name],

Closing the loop on the [County] listing.

If the timing isn't right, no problem. Reply "PASS" and I'll remove you
from the active list. You can always come back later.

If you want to move forward: the [County] Gold slot is currently open. I
can have your firm live within 24 hours of payment.

Reply "GO" and I'll send the checkout link.

— Jon

---

## Compliance notes

These are the lines we will not cross, ever. They're also the lines a
Montana State Bar reviewer will look at first, so every prospect-facing
email is built around them.

- **MRPC 7.1 (Truthful statements about legal services).** No "Top Rated",
  "Best", "Expert", "Guaranteed", or unsubstantiated comparisons in any
  listing copy or pitch. The current plan uses "Priority Placement",
  "Featured", and "Sponsored" only.
- **MRPC 7.2 (Referrals).** The directory is opt-in and compensated. We
  disclose the paid nature on every listing card and on the directory
  landing page. We do not steer specific leads to specific firms.
- **MRPC 7.3 (Solicitation).** The public intake form is the consumer's
  choice, not ours. The consumer checks the consent box. We don't cold-DM
  consumers from this product.
- **MRPC 7.4 (Identification of practice).** Every listing shows firm
  name, contact, practice area, and bar number. The state bar's lawyer
  referral service is linked from `/lawyers` as an alternative.
- **MRPC 7.5 (Firm names).** Listing firm names are not edited by
  Montana Blotter. If the firm uses a trade name, that's the firm's
  responsibility under 7.5.

A real Montana Rules of Professional Conduct review is still on the
project's launch checklist (see `docs/plans/2026-07-30-montana-lawyer-mrpc-review.md`).
Until that review is logged, do not change copy in a way that implies
endorsement, exclusivity, or outcome guarantees.

---

## Voice and tone

- One CTA per email. Not three.
- No fabricated metrics. No "we rank #1 for [County] jail roster" without a
  real search console screenshot.
- No implied endorsements. "Listed on Montana Blotter" is fine. "Endorsed
  by Montana Blotter" is not.
- No testimonials from past advertisers in pitch emails until we have real
  ones with written consent.

---

## What changed from the previous version

- Removed the "Felony Alert Sidebar" ($300/mo) and "24-Hour Hotline Header"
  ($450/mo) placements. They do not exist in the codebase. Any
  prospect who already saw those slides needs a direct email: "We've
  simplified the product — three packages on `/lawyers` instead of the
  five placements we discussed. Here is the current one-pager."
- Removed the claim "rank #1 for [County] jail roster". The internal
  page-views data (2026-07-30) does not support that claim for most
  launch counties — Google accounts for 21-73% of referrers to
  `/county/<slug>` pages, and we have no position-tracking data. The
  rewritten copy below is what the outreach team should use.
- Removed the "11x ROI" anecdote. We don't have it.
- Removed references to `hermes_context_revenue.py` — that script isn't
  part of the lawyer product surface.

---

## What we know about SEO performance for the launch counties

Pulled from `/root/montanablotter/data/page_views.db` on 2026-07-30. This
is the only objective signal we have without running a Search Console
export or a live SERP check (Search Console has never been imported into
the system — see the SEO admin at `/admin/seo/console` to upload the
CSV).

| County page | Total non-direct referrers | Google share | Other search share | Notes |
| --- | --- | --- | --- | --- |
| `/county/yellowstone` | 184 | 34% | 4% (bing/ddg) | 62% from other sources (likely direct + referrers we don't classify). Not a Google-dominated traffic source. |
| `/county/cascade` | 118 | 23% | 11% | Smallest sample. Multi-search-engine presence. |
| `/county/gallatin` | 135 | 28% | 0% | 72% other — almost certainly direct + bookmarked traffic. |
| `/county/missoula` | 179 | 21% | 6% | Lowest Google share of the five. |
| `/county/flathead` | 430 | 73% | 4% | Highest Google share. Most representative county for a search-driven pitch. |

**Rule for sales copy:** do not assert a Google ranking position for any
county unless you have one of:

1. A Search Console export showing average position ≤ 3 for the target
   query in that county, for the last 90 days.
2. A live Google search screenshot for the target query showing
   montanablotter.com in the top organic slot, taken within the last
   30 days.

If neither is available, talk about the traffic data above as
"Montanans searching for jail rosters, court activity, and warrants in
[County] are already landing on this site" without claiming a specific
ranking position.

**How to get the data:**

- Search Console CSV: open `/admin/seo/console`, upload the queries CSV
  for `montanablotter.com` filtered to the last 90 days, then look up
  position for query strings like `[county name] jail roster`,
  `[county name] arrests today`, `[county name] blotter`.
- Live SERP: open an incognito Google search session and screenshot the
  top 10 results for the same query strings.

Update this section every 90 days with fresh data. The "11x ROI" and
"rank #1" claims that came out of the previous doc are the kind of
language that an MRPC 7.1 reviewer will flag immediately.
