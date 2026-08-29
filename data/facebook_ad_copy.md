# Facebook Ad Copy — Montana Blotter (montanablotter.com)

Produced for paid Facebook placements (Feed / Story / right-column).
All CTAs point at https://montanablotter.com — swap the landing path per campaign.
Brand voice: plain-spoken, public-first, Montana-specific, no paywall, no registration
on the public site. Numbers below are placeholders — fill from the DB before launching.

---

## V1 — Statewide coverage (primary)
**Angle:** biggest differentiator — all 56 counties, every day, free.

**Primary text (Feed)**
> Every police blotter in every Montana county. Every day. Free.
>
> Montana Blotter pulls and publishes daily blotter reports from all 56 counties so you don't have to dig through a dozen county websites. Arrests, incidents, warrants — searchable by county, city, or keyword.
>
> See what's happening in your county, or anywhere in the state:
> montanablotter.com

**Headline (≤40 chars)**
> All 56 Montana counties. One free site.

**Description (optional, Story / right-column)**
> Daily police blotters. No signup. Statewide.

**CTA button**
> Learn More  →  montanablotter.com

---

## V2 — County-specific / local angle
**Angle:** county pages are the highest-intent surface. Targets readers who know their county.

**Primary text**
> [County] County arrests, incidents, and blotter reports — updated daily.
>
> Montana Blotter publishes every county's police blotter in one place. Search [County] County, browse recent incidents, and follow links into jail rosters and warrant resources when they're available.
>
> montanablotter.com/counties

**Headline (≤40 chars)**
> [County] County blotter — daily, free

**Description**
> Recent incidents, arrest coverage, jail links

**CTA button**
> Visit Site  →  montanablotter.com/counties

*Tip: spin this per county with the top counties first — Yellowstone, Cascade, Missoula, Gallatin, Flathead.*

---

## V3 — Transparency / civic duty
**Angle:** appeals to the reader who cares about accountability, public records, openness. No-liberal-conspiracy framing — just the records.

**Primary text**
> Public records are public. Montana Blotter makes them easy to read.
>
> Daily police blotters from across Montana — published free, searchable by county, and updated every day. No login, no paywall, no hand-picked spin. Just the records, organized.
>
> montanablotter.com

**Headline (≤40 chars)**
> Montana's police blotters. Read them.

**Description**
> All 56 counties · Free · Updated daily

**CTA button**
> Learn More  →  montanablotter.com

---

## V4 — Daily habit / "know your state"
**Angle:** position Montana Blotter as a daily habit, like a morning briefing. Good for remarketing.

**Primary text**
> What happened in Montana yesterday? Here's where to find out.
>
> Montana Blotter publishes daily police blotter reports from all 56 counties. Check your county, browse statewide, or search by city — all free, all public records.
>
> montanablotter.com

**Headline (≤40 chars)**
> What happened in Montana yesterday?

**Description**
> 56 counties · Daily blotters · Free

**CTA button**
> See More  →  montanablotter.com

---

## V5 — Short-form (Story / small placements)
**Angle:** minimal copy for placements that reward brevity. Pair with a strong visual (county map, blotter header image, or a simple "56 counties" graphic).

**Variant A**
> All 56 Montana counties. Daily police blotters. Free.
> montanablotter.com

**Variant B**
> Montana Blotter: every county. every day. free.
> montanablotter.com/counties

**Variant C**
> Search Montana arrests and incidents — by county, by city.
> montanablotter.com

**Variant D**
> Public records. Readable. Free. Statewide.
> montanablotter.com

---

## Placeholder numbers to fill from the DB before launch

Pull these once and bake them into the copy where they fit:

- Counties covered / counties total (currently 5 covered, 56 total — use "all 56 counties" framing regardless; the coverage page shows the real status)
- Recent incident volume: `SELECT COUNT(*) FROM posts WHERE created_at >= datetime('now', '-7 days')`
- Top 3 counties this week: grouped count from posts
- Any DUI / warrant / domestic stat that fits the campaign angle

Example with real numbers:
> 2,847 incidents logged across Montana this week. 56 counties. One free site. montanablotter.com

---

## Notes

- **Keep the hashtags out of ad copy.** Hashtags belong in organic posts (`#Montana #MontanaBlotter #PublicSafety`). Ads don't benefit from them and they eat character budget.
- **All CTAs point at montanablotter.com.** If a campaign needs a narrower landing (e.g. a specific county page or the coverage page), swap the path — but keep the root domain as the primary unless the test says otherwise.
- **Creative pairing:** the repo already has Unsplash town images wired up for organic Facebook posts (`facebook_publisher.py`). For ads, a clean county map, a blotter header, or a simple "56 counties" graphic will beat a generic town photo — but that's a creative call, not copy.
