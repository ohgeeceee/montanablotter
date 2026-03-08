"""
Processor - Handles PDF parsing and database insertion
Replaces the old processor.py with actual parsing logic
"""

import sqlite3
import os
import logging
from typing import Optional
import config
from pdf_parser import BlotterParser, parse_text_blotter
import summarizer
import blotter_auditor
from dedupe import incident_key_set, incident_keys
from pipeline_state import (
    ensure_source_document,
    increment_ingestion_retry,
    log_pipeline_event,
    sha256_bytes,
    set_ingestion_job_status,
)

DB_PATH = config.DB_PATH
DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f'PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}')
    return conn

def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(r[1] == column_name for r in rows)


def _post_count_for_blotter(blotter_id: int) -> int:
    conn = _connect_db()
    count = conn.execute(
        'SELECT COUNT(*) FROM posts WHERE blotter_id = ?',
        (blotter_id,),
    ).fetchone()[0]
    conn.close()
    return int(count)


def _load_existing_incident_keys(
    conn: sqlite3.Connection,
    county: Optional[str],
    incidents: list[dict],
) -> set[str]:
    existing_keys = set()
    raw_dates = sorted({(incident.get('date') or '').strip() for incident in incidents if incident.get('date')})
    cfs_numbers = sorted({(incident.get('cfs_number') or '').strip() for incident in incidents if incident.get('cfs_number')})

    clauses = []
    params: list[str] = []

    if county and county.strip().lower() != 'unknown' and raw_dates:
        placeholders = ','.join('?' for _ in raw_dates)
        clauses.append(f"(county = ? AND date IN ({placeholders}))")
        params.extend([county, *raw_dates])

    if cfs_numbers:
        placeholders = ','.join('?' for _ in cfs_numbers)
        clauses.append(f"(cfs_number IN ({placeholders}))")
        params.extend(cfs_numbers)

    if not clauses:
        return existing_keys

    rows = conn.execute(
        f"""
        SELECT cfs_number, date, time,
               COALESCE(incident_type, incident, '') AS incident_type,
               COALESCE(location, '') AS location,
               COALESCE(details, '') AS details,
               county
        FROM records
        WHERE {' OR '.join(clauses)}
        """,
        params,
    ).fetchall()
    return incident_key_set(rows)


def _filter_duplicate_incidents(
    conn: sqlite3.Connection,
    incidents: list[dict],
    county: Optional[str],
) -> tuple[list[dict], int]:
    existing_keys = _load_existing_incident_keys(conn, county, incidents)
    kept: list[dict] = []
    skipped = 0

    for incident in incidents:
        keys = incident_keys(incident, county=county or incident.get('county'))
        if keys and keys & existing_keys:
            skipped += 1
            continue
        kept.append(incident)
        existing_keys.update(keys)

    return kept, skipped


def _publish_blotter_outputs(
    blotter_id: int,
    sender_email: Optional[str] = None,
    ingestion_job_id: Optional[int] = None,
    label: str = 'blotter',
) -> int:
    post_count = 0

    try:
        post_count = summarizer.generate_posts(
            blotter_id,
            sender_email=sender_email,
            ingestion_job_id=ingestion_job_id,
        )
        logging.info(f"Generated {post_count} posts for {label} #{blotter_id}")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'summarize',
                'ok',
                {'post_count': post_count},
            )
    except Exception as e:
        logging.warning(f"Post generation failed for {label} #{blotter_id}: {e}")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'summarize',
                'warn',
                {'error': str(e)},
            )

    try:
        audit_results = blotter_auditor.audit_blotter_posts(blotter_id)
        flagged = [r for r in audit_results if not r.audit_passed]
        logging.info(
            f"Blotter auditor: {len(audit_results)} post(s) audited, "
            f"{len(flagged)} flagged for review"
        )
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'audit',
                'warn' if flagged else 'ok',
                {'audited': len(audit_results), 'flagged': len(flagged)},
            )
    except Exception as e:
        logging.warning(f"Blotter auditor failed for {label} #{blotter_id}: {e}")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'audit',
                'warn',
                {'error': str(e)},
            )

    visible_post_count = _post_count_for_blotter(blotter_id)
    if ingestion_job_id is not None:
        if visible_post_count > 0:
            log_pipeline_event(
                ingestion_job_id,
                'publish',
                'ok',
                {'blotter_id': blotter_id, 'post_count': visible_post_count},
            )
            set_ingestion_job_status(ingestion_job_id, 'published', finished=True)
        else:
            error_message = f'No public post was created for this {label}'
            log_pipeline_event(
                ingestion_job_id,
                'publish',
                'error',
                {'blotter_id': blotter_id, 'error': 'no-posts-created'},
            )
            increment_ingestion_retry(ingestion_job_id, error_message)
            set_ingestion_job_status(
                ingestion_job_id,
                'failed',
                last_error=error_message,
                finished=True,
            )

    return visible_post_count


def process_new_blotter(
    pdf_path: str,
    county: Optional[str] = None,
    source_document_id: Optional[int] = None,
    ingestion_job_id: Optional[int] = None,
) -> int:
    """
    Process a new blotter PDF file
    
    Args:
        pdf_path: Path to the PDF file
        county: Optional county name (will be auto-detected if not provided)
    
    Returns:
        batch_id: The ID of the created blotter batch
    """
    
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    filename = os.path.basename(pdf_path)
    if source_document_id is None:
        with open(pdf_path, 'rb') as handle:
            file_hash = sha256_bytes(handle.read())
        source_document_id = ensure_source_document(
            source_type='local_pdf',
            filename=filename,
            content_sha256=file_hash,
            storage_path=pdf_path,
            raw_text=None,
            extraction_method='local_file',
            extraction_warnings=[],
        )

    conn = _connect_db()
    source_column_exists = _table_has_column(conn, 'blotters', 'source_document_id')
    existing = None
    if source_document_id is not None and source_column_exists:
        existing = conn.execute(
            'SELECT id FROM blotters WHERE source_document_id = ?', (source_document_id,)
        ).fetchone()
    conn.close()
    if existing:
        logging.info(f"Skipping duplicate blotter: {filename} (already blotter #{existing[0]})")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'ingest',
                'ok',
                {'message': 'duplicate-skip', 'existing_blotter_id': existing[0]},
            )
        _publish_blotter_outputs(
            int(existing[0]),
            ingestion_job_id=ingestion_job_id,
            label='blotter',
        )
        return existing[0]

    logging.info(f"Processing blotter: {pdf_path}")
    
    # Step 1: Parse the PDF
    try:
        parser = BlotterParser(pdf_path)
        result = parser.parse()
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'parse',
                'ok',
                {'incident_count': result['total_count'], 'county': result['county']},
            )
            set_ingestion_job_status(ingestion_job_id, 'parsed')
    except Exception as e:
        logging.error(f"Failed to parse PDF: {e}")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'parse',
                'error',
                {'error': str(e)},
            )
            increment_ingestion_retry(ingestion_job_id, str(e))
            set_ingestion_job_status(ingestion_job_id, 'failed', last_error=str(e), finished=True)
        raise
    
    # Use detected county if not provided
    if not county:
        county = result['county']
    
    logging.info(f"Detected county: {county}, Found {result['total_count']} incidents")
    
    # Step 2: Insert into database
    conn = _connect_db()
    cursor = conn.cursor()
    
    try:
        incidents, skipped_duplicates = _filter_duplicate_incidents(
            conn,
            result['incidents'],
            county,
        )
        if skipped_duplicates:
            logging.info(
                f"Skipped {skipped_duplicates} duplicate incident(s) before insert for {filename}"
            )
        if not incidents:
            logging.info(f"All incidents already exist for {filename} — skipping batch creation")
            if ingestion_job_id is not None:
                log_pipeline_event(
                    ingestion_job_id,
                    'ingest',
                    'ok',
                    {'message': 'duplicate-skip-all-incidents', 'source_filename': filename},
                )
                set_ingestion_job_status(ingestion_job_id, 'published', finished=True)
            conn.close()
            return 0

        # Create the Batch Entry
        has_source_column = _table_has_column(conn, 'blotters', 'source_document_id')
        if has_source_column:
            cursor.execute(
                'INSERT INTO blotters (filename, county, incident_count, file_path, source_document_id) VALUES (?, ?, ?, ?, ?)',
                (filename, county, len(incidents), pdf_path, source_document_id)
            )
        else:
            cursor.execute(
                'INSERT INTO blotters (filename, county, incident_count, file_path) VALUES (?, ?, ?, ?)',
                (filename, county, len(incidents), pdf_path)
            )
        if cursor.lastrowid is None:
            raise RuntimeError('Failed to create blotter row')
        batch_id = int(cursor.lastrowid)
        logging.info(f"Created blotter batch #{batch_id}")
        
        # Insert individual incidents
        for incident in incidents:
            incident_type_val = incident.get('incident_type') or ''
            cursor.execute('''
                INSERT INTO records (
                    blotter_id, cfs_number, date, time, incident_type,
                    incident, location, details, county, officer
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                batch_id,
                incident.get('cfs_number'),
                incident.get('date'),
                incident.get('time'),
                incident_type_val,
                incident_type_val,  # legacy 'incident' column (NOT NULL)
                incident.get('location'),
                incident.get('details'),
                county,
                incident.get('officer')
            ))
            record_id = cursor.lastrowid
            
            # Insert command logs if available
            for log in incident.get('command_logs', []):
                cursor.execute('''
                    INSERT INTO command_logs (record_id, timestamp, officer, entry)
                    VALUES (?, ?, ?, ?)
                ''', (record_id, log.get('timestamp'), log.get('officer'), log.get('entry')))
        
        conn.commit()
        logging.info(f"✅ Batch #{batch_id} complete: {len(incidents)} incidents indexed")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'normalize',
                'ok',
                {
                    'blotter_id': batch_id,
                    'incident_count': len(incidents),
                    'duplicate_incidents_skipped': skipped_duplicates,
                },
            )
            set_ingestion_job_status(ingestion_job_id, 'normalized')
        # Release writer connection before downstream summary/audit writes.
        conn.close()
        conn = None

        _publish_blotter_outputs(
            batch_id,
            ingestion_job_id=ingestion_job_id,
            label='blotter',
        )

        return batch_id

    except Exception as e:
        if conn is not None:
            conn.rollback()
        logging.error(f"Database error: {e}")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'normalize',
                'error',
                {'error': str(e)},
            )
            increment_ingestion_retry(ingestion_job_id, str(e))
            set_ingestion_job_status(ingestion_job_id, 'failed', last_error=str(e), finished=True)
        raise
    finally:
        if conn is not None:
            conn.close()


def process_text_blotter(
    text: str,
    sender_email: Optional[str] = None,
    county: Optional[str] = None,
    source_document_id: Optional[int] = None,
    ingestion_job_id: Optional[int] = None,
) -> int:
    """
    Process a plain-text blotter from an email body.

    Args:
        text: Raw blotter text
        sender_email: Sender address for agency-type detection fallback
        county: Optional county override (auto-detected from text if omitted)

    Returns:
        blotter_id of the created blotter record
    """
    logging.info("Processing text blotter from email body")

    try:
        result = parse_text_blotter(text)
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'parse',
                'ok',
                {'incident_count': result['total_count'], 'county': result['county']},
            )
            set_ingestion_job_status(ingestion_job_id, 'parsed')
    except Exception as e:
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'parse',
                'error',
                {'error': str(e)},
            )
            increment_ingestion_retry(ingestion_job_id, str(e))
            set_ingestion_job_status(ingestion_job_id, 'failed', last_error=str(e), finished=True)
        raise

    if not county:
        county = result['county']

    logging.info(f"Text blotter: county={county}, incidents={result['total_count']}")

    conn = _connect_db()
    cursor = conn.cursor()

    try:
        existing = None
        has_source_column = _table_has_column(conn, 'blotters', 'source_document_id')
        if source_document_id is not None and has_source_column:
            existing = conn.execute(
                'SELECT id FROM blotters WHERE source_document_id = ?',
                (source_document_id,),
            ).fetchone()
        if existing:
            conn.close()
            conn = None
            logging.info(f"Skipping duplicate text blotter (already blotter #{existing[0]})")
            if ingestion_job_id is not None:
                log_pipeline_event(
                    ingestion_job_id,
                    'ingest',
                    'ok',
                    {'message': 'duplicate-skip', 'existing_blotter_id': existing[0]},
                )
            _publish_blotter_outputs(
                int(existing[0]),
                sender_email=sender_email,
                ingestion_job_id=ingestion_job_id,
                label='text blotter',
            )
            return int(existing[0])

        incidents, skipped_duplicates = _filter_duplicate_incidents(
            conn,
            result['incidents'],
            county,
        )
        if skipped_duplicates:
            logging.info(
                f"Skipped {skipped_duplicates} duplicate incident(s) before insert for text blotter"
            )
        if not incidents:
            logging.info("All text-body incidents already exist — skipping batch creation")
            if ingestion_job_id is not None:
                log_pipeline_event(
                    ingestion_job_id,
                    'ingest',
                    'ok',
                    {'message': 'duplicate-skip-all-incidents'},
                )
                set_ingestion_job_status(ingestion_job_id, 'published', finished=True)
            conn.close()
            return 0

        if has_source_column:
            cursor.execute(
                "INSERT INTO blotters (filename, county, incident_count, source_type, source_document_id) VALUES (?, ?, ?, ?, ?)",
                ("email-body", county, len(incidents), "text", source_document_id),
            )
        else:
            cursor.execute(
                "INSERT INTO blotters (filename, county, incident_count, source_type) VALUES (?, ?, ?, ?)",
                ("email-body", county, len(incidents), "text"),
            )
        if cursor.lastrowid is None:
            raise RuntimeError('Failed to create text blotter row')
        blotter_id = int(cursor.lastrowid)
        logging.info(f"Created text blotter batch #{blotter_id}")

        for incident in incidents:
            incident_type_val = incident.get('incident_type') or ''
            cursor.execute(
                """
                INSERT INTO records
                    (blotter_id, cfs_number, date, time, incident_type,
                     incident, location, details, county, officer)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    blotter_id,
                    incident.get('cfs_number'),
                    incident.get('date'),
                    incident.get('time'),
                    incident_type_val,
                    incident_type_val,  # legacy 'incident' column (NOT NULL)
                    incident.get('location'),
                    incident.get('details'),
                    county,
                    incident.get('officer'),
                ),
            )
            record_id = cursor.lastrowid

            for log in incident.get('command_logs', []):
                cursor.execute(
                    "INSERT INTO command_logs (record_id, timestamp, officer, entry) VALUES (?, ?, ?, ?)",
                    (record_id, log.get('timestamp'), log.get('officer'), log.get('entry')),
                )

        conn.commit()
        logging.info(f"✅ Text blotter #{blotter_id} complete: {len(incidents)} incidents indexed")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'normalize',
                'ok',
                {
                    'blotter_id': blotter_id,
                    'incident_count': len(incidents),
                    'duplicate_incidents_skipped': skipped_duplicates,
                },
            )
            set_ingestion_job_status(ingestion_job_id, 'normalized')
        # Release writer connection before downstream summary/audit writes.
        conn.close()
        conn = None

        _publish_blotter_outputs(
            blotter_id,
            sender_email=sender_email,
            ingestion_job_id=ingestion_job_id,
            label='text blotter',
        )

        return blotter_id

    except Exception as e:
        if conn is not None:
            conn.rollback()
        logging.error(f"Database error processing text blotter: {e}")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'normalize',
                'error',
                {'error': str(e)},
            )
            increment_ingestion_retry(ingestion_job_id, str(e))
            set_ingestion_job_status(ingestion_job_id, 'failed', last_error=str(e), finished=True)
        raise
    finally:
        if conn is not None:
            conn.close()


def update_web_data(pdf_path: str):
    """
    Legacy function name for compatibility with email_worker.py
    Calls process_new_blotter internally
    """
    return process_new_blotter(pdf_path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        pdf = sys.argv[1]
        county = sys.argv[2] if len(sys.argv) > 2 else None
        batch_id = process_new_blotter(pdf, county)
        print(f"Successfully processed blotter. Batch ID: {batch_id}")
    else:
        print("Usage: python processor.py <pdf_path> [county_name]")
