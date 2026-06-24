"""
Processor - Handles PDF parsing and database insertion
Replaces the old processor.py with actual parsing logic
"""

import sqlite3
import os
import logging
import threading
from typing import Optional
import config
from db import timed_db_transaction
from services.blotter.parser import BlotterParser, parse_text_blotter
import services.summarizer.engine as summarizer
import services.blotter.auditor as blotter_auditor
from services.blotter.analytics import classify_charge
from services.alerts.dispatcher import dispatch_alerts
from core.dedupe import incident_key_set, incident_keys
from core.pipeline_state import (
    ensure_source_document,
    increment_ingestion_retry,
    log_pipeline_event,
    sha256_bytes,
    set_ingestion_job_status,
    set_ingestion_job_status_legacy,
)

DB_PATH = config.DB_PATH
DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))

# Number of records to insert per transaction in store_parsed_pdf. Smaller
# batches release the SQLite write lock more often, reducing contention with
# concurrent jail roster ingests and other writers.
RECORD_INSERT_BATCH_SIZE = int(getattr(config, "BLOTTER_RECORD_INSERT_BATCH_SIZE", 100))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute(f'PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}')
    return conn

_ALLOWED_TABLES = {'blotters', 'records', 'posts', 'command_logs', 'ingestion_jobs'}

def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if table_name not in _ALLOWED_TABLES:
        return False
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


def _async_geocode_blotter_records(blotter_id: int) -> None:
    """Background-thread geocoding for freshly ingested records. Daemon so it never blocks shutdown."""
    def _run():
        try:
            from services.geo.pipeline import geocode_location, GEOCODE_SLEEP
            import time
            conn = _connect_db()
            rows = conn.execute(
                """SELECT r.id, r.location, r.county
                   FROM records r
                   LEFT JOIN incident_geocodes g ON r.id = g.record_id
                   WHERE r.blotter_id = ?
                     AND g.record_id IS NULL
                     AND r.location IS NOT NULL
                     AND trim(r.location) != ''
                   LIMIT 100""",
                (blotter_id,),
            ).fetchall()
            geocoded = 0
            for row in rows:
                result = geocode_location(row[1], county=row[2])
                if result:
                    conn.execute(
                        """INSERT OR IGNORE INTO incident_geocodes
                           (record_id, raw_location, lat, lng, geocode_confidence, county, geocoded_at)
                           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                        (row[0], row[1], result["lat"], result["lng"],
                         result["confidence"], row[2]),
                    )
                    geocoded += 1
                time.sleep(GEOCODE_SLEEP)
            conn.commit()
            conn.close()
            if geocoded:
                logging.info(f"Background geocoder: {geocoded} records geocoded for blotter #{blotter_id}")
        except Exception as e:
            logging.warning(f"Background geocoder failed for blotter #{blotter_id}: {e}")

    threading.Thread(target=_run, daemon=True).start()


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
        logging.error(f"Post generation failed for {label} #{blotter_id}: {e}")
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
        try:
            from facebook_publisher import auto_queue_post_if_enabled

            queued = 0
            for result in audit_results:
                if result.audit_passed and result.post_id is not None:
                    queue_result = auto_queue_post_if_enabled(int(result.post_id))
                    if queue_result.get("queued"):
                        queued += 1
            if queued:
                logging.info(f"Facebook auto-queued {queued} audited post(s) for {label} #{blotter_id}")
        except Exception as e:
            logging.warning(f"Facebook auto-queue after audit failed for {label} #{blotter_id}: {e}")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'audit',
                'warn' if flagged else 'ok',
                {'audited': len(audit_results), 'flagged': len(flagged)},
            )
    except Exception as e:
        logging.error(f"Blotter auditor failed for {label} #{blotter_id}: {e}")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'audit',
                'warn',
                {'error': str(e)},
            )

    try:
        _conn = _connect_db()
        _row = _conn.execute("SELECT county FROM blotters WHERE id=?", (blotter_id,)).fetchone()
        _blotter_county = (_row[0] if _row else None) or 'Unknown'
        _conn.close()
        alerts_sent = dispatch_alerts(blotter_id, _blotter_county)
        if alerts_sent:
            logging.info(f"Alert dispatcher: {alerts_sent} alert(s) sent for blotter #{blotter_id}")
    except Exception as e:
        logging.error(f"Alert dispatcher failed for blotter #{blotter_id}: {e}")

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
            # Check if this is a duplicate blotter with an existing post —
            # if so, mark published instead of failed.
            _dup_conn = _connect_db()
            _dup_row = _dup_conn.execute(
                'SELECT 1 FROM posts WHERE blotter_id = ? LIMIT 1', (blotter_id,)
            ).fetchone()
            _dup_conn.close()
            if _dup_row:
                log_pipeline_event(
                    ingestion_job_id,
                    'publish',
                    'ok',
                    {'blotter_id': blotter_id, 'post_count': 0, 'note': 'duplicate-blotter-existing-post'},
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

    _async_geocode_blotter_records(blotter_id)
    return visible_post_count


def parse_pdf(
    pdf_path: str,
    county: Optional[str] = None,
    ingestion_job_id: Optional[int] = None,
) -> dict:
    """Parse a blotter PDF into structured incidents without storing/publishing."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    parser = BlotterParser(pdf_path)
    result = parser.parse()
    result_county = county or result.get('county')
    parsed = {
        'county': result_county,
        'total_count': int(result.get('total_count') or 0),
        'incidents': result.get('incidents') or [],
    }
    if ingestion_job_id is not None:
        log_pipeline_event(
            ingestion_job_id,
            'parse',
            'ok',
            {'incident_count': parsed['total_count'], 'county': parsed['county']},
        )
        set_ingestion_job_status(ingestion_job_id, 'parsed')
    return parsed


def _extract_date_from_subject(subject: str) -> Optional[str]:
    """Extract a date from email subject or filename like 'LOG 5-4', '5/6/ log', '0507 log'."""
    import re
    from datetime import datetime

    year = datetime.today().year

    # Pattern: "5/6/ log" or "5/6 log" → YYYY-05-06
    m = re.search(r'(\d{1,2})/(\d{1,2})/?\s+', subject)
    if m:
        try:
            dt = datetime(year, int(m.group(1)), int(m.group(2)))
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

    # Pattern: "LOG 5-4" or "5-5 LOG" or "5-4 media log" → YYYY-05-04
    m = re.search(r'(?:^|\s)(\d{1,2})-(\d{1,2})(?:\s|$|\D)', subject)
    if m:
        try:
            dt = datetime(year, int(m.group(1)), int(m.group(2)))
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

    # Pattern: "0507 log" → YYYY-05-07
    m = re.search(r'(?:^|\s)(\d{2})(\d{2})\s+', subject)
    if m:
        try:
            month = int(m.group(1))
            day = int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                dt = datetime(year, month, day)
                return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

    return None


def _delete_partial_blotter(conn: sqlite3.Connection, blotter_id: int) -> None:
    """Remove a blotter and all of its records/command_logs.

    Used when batch-inserting a blotter fails partway through so the retry
    path can re-create it cleanly.
    """
    try:
        conn.execute(
            "DELETE FROM command_logs WHERE record_id IN (SELECT id FROM records WHERE blotter_id = ?)",
            (blotter_id,),
        )
        conn.execute("DELETE FROM records WHERE blotter_id = ?", (blotter_id,))
        conn.execute("DELETE FROM blotters WHERE id = ?", (blotter_id,))
        conn.commit()
        logging.info(f"Cleaned up partial blotter #{blotter_id}")
    except Exception as exc:
        conn.rollback()
        logging.error(f"Failed to clean up partial blotter #{blotter_id}: {exc}")
        raise


def store_parsed_pdf(
    pdf_path: str,
    parsed: dict,
    county: Optional[str] = None,
    source_document_id: Optional[int] = None,
    ingestion_job_id: Optional[int] = None,
) -> int:
    """
    Persist parsed incidents into blotters/records tables.
    Returns blotter id, or 0 when all incidents are duplicates.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    filename = os.path.basename(pdf_path)
    incidents = list(parsed.get('incidents') or [])
    parsed_county = county or parsed.get('county')

    # --- Date fallback: if parser couldn't extract dates, try source_document subject/filename ---
    fallback_date = None
    has_missing_dates = any(not inc.get('date') for inc in incidents)
    if has_missing_dates and source_document_id is not None:
        conn = _connect_db()
        try:
            row = conn.execute(
                'SELECT source_subject, filename FROM source_documents WHERE id = ?',
                (source_document_id,),
            ).fetchone()
            if row:
                subject, sd_filename = row[0], row[1]
                fallback_date = _extract_date_from_subject(subject or '')
                if not fallback_date:
                    fallback_date = _extract_date_from_subject(sd_filename or '')
                if not fallback_date:
                    fallback_date = _extract_date_from_subject(filename or '')
        except Exception:
            pass
        finally:
            conn.close()

    # Ultimate fallback: today's date
    if not fallback_date:
        from datetime import datetime
        fallback_date = datetime.now().strftime('%Y-%m-%d')

    # Apply fallback to any incident missing a date
    for inc in incidents:
        if not inc.get('date'):
            inc['date'] = fallback_date

    conn = _connect_db()
    source_column_exists = _table_has_column(conn, 'blotters', 'source_document_id')
    existing = None
    if source_document_id is not None and source_column_exists:
        existing = conn.execute(
            'SELECT id FROM blotters WHERE source_document_id = ?',
            (source_document_id,),
        ).fetchone()
    conn.close()
    if existing:
        existing_id = int(existing[0])
        logging.info(f"Skipping duplicate blotter store: {filename} (already blotter #{existing_id})")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'ingest',
                'ok',
                {'message': 'duplicate-skip', 'existing_blotter_id': existing_id},
            )
            set_ingestion_job_status(ingestion_job_id, 'normalized')
        return existing_id

    conn = _connect_db()
    cursor = conn.cursor()
    batch_id: Optional[int] = None

    try:
        with timed_db_transaction("store_parsed_pdf"):
            incidents, skipped_duplicates = _filter_duplicate_incidents(
                conn,
                incidents,
                parsed_county,
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

            has_source_column = _table_has_column(conn, 'blotters', 'source_document_id')
            if has_source_column:
                cursor.execute(
                    'INSERT INTO blotters (filename, county, incident_count, file_path, source_document_id) VALUES (?, ?, ?, ?, ?)',
                    (filename, parsed_county, len(incidents), pdf_path, source_document_id),
                )
            else:
                cursor.execute(
                    'INSERT INTO blotters (filename, county, incident_count, file_path) VALUES (?, ?, ?, ?)',
                    (filename, parsed_county, len(incidents), pdf_path),
                )
            if cursor.lastrowid is None:
                raise RuntimeError('Failed to create blotter row')
            batch_id = int(cursor.lastrowid)
            conn.commit()
            logging.info(f"Created blotter batch #{batch_id}")

            # Insert records in batches, committing each batch so the SQLite write
            # lock is released frequently. This prevents a large blotter from
            # monopolizing the database and blocking concurrent jail roster ingests.
            inserted_count = 0
            batch_size = max(1, RECORD_INSERT_BATCH_SIZE)
            for i in range(0, len(incidents), batch_size):
                chunk = incidents[i : i + batch_size]
                for incident in chunk:
                    incident_type_val = incident.get('incident_type') or ''
                    cursor.execute(
                        '''
                        INSERT INTO records (
                            blotter_id, cfs_number, date, time, incident_type,
                            incident, location, details, county, officer, charge_category
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            batch_id,
                            incident.get('cfs_number'),
                            incident.get('date'),
                            incident.get('time'),
                            incident_type_val,
                            incident_type_val,
                            incident.get('location'),
                            incident.get('details'),
                            parsed_county,
                            incident.get('officer'),
                            classify_charge(incident_type_val),
                        ),
                    )
                    record_id = cursor.lastrowid
                    if not record_id:
                        raise RuntimeError('Failed to insert record row — lastrowid is None')

                    for log in incident.get('command_logs', []):
                        cursor.execute(
                            '''
                            INSERT INTO command_logs (record_id, timestamp, officer, entry)
                            VALUES (?, ?, ?, ?)
                            ''',
                            (record_id, log.get('timestamp'), log.get('officer'), log.get('entry')),
                        )

                conn.commit()
                inserted_count += len(chunk)
                logging.info(
                    f"Batch #{batch_id}: inserted {inserted_count}/{len(incidents)} incidents"
                )

            # Normalize incident_count to the actual number inserted.
            conn.execute(
                'UPDATE blotters SET incident_count = ? WHERE id = ?',
                (inserted_count, batch_id),
            )
            conn.commit()
            logging.info(f"✅ Batch #{batch_id} complete: {inserted_count} incidents indexed")
            if ingestion_job_id is not None:
                log_pipeline_event(
                    ingestion_job_id,
                    'normalize',
                    'ok',
                    {
                        'blotter_id': batch_id,
                        'incident_count': inserted_count,
                        'duplicate_incidents_skipped': skipped_duplicates,
                    },
                )
                set_ingestion_job_status(ingestion_job_id, 'normalized')
            return batch_id

    except Exception:
        conn.rollback()
        if batch_id is not None:
            _delete_partial_blotter(_connect_db(), batch_id)
        raise
    finally:
        conn.close()


def publish_blotter(
    blotter_id: int,
    sender_email: Optional[str] = None,
    ingestion_job_id: Optional[int] = None,
    label: str = 'blotter',
) -> int:
    """Publish downstream artifacts for an existing blotter id."""
    if blotter_id <= 0:
        return 0
    return _publish_blotter_outputs(
        blotter_id,
        sender_email=sender_email,
        ingestion_job_id=ingestion_job_id,
        label=label,
    )


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

    try:
        logging.info(f"Processing blotter: {pdf_path}")
        parsed = parse_pdf(
            pdf_path,
            county=county,
            ingestion_job_id=ingestion_job_id,
        )
        logging.info(f"Detected county: {parsed.get('county')}, Found {parsed.get('total_count')} incidents")

        batch_id = store_parsed_pdf(
            pdf_path,
            parsed,
            county=county,
            source_document_id=source_document_id,
            ingestion_job_id=ingestion_job_id,
        )
        publish_blotter(
            batch_id,
            ingestion_job_id=ingestion_job_id,
            label='blotter',
        )
        return batch_id
    except Exception as e:
        logging.error(f"Pipeline error: {e}")
        if ingestion_job_id is not None:
            increment_ingestion_retry(ingestion_job_id, str(e))
            set_ingestion_job_status_legacy(
                ingestion_job_id, 'failed', last_error=str(e), finished=True
            )
        raise


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
                     incident, location, details, county, officer, charge_category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    classify_charge(incident_type_val),
                ),
            )
            record_id = cursor.lastrowid
            if not record_id:
                raise RuntimeError('Failed to insert record row — lastrowid is None')

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
