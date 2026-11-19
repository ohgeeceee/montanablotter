# Roadmap — Building Out Montana Blotter

Pulled from existing handoffs, docs, and owner decisions. Agents: pick items top-down
within a lane; respect `MISSION.md` authority tiers; move shipped items to
`progress.md`. Owner reprioritizes by editing this file.

## Lane 1 — Monetization (revenue first)

1. **Finish the paywall** — spec: `HANDOFF-paywall.md`.
   - [ ] Verify status: `services/monetization/` exists; audit against the handoff's
     acceptance criteria before writing code.
   - [ ] Fix `/alerts` 500 (import path) if still present — it blocks free signups.
   - [ ] `/pricing` page with Scout/Insider/Professional comparison + Stripe Checkout.
   - [ ] Preview-limit enforcement (3/day, 5/week rolling; session+IP for anonymous).
   - [ ] Gate `/incident/<id>`, booking detail, map popups; wire Stripe webhooks to
     `public_users`.
2. **Attorney/referral revenue** — seeds exist (`seed_attorney_referrals.py`,
   `seed_attorney_resources.py`); outreach sequence in
   `docs/criminal_defense_attorney_outreach_sequence.md`.
3. **Supporter tier & recovery ads** — `docs/SUPPORTER_TIER.md`,
   `services/monetization/`, `blueprints/recovery_ads.py`.

## Lane 2 — Coverage (the product's moat)

1. **County expansion** — plan: `docs/COUNTY_COVERAGE_EXPANSION_2026-08-11.md`;
   source inventory: `county_sources_manifest.md`.
2. **Jail roster tier-2** — assessment: `docs/jail_tier2_assessment.md`; FOIA/contact
   drafts: `docs/jail_coverage_foia_draft.md`, `docs/montana_jail_roster_gap_contacts.csv`.
3. **Ingestion health** — keep fetchers in `services/ingestion/fetchers/` current;
   watchdog alerts on stale sources.

## Lane 3 — Audience growth

1. **Social pipeline** — Facebook autopost shipped (2026-03); extend to Instagram
   (`instagram_publisher.py`) and TikTok (`TikTok_Launch_Package.txt`).
2. **SEO** — every public page: unique title/meta, schema.org markup, county landing
   pages. Sitemap freshness as coverage grows.
3. **Email digests** — morning briefing already runs; grow free-alert signups (needs
   the `/alerts` fix).

## Lane 4 — Product & experience

1. **Brand coherence decision** — Snitch Directory now has the archival-ledger design
   system; decide whether mtblotter.com adopts a shared identity (see
   `decisions.md`, 2026-11-17).
2. **Mobile app** — `mobile/` (Expo/React Native); `npm run ci:verify` gate.
3. **Warrant access add-on** — gating exists (`user_has_warrant_access()`); package
   and promote it once paywall is stable.

## Lane 5 — Ops maturity (keeps agents effective)

1. **Adopt this memory bank** — every agent reads it at start, updates it at end
   (`README.md`). Success metric: no agent asks a question this folder answers.
2. **Fix doc drift** — reconcile `/root` vs `/opt` paths across `AGENTS.md`,
   `docs/notes.md`, `HANDOFF-paywall.md`.
3. **Repo hygiene** — root is cluttered with one-off `test_*.py`, `.bak` files, and
   stray scripts; move tests into `tests/`, archive the rest.
4. **Staging path** — today `main` → production in 2 minutes with no staging;
   consider a `staging` branch + health-gated promote for risky changes.
