"""
Violent / Sexual Offender Registry Delta Engine

Compares current state against previous snapshot, classifies changes,
and writes sex_offender_changes records.

Usage:
    python sex_offender_delta.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any

from db import connect_db


def _classify_change(old: dict[str, Any] | None, new: dict[str, Any] | None) -> tuple[str, str]:
    """Classify change type and generate a factual note."""
    if old is None and new is not None:
        return 'new_registration', f"New registrant: {new.get('full_name', 'Unknown')} registered in {new.get('address_county', 'Unknown')} County."
    if new is None and old is not None:
        return 'removed', f"Registrant removed: {old.get('full_name', 'Unknown')} no longer listed in {old.get('address_county', 'Unknown')} County."

    old_addr = f"{old.get('address_street', '')}, {old.get('address_city', '')}"
    new_addr = f"{new.get('address_street', '')}, {new.get('address_city', '')}"
    if old_addr.strip(', ') != new_addr.strip(', '):
        return 'address_change', f"Address change: {old.get('full_name', 'Unknown')} moved from {old_addr} to {new_addr}."

    if old.get('status') != new.get('status'):
        return 'compliance_violation', f"Status change: {new.get('full_name', 'Unknown')} status changed from {old.get('status')} to {new.get('status')}."

    if old.get('risk_level') != new.get('risk_level'):
        return 'compliance_violation', f"Risk level change: {new.get('full_name', 'Unknown')} risk level changed from {old.get('risk_level')} to {new.get('risk_level')}."

    return 'updated', f"Record updated: {new.get('full_name', 'Unknown')} information was modified."


def compute_delta(conn: sqlite3.Connection, snapshot_id: int) -> list[dict[str, Any]]:
    """Compute delta since last snapshot and write change records."""
    prev = conn.execute(
        'SELECT id FROM sex_offender_snapshots WHERE id < ? ORDER BY id DESC LIMIT 1',
        (snapshot_id,),
    ).fetchone()

    changes = []

    if prev:
        prev_id = prev['id']
        prev_offenders = {
            r['registry_id']: dict(r)
            for r in conn.execute(
                'SELECT * FROM sex_offenders WHERE last_seen_at <= (SELECT snapshot_date FROM sex_offender_snapshots WHERE id = ?)',
                (prev_id,),
            ).fetchall()
        }
    else:
        prev_offenders = {}

    current_offenders = {
        r['registry_id']: dict(r)
        for r in conn.execute("SELECT * FROM sex_offenders WHERE status = 'active'").fetchall()
    }

    all_ids = set(prev_offenders.keys()) | set(current_offenders.keys())

    for rid in all_ids:
        old = prev_offenders.get(rid)
        new = current_offenders.get(rid)

        if old == new:
            continue

        change_type, note = _classify_change(old, new)
        offender = new or old
        offender_id = offender['id']

        conn.execute(
            '''
            INSERT INTO sex_offender_changes
            (offender_id, snapshot_id, change_type, change_note, old_value_json, new_value_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                offender_id,
                snapshot_id,
                change_type,
                note,
                json.dumps(old) if old else None,
                json.dumps(new) if new else None,
            ),
        )
        changes.append({
            'offender_id': offender_id,
            'change_type': change_type,
            'note': note,
        })

    conn.commit()

    new_count = sum(1 for c in changes if c['change_type'] == 'new_registration')
    removed_count = sum(1 for c in changes if c['change_type'] == 'removed')
    changed_count = len(changes) - new_count - removed_count

    conn.execute(
        'UPDATE sex_offender_snapshots SET new_count = ?, removed_count = ?, changed_count = ? WHERE id = ?',
        (new_count, removed_count, changed_count, snapshot_id),
    )
    conn.commit()

    return changes


def main():
    conn = connect_db()
    try:
        latest = conn.execute('SELECT id FROM sex_offender_snapshots ORDER BY id DESC LIMIT 1').fetchone()
        if not latest:
            print('No snapshots found. Run scraper first.')
            return
        changes = compute_delta(conn, latest['id'])
        print(f'Computed {len(changes)} changes for snapshot {latest["id"]}.')
        for c in changes[:10]:
            print(f"  [{c['change_type']}] {c['note']}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
