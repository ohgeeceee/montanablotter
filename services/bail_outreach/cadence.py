#!/usr/bin/env python3
"""Bail-agency outreach cadence.

Queues human-review email drafts (into ``outreach_drafts``) for agencies in
``bail_agency_outreach`` following the same aging ladder as the lawyer
pipeline:

  new            -> day_1 intro email   (if an email address is on file)
  new/contacted  -> phone task note     (no email on file)
  contacted +5d  -> day_5 follow-up
  contacted +10d -> day_10 final check-in
  contacted +14d -> stalled (no further auto work)

Drafts are NEVER sent automatically; a human reviews and ships them from
/admin/revenue/crm. Marking a day_1 draft sent flips the agency to
``contacted`` via :func:`mark_draft_sent`, which this cadence uses to age
follow-ups.

Idempotent via the UNIQUE campaign_dedupe_key on outreach_drafts.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, '/root/montanablotter')
import init_db  # noqa: E402

LOG_PATH = '/root/montanablotter/logs/bail_outreach_cadence.log'
log = logging.getLogger('bail_outreach.cadence')

WORKER_NAME = 'bail_outreach_cadence'

REPLY_TO = os.environ.get('LAWYER_OUTREACH_REPLY_TO',
                          'montanablotter@gmail.com')


# ------------------------------------------------------------ slot probe ---

def _exclusive_slot_state(conn: sqlite3.Connection,
                          county: str) -> tuple[int, int]:
    """Return (open, total) for the exclusive county sponsorship feed.

    total = 1. open = 0 when an *active* order owns this county (either it
    names the county or targets 'ALL'). Best-effort: if the orders table is
    missing (isolated test schema), report the slot open.
    """
    ok = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='bail_ad_orders'").fetchone()
    if not ok or not county:
        return (1, 1)
    rows = conn.execute(
        """SELECT county_targets FROM bail_ad_orders
           WHERE status = 'active'
             AND package_id = 'exclusive_county_sponsorship'"""
    ).fetchall()
    needle = county.strip().lower()
    for r in rows:
        targets = (r[0] or '').lower()
        if needle in targets or 'all' in targets:
            return (0, 1)
    return (1, 1)


# ------------------------------------------------------------- templates ---

def _first_name(contact_name: str | None) -> str:
    parts = (contact_name or '').split()
    return parts[0] if parts else 'there'


def day_1_subject(county: str) -> str:
    return f"Families searching for a bondsman in {county} right now"


def day_1_body(agency_name: str, county: str, contact_name: str | None,
               slots: tuple[int, int]) -> str:
    slot_open, slot_total = slots
    return (
        f"Hi {_first_name(contact_name)},\n\n"
        f"I run Montana Blotter — the open public-records platform that "
        f"indexes jail rosters, court activity, warrants, and blotter "
        f"reports from all 56 Montana counties.\n\n"
        f"When someone is booked in {county}, the family that needs a "
        f"bondsman starts searching within the hour — usually on a phone, "
        f"usually at 2am. Montana Blotter's roster and bail-bond pages are "
        f"where those searches land, because the records are the page.\n\n"
        f"We opened paid placement on /bail-bonds and the {county} jail "
        f"roster pages: a county-exclusive sponsorship (one agency only, "
        f"your name and 24/7 line on every booking in the county feed), a "
        f"sticky emergency-call sidebar, and a top banner. Agents on the "
        f"ground tell us placement converts because it shows up at the "
        f"exact moment the call gets made.\n\n"
        f"Happy to send a one-page breakdown with current traffic and "
        f"pricing for {county}. Reply \"SEND IT\" and I'll get it to you "
        f"today.\n\n"
        f"— Jon\n"
        f"Montana Blotter · {REPLY_TO}\n\n"
        + (
            f"P.S. — {county} currently has {slot_open} of {slot_total} "
            f"exclusive sponsorship slots open. It goes to whichever "
            f"agency commits first."
            if slot_open else
            f"P.S. — FYI the {county} exclusive sponsorship is already "
            f"held by another agency; the sidebar and banner slots are "
            f"still open there, and neighboring counties' exclusives are "
            f"available."
        )
    )


def day_5_subject(county: str) -> str:
    return f"Re: Families searching for a bondsman in {county}"


def day_5_body(agency_name: str, county: str,
               contact_name: str | None) -> str:
    return (
        f"Hi {_first_name(contact_name)},\n\n"
        f"Following up on the {county} placement. Two things that might "
        f"help you decide:\n\n"
        f"1. The county sponsorship is exclusive — one agency. Every "
        f"booking that hits the {county} roster page shows your name and "
        f"phone, and public intake calls route to you.\n"
        f"2. No lock-in grief: monthly billing, cancel any time, and I "
        f"can have you live within 24 hours of the go-ahead.\n\n"
        f"Want the traffic numbers for {county}? Reply \"SEND IT\" and "
        f"they're yours in the hour.\n\n"
        f"— Jon\nMontana Blotter · {REPLY_TO}"
    )


def day_10_subject(county: str) -> str:
    return f"Last note — {county} bondsmen slot"


def day_10_body(agency_name: str, county: str,
                contact_name: str | None, slots: tuple[int, int]) -> str:
    slot_open, _ = slots
    mid = (
        f"The {county} exclusive is still open — I'd rather fill it with a "
        f"local agency than leave it for sale indefinitely."
        if slot_open else
        f"The {county} exclusive was taken, but the sidebar/banner units "
        f"and neighboring-county exclusives are still open."
    )
    return (
        f"Hi {_first_name(contact_name)},\n\n"
        f"Closing the loop so I stop cluttering your inbox. {mid}\n\n"
        f"Reply \"GO\" and I'll send the checkout link — live within 24 "
        f"hours of payment. If the timing's wrong, reply \"PASS\" and "
        f"I'll leave you alone.\n\n"
        f"— Jon\nMontana Blotter · {REPLY_TO}"
    )


def phone_task_body(agency_name: str, county: str,
                    phone: str | None) -> str:
    return (
        f"Phone follow-up due for {agency_name} ({county or 'statewide'})."
        f" Call {phone or 'listed number'}, ask who handles marketing "
        f"intake, pitch the county-exclusive sponsorship on the jail "
        f"roster pages. After the call, update this agency's status in "
        f"bail_agency_outreach."
    )


# ---------------------------------------------------------------- main fn ---

def _counties(county_field: str | None) -> list[str]:
    return [c.strip() for c in (county_field or '').split(',') if c.strip()]


def _primary_county(county_field: str | None) -> str:
    cs = _counties(county_field)
    if not cs:
        return 'Montana'
    return cs[0] if len(cs) == 1 else f"{cs[0]} (+{len(cs)-1} more)"


def _body_county(county_field: str | None) -> str:
    cs = _counties(county_field)
    return (cs[0] if cs else 'Montana')


def _queue_draft(conn: sqlite3.Connection, *, dedupe_key: str,
                 agency_name: str, email: str | None, subject: str,
                 body: str) -> bool:
    try:
        conn.execute(
            """INSERT INTO outreach_drafts
               (worker_name, agency_name, email_address, recipient_role,
                subject, body, status, campaign_dedupe_key, created_at)
               VALUES (?,?,?,?,?,?, 'pending', ?, datetime('now'))""",
            (WORKER_NAME, agency_name, email or '', 'bail_agency_contact',
             subject, body, dedupe_key))
        return True
    except sqlite3.IntegrityError:
        return False  # already queued for this stage


def run_cadence(conn: sqlite3.Connection, *,
                dry_run: bool = False) -> dict[str, int]:
    counts = {'queued_email': 0, 'queued_phone': 0, 'queued_followup': 0,
              'skipped': 0, 'stalled': 0}

    agencies = conn.execute(
        """SELECT id, agency_name, contact_name, email, phone, counties,
                  outreach_status, last_contacted_at
           FROM bail_agency_outreach
           WHERE outreach_status NOT IN
                 ('closed_won', 'lost', 'unqualified', 'stalled')"""
    ).fetchall()

    for a in agencies:
        county = _body_county(a['counties'])
        slots = _exclusive_slot_state(conn, a['counties'])
        state = a['outreach_status'] or 'new'

        if state == 'new':
            if a['email']:
                dedupe = f'bail_day1_{a["id"]}'
                if _queue_draft(
                        conn, dedupe_key=dedupe,
                        agency_name=a['agency_name'], email=a['email'],
                        subject=day_1_subject(county),
                        body=day_1_body(a['agency_name'],
                                        _body_county(a['counties']),
                                        a['contact_name'], slots)):
                    conn.execute(
                        """UPDATE bail_agency_outreach
                           SET outreach_status='draft_queued',
                               next_follow_up_at=datetime('now','+3 days'),
                               updated_at=datetime('now') WHERE id=?""",
                        (a['id'],))
                    counts['queued_email'] += 1
                else:
                    counts['skipped'] += 1
            else:
                dedupe = f'bail_phone_{a["id"]}'
                if _queue_draft(
                        conn, dedupe_key=dedupe,
                        agency_name=a['agency_name'], email=None,
                        subject=f'PHONE — {a["agency_name"]} ({county})',
                        body=phone_task_body(a['agency_name'], county,
                                             a['phone'])):
                    counts['queued_phone'] += 1
                else:
                    counts['skipped'] += 1

        elif state in ('contacted', 'draft_sent'):
            if not a['last_contacted_at']:
                counts['skipped'] += 1
                continue
            try:
                last = datetime.strptime(
                    a['last_contacted_at'][:19], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                counts['skipped'] += 1
                continue
            age_days = (datetime.utcnow() - last).days
            if age_days >= 14:
                conn.execute(
                    """UPDATE bail_agency_outreach
                       SET outreach_status='stalled',
                           updated_at=datetime('now') WHERE id=?""",
                    (a['id'],))
                counts['stalled'] += 1
                continue
            if age_days >= 10:
                dedupe = f'bail_day10_{a["id"]}'
                if _queue_draft(
                        conn, dedupe_key=dedupe,
                        agency_name=a['agency_name'], email=a['email'],
                        subject=day_10_subject(county),
                        body=day_10_body(a['agency_name'],
                                         _body_county(a['counties']),
                                         a['contact_name'], slots)):
                    counts['queued_followup'] += 1
                else:
                    counts['skipped'] += 1
            elif age_days >= 5:
                dedupe = f'bail_day5_{a["id"]}'
                if _queue_draft(
                        conn, dedupe_key=dedupe,
                        agency_name=a['agency_name'], email=a['email'],
                        subject=day_5_subject(county),
                        body=day_5_body(a['agency_name'],
                                        _body_county(a['counties']),
                                        a['contact_name'])):
                    counts['queued_followup'] += 1
                else:
                    counts['skipped'] += 1
            else:
                counts['skipped'] += 1
        else:
            counts['skipped'] += 1

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return counts


def mark_draft_sent(conn: sqlite3.Connection, draft_id: int) -> bool:
    """Call when a human ships a bail draft. Flips the owning agency to
    ``contacted`` so follow-ups start aging. Returns True if handled."""
    d = conn.execute(
        "SELECT agency_name, campaign_dedupe_key FROM outreach_drafts "
        "WHERE id=? AND worker_name=?", (draft_id, WORKER_NAME)).fetchone()
    if not d:
        return False
    agency_id = None
    if d['campaign_dedupe_key']:
        agency_id = int(d['campaign_dedupe_key'].split('_')[-1])
    if agency_id is None:
        r = conn.execute("SELECT id FROM bail_agency_outreach WHERE agency_name=?",
                         (d['agency_name'],)).fetchone()
        agency_id = r['id'] if r else None
    if agency_id is None:
        return False
    conn.execute(
        """UPDATE bail_agency_outreach
           SET outreach_status='contacted',
               last_contacted_at=datetime('now'),
               next_follow_up_at=datetime('now','+5 days'),
               updated_at=datetime('now')
           WHERE id=? AND outreach_status NOT IN ('closed_won','lost','stalled')""",
        (agency_id,))
    conn.execute(
        "UPDATE outreach_drafts SET status='sent', sent_at=datetime('now') "
        "WHERE id=?", (draft_id,))
    conn.commit()
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--db', default='/root/montanablotter/blotter.db')
    args = ap.parse_args()

    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s')
    log.info('=== bail outreach cadence start (dry_run=%s)', args.dry_run)

    init_db.init_database()
    init_db.migrate()
    conn = sqlite3.connect(args.db, timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        counts = run_cadence(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    log.info('=== bail outreach cadence done: %s', counts)
    print(counts)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
