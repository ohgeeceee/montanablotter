#!/usr/bin/env python3
"""Seed Montana Blotter blog with evergreen, professionally-written posts.

These posts give the blog a human "crime news & public-safety info" voice
instead of only the daily auto-roundups. Idempotent by slug: rerunning
updates the existing row rather than duplicating it.

Run from the repo root:
    venv/bin/python3 scripts/maintenance/seed_blog_posts.py

This only INSERTs/UPDATEs rows in blog_posts. It does not touch blotter.db
schema or any source tables.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import config

DB_PATH = config.DB_PATH
AUTHOR = "Montana Blotter Staff"

POSTS = [
    {
        "slug": "what-is-a-police-blotter-montana-guide",
        "title": "What Is a Police Blotter? A Montana Reader's Guide",
        "primary_category": "guide",
        "tags": ["police blotter", "public records", "montana law", "transparency"],
        "excerpt": "A blotter is the running log of calls, arrests, and incidents a Montana agency records each day. Here's what it is, what it isn't, and how to read one.",
        "body": """## A blotter is the day's paper trail

In Montana, a **police blotter** is the chronological log a law-enforcement
agency keeps of what happened on its watch: traffic stops, welfare checks,
assault calls, theft reports, arrests, and the hundreds of smaller contacts
that make up a working day. Some Montana agencies publish theirs as a PDF.
Others post it to a web portal. A few still only keep it internally.

The blotter is not a conviction. It is not a judgment. It is a record of a
*contact* — often a single moment in a much longer story.

## What a blotter usually contains

- **The date and time** of the call or incident
- **A location**, usually a jurisdiction or general area rather than a street address
- **A call type or incident code** (for example, "traffic stop" or "disturbance")
- **A short narrative** written by the officer or dispatcher
- **An outcome**, when one is recorded (cited, arrested, referred, unfounded)

## What a blotter is *not*

This part matters, because blotters get misread:

- It is **not** proof someone committed a crime. Many entries are reports, not arrests.
- It is **not** a complete picture of a person. One line in a blotter says nothing about someone's life.
- It is **not** curated for fairness. It is raw operational data, written at speed.

## How to read one responsibly

When you look at a Montana blotter, read it the way a reporter would. Note the
call *type* before the name. Wait for court records before drawing conclusions.
And remember that the people in these logs are presumptively innocent — the
blotter is the start of a paper trail, not the end of a story.

Montana Blotter aggregates blotters from across the state so you can see the
pattern, not just the incident. That aggregate view is the whole point: a single
log tells you what happened on one shift in one county; the combined record
tells you what Montana's public-safety picture actually looks like.
""",
    },
    {
        "slug": "montana-crime-trends-2026-what-blotters-show",
        "title": "Montana Crime Trends in 2026: What the Blotters Actually Show",
        "primary_category": "analysis",
        "tags": ["crime trends", "data", "jail bookings", "montana 2026"],
        "excerpt": "We pulled 30 days of booking data across Montana's counties. The pattern is less about one 'bad county' and more about where the people are.",
        "body": """## The raw numbers, last 30 days

Pulling the most recent 30 days of jail-booking intake across Montana, the
volume breaks down the way you'd expect from population, not from any single
hotspot:

- **Hill County** — 732 new bookings
- **Yellowstone County** — 452
- **Missoula County** — 301
- **Silver Bow County** — 286
- **Ravalli County** — 181
- **Flathead County** — 174

The list tracks Montana's larger population centers closely. That is the first
lesson of reading this data: booking counts mostly measure *where people live*,
not *where crime is uniquely bad*.

## Why "most active" can mislead

A county that logs the most bookings is often just the county with the most
residents, the busiest highway, or the largest university. Gallatin County's
daily rounds frequently show high call volume for exactly that reason — Bozeman
is growing fast, and growth shows up in the blotter.

So when a headline says a county "led" Montana in law-enforcement activity, the
honest follow-up is: *led among whom, and compared to what?* Per-capita rates
tell a different story than raw counts, and we publish both views so readers
can decide.

## What the trend actually suggests

Across Montana's blotters, the steady drivers are familiar: traffic stops,
welfare checks, behavioral-health calls, and property crimes. The data does not
support a single dramatic narrative. It supports a quieter, more useful one —
that most Montana law-enforcement contact is routine, recurring, and tied to
community size.

That is the value of aggregating blotters instead of reading one. The noise
clears, and the signal — population-driven, repetitive, mostly non-violent —
comes into focus.
""",
    },
    {
        "slug": "why-montana-blotter-publishes-records",
        "title": "Why Montana Blotter Publishes Police Records (And What We Never Show)",
        "primary_category": "editorial",
        "tags": ["editorial", "transparency", "privacy", "public records"],
        "excerpt": "We publish what government already collects in public. We redact what the law and basic decency say to keep private. Here is the line we draw.",
        "body": """## Public records are already public

Montana Blotter exists on a simple principle: the records we publish were
already public the moment a government agency logged them. A police blotter
posted to a county website is, by definition, an open record. A jail roster
published by a sheriff's office is, by definition, an open record.

Our job is not to *create* that transparency. It is to *gather* it — across 56
counties, in one place, in a form a normal person can actually read.

## What we publish

- Blotter entries and incident summaries released by Montana agencies
- Jail booking rosters posted by county sheriffs and detention centers
- Warrant lists published by the state or by counties
- Court and missing-person records already available to the public

None of this is secret. We are a mirror, not a source.

## What we never show

Transparency has a limit, and we honor it on purpose:

- **We redact victims** in sensitive cases, especially domestic violence and sexual assault. Naming a victim is never news; it is harm.
- **We do not publish home addresses** or other details that invite harassment over a public record.
- **We do not editorialize** about a person's guilt. The blotter is the start of a story, not the verdict.
- **We honor removal requests** through our human-review suppression process, because a public record and a person's dignity are not the same thing.

## Why this matters in Montana

In a state where the nearest courthouse can be an hour away and local government
is spread thin, public records are how citizens check power. When those records
are scattered across dozens of county PDFs and portals, "public" becomes
theoretical. Gathering them in one readable place makes "public" real.

We will keep publishing the record. We will also keep drawing the line where
the law — and basic decency — says to stop.
""",
    },
    {
        "slug": "how-to-read-a-montana-arrest-record",
        "title": "How to Read a Montana Arrest Record (Charges, Bonds, and What 'Booking' Means)",
        "primary_category": "guide",
        "tags": ["arrest record", "booking", "bond", "charges", "public records"],
        "excerpt": "An arrest record is a snapshot, not a sentence. Here's how to parse the charges, the bond, and the difference between being booked and being convicted.",
        "body": """## Booking is paperwork, not a verdict

When someone is **booked** into a Montana jail, the agency is recording a moment:
a person was taken into custody and processed. That processing produces an
*arrest record* — a set of fields that look official because they are, but that
say nothing about guilt. In Montana, everyone in the booking log is presumed
innocent until a court says otherwise.

## The fields, decoded

- **Booking number** — the agency's internal ID for that intake. It is not a case number.
- **Charges summary** — what the person was arrested for, written in short form. "Possession" is not a conviction; it is an allegation.
- **Bond amount** — the dollar figure set for release pending court. A high bond is not a finding of danger; it is a court's risk calculation.
- **Arresting agency** — the office that made the contact (a county sheriff, a city police department, or a state agency).
- **Booking status** — often "in custody," "released," or "transferred."

## Charges vs. convictions

This is the single most misread part of any arrest record. A charge is an
*accusation*. A conviction is a *finding*, made later by a judge or jury. The
blotter shows the first; it cannot show the second, because the second has not
happened yet.

When you read an arrest record on Montana Blotter, read the charge as a
question the court will answer — not as an answer already given.

## Why the bond matters to Montana readers

Bond amounts reveal how a county balances flight risk against the cost of
holding someone. A $0 bond means the person was released on their own promise to
appear. A six-figure bond signals the court saw a higher risk. Neither number
tells you whether the person did what they are accused of — only how the system
handled the wait.

Reading arrest records well is a civic skill. The more Montanans can tell a
booking from a conviction, the less likely a single bad day in someone's life
becomes a permanent public verdict.
""",
    },
    {
        "slug": "montana-56-counties-blotter-tracking",
        "title": "Montana's 56 Counties: How We Track Every Blotter and Jail Roster",
        "primary_category": "guide",
        "tags": ["56 counties", "coverage", "jail rosters", "montana", "transparency"],
        "excerpt": "Montana has 56 counties, each with its own way of publishing public-safety records. Here's how we pull them into one searchable place.",
        "body": """## Fifty-six different front doors

Montana is one state with **56 counties**, and almost every one publishes its
public-safety records a little differently. Some post a daily blotter PDF. Some
run a web portal with a search box. A few publish nothing online at all and
rely on in-person requests. The result is a patchwork: the same public
information, behind 56 different doors.

## What we track

Across the state, Montana Blotter aggregates:

- **Jail bookings** — roughly 12,000 intake records collected so far, from county detention centers and sheriffs' offices
- **Blotter and incident logs** — the day-to-day call records agencies release
- **Warrant lists** — active warrants published by counties and the state
- **Court and missing-person records** — already-public case and alert data

In total, the database holds more than **56,000 individual records**, each one
tied back to the county that published it.

## Why one place beats 56

If you want to know what happened in your county last week, the county's own
site is fine. If you want to know how your county compares to the next one, or
what Montana's pattern looks like as a whole, 56 separate portals do not help.
That is the gap we fill: same records, one search, one map, one timeline.

## The honest limitation

Coverage is only as good as what each agency releases. Where a county publishes
nothing, we have nothing to show — and we say so. Transparency is a two-way
street: we can gather and present, but the agency has to open the door first.
Our coverage map shows exactly where the light is on and where it is not.
""",
    },
    {
        "slug": "montana-active-warrants-what-the-data-shows",
        "title": "4,141 Active Warrants: What Montana's Outstanding-Warrant Data Actually Tells Us",
        "primary_category": "analysis",
        "tags": ["warrants", "data", "montana", "analysis"],
        "excerpt": "Montana Blotter currently tracks 4,141 active warrants. That number sounds alarming. Here's what it does and does not mean.",
        "body": """## The headline number

Montana Blotter currently tracks **4,141 active warrants** across the state.
On its face, that is a large figure. But like most criminal-justice statistics,
the number only means something once you know what a warrant is — and what it is not.

## What a warrant is

A warrant is a court order authorizing law enforcement to take an action:
usually an arrest, sometimes a search. An *active* warrant means the order is
still open — the person named has not been served, has not appeared, or the
matter was never closed out.

Common reasons a warrant stays active:

- A missed court date (a "bench warrant")
- A citation that was never resolved
- A charge filed but the person not yet located
- An older matter that fell through the cracks and was never recalled

## What the number does not mean

A warrant count is **not** a count of dangerous people. Many active warrants in
Montana are for failures to appear on low-level matters — a missed traffic
hearing, an unpaid fine that escalated. They accumulate quietly, year after year,
and the total grows not because crime is spiking but because old orders are
rarely cleared.

## Why we publish them

Warrant lists are public records, and an open warrant is something a person
often does not even know exists until a traffic stop surfaces it. Publishing the
list does not shame anyone; it lets people check their own name, clear a matter
they forgot about, and avoid a worse surprise later.

The 4,141 figure is a reminder of how much unfinished business sits in the
system — not a scoreboard of danger. Read it that way.
""",
    },
    {
        "slug": "request-or-correct-your-montana-public-record",
        "title": "How to Request or Correct Your Montana Public Record",
        "primary_category": "guide",
        "tags": ["public records", "records request", "montana law", "correction"],
        "excerpt": "Found something wrong about you in a Montana public record? Here's the practical path to request it, correct it, or ask for it to be suppressed.",
        "body": """## Public does not mean permanent-or-wrong

Montana public records are assembled by humans, and humans make mistakes. A
misspelled name, a duplicate booking, a resolved case that still shows as open
— these happen. The good news: you are not powerless, and you have more than one
avenue.

## Step 1: Get the record

Start by pulling the actual document. Most Montana counties publish blotters and
rosters online; court records live with the clerk of court for the county where
the case was filed. Montana Blotter aggregates many of these in one search, which
is often the fastest way to see what is out there about you.

## Step 2: Ask the source to fix it

If the underlying record is wrong, the fix belongs at the source. Contact the
agency that published it — the county sheriff, the detention center, or the court
clerk — and request a correction. Keep it specific: "Booking #X lists the wrong
date of birth; please amend."

## Step 3: Ask for suppression where the law allows

Some records should not be public at all, or should be redacted. Montana Blotter
offers a **human-reviewed name-suppression** process: you request removal, a
person reviews it, and on approval your name is redacted across bookings,
warrants, and related posts. It is a one-time review, not an automatic erase, and
it exists precisely because a public record and a person's dignity are not the
same thing.

## A practical note

Do not assume a record is permanent just because it is online. Agencies correct
errors more often than people realize, and the request path is shorter than it
looks. The hardest part is usually just starting the email.
""",
    },
    {
        "slug": "sheriff-vs-city-police-montana",
        "title": "Sheriff's Office or City Police? What the Difference Means in Montana",
        "primary_category": "editorial",
        "tags": ["sheriff", "police", "montana", "government", "explainer"],
        "excerpt": "In Montana you'll meet both a county sheriff and a city police chief. They are not the same job. Here's why the distinction shows up in the blotter.",
        "body": """## Two badges, two jurisdictions

Drive across Montana and you will run into two kinds of local law enforcement.
The **county sheriff** covers the whole county — the towns, the rural roads, the
unincorporated stretches where no city police exist. The **city police
department** covers only the city limits of the town it serves.

That split is why a single county can have a sheriff's office *and* three or four
city departments, each with its own blotter, its own roster, and its own way of
publishing.

## Why it shows up in the records

When you read Montana Blotter, the **arresting agency** field tells you who made
the contact. A booking attributed to the "Cascade County Sheriff's Office" covers
a different footprint than one attributed to the "Great Falls Police Department" —
even though both operate in and around the same city.

This matters for accountability. A sheriff is an elected county official;
a city police chief is usually appointed by the city council or mayor. Different
bosses, different mandates, same public records.

## Why Montanans should care

In a rural state, the sheriff is often the only law-enforcement presence for
hundreds of square miles. Understanding who answers for what — and which agency
published the record you are reading — is the difference between blaming the
right office and blaming the wrong one.

When we label a record with its arresting agency, we are not being pedantic. We
are showing you exactly who was responsible for that moment in the log.
""",
    },
    {
        "slug": "missing-persons-montana-alert-system",
        "title": "Missing Persons in Montana: How the Alert System Works",
        "primary_category": "guide",
        "tags": ["missing persons", "amber alert", "montana", "public safety"],
        "excerpt": "When someone goes missing in Montana, a layered system kicks in. Here's how alerts escalate — and why public attention is the loudest tool we have.",
        "body": """## When someone disappears

When a person is reported missing in Montana, the response is not one size fits
all. It scales with risk. A low-risk adult who is simply out of contact is handled
differently from a child abduction or a vulnerable senior with dementia. The
system is built to escalate, not to alarm.

## The layers

- **Local report** — a missing-person report filed with the agency that has jurisdiction (city police or county sheriff).
- **NCIC entry** — the national crime-information system that lets any agency in the country see the person is missing.
- **Silver Alert** — used for missing seniors or people with cognitive conditions, broadcast to the public.
- **AMBER Alert** — reserved for suspected abductions of children where the danger is immediate.

Each layer widens the audience. A local report is known to one agency; an AMBER
Alert is known to everyone with a phone.

## Why publication helps

Montana Blotter carries missing-person records that agencies have already made
public. The value is repetition and reach: a notice posted once on a county site
is easy to miss. The same notice, aggregated and searchable, reaches the people
most likely to have seen something.

## The honest caveat

A missing-person record is a snapshot. Status changes fast — a person is found,
a case is closed — and our data only reflects what has been reported and
published. If you have information on any open missing-person case, the right move
is to contact the listing agency directly, not to speculate publicly.

Public attention is the loudest search tool there is. Used carefully, it brings
people home.
""",
    },
    {
        "slug": "montana-court-records-case-number-guide",
        "title": "Court Records 101: What a Montana Case Number Actually Means",
        "primary_category": "guide",
        "tags": ["court records", "case number", "montana courts", "explainer"],
        "excerpt": "Montana Blotter tracks roughly 420 court cases. Behind each one is a case number that encodes the court, the year, and the type. Here's how to read it.",
        "body": """## A case number is a filing address

Montana Blotter currently tracks about **420 court cases** pulled from public
court sources. Behind each one sits a case number that looks random but is
actually structured — a filing address for a specific court, year, and matter.

## How to read the pieces

While formats vary by court, a Montana case number typically tells you:

- **Which court** heard it — a justice court, a district court, or a city court, each with its own prefix
- **The year** it was filed
- **A sequence number** showing where it fell in that court's docket that year

So a case number is less a label and more a coordinate: it points to exactly one
folder, in one courthouse, in one year.

## Criminal vs. civil

Many of the cases we track are criminal — a charge filed by the state against a
person. But court dockets also include civil matters, family cases, and
probate. The case type field is what separates them, and it is worth checking
before you assume a case is about a crime.

## Why court records close the loop

A blotter tells you someone was contacted. A booking tells you they were taken
into custody. A **court record** tells you what happened next — the charge, the
plea, the outcome. Without it, the story stops at the arrest. With it, you can
see whether the allegation became a conviction, a dismissal, or something in
between.

That is why we track court cases alongside blotters and bookings. The arrest is
the beginning; the docket is where the question gets answered.
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
    print(f"blog seed complete: created={created} updated={updated}")


if __name__ == "__main__":
    seed()
