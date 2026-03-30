# Phase 1 — Foundation Design Spec
**Date:** 2026-03-30
**Project:** Montana Blotter
**Scope:** Web design foundation — Tailwind build, og:image, JSON-LD structured data

---

## Overview

Phase 1 fixes three technical gaps that underpin all future design improvements. None require backend changes — all work is in templates, static assets, and build tooling.

**Goals:**
- Eliminate Tailwind CDN runtime (~350KB JS saved per page load)
- Fix missing social share images (og:image/twitter:image) on all pages
- Add JSON-LD structured data to all remaining high-traffic public pages

---

## 1. Tailwind CSS Compiled Build

### Problem
Both `base.html` and `public_page_base.html` load Tailwind from `https://cdn.tailwindcss.com`. This is a ~350KB JavaScript bundle that parses HTML at runtime to generate CSS. Tailwind explicitly flags this as not suitable for production. It slows page load, blocks rendering, and inflates the page speed score penalty.

### Solution
Use the **Tailwind standalone CLI** (no Node.js required) to scan all templates and emit only the CSS classes actually used.

### Files changed
- `bin/tailwindcss` — Tailwind standalone binary (downloaded via `make install-tailwind`, gitignored — not committed due to ~50MB size)
- `tailwind.config.js` — content paths pointing at `templates/**/*.html`
- `static/css/main.css` — compiled output (committed to repo)
- `Makefile` — `make css` target that runs the CLI
- `templates/base.html` — replace CDN `<script>` with `<link rel="stylesheet" href="/static/css/main.css">`
- `templates/public_page_base.html` — same CDN replacement

### Build command
```bash
./bin/tailwindcss -i ./static/css/input.css -o ./static/css/main.css --minify
```

`static/css/input.css` contains only `@tailwind base; @tailwind components; @tailwind utilities;` plus any custom `@layer` overrides currently living in `<style>` blocks in the templates.

### Deployment
Add `make css` to the deploy checklist (or systemd `ExecStartPre`). Rebuild whenever templates change.

---

## 2. og:image — Branded Social Share Card

### Problem
`public_page_base.html` includes `og:title`, `og:description`, and `og:url` but has no `og:image` or `twitter:image`. Social shares on Facebook, Twitter/X, LinkedIn, and iMessage show a blank gray box instead of a preview card. This suppresses click-through rates on all shared links.

### Solution
Generate a single branded 1200×630px PNG using a one-time Python/Pillow script. Wire it into `public_page_base.html` with hardcoded absolute URL meta tags.

### Image design
- Background: `#0D0C0B` (site dark background)
- Headline: "The Montana Blotter" — large serif (Playfair Display or fallback DejaVu Serif TTF)
- Subhead: "Montana Police Blotter, Arrest Records & Jail Rosters"
- Accent bar: `#D4892A` (amber) — horizontal rule under the headline
- Small location label: "Great Falls, Montana" in IBM Plex Mono style (monospace TTF fallback)
- Output path: `static/images/og-default.png`

### Script
`scripts/generate_og_image.py` — run once, output committed to repo. Requires `Pillow` (already in venv via dependencies or added to `requirements.txt`).

### Template changes (`public_page_base.html`)
Add inside `<head>`, after existing og tags:
```html
<meta property="og:image" content="https://montanablotter.com/static/images/og-default.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:image" content="https://montanablotter.com/static/images/og-default.png">
```

`twitter:card` is already present in the template (`content="{{ twitter_card or 'summary_large_image' }}"`) — do not add a second tag.

---

## 3. JSON-LD Structured Data — Remaining Pages

### Problem
JSON-LD exists on 5 page types (homepage, post detail, annual roundup, case journey index, warrant county). The majority of high-traffic public pages have no structured data, missing out on Google rich results (breadcrumbs, article metadata, sitelinks).

### Solution
Add a `{% block jsonld %}{% endblock %}` slot to `public_page_base.html` just before `</head>`. Each template fills it with an inline `<script type="application/ld+json">` block using Jinja2 variables already available on that page.

### Pages and schema types

| Template | Schema Type | Key Fields |
|----------|-------------|------------|
| `arrests.html` | `CollectionPage` + `BreadcrumbList` | name, description, url |
| `jail_rosters.html` | `CollectionPage` + `BreadcrumbList` | name, description, url |
| `jail_bookings.html` | `CollectionPage` + `BreadcrumbList` | name, description, url |
| `court_case_detail.html` | `Article` + `BreadcrumbList` | headline, datePublished, url |
| `courts_index.html` | `CollectionPage` + `BreadcrumbList` | name, description, url |
| `blog.html` | `CollectionPage` + `BreadcrumbList` | name, description, url |
| `blog_post.html` | `Article` + `BreadcrumbList` | headline, datePublished, author, url |
| `charge_explainer.html` | `Article` + `BreadcrumbList` | headline, datePublished, url |
| `public_meetings.html` | `CollectionPage` + `BreadcrumbList` | name, description, url |
| `trends.html` | `WebPage` + `BreadcrumbList` | name, description, url |
| `patterns_hub.html` | `WebPage` + `BreadcrumbList` | name, description, url |
| County pages | `WebPage` + `BreadcrumbList` | name, description, url |
| City pages | `WebPage` + `BreadcrumbList` | name, description, url |

### BreadcrumbList pattern (consistent across all pages)
```json
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://montanablotter.com/"},
    {"@type": "ListItem", "position": 2, "name": "{{ page_title }}", "item": "{{ canonical_url }}"}
  ]
}
```

---

## Implementation Order

1. **Tailwind build** — foundation for all future template work; do first so compiled CSS is in place
2. **og:image script** — run once, commit PNG, add meta tags
3. **JSON-LD** — add `{% block jsonld %}` slot, then fill each template

---

## Testing

- **Tailwind:** Load the site locally, verify styles match CDN version. Check no missing classes by comparing before/after screenshots of homepage and admin panel.
- **og:image:** Use [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/) and [Twitter Card Validator](https://cards-dev.twitter.com/validator) to verify card renders correctly.
- **JSON-LD:** Use [Google Rich Results Test](https://search.google.com/test/rich-results) on each updated page. Confirm no errors or warnings.

---

## Files Created / Modified

| File | Action |
|------|--------|
| `bin/tailwindcss` | Downloaded via `make install-tailwind` (gitignored) |
| `tailwind.config.js` | Create |
| `static/css/input.css` | Create |
| `static/css/main.css` | Create (compiled output) |
| `Makefile` | Create (or update if exists) |
| `templates/base.html` | Modify (replace CDN script tag) |
| `templates/public_page_base.html` | Modify (replace CDN, add og:image tags, add jsonld block) |
| `scripts/generate_og_image.py` | Create |
| `static/images/og-default.png` | Create (generated) |
| 13 public templates | Modify (add jsonld block content) |
| `requirements.txt` | No change (Pillow 12.1.1 already present) |
