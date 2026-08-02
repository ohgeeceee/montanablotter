"""Normalize approved LEA drafts into the public records table."""
import json
import logging
from db import get_db

logger = logging.getLogger(__name__)


def normalize_and_publish(draft, conn=None):
    """Take an approved draft dict and insert into public records table.

    Steps:
    1. Map lea_blotter_drafts fields to records columns
    2. Set county from lea_agencies lookup
    3. Insert into records table (existing public table)
    4. Update draft status to 'published' and set published_at
    5. Log to lea_audit_log

    Returns dict with success/error.
    """
    if conn is None:
        conn = get_db()

    try:
        # Look up agency for metadata
        agency = conn.execute(
            "SELECT org_name, county_slug, county_name FROM lea_agencies WHERE id = ?",
            (draft['agency_id'],)
        ).fetchone()
        if not agency:
            return {'success': False, 'error': 'Agency not found'}

        agency = dict(agency)

        # Determine county slug
        county_slug = agency['county_slug']

        # Determine incident type from MCA code or default
        mca = (draft.get('primary_offense_mca') or '').strip()
        incident_type = mca if mca else 'Unknown'

        # Build the narrative/description
        narrative = draft.get('public_narrative') or ''
        location = draft.get('incident_location_block') or ''

        # We need a valid blotter record (FK constraint on records.blotter_id)
        conn.execute(
            "INSERT INTO blotters (filename, county) VALUES (?, ?)",
            ("lea_panel_batch", county_slug)
        )
        blotter_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert into public records table
        conn.execute(
            "INSERT INTO records (county, date, time, incident_type, cfs_number, "
            "incident, location, officer, blotter_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                county_slug,
                draft.get('incident_date', ''),
                draft.get('incident_time', ''),
                incident_type,
                draft.get('cad_number', ''),
                narrative,
                location,
                draft.get('responding_officer', ''),
                blotter_id,
            )
        )

        # Update draft status
        conn.execute(
            "UPDATE lea_blotter_drafts SET submission_status = 'published', "
            "published_at = datetime('now') WHERE id = ?",
            (draft['id'],)
        )

        # Log to audit log
        conn.execute(
            "INSERT INTO lea_audit_log (agency_id, user_id, action, resource_type, "
            "resource_id, change_summary) "
            "VALUES (?, NULL, 'blotter.publish', 'blotter', ?, ?)",
            (draft['agency_id'], str(draft['id']),
             f"Published incident {draft.get('cad_number', '')} via LEA pipeline")
        )

        conn.commit()
        return {'success': True, 'record_id': draft['id']}

    except Exception as e:
        logger.error("Failed to normalize draft %s: %s", draft.get('id'), e)
        return {'success': False, 'error': str(e)}


def process_all_approved_drafts(conn=None):
    """Fetch and publish all approved drafts."""
    from services.ingestion.poll_lea_panel import fetch_approved_drafts

    if conn is None:
        conn = get_db()

    drafts = fetch_approved_drafts(conn=conn)
    results = []
    for draft in drafts:
        result = normalize_and_publish(draft, conn=conn)
        results.append(result)

    return results


def run_once():
    """Entry point for cron: process all pending drafts."""
    conn = get_db()
    results = process_all_approved_drafts(conn)
    published = sum(1 for r in results if r.get('success'))
    failed = sum(1 for r in results if not r.get('success'))
    logger.info("LEA normalize: %d published, %d failed", published, failed)
    return {'published': published, 'failed': failed, 'total': len(results)}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_once()))
