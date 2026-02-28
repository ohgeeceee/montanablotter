"""
Processor - Handles PDF parsing and database insertion
Replaces the old processor.py with actual parsing logic
"""

import sqlite3
import os
import logging
from typing import Optional
from pdf_parser import BlotterParser, parse_text_blotter
import summarizer
from pipeline_state import (
    increment_ingestion_retry,
    log_pipeline_event,
    set_ingestion_job_status,
)

DB_PATH = '/root/montanablotter/blotter.db'
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(r[1] == column_name for r in rows)


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

    conn = sqlite3.connect(DB_PATH)
    source_column_exists = _table_has_column(conn, 'blotters', 'source_document_id')
    existing = None
    if source_document_id is not None and source_column_exists:
        existing = conn.execute(
            'SELECT id FROM blotters WHERE source_document_id = ?', (source_document_id,)
        ).fetchone()
    if not existing:
        existing = conn.execute(
            'SELECT id FROM blotters WHERE filename = ?', (filename,)
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
            set_ingestion_job_status(ingestion_job_id, 'published', finished=True)
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Create the Batch Entry
        has_source_column = _table_has_column(conn, 'blotters', 'source_document_id')
        if has_source_column:
            cursor.execute(
                'INSERT INTO blotters (filename, county, incident_count, file_path, source_document_id) VALUES (?, ?, ?, ?, ?)',
                (filename, county, result['total_count'], pdf_path, source_document_id)
            )
        else:
            cursor.execute(
                'INSERT INTO blotters (filename, county, incident_count, file_path) VALUES (?, ?, ?, ?)',
                (filename, county, result['total_count'], pdf_path)
            )
        if cursor.lastrowid is None:
            raise RuntimeError('Failed to create blotter row')
        batch_id = int(cursor.lastrowid)
        logging.info(f"Created blotter batch #{batch_id}")
        
        # Insert individual incidents
        for incident in result['incidents']:
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
        logging.info(f"✅ Batch #{batch_id} complete: {result['total_count']} incidents indexed")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'normalize',
                'ok',
                {'blotter_id': batch_id, 'incident_count': result['total_count']},
            )
            set_ingestion_job_status(ingestion_job_id, 'normalized')

        # Generate AI posts for all new records
        try:
            post_count = summarizer.generate_posts(batch_id)
            logging.info(f"Generated {post_count} posts for batch #{batch_id}")
            if ingestion_job_id is not None:
                log_pipeline_event(
                    ingestion_job_id,
                    'summarize',
                    'ok',
                    {'post_count': post_count},
                )
        except Exception as e:
            logging.warning(f"Post generation failed for batch #{batch_id}: {e}")
            if ingestion_job_id is not None:
                log_pipeline_event(
                    ingestion_job_id,
                    'summarize',
                    'warn',
                    {'error': str(e)},
                )

        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'publish',
                'ok',
                {'blotter_id': batch_id},
            )
            set_ingestion_job_status(ingestion_job_id, 'published', finished=True)

        return batch_id

    except Exception as e:
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

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        has_source_column = _table_has_column(conn, 'blotters', 'source_document_id')
        if has_source_column:
            cursor.execute(
                "INSERT INTO blotters (filename, county, incident_count, source_type, source_document_id) VALUES (?, ?, ?, ?, ?)",
                ("email-body", county, result['total_count'], "text", source_document_id),
            )
        else:
            cursor.execute(
                "INSERT INTO blotters (filename, county, incident_count, source_type) VALUES (?, ?, ?, ?)",
                ("email-body", county, result['total_count'], "text"),
            )
        if cursor.lastrowid is None:
            raise RuntimeError('Failed to create text blotter row')
        blotter_id = int(cursor.lastrowid)
        logging.info(f"Created text blotter batch #{blotter_id}")

        for incident in result['incidents']:
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
        logging.info(f"✅ Text blotter #{blotter_id} complete: {result['total_count']} incidents indexed")
        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'normalize',
                'ok',
                {'blotter_id': blotter_id, 'incident_count': result['total_count']},
            )
            set_ingestion_job_status(ingestion_job_id, 'normalized')

        try:
            post_count = summarizer.generate_posts(blotter_id, sender_email=sender_email)
            logging.info(f"Generated {post_count} posts for text blotter #{blotter_id}")
            if ingestion_job_id is not None:
                log_pipeline_event(
                    ingestion_job_id,
                    'summarize',
                    'ok',
                    {'post_count': post_count},
                )
        except Exception as e:
            logging.warning(f"Post generation failed for text blotter #{blotter_id}: {e}")
            if ingestion_job_id is not None:
                log_pipeline_event(
                    ingestion_job_id,
                    'summarize',
                    'warn',
                    {'error': str(e)},
                )

        if ingestion_job_id is not None:
            log_pipeline_event(
                ingestion_job_id,
                'publish',
                'ok',
                {'blotter_id': blotter_id},
            )
            set_ingestion_job_status(ingestion_job_id, 'published', finished=True)

        return blotter_id

    except Exception as e:
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
