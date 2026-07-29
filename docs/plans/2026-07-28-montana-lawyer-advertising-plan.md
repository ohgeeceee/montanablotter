# Montana Lawyer Advertising Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Sell measurable, county-targeted advertising and opted-in consumer leads to licensed Montana law offices while protecting Montana Blotter's public-trust mission.

**Architecture:** Keep `/attorneys` as the free editorial resource and operate `/lawyers` as a clearly disclosed paid directory. Stripe manages recurring subscriptions; Montana Blotter stores listings, consented intake leads, delivery history, and performance events in SQLite. Each advertiser gets a tokenized control panel for creative updates and metrics, while staff retains the admin CMS for approval and support.

**Tech Stack:** Flask, SQLite, Jinja2 newspaper design system, Stripe Checkout/webhooks, existing SMTP configuration.

---

## Product position

Sell speed, local relevance, and accountability. Do not sell a vague banner ad or imply that Montana Blotter endorses a firm. The offer is:

> County-targeted visibility and consented inquiries from Montanans already looking for legal help, with plain reporting on impressions, calls, clicks, and delivered leads.

The best initial customer is a Montana criminal-defense or DUI firm serving one to five counties. Those firms align most directly with the detention, warrant, court, and blotter audience. Expand to family law and personal injury only after the criminal-defense funnel has real conversion data.

## Packages

| Package | Price | Best fit | Core value |
|---|---:|---|---|
| Bronze Listing | $149/month or $1,520/year | Solo/small local office | Firm profile, phone, website, county targeting, basic lead delivery |
| Silver Featured | $299/month or $3,050/year | Regional office | Priority over Bronze, logo, larger description, monthly metrics |
| Gold Priority | $599/month or $6,110/year | Firm that values immediate intake | First placement by tier, photo/tagline, immediate email lead alert, complete conversion report |

Annual billing is approximately 15% below twelve monthly payments. Do not discount the first month by default. Instead, offer a 30-day pilot only to the first three launch firms, with a written baseline and renewal decision.

Do not use labels such as “Top Rated,” “Best,” “Expert,” or “Guaranteed” unless the advertiser supplies verifiable substantiation and the copy is approved. “Priority Placement” and “Featured” describe the product rather than the lawyer's quality.

## Inventory and exclusivity

Start with a maximum of five active advertisers per county:

- 1 Gold Priority
- 2 Silver Featured
- 2 Bronze Listings

Do not claim exclusivity until the application enforces it. If a county reaches capacity, start a waitlist rather than overcrowding the page. Review caps after 90 days using impressions, delivered leads, and response rates.

## Lead handling standard

Every lead must:

1. Include explicit consent to share contact information with paid attorney advertisers.
2. Record consent time, a privacy-preserving IP hash, and consent-text version.
3. Match counties case-insensitively and tolerate whitespace differences.
4. Be delivered to every eligible active advertiser unless the package contract says otherwise.
5. Record each delivery as sent or failed.
6. Tell the consumer how many eligible advertisers received the inquiry.
7. Avoid promising an attorney response time that Montana Blotter cannot verify.

Use email delivery at launch. Add SMS only after phone-consent language, opt-out handling, and Twilio delivery monitoring are explicitly implemented.

## Sales motion

### Launch cohort

Build a list of 30 firms across Yellowstone, Cascade, Gallatin, Missoula, and Flathead counties. Prioritize firms with:

- active criminal-defense or DUI practice;
- a working website and staffed intake number;
- service across multiple nearby counties;
- evidence they already buy search or directory advertising;
- a named office manager or intake lead.

### Outreach sequence

Day 1: concise email to the managing attorney or office manager with the county page link and a screenshot/mock placement.

Day 3: call the office. Ask who owns intake and paid marketing. Do not pitch the receptionist for ten minutes.

Day 7: send a one-page sample report showing the exact metrics the firm will receive.

Day 14: close the loop with a clear yes/no question and waitlist deadline. Stop after that unless they engage.

Use `advertising@montanablotter.com` as the public sales and support address. Keep internal sales notes out of prospect-facing pages.

### Core pitch

“Montana Blotter reaches people already researching arrests, bookings, warrants, and court activity in your service counties. Your firm receives a clearly labeled county placement, one-tap calls, and consented inquiries. You can see impressions, clicks, calls, and delivered leads instead of buying a blind directory listing.”

Never promise case volume, signed clients, or ROI before real cohort data exists.

## Onboarding

1. Confirm the attorney is licensed and the submitted bar number matches the advertised attorney or firm.
2. Confirm service counties, practice areas, phone, website, and intake email.
3. Review all claims for accuracy and remove unverifiable comparisons or outcomes.
4. Activate Stripe subscription.
5. Give the firm its tokenized control-panel link.
6. Run a test inquiry marked as test data and confirm receipt.
7. Record the launch date and baseline metrics.

## Reporting

Send a monthly report containing:

- directory impressions;
- website clicks;
- tap-to-call actions;
- leads delivered;
- delivery failures;
- advertiser-reported contacts, consultations, and retained matters;
- cost per delivered lead;
- cost per consultation and retained matter when the advertiser supplies those numbers.

Label calls/clicks as actions, not confirmed conversations. Never manufacture traffic estimates or conversion statistics.

## Trust and compliance controls

- Clearly disclose paid placement and tier ordering.
- Keep `/attorneys` as the free, non-paid resource directory.
- Require bar number at checkout; staff verifies it before treating the listing as vetted.
- Do not imply Montana Blotter recommends a lawyer.
- Do not use “Top Rated,” “Best,” or guaranteed-outcome copy by default.
- Make the lead-sharing consent explicit and link to the privacy policy.
- Keep control-panel pages `noindex` and protect them with high-entropy tokens.
- Review current Montana Rules of Professional Conduct and State Bar guidance before the first campaign. Web research was unavailable during implementation, so this is an operational requirement, not a claim that legal review is complete.

## 90-day rollout

### Days 1-14: instrument and recruit

- Verify Stripe webhook and SMTP delivery in production.
- Test public directory, county pages, checkout, webhook activation/cancellation, lead consent, delivery logs, admin CMS, and control panel.
- Recruit three pilot firms in three different counties.

### Days 15-45: prove response quality

- Review every delivery failure daily.
- Ask firms to classify leads as contacted, consultation, retained, duplicate, spam, or outside practice.
- Rewrite landing-page claims using measured outcomes only.

### Days 46-90: scale carefully

- Expand to 10-15 paying firms.
- Enforce county inventory caps before selling exclusivity.
- Add sponsor cards to the highest-intent public pages only where relevance and disclosure are clear.
- Consider SMS alerts only after consent and delivery controls are complete.

## Launch acceptance criteria

- `/lawyers`, `/lawyers/<county>`, and `/advertise/lawyers` are anonymously accessible.
- County pages never show out-of-county firms.
- Stripe activation is idempotent and cancellation deactivates the listing.
- Checkout requires a Montana bar number.
- Consumer intake requires explicit lead-sharing consent.
- Eligible firms receive actual lead email, and each attempt is logged.
- Admin routes load and allow listing management.
- Advertiser control panel loads from its token and updates allowed fields.
- Tests pass against a temporary SQLite database; production `blotter.db` is not modified by test execution.
