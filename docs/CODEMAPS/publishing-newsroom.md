# Code Map: Autonomous Newsroom — Publishing Pipeline

Path: `/root/montanablotter`  
Concise reference for how raw blotter records become blog posts, subscriber emails, charge-explanation pages, and curated digests.

---

## Module Map

| File | Responsibility |
|------|----------------|
| `services/publishing/news_planner.py` | Scans `records`/`jail_bookings` for newsworthiness and inserts ranked `story_candidates`. Enforces a daily creation cap. |
| `services/publishing/news_writer.py` | Takes approved/reviewed `story_candidates` and calls Claude (or fallback) to draft `blog_posts` as unpublished (`published=0`). |
| `services/publishing/news_editor.py` | Reviews drafted `blog_posts` + source candidates, writes `blog_draft_reviews`, and flips `published=1` only when checks pass. |
| `daily_blog_worker.py` | Orchestrates the candidate → draft → review → publish cycle in a single script. Idempotent daily driver. |
| `services/publishing/morning_briefing.py` | Sends the daily subscriber email digest at 7:00 am MT. Builds HTML per county preference, records `digest_runs`, and logs per-recipient status. |
| `services/publishing/weekly_digest.py` | Sends the weekly "Week in Review" subscriber digest on Mondays at 7:45 am MT, covering the 7-day window ending the most recent Sunday. |
| `services/publishing/daily_arrests_blotter.py` | Publishes a daily statewide narrative post: "What Happened in Montana Overnight" from jail bookings + recent records. |
| `services/publishing/weekly_top_calls.py` | Publishes a Sunday "Top 10 Police Calls of the Week" blog post. Idempotent via ISO-year/week slug. |
| `services/alerts/weekly_safety.py` | Per-county weekly public-safety trend reports using `services.blotter.analytics` + Claude. Runs Mondays 7:30 am. |
| `services/admin/charge_explainer.py` | Generates/staffs evergreen `charge_explainers` pages (statute references, plain-language explanations). |
| `blueprints/admin/blog.py` | Flask admin views for manually creating, editing, deleting, and templating `blog_posts`; reads/writes `blog_posts` directly. |

---

## Candidate → Draft → Review → Publish Flow

```text
records / jail_bookings
        │
        ▼
services/publishing/news_planner.py
        │ creates story_candidates (status='new')
        ▼
services/publishing/news_writer.py
        │ drafts blog_posts (published=0)
        ▼
services/publishing/news_editor.py
        │ writes blog_draft_reviews
        │ decision = approve | reject | revise
        ▼
    approved ──▶ published=1 (live)
    rejected ──▶ candidate/blog_post ignored or soft-deleted
    revise   ──▶ cycles back to writer
```

1. **Plan** — `news_planner.py` queries `records`, `jail_bookings`, and charge/trend signals. Each candidate gets a `dedupe_key` and `score`; the daily cap limits how many rows are inserted per calendar day.
2. **Draft** — `news_writer.py` fetches the highest-scoring `new`/`approved` candidates, loads supporting source records (via `blog_post_sources` and candidate facts), calls Claude, and saves an unpublished `blog_posts` row.
3. **Review** — `news_editor.py` audits each draft for sensitive terms, factual grounding, and tone, then inserts a `blog_draft_reviews` row with `decision`.
4. **Publish** — `daily_blog_worker.py` coordinates the loop and flips `published=1` on approved drafts. It also runs directly-idempotent scheduled posts (`daily_arrests_blotter` style) when configured.

---

## Key Tables

| Table | Purpose | Foreign Keys / Notes |
|-------|---------|----------------------|
| `blog_posts` | All publishable content: news stories, roundups, explainers | `slug` UNIQUE, `published` flag |
| `story_candidates` | Newsworthy raw ideas not yet drafted | `dedupe_key` UNIQUE, `status`, `score` |
| `blog_post_sources` | Source-material traceability for a published post | FK `blog_post_id` |
| `blog_draft_reviews` | Editorial decisions and reasoning per candidate/post pair | FKs to `blog_posts` and `story_candidates` |
| `digest_runs` | Morning/weekly email runs metadata | `status`, counts, `kind`, `target_date` |
| `digest_run_recipients` | Per-recipient send status within a run | FK `run_id` |
| `subscribers` | Email digest subscribers with county preferences and unsubscribe tokens | `email` UNIQUE |
| `charge_explainers` | Evergreen SEO pages for incident/charge types | `slug` UNIQUE, `incident_type` |

### `blog_posts` schema (selected columns)

- `id`, `title`, `slug`, `body`, `excerpt`, `author`
- `published` (0 = draft, 1 = live)
- `created_at`, `updated_at`

### `story_candidates` schema (selected columns)

- `id`, `candidate_type` ('news_story' default), `source_type`, `source_url`
- `headline_hint`, `facts_json`, `location_label`, `occurred_at`, `agency_name`
- `source_record_ids_json`, `dedupe_key` UNIQUE, `status`, `score`
- `created_at`, `updated_at`

### `blog_draft_reviews` schema

- `id`, `blog_post_id`, `story_candidate_id`, `decision`, `reason`, `evidence_json`
- `reviewed_at`, `reviewer_agent`

### `digest_runs` schema

- `id`, `kind`, `target_date`, `audience`, `status`
- `subject`, `preview_posts`, `preview_subscribers`, `sent_count`, `skipped_count`, `failed_count`
- `initiated_by`, `notes`, `created_by_user_id`, `started_at`, `finished_at`

### `charge_explainers` schema

- `id`, `incident_type`, `slug`, `title`, `body`, `excerpt`
- `statute_ref`, `charge_category`, `published`, `generated_by`, `view_count`

---

## Cron Schedule (from `crontab.txt`)

| Script | Schedule | Notes |
|--------|----------|-------|
| `daily_blog_worker.py` | `30 5 * * *` | Daily orchestrator; publishes one statewide post. Runs under `nice -n 19`. |
| `services.publishing.morning_briefing` | `0 13 * * *` | 13:00 UTC = 7:00 am MT daily subscriber digest. |
| `services.publishing.news_planner` | `15 */3 * * *` | Re-enabled 2026-06-11; throttled to every 3 hours. |
| `services.publishing.news_writer` | `25 */3 * * *` | Staggered after planner. |
| `services.publishing.news_editor` | `35 */3 * * *` | Staggered after writer. |
| `services.alerts.weekly_safety` | `30 7 * * 1` | Mondays 7:30 am MT, per-county trend posts. Defined in file header (not in committed `crontab.txt`). |
| `services.publishing.weekly_digest` | `45 7 * * 1` | Mondays 7:45 am MT. Defined in file header (not in committed `crontab.txt`). |
| `services.publishing.weekly_top_calls` | Sundays 8 pm MT | Header comment; not present in `crontab.txt`. |
| `services.publishing.daily_arrests_blotter` | Daily 7:10 am MT | Header comment for daily post; not present in `crontab.txt`. |

> Note: Some header-comment schedules are not reflected in the currently-committed `crontab.txt`. Verify production deployment status before expecting them to run.

---

## CLI Commands

### Newsroom agents

```bash
cd /root/montanablotter
python -m services.publishing.news_planner      # scan and create story candidates
python -m services.publishing.news_writer       # draft blog posts from candidates
python -m services.publishing.news_editor       # review/publish drafts
python daily_blog_worker.py                     # run full planning → publishing loop
python daily_blog_worker.py --dry-run           # preview only
```

### Subscriber digests

```bash
python -m services.publishing.morning_briefing --dry-run
python -m services.publishing.weekly_digest --dry-run
python -m services.publishing.weekly_digest --date YYYY-MM-DD
```

### Curated posts

```bash
python -m services.publishing.daily_arrests_blotter
python -m services.publishing.weekly_top_calls --dry-run
python services/alerts/weekly_safety.py           # all active counties
python services/alerts/weekly_safety.py --county Cascade
python services/alerts/weekly_safety.py --stdout
python services/alerts/weekly_safety.py --draft
python services/alerts/weekly_safety.py --force
```

### Charge explainers

```bash
python services/admin/charge_explainer.py       # generate missing explainers
python services/admin/charge_explainer.py --incident-type "DUI"
```

(There is no standalone `charge_explainer_worker.py`; use `services/admin/charge_explainer.py` directly.)

---

## Ops / Security Notes

- **API keys**: Claude/Anthropic calls require `ANTHROPIC_API_KEY` and check `config.USE_PAID_LLM`. Keep keys in `.env`; do not log prompts/responses at a verbosity that leaks private incident details.
- **Subscriber tokens**: `subscribers.token` is used for unsubscribe links. Treat tokens as unguessable secrets.
- **Email deliverability**: Morning/weekly digests use the same SMTP helper (`send_email` in `morning_briefing.py`). Monitor `digest_runs.failed_count` and `digest_run_recipients.error_message`.
- **Revision safety**: `--force` on `weekly_safety.py` overwrites existing `slug`, destroying prior analytics; use sparingly.
- **Sensitive term filtering**: News writer and editor share a block list (`sensitive terms`); rejected candidates should not be re-drafted without review.

---

## Gotchas

1. **Daily cap = 5** — `news_planner.py` refuses to insert more than five `story_candidates` per calendar day. If production needs bursts, raise the constant carefully and remember downstream writer capacity.
2. **Sensitive terms** — The writer/editor reject or rewrite content containing flagged words (e.g., certain violent, tragic, or identifying terms). Public-facing posts are intentionally factual and neutral.
3. **Idempotency** — Posts keyed on `slug` are not recreated if `slug` already exists (`weekly_top_calls.py`, `daily_arrests_blotter.py`, etc.). Re-running is safe and emits a skip message.
4. **Drafts are public only when `published=1`** — admin editing and the orchestrator must explicitly flip the flag. `news_writer.py` never publishes.
5. **Candidate/post mismatch** — A `story_candidate` may map to zero, one, or many `blog_draft_reviews`; always join on `(blog_post_id, story_candidate_id)` when auditing.
6. **`weekly_safety.py` requires categorized data** — Counties with `< MIN_RECORDS` (default 5) or no analytics baseline are skipped.
7. **Charge explainer table name** — Use `charge_explainers` (plural). No standalone worker module exists; the admin service + any admin page/blueprint that calls it drives the pipeline.
8. **Admin blog blueprint does not enforce the newsroom workflow** — `blueprints/admin/blog.py` can create/edit posts directly, bypassing candidates and draft reviews. Reserve for curated/templated content.
9. **Fallback content** — When Claude is unavailable, `daily_arrests_blotter.py` and `weekly_top_calls.py` produce structured plain-text fallback posts so the site still updates.
