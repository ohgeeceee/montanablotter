# /attorneys — Data Sources & Editorial Standard

The `/attorneys` page is a public directory of Montana defense attorneys
by county. As of 2026-08-02 it surfaces three kinds of entries:

1. **Sponsored listings** — paid (Silver / Gold) attorney entries submitted
   through `attorney_sponsored_claims`. Editorial review required.
2. **Statewide resources** — public agencies and non-profits (State Bar
   referral, MLSA, MontanaLawHelp.org, Courthouse Help Centers, Crime Victims
   Ombudsman, plus the State Public Defender's office). Free, public
   source. Appears in every county bucket via the `["*"]` coverage
   sentinel in the `attorney_referrals.counties` JSON column.
3. **Regional Public Defender offices** — eight offices (one main + seven
   regional) covering 38 of 56 Montana counties. Seeded by
   `seed_attorney_resources.py`. Re-running the seed is idempotent — it
   adds only new rows, never deletes.

## What was added in the 2026-08-02 rebuild

- New JSON column `attorney_referrals.counties` (added in `init_db.py`
  via the existing idempotent-migration pattern). Default `'[]'`. Use
  `["*"]` for statewide coverage, otherwise a list of county names.
- Backfill: existing rows with `counties == '[]'` get their legacy
  `county` value as a single-element list. Statewide rows get `["*"]`.
- Regional offices seeded into the table — see
  `seed_attorney_resources.py` for the canonical list. Each entry has
  a documented `counties_served` list (the counties that office
  serves, per publicdefender.mt.gov).
- Route `/attorneys` rewritten in `app.py` to expand statewide coverage
  into every county bucket, deduplicate by `name`, and render all 56
  counties alphabetically. By-the-numbers card now reports regional /
  counties / statewide separately so users aren't misled by the
  "every county" duplication.
- Template `templates/attorneys.html` reorganized: statewide resources
  get a dedicated top section; each county gets a regional section
  + a collapsed list of statewide resources that apply to that county.
- Card markup extracted to `templates/partials/_attorney_card.html`
  for reuse.

## Source policy

Every entry this rebuild adds came from one of:

- **publicdefender.mt.gov** — Office of the State Public Defender
  (statewide + 7 regional offices). Public agency phone numbers from
  the agency's published directory.
- **montanabar.org** — State Bar of Montana Find-a-Lawyer referral
  (existing entry, not changed).
- **mtlsa.org / montanalawhelp.org** — Montana Legal Services
  Association (existing entries, not changed).
- **Supreme Court Self-Represented Litigants Project / Courthouse
  Help Centers** (existing entries, not changed).
- **Crime Victims Ombudsman** (existing entry, not changed).

**No fabricated private attorney listings were added.** The
"38 counties covered" figure reflects only real regional public
defender offices, not invented attorney names.

## Counties with no regional office listed

18 counties have no dedicated regional office on the page yet. They
fall into two groups:

1. Small-population rural counties that the 7 regional offices
   likely service by travel but for which we do not have a
   documented public-regional-office name to list:
   Beaverhead, Big Horn, Blaine, Carter, Custer, Deer Lodge,
   Fallon, Fergus, Hill, Judith Basin, Liberty, Phillips, Powder
   River, Rosebud, Valley, Wheatland.

2. Counties that have *no* regional office in the public record
   because they're serviced through the Helena (main) office or
   by travel. For these the page surfaces the Statewide Resources
   block + a call-to-action linking to the State Bar referral.

When an editor verifies a regional office's coverage and gets
permission, add it via:

```sql
INSERT INTO attorney_referrals
    (county, name, firm, phone, website, practice_areas,
     blurb, is_active, sort_order, sponsored, sponsor_tier, counties)
VALUES
    ('Lewis and Clark', 'Public Defender — [Town] Office',
     'Montana State Public Defender', '(406) 555-0100',
     'https://publicdefender.mt.gov', 'Criminal defense — [district]',
     'Regional office covering ...', 1, 99, 0, NULL,
     '["Lewis and Clark", "Adjacent", ...]');
```

The `counties` JSON list is the source of truth — keep it short
and verifiable.

## Editorial standard

- **No fabricated entries.** A row that can't be sourced is not added.
  The page is willing to show "No regional office listed · statewide
  resources available" rather than fake a name.
- **No scanned scrapes.** State Bar / MLSA / PD data only if it's
  open public-record data we have permission to mirror. Paid placements
  go through the lawyer-ads funnel.
- **Honest counts.** The by-the-numbers card separates regional
  vs. statewide so users can see at a glance how complete the directory
  is. The 38/56 figure is real and surfaced as a fact, not hidden.
- **Statewide resources get visible badges.** Every county bucket
  shows the statewide resources under a `+ Show N statewide resources
  that apply to [County]` disclosure so users can choose to expand
  it without scrolling past them.
- **Disclaimer preserved.** The right-side disclaimer points users
  to the State Bar referral as a backup, makes clear this isn't legal
  advice, and notes that listings are opt-in / self-reported.

## Verification

After the rebuild, the page renders at:

```
HTTP 200 / 56 counties alphabetically / 1 statewide panel / 0 template errors
```

HTML smoke test (no live POST):

```bash
curl -sL http://127.0.0.1:5000/attorneys | grep -c 'public-detail-title'   # 57 (statewide + 56 counties)
curl -sL http://127.0.0.1:5000/attorneys | grep -c 'No regional office'   # 18
curl -sL http://127.0.0.1:5000/attorneys | grep -cE 'Traceback|Server Error'  # 0
```

Visual smoke test (headless Chrome at 1280 and 390 viewports) lives
in `/tmp/atty_v*.png` after each redeploy.
