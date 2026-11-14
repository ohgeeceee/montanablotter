# Blog Team — mtblotter.com

Companion to `docs/CODEMAPS/publishing-newsroom.md`. Defines the human-in-the-loop team that
develops and operates the Montana Blotter blog, and the backlog they own.

## Roles

| Role | Owner | Responsibility |
|------|-------|----------------|
| Content Lead | Jon | Editorial direction, approvals, Red-tier sign-off |
| Planner | `news_planner` agent | Scan records/jail_bookings, score newsworthiness, insert `story_candidates` (cap 5/day) |
| Writer | `news_writer` agent | Draft `blog_posts` (published=0) from candidates via Claude/fallback |
| Editor | `news_editor` agent | Review drafts, write `blog_draft_reviews`, flip published=1 on approve |
| Frontend | blotter-dev | Blog templates, SEO, accessibility, mobile layout |
| Ops | blotter-ops | Cron health, LLM key, digest delivery, error triage |
| Civic | blotter-civic | County outreach, press mentions, FAQ updates |

## Pipeline (existing)

records / jail_bookings → planner → writer → editor → published post
plus daily_blog_worker.py orchestrator, morning/weekly digests, weekly_top_calls,
daily_arrests_blotter, weekly_safety alerts, charge explainers.

## Team backlog (initial)

1. Add unit tests for news_planner dedupe + daily cap.
2. Add unit tests for news_editor decision paths (approve/reject/revise).
3. Improve blog index SEO (meta, OG tags, canonical).
4. Add RSS/Atom feed for /blog.
5. Add per-county blog archive pages.
6. Improve fallback article variety (more openers).
7. Monitor LLM spend; add cost logging to news_writer.
8. Add admin UI for story_candidate triage.

## Working rules

- All code changes are Red-tier: draft as PR, Jon merges.
- Agents never publish without editor approval.
- Sensitive-term block list shared between writer and editor.
- Idempotent slugs: re-runs skip existing posts.
