# RSS.app Ticker Embed — Homepage Design

## Goal

Add the RSS.app ticker at `https://rss.app/embed/v1/ticker/t9giebudER6tUys4` to the Montana Blotter homepage, below the site header and breadcrumb bar, visible on all screen sizes.

## Placement

- File: `templates/index.html`
- Location: inside `{% block content %}`, after the missing-person alert block and before the lead-report / blotter sections.
- Scope: homepage only. No other public pages are affected.

## Markup

- A full-width wrapper `<div class="mb-rss-ticker">` containing:
  - `<iframe src="https://rss.app/embed/v1/ticker/t9giebudER6tUys4" width="100%" frameborder="0" loading="lazy" title="Recent headlines"></iframe>`
- The iframe height will be set inline (e.g. `height="60"`) and mirrored in CSS `min-height` to prevent layout shift while the third-party content loads.

## Styling

- Add a `<style>` block inside the `{% block extra_head %}` of `templates/index.html`:
  - `.mb-rss-ticker` → `width: 100%`, `min-height` matching iframe height, `overflow: hidden`, subtle top/bottom border (`1px solid var(--border-default)`), background matches the page surface.
  - `.mb-rss-ticker iframe` → `display: block`, `width: 100%`, `border: 0`.
- Visible on all breakpoints. No mobile breakpoint hides it.
- Keeps the change scoped to the homepage template; no separate CSS file needs a cache-bust bump.

## Safety / Performance

- Confined to one template and one stylesheet.
- No backend routes, database schema, auth, paywall, or ingestion logic is changed.
- Uses `loading="lazy"` so the embed does not block the critical render path.
- The wrapper reserves vertical space with `min-height` to reduce cumulative layout shift.

## Verification

- Run `./venv/bin/python3 -m pytest` to ensure existing tests still pass.
- Curl `http://127.0.0.1:5000/` (or the local dev server) and confirm the iframe `src` appears in the response.
- Restart `montanablotter.service` after deploy.
- Spot-check on a narrow viewport to confirm no horizontal overflow.

## Rollback

- Revert `templates/index.html` and restart `montanablotter.service`.
