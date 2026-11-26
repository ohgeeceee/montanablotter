# Session: Black Friday free-member email

- **Date:** 2026-11-26
- **Agent:** kimi-code
- **Scope:** One owner-requested Black Friday email to eligible opted-in recipients

## Audience safeguards

- Combined active email subscribers with registered users who opted into digest emails.
- Deduplicated addresses and required a valid subscriber email/token or registered-user opt-in.
- Excluded every address associated with a current or former paid subscription using plan, Stripe subscription, status, activation, and cancellation fields.
- Required an unsubscribe token or account preference link for every recipient.
- Preflight found four eligible recipients and no existing campaign. Sending was aborted automatically if the audience changed.

## Sent

- Campaign: `email_campaigns.id=1`, `Black Friday 2026 - First Year Offer`
- Audience: `black_friday_2026_free_members`
- Subject: `Black Friday: 50% off your first year of Plus or Pro`
- Tracked URL used `utm_source=member_email`, `utm_medium=email`, and `utm_campaign=black_friday_2026`.
- Email explicitly states the Friday, November 27 at 12:00 a.m. MT start, Plus/Pro prices, features, end time, new-customer eligibility, standard renewal, cancellation, and free-report availability.
- SMTP accepted all four messages with zero failures at 2026-11-26 16:04:34 UTC.
- Campaign log contains four unique delivery rows, all `sent`, and campaign status `sent`.

## Notes

- SMTP acceptance confirms sending, not inbox delivery or engagement.
- No reminder email, paid advertising, or additional social post was sent or scheduled.
