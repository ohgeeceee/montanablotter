---
profile: ops|ingest|dev|civic
created: 2026-05-15T03:42:00-06:00
tier: green|yellow|red
status: open
priority: low|med|high
related_county: ""
related_files: []
---

# Summary

One sentence. What is this item and why is it in the queue.

# Observation

What did the fleet see that triggered this item? Include the actual log lines, metric values, file paths, or query results — not paraphrases. Timestamps in MT.

# Proposed action

Concrete. Pick the format that fits:

- **Code change** → unified diff inline, plus a note on which branch
- **Email draft** → full text, recipient, subject
- **Command** → exact command(s), expected output
- **Config change** → before/after with line numbers

If multiple options were considered, list them and say which is recommended and why.

# Reasoning

Why this is the right action. What we ruled out and why.

# Rollback

How to reverse this action if it goes wrong. Specific commands or file moves. "Revert the commit" is not enough — say which commit and what side effects to clean up.

# Verification

How the fleet (or Jon) will confirm the action worked. Specific check: a URL returns 200, a county's record count rises above N, a draft email got a reply, etc.

# Disposition

(Filled in by Jon when closing this item.)

- Outcome: approved | rejected | deferred
- Date: 
- Notes: