"""Poll LEA panel for approved drafts ready for normalization."""
import logging
from db import get_db

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
POLL_INTERVAL_SECONDS = 60  # For standalone mode


def fetch_approved_drafts(conn=None, limit=BATCH_SIZE):
    """Fetch approved blotter drafts ready for publication."""
    if conn is None:
        conn = get_db()
    rows = conn.execute(
        "SELECT id, agency_id, incident_date, incident_time, cad_number, case_number, "
        "primary_offense_mca, charges_json, incident_location_block, "
        "incident_location_latitude, incident_location_longitude, "
        "public_narrative, arresting_agency, responding_officer "
        "FROM lea_blotter_drafts WHERE submission_status = 'approved' "
        "ORDER BY created_at ASC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_staged_rosters(conn=None, limit=BATCH_SIZE):
    """Fetch staged roster snapshots."""
    if conn is None:
        conn = get_db()
    rows = conn.execute(
        "SELECT id, agency_id, snapshot_date, sync_type, roster_json, total_inmates, hash_checksum "
        "FROM lea_roster_snapshots WHERE ingestion_status = 'staged' "
        "ORDER BY created_at ASC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def mark_draft_publishing(draft_id, conn=None):
    """Mark a draft as 'processing' (in-flight)."""
    if conn is None:
        conn = get_db()
    conn.execute(
        "UPDATE lea_blotter_drafts SET submission_status = 'submitted' "
        "WHERE id = ? AND submission_status = 'approved'",
        (draft_id,)
    )
    conn.commit()
