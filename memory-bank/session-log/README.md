# Session Log

One file per work session: `YYYY-MM-DD-<agent>-<slug>.md` (e.g.
`2026-11-19-kimi-memory-bank-setup.md`). Copy the template below.
Keep entries short — detail belongs in commits and PRs. Archive anything older than
~90 days into `session-log/archive/`.

---

# TEMPLATE (copy below this line)

```markdown
# Session: <short title>

- **Date:** YYYY-MM-DD
- **Agent:** <hermes|codex|kimi-code|openclaw|claude|human+tool>
- **Scope:** <files/services touched>

## Goal
<what this session set out to do>

## Done
- <shipped / changed, with commit refs if any>

## Not done / blockers
- <what remains, why>

## State changes
- <services restarted, cron changed, DB migrated, env touched — or "none">

## Handoff notes
- <what the next agent must know; point to decisions.md/gotchas.md entries you added>
```
