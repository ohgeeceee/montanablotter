import contextlib
import hashlib
import json
import os
import sqlite3
from datetime import datetime, UTC
from typing import Any

from queue_config import redis_conn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.getenv("MB_DB_PATH", os.path.join(BASE_DIR, "blotter.db"))


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_jobs_table(db_path: str = DEFAULT_DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE,
                queue_name TEXT NOT NULL,
                task_name TEXT NOT NULL,
                source_type TEXT,
                source_key TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error_text TEXT,
                payload_json TEXT
            )
            """
        )
        conn.commit()


def ensure_dead_letters_table(db_path: str = DEFAULT_DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT,
                queue_name TEXT NOT NULL,
                task_name TEXT NOT NULL,
                source_type TEXT,
                source_key TEXT,
                error_text TEXT NOT NULL,
                payload_json TEXT,
                failed_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def upsert_job(
    job_id: str,
    queue_name: str,
    task_name: str,
    status: str,
    source_type: str | None = None,
    source_key: str | None = None,
    payload: dict[str, Any] | None = None,
    error_text: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    ensure_jobs_table(db_path=db_path)
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None

    with sqlite3.connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE jobs
                SET queue_name = ?,
                    task_name = ?,
                    source_type = ?,
                    source_key = ?,
                    status = ?,
                    started_at = COALESCE(?, started_at),
                    finished_at = COALESCE(?, finished_at),
                    error_text = ?,
                    payload_json = COALESCE(?, payload_json)
                WHERE job_id = ?
                """,
                (
                    queue_name,
                    task_name,
                    source_type,
                    source_key,
                    status,
                    started_at,
                    finished_at,
                    error_text,
                    payload_json,
                    job_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, queue_name, task_name, source_type, source_key,
                    status, created_at, started_at, finished_at, error_text, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    queue_name,
                    task_name,
                    source_type,
                    source_key,
                    status,
                    utcnow_iso(),
                    started_at,
                    finished_at,
                    error_text,
                    payload_json,
                ),
            )
        conn.commit()


def record_dead_letter(
    *,
    job_id: str,
    queue_name: str,
    task_name: str,
    source_type: str | None = None,
    source_key: str | None = None,
    payload: dict[str, Any] | None = None,
    error_text: str,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    ensure_dead_letters_table(db_path=db_path)
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO dead_letters (
                job_id, queue_name, task_name, source_type, source_key,
                error_text, payload_json, failed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                queue_name,
                task_name,
                source_type,
                source_key,
                error_text,
                payload_json,
                utcnow_iso(),
            ),
        )
        conn.commit()


@contextlib.contextmanager
def redis_lock(name: str, timeout: int = 1800):
    lock = redis_conn.lock(name, timeout=timeout)
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass
