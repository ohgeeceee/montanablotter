#!/usr/bin/env python3
"""Batch 2 of evergreen Montana Blotter blog posts (guide / analysis / editorial).

Companion to scripts/maintenance/seed_blog_posts.py. Same idempotent-by-slug
behavior: rerunning updates existing rows instead of duplicating. All figures
cited are grounded in real production aggregates pulled from config.DB_PATH
(30-day booking counts by county, active warrants, total records, current
bookings, missing-person rows) on 2026-08-31. No statute numbers are cited
because those must be verified against the current MCA before publication.

Run from repo root:
    venv/bin/python3 scripts/maintenance/seed_blog_posts_batch2.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import config

DB_PATH = config.DB_PATH
AUTHOR = "Montana Blotter Staff"

# Verified aggregates (2026-08-31, config.DB_PATH = data/blotter.db):
#   30-day new bookings by county (top): Hill 732, Yellowstone 452, Missoula 302,
#     Silver Bow 286, Ravalli 181, Flathead 175, Garfield 147, Lewis and Clark 125
#   active warrants tracked: 4,141 | total records: 56,579
#   current jail bookings (is_current=1): 1,873 | missing-persons rows: 586

POSTS = [
    {
        "slug": "montana-protective-order-guide",
        "title": "Protective Orders in Montana: A Plain-English Guide",
        "primary_category": "guide",
        "tags": ["protective order", "domestic violence", "montana law", "guide", "safety"],
        "excerpt": "A protective order is a court's instruction to stay away. In Montana there are several kinds, each with its own trigger. Here's how they work and where to start.",
        "body": """## A court tells someone to stay away

A **protective order** is a civil order from a Montana court directing a person to
stay away from, or stop contacting, someone else. It is one of the most common
tools Montana families encounter through the justice system, and it is also one of
the most misunderstood.

## The main kinds in Montana

- **Order of protection** — generally tied to domestic or family relationships, often filed alongside a dissolution or custody matter.
- **Temporary restraining order (TRO)** — short-term relief granted quickly, pending a hearing.
- **Sexual or stalking violence protective order** — available even without a domestic relationship, for survivors of stalking or sexual assault.

Each has its own filing path and standard of proof. The exact forms and thresholds
change, so the right move is to start with the clerk of court in the county where
you live or where the respondent is.

## What an order does

When a court issues the order, violating it can become a separate criminal matter.
That is the teeth: the order is not just a piece of paper, it is enforceable, and
law enforcement can act on a knowing violation.

## Where Montana Blotter fits

We do not publish protective-order filings raw. When a related incident appears in
a blotter, we apply our strict victim-redaction rules — no victim name, no
address, no speculation. The public value of the record is that a response happened;
the survivor's identity is not part of that.

If you need one, do not wait for the blotter to tell you. Contact your county court
or a Montana legal-aid service. The order exists to keep you safe, and the process
is built to move quickly.
""",
    },
    {
        "slug": "montana-record-expungement-sealing",
        "title": "Can You Erase a Montana Arrest Record? Expungement and Sealing",
        "primary_category": "guide",
        "tags": ["expungement", "record sealing", "montana law", "arrest record", "guide"],
        "excerpt": "Montana does allow some records to be sealed or expunged — but the rules are narrow. Here's the honest outline of what can and cannot be cleared.",
        "body": """## Most records stay

The blunt truth: in Montana, most arrest and conviction records do **not** go away
on their own. An arrest shows up in a blotter the day it happens, and unless
something legal happens afterward, it stays searchable. That is why understanding
the clearance paths matters.

## What Montana allows

Montana law provides limited pathways to seal or expunge certain records. The
general shape:

- **Acquittals and dismissals** — a charge that ended without a conviction is the
  strongest candidate for clearance, because there is no public safety finding to preserve.
- **Certain marijuana offenses** — offenses that have since been decriminalized or legalized
  can qualify for relief under newer statutory provisions.
- **Some juvenile records** — handled under a separate, more protective track than adult records.

The specific eligibility, waiting periods, and filing steps shift with the statute,
so treat the above as a map, not a manual. Verify the current MCA section with a
Montana attorney or the clerk of court before relying on it.

## What does not clear easily

A conviction that resulted in a finding of guilt generally stays. Pardons and
governor's clemency are a separate, rarer path. And a record that is *sealed* is
not the same as *destroyed* — certain agencies may still see it.

## Our role

Montana Blotter aggregates what agencies publish. When a record is legally
suppressed or sealed at the source, we honor that through our human-reviewed
name-suppression process. But we cannot erase a record a court has not cleared. The
fix for an unfair record lives at the court, not on our site.
""",
    },
    {
        "slug": "montana-search-warrant-vs-arrest-warrant",
        "title": "Search Warrant vs. Arrest Warrant in Montana: What the Difference Means",
        "primary_category": "guide",
        "tags": ["warrants", "search warrant", "arrest warrant", "montana law", "explainer"],
        "excerpt": "Montana Blotter tracks 4,141 active warrants — but not all warrants are the same. A search warrant and an arrest warrant authorize two different things.",
        "body": """## Two orders, two purposes

Montana Blotter currently tracks about **4,141 active warrants** across the state.
That single number hides two very different court orders, and the difference
matters for how to read any warrant list.

## An arrest warrant

An **arrest warrant** authorizes law enforcement to take a named person into
custody. It is issued when a judge finds probable cause that the person committed
an offense, or when someone misses a required court appearance (a bench warrant).
Most of the warrants in our tracker are arrest warrants — many of them for missed
court dates on low-level matters, not fresh crimes.

## A search warrant

A **search warrant** authorizes law enforcement to search a specific place for
specific evidence. It names a location and a thing to look for; it does not name a
person to be arrested. You will rarely see search warrants in a public blotter
because they describe a private place, and publishing them can compromise an
investigation.

## Why the distinction shows up in the record

When you see a warrant on Montana Blotter, it is almost always an **arrest** warrant
tied to a named individual and an open matter. The list is a public record, and an
open warrant is often something the person does not even know exists until a routine
traffic stop surfaces it.

That is the point of publishing the list: not to shame anyone, but to let people
check their own name, clear a matter they forgot about, and avoid a worse surprise
later. An arrest warrant is a court's open question — not a finding of guilt.
""",
    },
    {
        "slug": "montana-tribal-jurisdiction-public-records",
        "title": "Tribal Lands and Montana Public Records: A Complicated Map",
        "primary_category": "editorial",
        "tags": ["tribal", "jurisdiction", "montana", "public records", "editorial"],
        "excerpt": "Montana has seven Indian reservations and a layered justice system. Here's why a blotter from the Blackfeet or Crow Nation is not the same animal as a county sheriff's log.",
        "body": """## More than one justice system

Montana is not a single law-enforcement map. The state holds 56 counties, and
layered across them are **seven Indian reservations**, each with its own tribal
government and, in most cases, its own tribal court and police. When you read
"Montana public safety," you are reading at least two systems at once.

## Why the records differ

A county sheriff's blotter and a tribal police log are published under different
authorities. Tribal records often follow tribal-court rules about what is public,
and those rules are not the same as Montana's county-level presumptive-open
standard. The result:

- Some reservations publish robust public logs; others publish little or nothing online.
- A single incident can touch tribal, county, state, and federal jurisdiction at once — a "cross-deputization" reality that complicates who logs what.
- Federal agencies (BIA, FBI) may hold records that never appear in a county blotter.

## What this means for aggregation

Montana Blotter aggregates what government agencies actually publish. Where a tribe
publishes an open blotter, we can carry it. Where it does not, we have nothing to
show — and we say so. We do not manufacture tribal records to fill a map gap,
because that would be both inaccurate and disrespectful of sovereignty.

The honest takeaway: Montana's public-safety picture has blind spots by design.
The reservation boundary is one of them. A complete record would require every
jurisdiction to open its door — and not all have.
""",
    },
    {
        "slug": "montana-court-outcome-after-charge",
        "title": "What Happens After the Charge: How a Montana Case Actually Ends",
        "primary_category": "analysis",
        "tags": ["court", "conviction", "dismissal", "montana", "analysis"],
        "excerpt": "A blotter shows the arrest. A court record shows the ending. Most Montana cases don't end the way the headline implies — here's the range of outcomes.",
        "body": """## The blotter is the opening, not the story

When Montana Blotter publishes a booking, what you are seeing is the **start** of a
process, not its result. The charge alleges; the court resolves. And the range of
resolutions is wider than most readers assume.

## The ways a case ends

Without citing case-level stats (those vary county to county), the common outcomes
in Montana's dockets are:

- **Dismissal** — the charge is dropped, sometimes for lack of evidence, sometimes as part of a deal.
- **Deferred or suspended sentence** — the person pleads or is found guilty, but sentencing is paused or softened on conditions (often probation).
- **Conviction with a sentence** — jail, fine, or both.
- **Acquittal** — a judge or jury finds the state did not meet its burden.
- **Diversion** — a pre-trial path that can end with the charge cleared on completion.

The point: a large share of contacts in the blotter never become a clean
conviction. Many become a deferred sentence, a dismissal, or a resolution that
looks nothing like the arrest narrative.

## Why we track court records separately

Montana Blotter carries court records alongside blotters and bookings precisely
because the arrest and the outcome live in different places. Reading only the
booking log lets you see who was contacted; reading the docket lets you see what
actually happened next.

If you look up a name on our site, look for both. The booking tells you the moment;
the docket tells you the answer. Anyone who draws a conclusion from the first
without checking the second is reading half the story.
""",
    },
    {
        "slug": "montana-juvenile-records-privacy",
        "title": "Juvenile Records in Montana: Why You Rarely See a Minor in the Blotter",
        "primary_category": "guide",
        "tags": ["juvenile", "privacy", "montana law", "records", "guide"],
        "excerpt": "Montana law treats a minor's record differently from an adult's. Here's why you almost never see a teenager named in a Montana blotter — and what that protection means.",
        "body": """## Minors are handled apart

When a Montana law-enforcement record involves a **juvenile** — generally someone
under 18 — the rules change. The state treats a minor's contact with the system as
a matter of rehabilitation, not public spectacle, and the records reflect that.

## What gets protected

Montana's confidential-records provisions keep most juvenile proceedings out of the
public view:

- The minor's **name and identifiers** are not published the way an adult's are.
- Juvenile **court files** are restricted, with narrow exceptions for serious offenses.
- Even when a matter is serious enough to be public, the identifiers are usually shielded.

This is why, when you scroll Montana Blotter, you will see adults named (as the
arresting records publish them) but minors almost never. The absence is by design.

## Why this matters

The policy rests on a simple idea: a mistake made at 16 should not be a permanent,
searchable verdict at 30. Public accountability for agencies still exists — the
*contact* can be reflected in aggregate counts — but the child's identity is
protected so the record does not define the rest of their life.

Montana Blotter applies this automatically. Where a source record names a minor, we
redact. Where the law closes the file, we do not carry it. Transparency about the
system and privacy for the child are not the same question — and the line is drawn
at the child.
""",
    },
    {
        "slug": "how-to-find-montana-criminal-defense-lawyer",
        "title": "How to Find a Montana Criminal-Defense Lawyer (Without Panicking)",
        "primary_category": "guide",
        "tags": ["lawyer", "attorney", "montana", "defense", "guide", "resources"],
        "excerpt": "If you've been booked in Montana, the clock on your defense starts immediately. Here's a calm, practical path to finding a criminal-defense attorney.",
        "body": """## The moment after booking

If you or someone you know has been booked into a Montana jail, the most useful
thing to do next is boring: find a lawyer. The public defender handles those who
qualify; everyone else needs to choose. Here is a practical path that does not
require a law degree.

## Start with the bar

The **State Bar of Montana** runs a lawyer referral service. It will point you to
attorneys by practice area and location. For criminal defense, you want someone
whose practice is mostly that — not a generalist who dabbles.

## What to look for

- **Local experience** — an attorney who regularly appears in the county where the charge was filed knows the court, the prosecutors, and the habits of the bench.
- **A clear fee conversation up front** — defense is almost always flat-fee or hourly; make sure you understand which before you sign.
- **No guarantee of outcome** — anyone who promises a specific result is selling something.

## The free directory vs. paid placement

Montana Blotter carries a **free, opt-in directory of licensed Montana attorneys** at
/attorneys — a neutral place to start, no cost to the lawyer or to you. For firms
that want a featured, county-targeted placement with tap-to-call and lead capture,
there is a separate paid program at /advertise/lawyers. Both link to the same goal:
helping a Montanan reach a qualified attorney at the moment they need one.

## A note on urgency

You do not have to hire in the booking room. But the earlier a defense attorney is
involved, the more they can do — from advising on statements to catching a
procedural error while it is still fixable. Start the search the day of the booking,
not the day before court.
""",
    },
    {
        "slug": "montana-30-day-booking-volume-by-county",
        "title": "Montana's Booking Volume, County by County: A 30-Day Snapshot",
        "primary_category": "analysis",
        "tags": ["bookings", "data", "counties", "montana", "analysis"],
        "excerpt": "We pulled the last 30 days of jail-booking intake across Montana. The spread tracks population centers closely — and says more about where people live than where 'crime' is worst.",
        "body": """## The last 30 days, by the numbers

Pulling the most recent 30 days of new jail-booking intake from Montana Blotter's
aggregated records, the top counties by volume are:

- **Hill County** — 732 new bookings
- **Yellowstone County** — 452
- **Missoula County** — 302
- **Silver Bow County** — 286
- **Ravalli County** — 181
- **Flathead County** — 175
- **Garfield County** — 147
- **Lewis and Clark County** — 125

(Counts reflect first-seen intake in the window; a single person can appear more
than once if re-booked.)

## What the order tells you

The list tracks Montana's population centers and busiest corridors almost exactly.
Hill County's high number reflects both population and the role of its regional
detention center; Yellowstone (Billings) and Missoula are the state's largest
cities; Silver Bow (Butte) and Ravalli sit on major highways.

This is the first lesson of reading booking data: **raw counts mostly measure where
people are**, not where "crime" is uniquely bad. A county with more residents, a
busy interstate, or a large university will log more contacts — full stop.

## The honest caveat

These are *intake* numbers, not conviction numbers. A good share of daily intake is
people returning through probation or pretrial supervision, not fresh offenses. And
the total sits against roughly **1,873 people currently in Montana jails** and
**56,579 total records** in the system — a reminder that the daily churn is the
visible tip of a much larger, steadier population.

Read the county list as a map of where Montana lives and polices, not as a
scoreboard of danger.
""",
    },
    {
        "slug": "montana-missing-persons-what-the-data-shows",
        "title": "586 Missing-Person Records: What Montana's Data Actually Tells Us",
        "primary_category": "analysis",
        "tags": ["missing persons", "data", "montana", "public safety", "analysis"],
        "excerpt": "Montana Blotter carries 586 missing-person records drawn from public sources. That number is a starting point for searches, not a verdict on risk.",
        "body": """## The headline figure

Montana Blotter currently carries about **586 missing-person records** aggregated
from public sources, including state and county listings. On its face that is a
sobering count. Like most criminal-justice statistics, it only means something once
you know what the records are — and what they are not.

## What a missing-person record is

Each entry is a notice: a person reported missing, with whatever detail the source
published — name, last-seen date, county, and sometimes a physical description. The
records span a wide range:

- **Adults who simply lost contact** — often resolved quietly when the person reappears
- **Seniors with cognitive conditions**, where a Silver Alert was issued
- **Children**, some under AMBER Alert for suspected abductions
- **Long-term cases** that remain open in the system year after year

## What the number does not mean

A missing-person count is **not** a count of danger or of foul play. Many entries
resolve within days — a miscommunication, a phone left uncharged, a planned trip no
one knew about. The total grows not because Montana is uniquely perilous but because
old cases stay open until formally closed.

## Why we publish them

Missing-person notices are public records, and the value of publishing is reach.
A notice posted once on a county site is easy to miss; the same notice, aggregated
and searchable, reaches the people most likely to have seen something. Public
attention is the loudest search tool there is.

The 586 figure is a reminder of how much unfinished human business sits in the
system — not a measure of threat. If you have information on any open case, the
right move is to contact the listing agency directly, not to speculate publicly.
""",
    },
    {
        "slug": "what-active-means-montana-blotter",
        "title": "What 'Active' Means on Montana Blotter (And Why It Matters)",
        "primary_category": "editorial",
        "tags": ["active", "records", "montana", "transparency", "editorial"],
        "excerpt": "You'll see 'active' on warrants, bookings, and missing persons. The word does real work — it means the record is open, not resolved. Here's why that distinction is the whole point.",
        "body": """## A small word that carries weight

Scroll Montana Blotter and you will meet the word **active** constantly: active
warrants, active bookings, active missing persons. It is not decoration. It is the
single most important status flag we carry, because it separates an *open* matter
from a *closed* one.

## What 'active' means by record type

- **Active warrant** — the court order is still open; the person has not been served, has not appeared, or the matter was never closed.
- **Active booking** — the person is currently in custody (is_current = 1), not a historical intake.
- **Active missing person** — the case is still open; no resolution has been published.

When a record stops being active, it means something resolved: the warrant was
served or recalled, the person was released or transferred, the case was closed.

## Why the distinction is the point

Transparency is only useful if it is *current*. A warrant list full of orders that
were quietly resolved years ago would mislead more than it informs. Marking records
active vs. resolved is how we keep the public looking at what is actually open
right now.

It also protects people. An old arrest that shows as "active" when it was dismissed
is a harm — it implies a live matter that does not exist. Our job is to reflect the
source's current status, not a stale snapshot.

## The honest limit

We can only show what the publishing agency reports. If a county clears a warrant
but never updates its public list, we cannot know. That is why we pair every
aggregate with its source and date: the "active" flag is only as fresh as the last
time the agency opened the door. Read it as "open as of the last publication" — not
"open forever."
""",
    },
]


def seed() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    created = updated = 0

    for p in POSTS:
        existing = conn.execute(
            "SELECT id FROM blog_posts WHERE slug = ?", (p["slug"],)
        ).fetchone()
        tags_json = json.dumps(p["tags"], ensure_ascii=False)
        if existing:
            conn.execute(
                """UPDATE blog_posts
                   SET title=?, body=?, excerpt=?, author=?, primary_category=?,
                       tags_json=?, published=1, updated_at=?
                   WHERE id=?""",
                (p["title"], p["body"], p["excerpt"], AUTHOR,
                 p["primary_category"], tags_json, now, int(existing["id"])),
            )
            updated += 1
            continue
        conn.execute(
            """INSERT INTO blog_posts
               (title, slug, body, excerpt, author, published,
                primary_category, tags_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            (p["title"], p["slug"], p["body"], p["excerpt"], AUTHOR,
             p["primary_category"], tags_json, now, now),
        )
        created += 1

    conn.commit()
    conn.close()
    print(f"blog seed batch 2 complete: created={created} updated={updated}")


if __name__ == "__main__":
    seed()
