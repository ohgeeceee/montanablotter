---
profile: ops
created: 2026-05-19T09:00:00-06:00
tier: yellow
status: open
priority: high
related_county: ""
related_files:
  - /root/.hermes/profiles/blotter-ops/.env
  - /root/.hermes/profiles/blotter-ingest/.env
  - /root/.hermes/profiles/blotter-dev/.env
  - /root/.hermes/profiles/blotter-civic/.env
  - /root/.hermes/profiles/blotter-ops/config.yaml
  - /root/.hermes/profiles/blotter-ingest/config.yaml
  - /root/.hermes/profiles/blotter-dev/config.yaml
  - /root/.hermes/profiles/blotter-civic/config.yaml
---

# Summary

The Hermes profile fleet (blotter-ops, blotter-ingest, blotter-dev, blotter-civic) cannot spawn one-shot CLI sessions because of API authentication misconfiguration. The digest pipeline (`run-digest.sh`) fails, raw digest sections are MISSING, and cron jobs that rely on LLM analysis error out.

# Observation

1. `blotter-ops chat` fails with HTTP 401 Invalid Authentication from kimi-coding / api.moonshot.ai.
2. `blotter-ingest chat` hangs for 180s then drops with openrouter RemoteProtocolError.
3. `blotter-dev` and `blotter-civic` profiles had no model block in `config.yaml` (fixed by ops 2026-05-19).
4. `blotter-ops/.env` contains a `KIMI_API_KEY` line; the other profiles symlink to global `.env` which lacks `KIMI_API_KEY`.
5. `hermes auth list` shows the kimi-coding credential is rate-limited (`engine_overloaded_error`, ~30m cooldown at time of check).
6. Three Hermes cron jobs (`mb_growth_brief_weekly`, `mb_source_coverage_daily`, `mb_revenue_strategy_weekly`) have been failing with HTTP 404/401 errors since 2026-05-16.

# Proposed action

1. **Verify the `KIMI_API_KEY` in `/root/.hermes/profiles/blotter-ops/.env` is valid and non-empty.** If it is empty or stale, replace it with the working key from the auth pool or regenerate it.
2. **Copy the valid `KIMI_API_KEY` into `/root/.hermes/.env`** so all profiles that symlink there can authenticate. Alternatively, symlink all profile `.env` files to `blotter-ops/.env` once it is confirmed correct.
3. **After auth is fixed, re-run the digest manually** to verify all four profiles can write raw sections:
   ```bash
   /root/montanablotter/agent-queue/bin/run-digest.sh daily
   ```
4. **Re-run the three error'd Hermes cron jobs** to confirm they complete:
   ```bash
   hermes cron run f5c6f444c5c5
   hermes cron run efce83b0f921
   hermes cron run 3554e355988f
   ```

# Reasoning

The 401 error on `blotter-ops` suggests the `.env` key is invalid or blank. The openrouter hang on `blotter-ingest` was caused by a missing model block (now fixed) combined with a missing API key in the global `.env`. Unifying the key source and verifying validity will restore the fleet.

# Rollback

- Revert config changes: `git checkout -- /root/.hermes/profiles/*/config.yaml` (though these are not in a git repo, so keep the pre-change backups mentally — the changes were only model-block additions).
- If a new key breaks things, restore the previous `.env` content from memory or re-run `hermes setup`.

# Verification

- `blotter-ops chat -q "say ok"` returns "ok" without 401.
- `blotter-ingest chat -q "say ok"` returns "ok" without hanging.
- Daily digest raw sections appear in `/root/montanablotter/agent-queue/digests/raw/*/$(date +%F).md`.
- The three error'd cron jobs show `last_status: ok` after their next run.
