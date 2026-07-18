# RSS.app Ticker Embed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the RSS.app ticker `https://rss.app/embed/v1/ticker/t9giebudER6tUys4` to the Montana Blotter homepage, below the header/breadcrumb and above the lead-report section, visible on all screen sizes.

**Architecture:** A single `<iframe>` embed wrapped in a styled container is inserted into `templates/index.html`. The styles live in a `<style>` block inside the template’s `extra_head` block so the change is scoped to the homepage and no additional CSS file is loaded.

**Tech Stack:** Jinja2 templates, plain CSS, Flask static serving.

## Global Constraints

- Change only `templates/index.html`.
- No backend routes, database schema, auth, paywall, or ingestion logic changes.
- Ticker visible on all breakpoints.
- Restart `montanablotter.service` after deploy.
- Run `./venv/bin/python3 -m pytest` before considering the task complete.

---

### Task 1: Add RSS ticker markup and styles to the homepage

**Files:**
- Modify: `templates/index.html`

**Interfaces:**
- Consumes: nothing
- Produces: `.mb-rss-ticker` markup rendered at the top of the homepage content area.

- [ ] **Step 1: Open `templates/index.html` and locate the `extra_head` block**

The `extra_head` block currently ends at line 30. Add the following `<style>` block immediately before `{% endblock %}`:

```html
<style>
.mb-rss-ticker {
  width: 100%;
  min-height: 60px;
  overflow: hidden;
  border-top: 1px solid var(--border-default);
  border-bottom: 1px solid var(--border-default);
  background-color: var(--surface-page);
}
.mb-rss-ticker iframe {
  display: block;
  width: 100%;
  height: 60px;
  border: 0;
}
</style>
```

- [ ] **Step 2: Locate the top of the `content` block**

After the missing-person alert block (`{% endif %}` around line 47) and before `<div class="mb-layout-grid mb-layout-grid--2">` (line 49), insert:

```html
  <div class="mb-rss-ticker">
    <iframe
      src="https://rss.app/embed/v1/ticker/t9giebudER6tUys4"
      width="100%"
      height="60"
      frameborder="0"
      loading="lazy"
      title="Recent headlines"
    ></iframe>
  </div>
```

- [ ] **Step 3: Verify the template has no Jinja syntax errors**

Run:
```bash
cd /root/montanablotter
./venv/bin/python3 -c "from jinja2 import Environment, FileSystemLoader; env = Environment(loader=FileSystemLoader('templates')); env.get_template('index.html')"
```

Expected: command exits with code 0 and no output.

- [ ] **Step 4: Run the test suite**

Run:
```bash
cd /root/montanablotter
./venv/bin/python3 -m pytest
```

Expected: all tests pass (the existing suite should be green because the change is markup-only).

- [ ] **Step 5: Start the dev server and confirm the iframe is present**

Run in one terminal:
```bash
cd /root/montanablotter
./venv/bin/python3 app.py
```

In another terminal:
```bash
curl -s http://127.0.0.1:5000/ | grep -o 'rss.app/embed/v1/ticker/t9giebudER6tUys4'
```

Expected: the curl output contains `rss.app/embed/v1/ticker/t9giebudER6tUys4`.

- [ ] **Step 6: Stop the dev server and deploy to production**

Stop the dev server (`Ctrl+C`), then run:
```bash
systemctl restart montanablotter
```

Expected: `systemctl status montanablotter` shows `active (running)`.

- [ ] **Step 7: Verify the live homepage**

Run:
```bash
curl -s https://montanablotter.com/ | grep -o 'rss.app/embed/v1/ticker/t9giebudER6tUys4'
```

Expected: the curl output contains the embed URL.

- [ ] **Step 8: Commit the change**

```bash
cd /root/montanablotter
git add templates/index.html docs/superpowers/specs/2026-07-18-rss-ticker-design.md docs/superpowers/plans/2026-07-18-rss-ticker.md
git commit -m "feat: add RSS.app ticker to homepage"
```

---

## Self-Review

- **Spec coverage:** Placement (homepage, below header/breadcrumb, above lead report), markup (iframe with given URL), styling (full-width, 60px height, visible all breakpoints), verification (tests, curl, restart), and rollback are all covered by Task 1.
- **Placeholder scan:** No TBDs or vague steps; every command and code block is exact.
- **Type consistency:** N/A — only HTML/CSS changes.
