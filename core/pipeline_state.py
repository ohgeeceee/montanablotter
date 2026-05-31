import hashlib
import json
import sqlite3
import time
from typing import List, Optional

import config

DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))
DB_WRITE_RETRIES = int(getattr(config, "DB_WRITE_RETRIES", 3))
DB_RETRY_DELAY_SECONDS = float(getattr(config, "DB_RETRY_DELAY_SECONDS", 0.35))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn


def _execute_write(
    query: str,
    params: tuple,
    *,
    suppress_locked: bool = False,
) -> None:
    last_error: Optional[Exception] = None

    for attempt in range(DB_WRITE_RETRIES):
        conn = _connect()
        try:
            conn.execute(query, params)
            conn.commit()
            conn.close()
            return
        except sqlite3.OperationalError as exc:
            conn.close()
            last_error = exc
            if "database is locked" in str(exc).lower() and attempt + 1 < DB_WRITE_RETRIES:
                time.sleep(DB_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            if suppress_locked and "database is locked" in str(exc).lower():
                return
            raise

    if suppress_locked and isinstance(last_error, sqlite3.OperationalError):
        if "database is locked" in str(last_error).lower():
            return
    if last_error is not None:
        raise last_error


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_text(content: str) -> str:
    return sha256_bytes(content.encode("utf-8", errors="replace"))


def ensure_source_document(
    *,
    source_type: str,
    content_sha256: str,
    source_message_id: Optional[str] = None,
    source_sender: Optional[str] = None,
    source_subject: Optional[str] = None,
    source_received_at: Optional[str] = None,
    filename: Optional[str] = None,
    storage_path: Optional[str] = None,
    raw_text: Optional[str] = None,
    extraction_method: Optional[str] = None,
    extraction_warnings: Optional[List[str]] = None,
) -> int:
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id FROM source_documents
        WHERE source_type = ? AND content_sha256 = ?
        """,
        (source_type, content_sha256),
    )
    row = cursor.fetchone()
    if row:
        conn.close()
        return int(row["id"])

    cursor.execute(
        """
        INSERT INTO source_documents (
            source_type,
            source_message_id,
            source_sender,
            source_subject,
            source_received_at,
            filename,
            content_sha256,
            storage_path,
            raw_text,
            extraction_method,
            extraction_warnings
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_type,
            source_message_id,
            source_sender,
            source_subject,
            source_received_at,
            filename,
            content_sha256,
            storage_path,
            raw_text,
            extraction_method,
            json.dumps(extraction_warnings or []),
        ),
    )
    if cursor.lastrowid is None:
        conn.close()
        raise RuntimeError("Failed to create source_documents row")
    source_document_id = int(cursor.lastrowid)
    conn.commit()
    conn.close()
    return source_document_id


def ensure_ingestion_job(source_document_id: int) -> int:
    """Create or return ingestion job ID for a source document."""
    conn = _connect()
    try:
        row = conn.execute(
            'SELECT id FROM ingestion_jobs WHERE source_document_id = ?',
            (source_document_id,),
        ).fetchone()
        if row:
            return int(row['id'])
        # Use INSERT OR IGNORE to handle races gracefully, then re-query.
        conn.execute(
            '''
            INSERT OR IGNORE INTO ingestion_jobs (source_document_id, status, started_at)
            VALUES (?, 'idle', datetime('now'))
            ''',
            (source_document_id,),
        )
        conn.commit()
        row = conn.execute(
            'SELECT id FROM ingestion_jobs WHERE source_document_id = ?',
            (source_document_id,),
        ).fetchone()
        if row:
            return int(row['id'])
        return 0
    finally:
        conn.close()


def get_ingestion_job_status(source_document_id: int) -> Optional[str]:
    conn = _connect()
    try:
        row = conn.execute(
            'SELECT status FROM ingestion_jobs WHERE source_document_id = ?',
            (source_document_id,),
        ).fetchone()
        return row['status'] if row else None
    finally:
        conn.close()


def set_ingestion_job_status(job_id: int, status: str, *, last_error: Optional[str] = None, finished: bool = False) -> None:
    fields = ['status = ?', 'updated_at = datetime(\'now\')']
    params: List = [status]
    if last_error is not None:
        fields.append('last_error = ?')
        params.append(last_error)
    if finished:
        fields.append('finished_at = datetime(\'now\')')
    query = f"UPDATE ingestion_jobs SET {', '.join(fields)} WHERE id = ?"
    params.append(job_id)
    _execute_write(query, tuple(params))





def log_scraper_event(source_key: str, event: str, data: dict) -> None:
    _execute_write(
        '''
        INSERT INTO scraper_events (source_key, event_type, event_data, created_at)
        VALUES (?, ?, ?, datetime('now'))
        ''',
        (source_key, event, json.dumps(data)),
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# Legacy functions for backward compatibility with existing code

def get_ingestion_job_status_legacy(source_document_id: int) -> Optional[str]:
    conn = _connect()
    row = conn.execute(
        "SELECT status FROM ingestion_jobs WHERE source_document_id = ?",
        (source_document_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return str(row["status"])


def set_ingestion_job_status_legacy(job_id: int, status: str, last_error: Optional[str] = None, finished: bool = False) -> None:
    if finished:
        _execute_write(
            """
            UPDATE ingestion_jobs
            SET status = ?, last_error = ?, finished_at = datetime('now')
            WHERE id = ?
            """,
            (status, last_error, job_id),
        )
    else:
        _execute_write(
            """
            UPDATE ingestion_jobs
            SET status = ?, last_error = ?, finished_at = NULL
            WHERE id = ?
            """,
            (status, last_error, job_id),
        )


def increment_ingestion_retry_legacy(job_id: int) -> None:
    _execute_write(
        """
        UPDATE ingestion_jobs
        SET retry_count = retry_count + 1, updated_at = datetime('now')
        WHERE id = ?
        """,
        (job_id,),
    )


def log_pipeline_event_legacy(job_id: int, event_type: str, payload: dict) -> None:
    _execute_write(
        """
        INSERT INTO pipeline_events (job_id, event_type, payload, created_at)
        VALUES (?, ?, ?, datetime('now'))
        """,
        (job_id, event_type, json.dumps(payload)),
    )


def increment_ingestion_retry(job_id: int, last_error: Optional[str] = None) -> None:
    if last_error is not None:
        _execute_write(
            """
            UPDATE ingestion_jobs
            SET retry_count = retry_count + 1,
                status = 'failed',
                last_error = ?
            WHERE id = ?
            """,
            (last_error, job_id),
        )
    else:
        _execute_write(
            """
            UPDATE ingestion_jobs
            SET retry_count = retry_count + 1,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (job_id,),
        )


def log_pipeline_event(job_id: int, stage: str, status: str, details: Optional[dict] = None) -> None:
    _execute_write(
        """
        INSERT INTO pipeline_events (ingestion_job_id, stage, status, details_json)
        VALUES (?, ?, ?, ?)
        """,
        (job_id, stage, status, json.dumps(details or {})),
        suppress_locked=True,
    )
