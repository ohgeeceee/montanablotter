# Roadmap — Building Out Montana Blotter

Pulled from existing handoffs, docs, and owner decisions. Agents: pick items top-down
within a lane; respect `MISSION.md` authority tiers; move shipped items to
`progress.md`. Owner reprioritizes by editing this file.

## Lane 1 — Monetization (revenue first)

1. **Paywall** — SHIPPED; audited 2026-11-19 (`docs/audits/2026-11-19-paywall-audit.md`).
   - [x] Audit against `HANDOFF-paywall.md` acceptance criteria — done 2026-11-19
   - [x] `/alerts` 500 import fix — confirmed fixed
   - [ ] **Owner: 4 decisions** (audit doc) — price truth source, map popups,
     warrant-access scope, source-PDF bypass
   - [ ] Fix `/record/<id>/source` bypass (pending owner sign-off)
   - [ ] Delete duplicate `/pricing` route in `app.py:15306`; fix
     `already_subscribed` to check `user_has_access('plus')`
   - [ ] Move `/pricing` + `/checkout/<plan>` from `bail_bond_ads.py` to
     `blueprints/payments.py`
   - [ ] One live round-trip test: signup → checkout → webhook → cancel
   - [ ] Enforce (or remove from pricing page) the unused `has_feature()` matrix —
     history-window and saved-search caps are advertised but not systematically gated
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
3. **Email digests** — morning briefing already runs; grow free-alert signups via
   `/alerts` (now fixed).

## Lane 4 — Product & experience

1. **Brand coherence decision** — Snitch Directory now has the archival-ledger design
   system; decide whether mtblotter.com adopts a shared identity (see
   `decisions.md`, 2026-11-17).
2. **Mobile app** — `mobile/` (Expo/React Native); `npm run ci:verify` gate.
3. **Warrant access add-on** — resolve scope conflict first (audit Decision 3), then
   package and promote.

## Lane 5 — Ops maturity (keeps agents effective)

1. **Adopt this memory bank** — every agent reads it at start, updates it at end
   (`README.md`). Success metric: no agent asks a question this folder answers.
2. **Fix doc drift** — `AGENTS.md` done 2026-11-19; remaining: `docs/notes.md`,
   `HANDOFF-paywall.md` (`/root` paths, stale prices, stale DB size),
   `docs/CODEMAPS/monetization.md` (`subscription_gate()` doesn't exist).
3. **Repo hygiene** — root is cluttered with one-off `test_*.py`, `.bak` files, and
   stray scripts; move tests into `tests/`, archive the rest.
4. **Staging path** — today `main` → production in 2 minutes with no staging;
   consider a `staging` branch + health-gated promote for risky changes.
