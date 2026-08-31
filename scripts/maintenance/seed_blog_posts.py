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
    {
        "slug": "montana-probation-violations-what-they-mean",
        "title": "Probation Violations: The Charge Behind Montana's Busiest Court Dockets",
        "primary_category": "analysis",
        "tags": ["probation", "violations", "court", "montana", "analysis"],
        "excerpt": "Scroll Montana's booking logs and one charge shows up again and again: probation violation. Here's why it dominates, and what it actually means.",
        "body": """## The charge you see most

Read enough Montana booking records and a pattern emerges fast. Alongside drug
and theft charges, one entry recurs more than almost any other: **probation
violation**. It is not a new crime in the street sense. It is a claim that
someone already on supervision broke the rules of that supervision.

## What a probation violation is

When a Montana court sentences someone to probation instead of jail, it attaches
conditions: check in with a probation officer, stay sober, keep a job, avoid
certain people. A **probation violation** means the state says one of those
conditions was broken. The original charge may have been resolved months or
years earlier — the violation is about the supervision, not a fresh offense.

Common triggers we see in the logs:

- A missed check-in with the probation officer
- A failed or skipped drug test
- A new arrest while still on supervision
- Leaving the county without permission

## Why it fills the dockets

Probation violations are a large share of Montana's jail intake because the
system is self-reinforcing. A person on probation lives under constant
monitoring; any slip — even a missed appointment — can become a new booking.
That is different from someone who was never on supervision and simply goes about
their life.

It also means the raw booking count overstates how much *new* crime is
happening. A good chunk of daily intake is people returning through a door they
were already standing in.

## What readers should take from this

When you see "probation violation" in a blotter, read it as a supervision failure,
not necessarily a dangerous act. It tells you about how Montana manages the people
it has already sentenced — and how easily that management turns back into a
booking. The number is a window into the supervision system, not just the crime rate.
""",
    },
    {
        "slug": "montana-bail-bond-how-it-works",
        "title": "How Bail and Bonds Work in Montana (Without the Movie Nonsense)",
        "primary_category": "guide",
        "tags": ["bail", "bond", "montana", "court", "explainer"],
        "excerpt": "Bail in Montana rarely looks like the movies. Here's how a bond actually gets set, what a bondsman does, and why some people walk free immediately.",
        "body": """## Bail is not a fine

A common misunderstanding: people think bail is a punishment you pay and that's
the end of it. It is not. **Bail** is a promise — money held so that a person
shows up for court. If they appear, the money comes back (minus any fees). If
they skip, the court keeps it.

## How a bond gets set

After a booking, a judge or court officer sets a **bond amount**. That number
reflects a risk call: how likely is this person to flee, and how serious is the
charge? A $0 bond means "own recognizance" — the person is released on their
word. A high bond means the court wanted a real financial lock.

## Where a bondsman fits

Most Montanans cannot post a $5,000 or $20,000 bond in cash. That is where a
**bail bondsman** comes in: you pay the bondsman a non-refundable fee (often
around 10%), and they post the full bond for you. You walk out; they carry the
risk. If you skip, they lose the money and will try to find you.

## What this means on the ground

A high bond is not a finding of guilt. It is a holding decision made while the
case is pending. And a low or zero bond is not a slap on the wrist — it is often
a recognition that the person is low-flight-risk and that holding them costs the
county more than releasing them would.

When you read a bond figure in a Montana Blotter record, you are seeing a single
moment of risk management — not a verdict, and not a punishment.
""",
    },
    {
        "slug": "lewis-and-clark-flathead-warrants-deep-dive",
        "title": "Why Lewis and Clark and Flathead Lead Montana's Active Warrant Count",
        "primary_category": "analysis",
        "tags": ["warrants", "lewis and clark", "flathead", "montana", "analysis"],
        "excerpt": "Two counties — Lewis and Clark and Flathead — account for most of Montana's 4,141 active warrants. The reason is about population and process, not danger.",
        "body": """## Where the warrants concentrate

Of the **4,141 active warrants** Montana Blotter currently tracks, two counties
stand out: **Lewis and Clark** (around 2,000) and **Flathead** (around 1,580).
Together they make up the bulk of the statewide total. If you only skimmed the
headline number, you might read that as "those are the dangerous places." The
data says otherwise.

## Population explains part of it

Lewis and Clark County is home to Helena, the state capital, and a sizable
population. Flathead County wraps Kalispell and one of Montana's fastest-growing
regions. More residents means more court activity, more citations, and — naturally
— more warrants when those citations go unresolved.

## Process explains the rest

Both counties run active, well-maintained warrant systems that publish their
lists. That matters more than people realize: a county that diligently tracks and
posts warrants will *look* like it has more, simply because it is counting them.
A county that lets old warrants sit in a drawer unseen has fewer "active" ones in
any public count — not because it is safer, but because it is quieter about the
backlog.

## The honest read

The warrant concentration in Lewis and Clark and Flathead is mostly a story of
size and diligence, not threat. It is a useful reminder that any single aggregate
number inherits the habits of the agencies that report it. We publish the lists so
residents can clear old matters — not so anyone can scoreboard a county.
""",
    },
    {
        "slug": "funniest-police-blotter-entries-montana",
        "title": "The Funniest Things We've Seen in Montana Police Blotters",
        "primary_category": "editorial",
        "tags": ["human interest", "blotter", "montana", "lighthearted"],
        "excerpt": "Not every blotter entry is grim. Some are plainly human. A look at the lighter side of Montana's public-safety logs — names changed, dignity intact.",
        "body": """## Blotters are human, not just serious

Most of what shows up in a Montana police blotter is exactly what you'd expect:
traffic, theft, disturbances. But read enough of them and you start to see the
human comedy underneath the official language. Officers write these logs at 2
a.m., after a long shift, and occasionally the dry facts tell a story all their
own.

## A few that stuck with us

We are not here to mock anyone by name — these are composites drawn from the
*style* of entries we see, with no real person attached:

- The caller who reported a "suspicious looking cloud" parked too long in a
  church lot. It was a van. With a very committed nap inside.
- The theft report for "one (1) inflatable reindeer, clearly the better one of
  the pair." The other reindeer was reportedly unharmed.
- The welfare check that resolved when the subject answered the door holding a
  sandwich and asked if everything was okay, because the commotion seemed
  unusual for a Tuesday.
- The burglary call placed *by* the person who had locked themselves out, then
  remembered they were the homeowner mid-explanation.

## Why we share this

Montana Blotter exists to publish public records, not to embarrass. But the
lighter entries are a reminder that behind every line is a person having an
ordinary, messy, sometimes absurd day — same as the rest of us.

The blotter is the public's record. That includes the funny parts. We just keep
the names out of it.
""",
    },
    {
        "slug": "montana-dui-traffic-stops-what-happens",
        "title": "What Happens After a DUI Traffic Stop in Montana",
        "primary_category": "guide",
        "tags": ["dui", "traffic stop", "montana", "arrest", "explainer"],
        "excerpt": "A DUI stop is one of the most common entries in Montana blotters. Here's the step-by-step of what actually happens, from the shoulder to the booking.",
        "body": """## The most common late-night call

Drive any Montana highway on a weekend and the blotter will show it: DUI stops
are among the most frequent impaired-driving contacts in the state. Here is what
the record usually represents, step by step.

## From the shoulder to the station

1. **The stop** — an officer pulls a vehicle for a moving violation or a cue
   (lane drift, late-night speed). Field observations start the record.
2. **The assessment** — the officer may ask the driver to do roadside tasks or
   take a breath test. Refusal has its own legal consequence in Montana.
3. **The arrest** — if impairment is found, the person is taken into custody.
   This is the moment a *booking* is created.
4. **The booking** — fingerprint, photo, charge entry, and a bond set for
   release pending court.
5. **The court date** — the blotter ends; the docket begins.

## What the blotter does and does not show

A DUI entry in Montana Blotter shows the arrest and the charge. It does **not**
show the outcome — whether the person was convicted, took a deferred sentence, or
was acquitted. That part lives in the court record, which we track separately.

## Why this matters to readers

DUI is one of the few charges where the *stop* itself generates a public record
almost immediately. That is why you see so many of them, and why it is worth
waiting for the court record before drawing any conclusion about a person. The
blotter is the moment of contact; the docket is the answer.
""",
    },
    {
        "slug": "montana-small-town-policing",
        "title": "Small-Town Policing in Montana: One Officer, One Blotter",
        "primary_category": "editorial",
        "tags": ["small town", "rural", "montana", "policing", "editorial"],
        "excerpt": "In much of Montana, law enforcement means a single officer covering hundreds of square miles. Here's what that looks like in the public record.",
        "body": """## The rural reality

Montana is a big, empty state in most of its miles. In dozens of towns, the
entire police force is one or two people — sometimes a town marshal who also
answers the code-enforcement calls. The county sheriff covers everything the
town does not.

When you read a blotter from one of these places, the volume is small. A handful
of entries a week, if that. But each one carries more weight locally than a
busy city log ever could, because everyone in town knows the names.

## What the record shows

In a small Montana community, the public record is intimate. A DUI, a dispute, a
theft — it is all visible to neighbors. That visibility is the point of public
records: in a town with no newspaper and no TV station, the blotter *is* the local
news.

## The trade-off

The upside is accountability — a small department cannot easily hide what it
does. The downside is exposure for the people in the log, who cannot disappear
into a crowd the way they might in Billings or Missoula.

Montana Blotter publishes these records the same way we publish the big-city
ones, because the law does not grade transparency by population. But we apply the
same redaction rules everywhere: victims protected, addresses out, dignity
preserved. Small town or big city, the line is the same.
""",
    },
    {
        "slug": "montana-public-records-law-explained",
        "title": "Montana's Public Records Law, in Plain English",
        "primary_category": "guide",
        "tags": ["public records law", "montana law", "transparency", "guide"],
        "excerpt": "Montana has one of the stronger open-records traditions in the country. Here's the plain-English version of what the law actually says.",
        "body": """## A constitutional right to know

Montana's constitution is unusually direct about this: it says the public has the
right to know what its government is doing. That single clause underpins almost
everything Montana Blotter publishes. The records are not released as a favor.
They are released because the public is legally entitled to them.

## What counts as a public record

Broadly, any record an agency makes or keeps in the course of its work — reports,
rosters, warrants, meeting minutes, emails in some cases — is presumptively
public. The default is *open*.

## The exceptions that matter

The law is not absolute. Records can be withheld for reasons like:

- **Personal privacy** — a genuine invasion risk, not mere embarrassment
- **Ongoing investigations** — material that would compromise a case
- **Victim identities** — especially in sensitive crimes
- **Security details** — that would put people in danger

The key word is *presumptively*. The agency must justify closing a record, not
the public justify opening it.

## Why this is your right, not ours

Montana Blotter is a convenience, not a source of the right. You could request
any of these records yourself, from the agency that holds them, often for free or
a small copy fee. We aggregate them so you do not have to file 56 separate
requests. But the entitlement is yours — written into the state constitution.
""",
    },
    {
        "slug": "montana-jail-overcrowding-pretrial",
        "title": "Pretrial Detention in Montana: Why Some Await Trial in Jail",
        "primary_category": "analysis",
        "tags": ["pretrial", "jail", "montana", "analysis", "bond"],
        "excerpt": "A lot of Montana's jail population is not convicted of anything. They are waiting. Here's the pretrial piece of the booking picture.",
        "body": """## Waiting is most of the population

One of the least-understood facts about Montana jails: a large share of the
people inside have not been convicted. They are **pretrial** — awaiting a court
date, unable (or not allowed) to post bond. The booking log captures the moment
they went in; it says nothing about what a jury later decides.

## Why people stay in

Several forces keep a person in jail before trial:

- A **bond they cannot afford**, even a modest one
- A **hold from another case** or jurisdiction
- A **court's risk finding** that release was not appropriate
- Simply **timing** — rural courts sit less often, so waits stretch

None of these is a verdict. They are logistics and risk calls made while the case
is pending.

## What the data hides

Because blotters only show the intake, a reader can mistake a pretrial detainee
for a convicted person. They are not the same, and the law is explicit that the
presumption of innocence applies until a finding. The docket — which we track
alongside bookings — is where that question gets resolved.

## Why it matters

Montana's counties pay to house pretrial detainees, and those detainees lose work,
housing, and stability while they wait. The booking number is the visible tip; the
pretrial wait is the part the public rarely sees. Reading both together is the
only honest way to understand the jail.
""",
    },
    {
        "slug": "montana-seasonal-crime-patterns",
        "title": "Do Montana Crime Patterns Actually Follow the Seasons?",
        "primary_category": "analysis",
        "tags": ["seasonal", "trends", "montana", "analysis", "data"],
        "excerpt": "Do crimes rise in summer and fall in winter in Montana? The blotters suggest the truth is quieter — and tied to weather and tourism more than anything.",
        "body": """## The intuition

Ask anyone and they will tell you: crime goes up in summer. Longer days, more
people outside, more opportunity. It is a tidy story. The Montana blotters
partly support it — and partly complicate it.

## What the logs show

Across the records we aggregate, a few seasonal threads appear:

- **Summer tourism** in gateway towns (Columbia Falls, West Yellowstone,
  Whitefish) pushes call volume up — more visitors means more contacts, not
  necessarily more "crime."
- **Winter** drops outdoor calls but shifts some activity indoors — domestic
  and welfare checks persist year-round.
- **Holiday periods** show bursts of theft and DUI, then a quick return to baseline.

## The honest caveat

Most Montana law-enforcement contact is routine and repeats regardless of season:
traffic, welfare checks, property crimes. The seasonal swings are real but small
next to the steady baseline. A county's busiest month is usually explained by a
single event — a festival, a storm, a highway project — more than by the calendar.

## What readers should take

If you want a dramatic seasonal crime wave, the data will disappoint you. Montana's
blotters tell a steadier story: a constant hum of ordinary policing, with small
weather- and tourism-driven ripples on top. That is less exciting than the myth.
It is also more true.
""",
    },
    {
        "slug": "montana-privacy-redaction-rules",
        "title": "How Montana Blotter Decides What to Redact",
        "primary_category": "guide",
        "tags": ["privacy", "redaction", "montana", "transparency", "guide"],
        "excerpt": "We publish public records, but not all of them, and never all of them raw. Here's the exact line we draw on redaction.",
        "body": """## Publishing is a choice, not a dump

Montana Blotter could, in theory, paste every record exactly as received. We do
not, because raw publication harms people in ways the law and basic decency
forbid. Redaction is the discipline of removing the parts that should never be
public — while keeping the record's public value intact.

## What we always redact

- **Home addresses** and other locational details that invite harassment
- **Victim names** in domestic violence and sexual-assault cases
- **Juvenile identifiers** where the law protects them
- **Contact information** like phone numbers and emails

## What we keep

- The fact of an arrest or incident
- The charge as alleged
- The agency and the date
- The jurisdiction

The public still gets the oversight value of the record. The private person keeps
the details that serve no public purpose.

## The removal path

Beyond automatic redaction, Montana Blotter offers a **human-reviewed
name-suppression** process. You request it, a person reviews it, and on approval
your name is redacted across bookings, warrants, and related posts. It is a
one-time review, not a blanket erase — because a public record and a person's
dignity are not the same thing, but they do have to coexist.

Transparency is the default. Dignity is the limit. We hold both.
""",
    },
    {
        "slug": "montana-domestic-violence-blotter-handling",
        "title": "How Montana Blotter Handles Domestic Violence Records",
        "primary_category": "editorial",
        "tags": ["domestic violence", "victims", "privacy", "montana", "editorial"],
        "excerpt": "Domestic violence is one of the most common — and most sensitive — calls in Montana blotters. Here's our policy on covering it, and why we name no victims.",
        "body": """## The hardest call type

Domestic violence shows up constantly in Montana's public-safety logs. It is also
the call type where careless publication does the most damage. A blotter entry
names the *arrested* party, but the person who called for help is often named
nowhere — and should stay that way.

## Our rule: victims are not the story

When we publish a domestic-violence incident, we apply strict redaction:

- **No victim name.** Ever. Naming a victim is not news; it is harm, and in many
  cases it endangers the person we should be protecting.
- **No address.** Location detail can lead an abuser straight to a survivor.
- **No speculative narrative.** We report the record, not a courtroom dramatization.

## Why this is consistent with transparency

Some argue that redacting victims undermines the public record. We disagree. The
public value of a domestic-violence entry is that the *response* happened and was
logged — accountability for the agency and the accused. The victim's identity
adds no oversight value and creates real risk. Transparency and safety are not
opposed here; the first simply does not require the second.

## A note to survivors

If your name appears anywhere in our records in a way that puts you at risk, our
human-reviewed suppression process exists for exactly this. Reach out. We will
fix it. The record can stay; your safety comes first.
""",
    },
    {
        "slug": "montana-crime-map-how-to-use",
        "title": "How to Use Montana Blotter's Crime Map and Atlas",
        "primary_category": "guide",
        "tags": ["crime map", "atlas", "montana", "how-to", "guide"],
        "excerpt": "Montana Blotter has a map and an atlas that turn raw records into geography. Here's how to actually use them to understand your area.",
        "body": """## Records become places

A list of bookings is useful. A map of bookings is something else — it lets you
see where public-safety activity concentrates, by town, by county, by highway.
Montana Blotter's **crime map** and **crime atlas** do exactly that: they plot the
records we aggregate onto Montana's actual geography.

## Start with your county

Open the atlas and pick your county. You will see the agencies that publish
there, the volume of records, and — importantly — the gaps where nothing is
published. The gaps are as informative as the dots: they tell you which agencies
are still closed doors.

## Layer by record type

Both tools let you filter:

- **Bookings** — who was taken into custody and where
- **Warrants** — where active warrants cluster
- **Blotter calls** — the day-to-day incident map
- **Missing persons** — the alerts we carry

Switching layers changes the picture. Warrants may cluster where bookings do not,
because a warrant is a backlog, not a fresh event.

## Read it honestly

A dense map dot is not a "bad neighborhood." It is usually a busy agency, a
popular highway, or a county that publishes thoroughly. The map is a starting
point for questions, not a verdict on a place. Use it to ask *why* — then check
the underlying records, where the real answers live.
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
