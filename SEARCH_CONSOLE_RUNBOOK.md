# Search Console Runbook

Use this after deploys to turn indexing into a measurable workflow instead of guessing.

## 1. Verify the property

Use a Google Search Console `Domain property` for `montanablotter.com`.

If DNS verification is not practical, use a `URL prefix` property for:

- `https://montanablotter.com/`

## 2. Submit the sitemap

Submit:

- `https://montanablotter.com/sitemap.xml`

The sitemap index is generated in [`app.py`](/root/montanablotter/app.py#L3243) and includes static, location, post, pattern, and blog sitemap segments.

## 3. Inspect the priority URLs first

Request indexing for the pages most likely to become search-entry pages:

- `https://montanablotter.com/`
- `https://montanablotter.com/counties`
- `https://montanablotter.com/cities`
- `https://montanablotter.com/jail-rosters`
- `https://montanablotter.com/warrants`
- `https://montanablotter.com/patterns`
- `https://montanablotter.com/blog`

Then inspect the strongest county and city pages:

- Flathead County
- Yellowstone County
- Missoula County
- Gallatin County
- Billings
- Missoula
- Bozeman
- Whitefish

Then inspect the best recent pages in each class:

- 5 recent `/post/<id>` pages
- 3 recent `/blog/<slug>` pages
- 3 county warrant pages
- 3 pattern pages

## 4. Watch the right Search Console reports

In `Performance`:

- Filter by `Search type: Web`
- Review `Queries` and `Pages`
- Sort by `Impressions`
- Look for pages with impressions but weak CTR or average position worse than 8

These are the best candidates for title, meta description, and internal-link improvements.

## 5. Use this weekly optimization loop

1. Export the top pages with impressions.
2. Group them into homepage, county, city, post, blog, warrant, roster, and pattern pages.
3. Improve only the pages already getting impressions.
4. Add 3-5 new internal links from stronger pages into those targets.
5. Re-request indexing only for materially changed pages.

## 6. Track technical issues

Check these reports weekly:

- `Pages` for `Crawled - currently not indexed`
- `Pages` for canonical mismatches
- `Enhancements` or `Rich results` for structured-data errors

If a template change causes widespread errors, inspect one live URL, fix the template, redeploy, and validate the fix in Search Console.

## 7. Page types to improve in this order

1. Homepage
2. County pages
3. City pages
4. Warrant and jail-roster hubs
5. Pattern pages
6. Blog posts
7. Individual daily report pages

## 8. What to ignore

Do not spend time chasing:

- rank-tracking vanity queries with no impressions
- bulk indexing requests for low-value pages
- title rewrites across the whole site without Search Console evidence
- “guaranteed #1” SEO tactics
