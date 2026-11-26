# Gotchas

Hard-won lessons. Append with a date; don't delete — strike through if obsolete.

## Repo & secrets

- **This repo is PUBLIC** (`ohgeeceee/montanablotter`). No secrets, no `.env`, no
  `*.db`, no logs, no real PII — ever. gitleaks runs in CI (`.gitleaks.toml`) and
  `git_sync.sh` scans before pushing.
- An old fine-grained PAT lived in a git remote URL until 2026-11-16. If you find a
  credential anywhere in history, treat it as compromised: scrub + tell the owner to
  revoke.
- Chat interfaces can mangle `+`/`/` in pasted keys. Share keys as downloadable
  files, not pasted text (burned us 2026-11-16).

## Deploys & ops

- **Pushing to `main` deploys to production in ~2 min** and restarts the app via
  `git_sync.sh`. Never push untested code "to see if it works" — there is no staging.
- Doc drift is real: older docs say `/root/montanablotter` + `montanablotter.service`;
  production is `/opt/montanablotter` + `mtblotter.service`, user `ubuntu`
  (see `active-context.md`).
- **SQLite is in WAL mode.** Never `cp` a live DB; use `scripts/ops/backup_db.sh`.
- Two page-view stores exist: the `page_views.db` file and a stale `page_views`
  table inside `blotter.db`. Use the file.

## Codebase

- `app.py` is a ~660 KB monolith. New routes go in `blueprints/`, new domain logic in
  `services/` — don't grow the monolith.
- Root has scattered `test_*.py` one-offs and `.bak` files; the real suite is
  `tests/`. Run `./venv/bin/python3 -m pytest` before pushing code.
- Tests must patch `DB_PATH` to a temp DB. Never run anything against production
  `blotter.db` from a test or a REPL experiment.
- ~~`/alerts` 500 from `import alert_dispatcher`~~ Fixed (2026-11-19 audit).
- ~~**Duplicate `/pricing` route (2026-11-19)**~~ Removal staged in
  `agent-queue/dev/2026-11-19-paywall-warrant-scope/` (apply on vm2778).
  `/pricing` and `/checkout/<plus|pro>` live in `blueprints/bail_bond_ads.py`.
- ~~**Docs quote dead prices (2026-11-19)**~~ Resolved: owner confirmed the live
  page (Plus $7.99/mo, $79/yr; Pro $26/mo, $260/yr) and the docs were updated.
  Sources of truth: `templates/pricing.html` and the price IDs in
  `blueprints/payments.py`.
- ~~**`/record/<id>/source` bypasses the paywall (2026-11-19)**~~ Fix staged in
  the same queue draft (owner approved): source PDFs enforce
  `preview_allowed(resource_type='source_pdf')` and redirect to the gated
  record page when the preview budget is exhausted.
- **Warrant scope model (2026-11-19):** warrants = Pro + standalone
  `warrant_access` add-on; Plus excluded. Buyers from before the fix were stored
  as `plus` — `scripts/maintenance/2026-11-19-fix-warrant-access-plans.py`
  restores them (dry-run first, then `--apply`).

## Monetization / Stripe

- **Live price verification (2026-11-26):** the legacy default checkout price IDs
  bill Plus/Pro at $5.99/$19.99 monthly and $59/$199 annually even though the
  owner-confirmed display prices are $7.99/$26 and $79/$260. The Black Friday
  campaign uses inline prices with the correct annual amounts; the standard
  checkout mismatch remains a separate open issue. Retrieve live `Price` objects
  before advertising or changing a checkout offer.
- **Stripe `2026-11-18.endive` API:** `PromotionCode.create` requires
  `promotion={'type': 'coupon', 'coupon': coupon_id}` instead of the legacy
  top-level `coupon` parameter. Coupon `applies_to` is includable; retrieve with
  `expand=['applies_to']` to verify product restrictions. Newly created price IDs
  also proved intermittently unavailable to Checkout despite successful reads;
  campaign checkout uses inline annual `price_data` on the existing products to
  avoid that replication dependency.

## Agent fleet

- Multiple agents (Hermes, Codex, Kimi Code, OpenClaw) commit on the same box. Pull
  before you start; keep commits small and Conventional-Commit formatted to make the
  2-minute sync painless.
- Red-tier actions (code changes, restarts, DB writes outside ingestion) need a
  human-approved `agent-queue/` draft per `MISSION.md`.
