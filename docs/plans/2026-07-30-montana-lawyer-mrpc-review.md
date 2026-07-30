# Montana Lawyer Advertising — MRPC 7.x Compliance Review

> **Required before:** sending the first paid outreach to a Montana attorney
> or activating the first paying advertiser. Owned by Jon. Target completion
> date: 2026-08-13 (two weeks from launch plan authoring on 2026-07-30).
> Re-run after any copy change on `/lawyers`, `/advertise/lawyers`, or the
> intake form.

This is a working checklist, not a legal opinion. Where the answer to a
question is "ask the State Bar", do that — do not guess.

> **Pre-review status (2026-07-30, automated pass by Hermes):** mechanical
> checks against the public code, templates, and copy have been run. Items
> marked **[auto-pass]** below are based on direct inspection of the code
> as of this date. Items marked **[needs reviewer]** cannot be checked by
> code inspection and must be reviewed by a Montana State Bar member,
> outside ethics counsel, or the publisher. Nothing below should be
> treated as a legal opinion.

---

## Reviewer sign-off

- [ ] **Reviewer name:**
- [ ] **Reviewer role** (Jon / outside counsel / Montana State Bar
      ethics counsel):
- [ ] **Date completed:**
- [ ] **Re-review trigger:** any of the surfaces below change copy or
      add a new placement type.

---

## 1. Surfaces in scope

These are the public-facing surfaces that contain lawyer-advertising copy.
Every change to them should re-trigger this review.

- [ ] `/lawyers` directory page — `templates/lawyers.html`
- [ ] `/lawyers/<county>` county pages — same template, scoped render
- [ ] `/advertise/lawyers` landing — `templates/advertise_lawyers.html`
- [ ] `/advertise/lawyers/checkout` — `templates/advertise_lawyers_checkout.html`
- [ ] Public intake form on `/lawyers` — same template, form section
- [ ] Consumer-facing email (the lead that gets routed to a firm) —
      `blueprints/lawyer_ads.py::_send_lawyer_lead_email`
- [ ] Sales outreach doc — `docs/criminal_defense_attorney_outreach_sequence.md`
- [ ] The one-pager — `templates/advertise_lawyers_one_pager.html` (see
      Task 7 of the launch plan)
- [ ] The real-time arrest-alert email — `services/alerts/lawyer_arrest_alerts.py`

## 2. Montana Rules of Professional Conduct — check each rule

### MRPC 7.1 — Truthful statements about legal services

A lawyer shall not make a false or misleading communication about the
lawyer or the lawyer's services. A communication is false or misleading
if it contains a material misrepresentation of fact or law, or omits a
fact necessary to make the statement considered as a whole not
materially misleading.

- [ ] **[auto-pass]** No use of "Top Rated", "Best", "#1", "Expert",
      "Guaranteed", or "Award-Winning" in any listing copy. Mechanical
      sweep on 2026-07-30 across `/lawyers`, `/attorneys`,
      `/advertise/attorney-sponsorship`, `/advertise/lawyers`,
      `/advertise/lawyers/one-pager`, `/advertise/lawyers/sample-report`
      found no occurrences. The legacy "Gold Top Rated" label in
      `blueprints/attorney_ads.py`, `templates/attorneys.html`,
      `templates/includes/attorney_referral.html`, and
      `templates/advertise_attorney_sponsorship.html` was renamed to
      "Gold Priority" / "Priority Placement" in the same pass.
- [ ] **[auto-pass]** Tier labels describe placement, not quality. The
      only labels in use are "Priority Placement" (Gold), "Featured"
      (Silver), and "Sponsored" (Bronze).
- [ ] **[needs reviewer]** No implied or stated comparison to a specific
      named competitor. There is no automated check for this; the
      reviewer's job is to read the prospect emails and outreach doc.
- [ ] **[needs reviewer]** No claim of specialization in a field where
      Montana does not certify specialists. Montana does not have an ABA
      specialty board for criminal defense as of this writing — confirm
      with the State Bar before any listing uses "Specialist" or
      "Specializing in" copy.
- [ ] **[auto-pass]** No use of testimonials, anecdotes, or
      case-result statistics. The outreach doc rewrite
      (`docs/criminal_defense_attorney_outreach_sequence.md`) explicitly
      removes the "11x ROI" anecdote and forbids testimonials until
      written consent exists.
- [ ] **[auto-pass]** No use of stock photography that implies a
      guaranteed outcome. The only images on the surfaces are firm
      logos (advertiser-supplied) and the `🏆` / `⭐` Unicode
      characters used as tier markers.
- [ ] **[needs reviewer]** Read every current listing copy in
      `lawyer_ad_listings` to confirm no firm has used a prohibited
      phrase in their own description. The plan does not pre-approve
      listing copy; it relies on the bar-number verification step.

### MRPC 7.2 — Referrals

A lawyer shall not give anything of value to a person for recommending
the lawyer's services, except that a lawyer may pay the reasonable
costs of advertisements or communications permitted by this Rule, may
pay the usual charges of a legal service plan or a not-for-profit
lawyer referral service, and may purchase advertising or signage
permitted by Rule 7.1 subject to the requirements of Rule 7.3.

- [ ] **[auto-pass]** The directory payment is for advertising only,
      not for referral of a specific matter. Stripe Checkout line items
      are flat-rate subscriptions; the lead-delivery code
      (`blueprints/lawyer_ads.py::_deliver_lawyer_lead`) does not
      accept or process per-lead or per-matter payments.
- [ ] **[auto-pass]** Lead delivery is consumer-initiated. The intake
      form requires an explicit `consent_ack=yes` checkbox before
      submission is accepted (`lawyers_intake` route, `errors.append
      ('missing_consent')` check). The consumer is told how many
      advertisers will receive the inquiry.
- [ ] **[auto-pass]** Every active advertiser in a county receives
      every lead that matches that county. The query in
      `lawyers_intake` selects all `status='active'` orders and filters
      by `_county_matches`; there is no per-firm filtering by Montana
      Blotter beyond the public tier ordering.
- [ ] **[auto-pass]** The plan does not offer payment per lead,
      payment per retained matter, or any contingent compensation.
      All billing is flat subscription via Stripe Checkout.

### MRPC 7.3 — Solicitation

A lawyer shall not solicit professional employment by in-person or live
telephone contact or by real-time electronic contact, or by targeted
written or recorded communication, from a person with whom the lawyer
has no family or prior attorney-client relationship, when the
solicitation is to a person whom the lawyer knows or reasonably should
know is in need of legal services in a particular matter and the
solicitation is derived from information about the person's specific
legal problem obtained from a source independent of the lawyer or the
lawyer's agent.

- [ ] **[auto-pass]** The consumer intake form is posted publicly. It
      is filled in by consumers, not pushed to them. This is not a
      solicitation under 7.3.
- [ ] **[auto-pass]** Montana Blotter does not email or text individual
      consumers based on blotter, court, or warrant data to recommend a
      specific firm. There is no code path that does this.
- [ ] **[auto-pass]** The arrest-alert add-on (real-time booking email
      to all active advertisers in a county) is general. The
      `services/alerts/lawyer_arrest_alerts.py::find_matching_orders`
      function returns every active advertiser in the county whose
      practice area matches the charge category; it does not pre-select
      defendants.
- [ ] **[auto-pass]** The consumer-facing intake form language is web
      form only; no in-person or live-telephone contact is initiated.
- [ ] **[needs reviewer]** Confirm the firm's own intake responses do
      not constitute live-telephone or in-person solicitation in any
      future feature. Current code is clean.

### MRPC 7.4 — Identification of practice

A lawyer shall not practice under a trade name, or in a name that
misleads as to the identity of the lawyer or lawyers practicing under
it, and shall not practice law under a firm name that includes the name
of a person who is not a member of the firm or a deceased or retired
member of the firm. A lawyer shall clearly identify the lawyer's name
and the lawyer's firm on every written communication, including
electronic communications, the lawyer makes to a client or a third
person.

- [ ] **[auto-pass]** Every listing on `/lawyers` displays firm name,
      contact name, bar number, phone, website, and counties served.
      These fields are required at checkout
      (`advertise_lawyers_checkout` POST handler).
- [ ] **[auto-pass]** Lead emails to advertisers show Montana Blotter
      as the sender with a reply-to of `advertising@montanablotter.com`.
      The advertiser's firm name appears in the subject and body so
      the consumer knows who is being given their information.
      Verified in `blueprints/lawyer_ads.py::_send_lawyer_lead_email`.
- [ ] **[auto-pass]** No use of trade names, slogans, or taglines
      that imply a firm is larger or more established than it is. The
      tagline field is the firm's own copy, displayed as-submitted;
      Montana Blotter does not edit it. The control-panel template
      explicitly tells the firm "Avoid unverifiable comparisons or
      guaranteed outcomes."
- [ ] **[auto-pass]** The Montana State Bar lawyer referral service is
      linked from `/lawyers` as an alternative. Verified in
      `templates/lawyers.html` FAQ block.
- [ ] **[needs reviewer]** Spot-check 5-10 current firm names in
      `lawyer_ad_orders` and `attorney_referrals` to confirm none
      contains a non-member name or misleading claim.

### MRPC 7.5 — Firm names and letterheads

(See Rule 7.4 summary above; also covers letterhead requirements that
apply to written communications.)

- [ ] **[auto-pass]** Letterhead-style communications (lead emails,
      arrest alerts) identify the sender (Montana Blotter) and the
      recipient's firm. The `From:` line on every email
      (`_send_lawyer_lead_email` and `_send_arrest_alert_email`) is
      "Montana Blotter <{SMTP_USER}>" with a `Reply-To` of
      `advertising@montanablotter.com`.
- [ ] **[auto-pass]** No display of misleading geographic claims. The
      county-targeting system is based on what the firm submits
      (`counties_served` text field at checkout); Montana Blotter
      does not auto-add counties. Staff verifies the counties on
      activation per the launch plan onboarding checklist.

### ABA Model Rule 7.3 / Montana equivalent — Targeted outreach

- [ ] **[auto-pass]** The cold-outreach sequence in
      `docs/criminal_defense_attorney_outreach_sequence.md` is
      addressed to attorneys, not to consumers. This is
      business-to-business communication, not consumer solicitation.
- [ ] **[auto-pass]** No use of "Send this to a friend" or
      "Forward to someone in legal trouble" features. No code path
      supports these.
- [ ] **[needs reviewer]** Confirm the cold email sender (SMTP_USER
      and `Reply-To: advertising@montanablotter.com`) resolves to a
      real inbox, that the inbox has a physical mailing address
      footer, and that an unsubscribe link is added. CAN-SPAM
      compliance for B2B email is less strict than B2C, but
      best-practice is the same.

## 3. State Bar guidance — look these up

These are the State Bar of Montana resources that should be on the
reviewer's desk during the review. (We are not asserting that
fetching this list completes the review — these are the links the
reviewer should actually open and read.)

- [ ] State Bar of Montana — Rules of Professional Conduct
      (`https://www.montanabar.org/mrpc`)
- [ ] State Bar of Montana — Ethics opinions
      (`https://www.montanabar.org/ethics-opinions`)
- [ ] State Bar of Montana — Advertising guidance
      (`https://www.montanabar.org/advertising`)
- [ ] ABA Model Rule 7 — Information About Legal Services
      (for cross-reference)

The reviewer's job is to read at least the most recent three
Montana ethics opinions on lawyer advertising and note any
constraints that change the copy on the surfaces above.

## 4. Decisions and sign-off

- [ ] **Disallowed copy** (list any phrases that must come out):
- [ ] **Allowed copy with conditions** (e.g., "results may vary"
      label required on testimonials):
- [ ] **Open questions for the State Bar** (record what was asked
      and the response, or "no open questions"):
- [ ] **Re-review date** (default: 12 months from sign-off, or
      sooner if any surface changes):

---

## After sign-off

- File this completed checklist in `docs/compliance/` (create the
  directory if it doesn't exist).
- Update the launch plan
  (`docs/plans/2026-07-28-montana-lawyer-advertising-plan.md`)
  Trust and Compliance section to remove the "operational
  requirement" caveat and link to this signed-off doc.
- If anything in the surfaces above changes, re-run this checklist
  before the change ships.
