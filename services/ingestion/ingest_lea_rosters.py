"""Convert LEA roster snapshots into the public jail_bookings table."""
import json
import logging
import hashlib
from db import get_db

logger = logging.getLogger(__name__)


def ingest_roster(snapshot, conn=None):
    """Take a staged roster snapshot and insert inmates into jail_bookings.

    Steps:
    1. Parse roster_json (array of inmate dicts)
    2. For each inmate, dedup by hash_checksum of name + booking_date
    3. Insert new inmates into jail_bookings
    4. Mark snapshot as 'published'
    5. Log to audit log
    """
    if conn is None:
        conn = get_db()

    try:
        inmates = json.loads(snapshot['roster_json'])
        if not isinstance(inmates, list):
            return {'success': False, 'error': 'roster_json is not a list'}

        # Look up agency for county info
        agency = conn.execute(
            "SELECT org_name, county_slug, county_name FROM lea_agencies WHERE id = ?",
            (snapshot['agency_id'],)
        ).fetchone()
        if not agency:
            return {'success': False, 'error': 'Agency not found'}
        agency = dict(agency)

        inserted = 0
        for inmate in inmates:
            name = (inmate.get('name') or inmate.get('full_name') or '').strip()
            booking_date = (inmate.get('booking_date') or
                            inmate.get('date') or
                            snapshot.get('snapshot_date', ''))

            # Dedup hash
            dedup_raw = f"{name}|{booking_date}|{snapshot['agency_id']}"
            dedup_hash = hashlib.sha256(dedup_raw.encode()).hexdigest()

            # Check if already exists
            existing = conn.execute(
                "SELECT id FROM jail_bookings WHERE hash_id = ?",
                (dedup_hash,)
            ).fetchone()
            if existing:
                continue

            conn.execute(
                "INSERT INTO jail_bookings (person_name, booking_at, facility_name, "
                "county_slug, county_name, hash_id, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    booking_date,
                    inmate.get('agency', inmate.get('facility', '')),
                    agency['county_slug'],
                    agency['county_name'],
                    dedup_hash,
                    json.dumps(inmate)
                )
            )
            inserted += 1

        # Mark snapshot as processing first (CJIS workflow compliance)
        conn.execute(
            "UPDATE lea_roster_snapshots SET ingestion_status = 'processing' "
            "WHERE id = ? AND ingestion_status = 'staged'",
            (snapshot['id'],)
        )

        # Then mark as published
        conn.execute(
            "UPDATE lea_roster_snapshots SET ingestion_status = 'published', "
            "published_at = datetime('now') "
            "WHERE id = ? AND ingestion_status = 'processing'",
            (snapshot['id'],)
        )

        # Audit log
        conn.execute(
            "INSERT INTO lea_audit_log (agency_id, user_id, action, resource_type, "
            "resource_id, change_summary) "
            "VALUES (?, NULL, 'roster.publish', 'roster', ?, ?)",
            (snapshot['agency_id'], str(snapshot['id']),
             f"Published {inserted} inmates from roster snapshot")
        )

        conn.commit()
        return {'success': True, 'inserted': inserted}

    except Exception as e:
        logger.error("Failed to ingest roster %s: %s", snapshot.get('id'), e)
        return {'success': False, 'error': str(e)}


def process_all_staged_rosters(conn=None):
    """Fetch and ingest all staged rosters."""
    from services.ingestion.poll_lea_panel import fetch_staged_rosters

    if conn is None:
        conn = get_db()

    snapshots = fetch_staged_rosters(conn=conn)
    results = []
    for snapshot in snapshots:
        result = ingest_roster(snapshot, conn=conn)
        results.append(result)

    return results


def run_once():
    """Entry point for cron: process all staged rosters."""
    conn = get_db()
    results = process_all_staged_rosters(conn)
    total_inserted = sum(r.get('inserted', 0) for r in results if r.get('success'))
    failed = sum(1 for r in results if not r.get('success'))
    logger.info("LEA roster: %d inmates inserted, %d snapshots failed",
                total_inserted, failed)
    return {'inserted': total_inserted, 'failed': failed, 'total': len(results)}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_once()))
