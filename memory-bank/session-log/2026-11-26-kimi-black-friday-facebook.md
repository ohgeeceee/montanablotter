# Session: Black Friday Facebook announcement

- **Date:** 2026-11-26
- **Agent:** kimi-code
- **Scope:** One owner-requested organic Facebook post for Montana Blotter

## Done

- Confirmed the Montana Blotter Facebook page was enabled and had no recent Black Friday post or queued campaign copy.
- Published a photo post using `static/icons/black-friday-2026.png` and the tracked organic-social campaign URL.
- Caption explicitly states the offer opens Friday, November 27 at 12:00 a.m. MT, lists Plus at $39.50 and Pro at $130, and includes eligibility, end time, standard renewal, cancellation, and free-report disclosures.
- Graph API verification confirmed the post, campaign image, prices, start time, tracked link, and renewal disclosure.
- Recorded the publication in `facebook_page_actions.id=1` with category `black_friday_2026` and status `posted`.

## Publication

- URL: https://www.facebook.com/122118820917285535/posts/122127829329285535
- Facebook post ID: `965976219938431_122127829329285535`
- Published: 2026-11-26 15:40:48 UTC

## Notes

- Two queue attempts failed before any Facebook request because the current queue schema requires a real blotter/blog foreign key for custom posts. No duplicate posts were created. The issue and direct-audit approach are documented in `memory-bank/gotchas.md`.
- No email, paid ads, Instagram post, or additional Facebook reminder was sent or scheduled.
