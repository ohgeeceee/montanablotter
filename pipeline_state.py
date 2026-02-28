import hashlib
import json
import sqlite3
from typing import List, Optional

import config


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


def ensure_ingestion_job(source_document_id: int, status: str = "received") -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM ingestion_jobs WHERE source_document_id = ?",
        (source_document_id,),
    )
    row = cursor.fetchone()
    if row:
        job_id = int(row["id"])
    else:
        cursor.execute(
            """
            INSERT INTO ingestion_jobs (source_document_id, status)
            VALUES (?, ?)
            """,
            (source_document_id, status),
        )
        if cursor.lastrowid is None:
            conn.close()
            raise RuntimeError("Failed to create ingestion_jobs row")
        job_id = int(cursor.lastrowid)
        conn.commit()
    conn.close()
    return job_id


def get_ingestion_job_status(source_document_id: int) -> Optional[str]:
    conn = _connect()
    row = conn.execute(
        "SELECT status FROM ingestion_jobs WHERE source_document_id = ?",
        (source_document_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return str(row["status"])


def set_ingestion_job_status(job_id: int, status: str, last_error: Optional[str] = None, finished: bool = False) -> None:
    conn = _connect()
    cursor = conn.cursor()
    if finished:
        cursor.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?, last_error = ?, finished_at = datetime('now')
            WHERE id = ?
            """,
            (status, last_error, job_id),
        )
    else:
        cursor.execute(
            """
            UPDATE ingestion_jobs
            SET status = ?, last_error = ?
            WHERE id = ?
            """,
            (status, last_error, job_id),
        )
    conn.commit()
    conn.close()


def increment_ingestion_retry(job_id: int, last_error: str) -> None:
    conn = _connect()
    conn.execute(
        """
        UPDATE ingestion_jobs
        SET retry_count = retry_count + 1,
            status = 'failed',
            last_error = ?
        WHERE id = ?
        """,
        (last_error, job_id),
    )
    conn.commit()
    conn.close()


def log_pipeline_event(job_id: int, stage: str, status: str, details: Optional[dict] = None) -> None:
    conn = _connect()
    conn.execute(
        """
        INSERT INTO pipeline_events (ingestion_job_id, stage, status, details_json)
        VALUES (?, ?, ?, ?)
        """,
        (job_id, stage, status, json.dumps(details or {})),
    )
    conn.commit()
    conn.close()
