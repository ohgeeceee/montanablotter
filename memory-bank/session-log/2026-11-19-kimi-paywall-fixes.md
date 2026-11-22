# Session Log — 2026-11-19 — Kimi: paywall decisions implemented

**Agent:** Kimi (chat session, working via GitHub API)
**Scope:** implement the 4 owner decisions from `docs/audits/2026-11-19-paywall-audit.md`

## Owner decisions (received 2026-11-19)

1. **Prices:** the live page is truth (Plus $7.99/mo, $79/yr; Pro $26/mo, $260/yr).
2. **Crime-map popups:** keep current behavior — free summary popups, gate at
   `/record/<id>` (owner: better for SEO). Spec updated, not code.
3. **Warrant access scope:** confirmed bug. New model: warrants come with Pro
   and the standalone `warrant_access` add-on; Plus excluded.
4. **Source-PDF bypass:** fix approved.

## Shipped in this commit (docs + tooling only; no subscriber-visible change)

- `scripts/maintenance/2026-11-19-fix-warrant-access-plans.py` (new): finds
  warrant buyers the old webhook stored as `plus`, restores
  `subscriber_plan='warrant_access'`. Dry-run default; `--apply` writes.
- Docs: `CLAUDE.md` (warrant model, removed dead `_apply_warrant_access_stripe_event`
  section, `/root`→`/opt` paths, app.py line count), `docs/CODEMAPS/monetization.md`
  (`subscription_gate()` never existed; current helpers + plan ranks),
  `HANDOFF-paywall.md` (superseded banner), `AGENTS.md` (warrant bullet),
  audit addendum, memory-bank updates.

## Staged for on-box execution (Red-tier)

`agent-queue/dev/2026-11-19-paywall-warrant-scope/` patches `app.py` (webhook
write remap removal + write set, source-PDF gate, duplicate `/pricing` removal),
`services/monetization/paywall.py` (`WARRANT_PLANS = {'pro','warrant_access'}`,
`warrant_access` first-class at level 0, docstring prices),
`blueprints/bail_bond_ads.py` (`already_subscribed` → `user_has_access('plus')`;
legacy `POST /checkout/subscription` retired to a redirect), and
`tests/test_stripe_checkout_redirects.py` (updated for the redirect); adds
`tests/test_warrant_scope.py`. Run order on vm2778: patch → pytest → review
diff → commit (git_sync deploys in ~2 min) → migration script.

## Deploy note

Until the staged patch runs, code still grants Plus subscribers warrant access
(the bug), `/record/<id>/source` is still ungated, and the legacy
`/checkout/subscription` still sells Professional at the old $14.99/mo price.
Docs describe the target model and point at the queue draft.
