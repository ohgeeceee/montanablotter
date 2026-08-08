"""Per-firm lawyer outreach cadence worker.

Reads `lawyer_outreach_prospects`, queues Day 1 / Day 3 / Day 5 / Day 10
emails into `lawyer_outreach_emails`, and advances stages based on
`last_action_at`. The worker NEVER sends email — that is the admin blueprint's
job (`/admin/lawyer-outreach/<email_id>/send`).

Cadence (per docs/criminal_defense_attorney_outreach_sequence.md):
  day_1   initial email                 "Montana families searching..."
  day_3   phone follow-up               (no email body — operator note only)
  day_5   sample report email           "Sample monthly report — ..."
  day_10  close                         "Final check-in — ..."

Stage rules:
  - If status in ('won', 'lost', 'unqualified'), skip.
  - If no contact_email, skip — operator must research first.
  - If a row in lawyer_outreach_emails already exists for (prospect, stage),
    skip the queue insert (UNIQUE dedupe key handles this).
  - If stage == 'day_1' and last_action_at is within 14 days, skip — give the
    firm time to respond before re-queuing.

Usage:
    source venv/bin/activate && python3 -m services.lawyer_outreach.cadence [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime

import init_db
from services.lawyer_outreach.importer import import_prospects_from_csv

LOG_PATH = '/root/montanablotter/logs/lawyer_outreach_cadence.log'
log = logging.getLogger('lawyer_outreach.cadence')


# ----------------------------------------------------------------- templates --

def _day_1_subject(firm_name: str, county: str) -> str:
    return f"Montana families searching for a defense attorney in {county}"


def _day_1_body(firm_name: str, county: str, contact_name: str,
                ad_slots: tuple[int, int], cap: tuple[int, int, int]) -> str:
    first_name = (contact_name or '').split()[0] if contact_name else 'there'
    gold_open, gold_total = ad_slots
    return (
        f"Hi {first_name},\n\n"
        f"I run Montana Blotter — the open public-records platform that indexes "
        f"jail rosters, court activity, warrants, and blotter reports from all "
        f"56 Montana counties.\n\n"
        f"When someone is arrested or booked in {county}, their family usually "
        f"starts searching within the hour. They search \"{county} criminal "
        f"defense attorney\" or \"{county} jail roster\" — and Montana Blotter "
        f"is the page that ranks for those searches because the records are "
        f"the page.\n\n"
        f"We just opened a paid directory at /lawyers that puts a firm's name, "
        f"phone, and intake link directly on those pages. Listings are "
        f"county-targeted and tiered: Bronze, Silver Featured, and Gold "
        f"Priority. Public intake inquiries from those county pages route to "
        f"every active advertiser in the county, with Gold-tier firms "
        f"notified first.\n\n"
        f"If this is the right time, I can send a one-page sample report "
        f"showing the exact metrics the firm will receive each month. Reply "
        f"\"SEND REPORT\" and I'll get it to you today.\n\n"
        f"— Jon\n"
        f"Montana Blotter · support@montanablotter.com\n\n"
        f"P.S. — {county} currently has {gold_open} of {gold_total} Gold slots "
        f"open. The Gold slot goes to whichever firm commits first."
    )


def _day_5_subject(firm_name: str, county: str) -> str:
    return f"Sample monthly report — {firm_name} on /lawyers/{county}"


def _day_5_body(firm_name: str, county: str, contact_name: str) -> str:
    first_name = (contact_name or '').split()[0] if contact_name else 'there'
    return (
        f"Hi {first_name},\n\n"
        f"Attached / linked: a one-page sample monthly report showing the exact "
        f"metrics the firm would receive on /lawyers/{county.lower().replace(' ', '-')}.\n\n"
        f"What you'll see in the report:\n"
        f"- Directory impressions in {county} (deduped per visitor per day)\n"
        f"- Tap-to-call actions\n"
        f"- Website / target URL clicks\n"
        f"- Consumer intake leads delivered to your inbox\n"
        f"- Delivery failures (with the destination that bounced)\n"
        f"- Advertiser-reported contact / consultation / retained counts — the firm fills these in\n"
        f"- Cost per delivered lead\n"
        f"- Cost per consultation and retained matter when those numbers exist\n\n"
        f"The report is real data, not estimates. We will not promise case "
        f"volume or ROI before we have cohort data. After 90 days we can talk "
        f"about the conversion numbers we are actually seeing.\n\n"
        f"— Jon\nMontana Blotter · support@montanablotter.com"
    )


def _day_10_subject(firm_name: str, county: str) -> str:
    return f"Final check-in — {county} Gold slot"


def _day_10_body(firm_name: str, county: str, contact_name: str,
                 gold_open: int) -> str:
    first_name = (contact_name or '').split()[0] if contact_name else 'there'
    if gold_open <= 0:
        body_mid = (
            f"If the timing isn't right, no problem. Reply \"PASS\" and I'll "
            f"remove you from the active list. You can always come back later.\n\n"
            f"If you want to move forward: the {county} directory is open. I "
            f"can have {firm_name} live within 24 hours of payment."
        )
    else:
        body_mid = (
            f"If the timing isn't right, no problem. Reply \"PASS\" and I'll "
            f"remove you from the active list. You can always come back later.\n\n"
            f"If you want to move forward: the {county} Gold slot is currently "
            f"open. I can have {firm_name} live within 24 hours of payment."
        )
    return (
        f"Hi {first_name},\n\n"
        f"Closing the loop on the {county} listing.\n\n"
        f"{body_mid}\n\n"
        f"Reply \"GO\" and I'll send the checkout link.\n\n"
        f"— Jon\nMontana Blotter · support@montanablotter.com"
    )


# ------------------------------------------------------- county cap probe ---

def _gold_slot_counts(conn: sqlite3.Connection, counties: list[str]) -> dict[str, tuple[int, int]]:
    """Return {(county_lower): (gold_open, gold_total)} for /lawyers inventory.

    gold_total = 1 (the cap). gold_open = 1 if zero active Gold orders, else 0.
    Best-effort: if the lawyer_ad_orders table doesn't exist in this DB
    (typical for unit tests against an isolated schema), every slot is
    reported as open. Returns {(county_lower): (1, 1)} for every county
    passed in.
    """
    out: dict[str, tuple[int, int]] = {c.lower(): (1, 1) for c in counties}
    if not counties:
        return out
    table_check = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lawyer_ad_orders'"
    ).fetchone()
    listings_check = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lawyer_ad_listings'"
    ).fetchone()
    if not table_check or not listings_check:
        return out

    rows = conn.execute(
        '''
        SELECT o.counties_served
        FROM lawyer_ad_orders o
        JOIN lawyer_ad_listings l ON l.order_id = o.id
        WHERE o.status = 'active' AND l.is_active = 1
          AND o.package_id = 'gold'
        '''
    ).fetchall()
    counties_with_gold = set()
    for r in rows:
        served = (r['counties_served'] or '').lower()
        for c in counties:
            if c.lower() in served:
                counties_with_gold.add(c.lower())
    for c in counties:
        key = c.lower()
        total = 1
        open_ = 0 if key in counties_with_gold else 1
        out[key] = (open_, total)
    return out


# ---------------------------------------------------------------- main fn ---

def _stage_day_offset(stage: str) -> int:
    return {'day_1': 1, 'day_3': 3, 'day_5': 5, 'day_10': 10}.get(stage, 0)


def _advance_due_stages(conn: sqlite3.Connection, counts: dict[str, int]) -> None:
    """Move prospects to the next stage once the previous stage has been sent
    AND last_action_at is at least one day older than the new stage's offset.

    Day 1 → Day 3: requires Day 1 email sent AND last_action_at > 3 days ago.
    Day 3 → Day 5: requires Day 3 entry sent AND last_action_at > 5 days ago.
    Day 5 → Day 10: requires Day 5 email sent AND last_action_at > 10 days ago.
    Day 10 → won: requires Day 10 email sent AND last_action_at > 14 days ago.
    """
    order = ['day_1', 'day_3', 'day_5', 'day_10', 'won']
    advance_map = {'day_1': 'day_3', 'day_3': 'day_5', 'day_5': 'day_10', 'day_10': 'won'}
    for current, next_stage in advance_map.items():
        threshold = _stage_day_offset(next_stage) if next_stage != 'won' else 14
        rows = conn.execute(
            '''
            SELECT p.id
            FROM lawyer_outreach_prospects p
            WHERE p.stage = ? AND p.status NOT IN ('won', 'lost', 'unqualified')
              AND p.last_action_at IS NOT NULL
              AND p.last_action_at <= datetime('now', ?)
              AND EXISTS (
                  SELECT 1 FROM lawyer_outreach_emails e
                  WHERE e.prospect_id = p.id AND e.stage = ? AND e.status = 'sent'
              )
            ''',
            (current, f'-{threshold} days', current),
        ).fetchall()
        for r in rows:
            new_status = 'in_progress' if next_stage != 'won' else 'won'
            conn.execute(
                '''UPDATE lawyer_outreach_prospects
                   SET stage = ?, status = ?, last_action_at = datetime('now'),
                       next_action_at = datetime('now', '+1 day'),
                       updated_at = datetime('now')
                   WHERE id = ?''',
                (next_stage, new_status, r['id']),
            )
            counts[f'advanced_to_{next_stage}'] = counts.get(f'advanced_to_{next_stage}', 0) + 1


def run_cadence(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Queue the next stage email for each prospect whose stage has aged.

    Idempotent: a row that already has a pending/sent email for (stage, attempt=1)
    is skipped via the UNIQUE campaign_dedupe_key constraint.

    Returns counts {queued, skipped_no_email, skipped_won, skipped_recent}.
    """
    counts = {'queued': 0, 'skipped_no_email': 0, 'skipped_won': 0, 'skipped_recent': 0}

    # Pre-compute Gold-slot inventory for the counties we care about.
    counties_in_play = [
        r['county'] for r in conn.execute(
            "SELECT DISTINCT county FROM lawyer_outreach_prospects "
            "WHERE status NOT IN ('won', 'lost', 'unqualified') AND county IS NOT NULL"
        ).fetchall()
    ]
    gold_slots = _gold_slot_counts(conn, counties_in_play)

    prospects = conn.execute(
        '''
        SELECT id, firm_name, county, contact_email, contact_name,
               stage, status, last_action_at, next_action_at
        FROM lawyer_outreach_prospects
        WHERE status NOT IN ('won', 'lost', 'unqualified')
        '''
    ).fetchall()

    for p in prospects:
        email = (p['contact_email'] or '').strip()
        if not email:
            counts['skipped_no_email'] += 1
            continue

        stage = p['stage']
        # Check if a row already exists for this (prospect, stage).
        existing = conn.execute(
            '''SELECT id, status FROM lawyer_outreach_emails
               WHERE prospect_id = ? AND stage = ? AND attempt = 1''',
            (p['id'], stage),
        ).fetchone()
        already_queued_for_stage = bool(existing)
        # First-time queue for a stage is always allowed — the recency guard
        # only suppresses re-queueing the SAME stage for a firm that hasn't
        # replied. A prospect just imported (no email row for this stage)
        # should get a Day 1 email on the very first cadence run.
        # If we re-queued Day 1 because it was previously skipped/lost, give
        # the firm at least 14 days to respond before re-touching them.
        if stage == 'day_1' and already_queued_for_stage and p['last_action_at']:
            try:
                last_dt = datetime.strptime(p['last_action_at'][:19], '%Y-%m-%d %H:%M:%S')
                age_days = (datetime.utcnow() - last_dt).days
                if age_days < 14:
                    counts['skipped_recent'] += 1
                    continue
            except ValueError:
                pass

        if existing and existing['status'] in ('pending', 'sent'):
            continue

        # Build subject + body per stage.
        county = p['county'] or 'Montana'
        slot = gold_slots.get(county.lower(), (1, 1))
        if stage == 'day_1':
            subject = _day_1_subject(p['firm_name'], county)
            body = _day_1_body(p['firm_name'], county, p['contact_name'] or '',
                               slot, (1, 1))
        elif stage == 'day_3':
            # Day 3 is phone-only per the outreach sequence. The admin panel
            # surfaces a phone-task note instead of an email; we still create
            # a placeholder row so the operator can mark "called / no answer".
            subject = '(phone call)'
            body = (
                f'Phone follow-up due for {p["firm_name"]} ({county}). '
                f'Ask who owns intake and paid marketing. Do not pitch the '
                f'receptionist for ten minutes. After the call, advance the '
                f'prospect to day_5 in /admin/lawyer-outreach.'
            )
        elif stage == 'day_5':
            subject = _day_5_subject(p['firm_name'], county)
            body = _day_5_body(p['firm_name'], county, p['contact_name'] or '')
        elif stage == 'day_10':
            subject = _day_10_subject(p['firm_name'], county)
            body = _day_10_body(p['firm_name'], county, p['contact_name'] or '',
                                slot[0])
        else:
            # Unknown stage — skip silently.
            continue

        dedupe_key = f'lawyer_outreach:{p["id"]}:{stage}:1'

        if not dry_run:
            try:
                conn.execute(
                    '''INSERT INTO lawyer_outreach_emails
                       (prospect_id, stage, attempt, to_addr, subject, body,
                        status, campaign_dedupe_key)
                       VALUES (?, ?, 1, ?, ?, ?, 'pending', ?)''',
                    (p['id'], stage, email, subject, body, dedupe_key),
                )
                counts['queued'] += 1
            except sqlite3.IntegrityError:
                # UNIQUE collision on campaign_dedupe_key — another run beat us.
                pass

    if not dry_run:
        conn.commit()
        _advance_due_stages(conn, counts)
        conn.commit()
    return counts


# ---------------------------------------------------------------- CLI ---

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--skip-import', action='store_true',
                        help='Skip the target_list.csv import step')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [lawyer_outreach] %(message)s',
        handlers=[
            logging.FileHandler(LOG_PATH),
            logging.StreamHandler(sys.stdout),
        ],
    )

    conn = sqlite3.connect(init_db.DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db.ensure_lawyer_outreach_schema(conn)

    if not args.skip_import:
        from services.lawyer_outreach.importer import import_prospects_from_csv, DEFAULT_CSV_PATH
        import_counts = import_prospects_from_csv(conn, DEFAULT_CSV_PATH,
                                                  dry_run=args.dry_run)
        log.info("importer: %s", import_counts)

    counts = run_cadence(conn, dry_run=args.dry_run)
    log.info("cadence: %s", counts)
    conn.close()
    return 0


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())