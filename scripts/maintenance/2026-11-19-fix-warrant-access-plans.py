#!/usr/bin/env python3
"""
One-off migration: restore subscriber_plan='warrant_access' for buyers the old
webhook stored as 'plus' (2026-11-19).

Background: until 2026-11-19, _apply_subscription_stripe_event (app.py)
remapped plan='warrant_access' -> 'plus' at write time, and paywall.py remapped
it back at read time. The warrant-scope fix removes both remaps, so those
buyers must be stored as 'warrant_access' or they lose /wanted access.

Detection: scan payment_webhook_events.payload_json and
subscription_events.payload_json for Stripe objects whose metadata carries
flow='warrant_access' or plan='warrant_access'; the buyer is
metadata.public_user_id or the session's client_reference_id.

Usage (on vm2778):
    cd /opt/montanablotter
    ./venv/bin/python3 scripts/maintenance/2026-11-19-fix-warrant-access-plans.py
    ./venv/bin/python3 scripts/maintenance/2026-11-19-fix-warrant-access-plans.py --apply
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config  # noqa: E402

DB_PATH = config.DB_PATH


def find_warrant_buyer_ids(conn):
    """Return the set of public_user IDs with a warrant_access purchase in event logs."""
    ids = set()
    for table in ('payment_webhook_events', 'subscription_events'):
        try:
            rows = conn.execute(f'SELECT payload_json FROM {table}').fetchall()
        except sqlite3.OperationalError as exc:
            print(f'  note: could not read {table}: {exc}')
            continue
        for (payload_text,) in rows:
            try:
                payload = json.loads(payload_text or '')
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            # payment_webhook_events stores the full event (object under
            # data.object); subscription_events stores data_object directly.
            candidates = [payload]
            nested = (payload.get('data') or {}).get('object')
            if isinstance(nested, dict):
                candidates.append(nested)
            for cand in candidates:
                meta = cand.get('metadata') or {}
                flow = str(meta.get('flow') or '').strip().lower()
                plan = str(meta.get('plan') or '').strip().lower()
                if flow != 'warrant_access' and plan != 'warrant_access':
                    continue
                uid = str(meta.get('public_user_id') or cand.get('client_reference_id') or '').strip()
                if uid.isdigit():
                    ids.add(int(uid))
    return ids


def main():
    apply_changes = '--apply' in sys.argv
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    buyer_ids = find_warrant_buyer_ids(conn)
    print(f'warrant_access purchases found in event logs: {len(buyer_ids)}')
    if not buyer_ids:
        print('Nothing to do.')
        return

    placeholders = ','.join('?' * len(buyer_ids))
    rows = conn.execute(
        f"""SELECT id, email, subscriber_plan, subscription_status, is_subscribed
            FROM public_users WHERE id IN ({placeholders})""",
        sorted(buyer_ids),
    ).fetchall()

    to_fix, skipped = [], []
    for row in rows:
        if row['subscriber_plan'] == 'plus' and row['is_subscribed']:
            to_fix.append(row)
        else:
            skipped.append(row)

    print(f'users to restore to warrant_access: {len(to_fix)}')
    for row in to_fix:
        print(f"  id={row['id']} email={row['email']} plan={row['subscriber_plan']} status={row['subscription_status']}")
    if skipped:
        print(f'skipped (already correct or not active): {len(skipped)}')
        for row in skipped:
            print(f"  id={row['id']} email={row['email']} plan={row['subscriber_plan']} status={row['subscription_status']}")

    if not apply_changes:
        print('\nDRY RUN — re-run with --apply to write changes.')
        return

    conn.execute('BEGIN IMMEDIATE')
    for row in to_fix:
        conn.execute(
            """UPDATE public_users
               SET subscriber_plan = 'warrant_access'
               WHERE id = ? AND subscriber_plan = 'plus' AND is_subscribed = 1""",
            (row['id'],),
        )
    conn.commit()
    print(f'\nApplied: {len(to_fix)} user(s) now have subscriber_plan=warrant_access.')


if __name__ == '__main__':
    main()
