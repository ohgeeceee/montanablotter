# Paywall: warrant-access scope fix + source-PDF gate

**Date:** 2026-11-19 · **Tier:** Red (code changes on production paths)
**Approved:** owner, in chat, 2026-11-19 ("Bug, please audit and fix to whatever
would be best for this site" / "Please")

## Summary

Implements owner decisions 3 and 4 from `docs/audits/2026-11-19-paywall-audit.md`:

1. **Warrant-scope bug.** Today the webhook remaps `warrant_access → plus` at
   write time and `WARRANT_PLANS` includes `plus`, so every Plus subscriber gets
   `/wanted`. New model: warrants come with **Pro** and the standalone
   **warrant_access** add-on; **Plus does not include them**.
2. **Source-PDF bypass (H2, open since the July audit).** `/record/<id>/source`
   serves pre-redaction originals with no gate. The patch enforces
   `preview_allowed(resource_type='source_pdf')` and redirects exhausted viewers
   to `/record/<id>`.

Also removes the shadowed duplicate `/pricing` route in `app.py` (the blueprint
copy wins), fixes stale prices in the `paywall.py` docstring, fixes `/pricing`'s
`already_subscribed` banner (was checking warrant access; now checks
`user_has_access('plus')`), and retires the legacy `POST /checkout/subscription`
endpoint — it was still selling Professional at the old $14.99/mo via inline
`price_data`. It now redirects onto `/checkout/<plus|pro>`.

## Files touched

- `app.py` — `_apply_subscription_stripe_event`: keep `warrant_access` as-is
  (no remap) and accept it in the write set; `public_record_source`: preview
  gate; delete duplicate `/pricing`.
- `services/monetization/paywall.py` — `WARRANT_PLANS = {'pro','warrant_access'}`;
  `warrant_access` added to `PLAN_HIERARCHY` at level 0 (recognized, no Plus
  features); `warrant_access→plus` dropped from `LEGACY_PLAN_REMAP`; docstring
  prices updated to the live values.
- `blueprints/bail_bond_ads.py` — `/pricing` `already_subscribed` fix; legacy
  `/checkout/subscription` retired to a redirect.
- `tests/test_warrant_scope.py` — new regression coverage for the warrant model.
- `tests/test_stripe_checkout_redirects.py` — updated for the retired legacy
  checkout (asserts the redirect mapping).

## How to apply (on vm2778)

```bash
cd /opt/montanablotter
git pull --ff-only origin main
./venv/bin/python3 agent-queue/dev/2026-11-19-paywall-warrant-scope/patch_paywall_warrant.py
# review the result:
git diff
./venv/bin/python3 -m pytest tests/test_warrant_scope.py tests/test_paywall.py tests/test_ad_unlock.py tests/test_stripe_checkout_redirects.py -q
git add -A && git commit -m "fix(paywall): scope warrants to Pro + standalone add-on; gate source PDFs"
# git_sync.sh pushes and deploys within ~2 minutes, then restore legacy buyers:
./venv/bin/python3 scripts/maintenance/2026-11-19-fix-warrant-access-plans.py          # dry run
./venv/bin/python3 scripts/maintenance/2026-11-19-fix-warrant-access-plans.py --apply
```

## Rollback

`git revert <commit>` (the patch script also leaves `*.bak-20261119-warrant-scope`
copies next to each edited file; the migration is a narrow, re-runnable UPDATE).

## Notes

- Between deploy and the migration, legacy warrant buyers (stored as `plus`)
  temporarily lose `/wanted` — run the migration right after deploy.
- True Plus subscribers losing `/wanted` is the intended fix: they never paid
  for it. If anyone complains, the warrant weekly plan is the cheap answer.
