# Chat Notes

This file stores session notes so I can reference prior requests and decisions in future turns.

## 2026-03-05

- User reported homepage issue: "filters" panel taking full screen and hiding blotters.
- I updated `templates/index.html` to make sidebar layout/toggle behavior independent of Tailwind utility availability:
  - Added explicit `.home-layout`, `.home-sidebar`, `.home-feed` CSS.
  - Added explicit `#sidebar-content` open/closed behavior via `is-open` class.
  - Updated sidebar toggle JS with responsive `matchMedia` syncing.
- Restarted service: `systemctl restart montanablotter.service`.
- Service check returned: `active`.
- User requested persistent notes file for chat context and self-reference.
- User selected roadmap option `1` (Facebook autopost MVP).
- Implemented Facebook autopost MVP:
  - Added DB tables: `app_settings`, `facebook_post_queue`.
  - Added publisher engine: `facebook_publisher.py` (queue, dedupe, template render, Graph API publish, runner).
  - Added worker script: `facebook_worker.py`.
  - Added admin UI route/page: `/admin/facebook` with settings, queue controls, and publish actions.
  - Added quick queue action from blotters page and dashboard link.
  - Added auto-queue hook in `summarizer.py` for newly generated posts.
  - Added cron schedule for worker in `crontab.txt` and installed it with `crontab /root/montanablotter/crontab.txt`.
- Verified:
  - Python compile checks passed for updated modules.
  - Migration completed and new tables exist.
  - Worker runs and returns `facebook_disabled` until settings are configured.
  - Service restart completed; status `active`.
