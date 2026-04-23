import os
import traceback
from datetime import datetime, UTC
from typing import Any

from rq import Retry, get_current_job

from queue_config import parsing_q, publishing_q
from queue_helpers import record_dead_letter, sha256_file, upsert_job
from pipeline_state import (
    ensure_ingestion_job,
    ensure_source_document,
    get_ingestion_job_status,
    increment_ingestion_retry,
    log_pipeline_event,
    set_ingestion_job_status,
)
from processor import parse_pdf as parse_pdf_document, publish_blotter, store_parsed_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PARSING_RETRY = Retry(max=4, interval=[30, 120, 300, 900])
PUBLISHING_RETRY = Retry(max=4, interval=[30, 120, 300, 900])


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mark_started(task_name: str, queue_name: str, payload: dict[str, Any]) -> str:
    job = get_current_job()
    job_id = job.id if job else f"manual-{task_name}-{utcnow_iso()}"
    upsert_job(
        job_id=job_id,
        queue_name=queue_name,
        task_name=task_name,
        status="started",
        source_type=payload.get("source_type"),
        source_key=payload.get("source_key"),
        payload=payload,
        started_at=utcnow_iso(),
    )
    return job_id


def _mark_success(job_id: str, queue_name: str, task_name: str, payload: dict[str, Any]) -> None:
    upsert_job(
        job_id=job_id,
        queue_name=queue_name,
        task_name=task_name,
        status="succeeded",
        source_type=payload.get("source_type"),
        source_key=payload.get("source_key"),
        payload=payload,
        finished_at=utcnow_iso(),
    )


def _mark_failure(job_id: str, queue_name: str, task_name: str, payload: dict[str, Any], exc: Exception) -> None:
    error_text = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    upsert_job(
        job_id=job_id,
        queue_name=queue_name,
        task_name=task_name,
        status="failed",
        source_type=payload.get("source_type"),
        source_key=payload.get("source_key"),
        payload=payload,
        error_text=error_text,
        finished_at=utcnow_iso(),
    )
    record_dead_letter(
        job_id=job_id,
        queue_name=queue_name,
        task_name=task_name,
        source_type=payload.get("source_type"),
        source_key=payload.get("source_key"),
        payload=payload,
        error_text=error_text,
    )


def process_incoming_email_item(item: dict[str, Any]) -> dict[str, Any]:
    """
    item is expected to contain enough information to download/save
    an email attachment or refer to a previously saved file.

    Expected minimal shape:
    {
        "source_type": "email",
        "source_key": "<message-id or unique key>",
        "attachment_path": "/root/montanablotter/uploads/foo.pdf"   # preferred for first pass
    }
    """
    task_name = "process_incoming_email_item"
    queue_name = "ingestion"
    job_id = _mark_started(task_name, queue_name, item)

    try:
        attachment_path = item["attachment_path"]
        if not os.path.exists(attachment_path):
            raise FileNotFoundError(f"Attachment does not exist: {attachment_path}")

        file_hash = sha256_file(attachment_path)

        source_document_id = item.get("source_document_id")
        ingestion_job_id = item.get("ingestion_job_id")
        if source_document_id is None:
            source_document_id = ensure_source_document(
                source_type=item.get("source_type", "email"),
                source_message_id=item.get("source_key"),
                filename=os.path.basename(attachment_path),
                content_sha256=file_hash,
                storage_path=attachment_path,
                extraction_method="pdf_attachment",
                extraction_warnings=[],
            )
        if ingestion_job_id is None:
            ingestion_job_id = ensure_ingestion_job(int(source_document_id))

        existing_status = get_ingestion_job_status(int(source_document_id))
        if existing_status == "published":
            _mark_success(job_id, queue_name, task_name, {**item, "status": "duplicate-published-skip"})
            return {"ok": True, "skipped": True, "reason": "already_published"}

        set_ingestion_job_status(int(ingestion_job_id), "extracted")
        log_pipeline_event(
            int(ingestion_job_id),
            "extract",
            "ok",
            {"attachment_path": attachment_path},
        )

        parse_payload = {
            "source_type": item.get("source_type", "email"),
            "source_key": item.get("source_key", attachment_path),
            "attachment_path": attachment_path,
            "file_hash": file_hash,
            "source_document_id": int(source_document_id),
            "ingestion_job_id": int(ingestion_job_id),
        }

        queued_job = parsing_q.enqueue(
            "tasks.parse_pdf",
            parse_payload,
            job_timeout=30 * 60,
            retry=PARSING_RETRY,
            result_ttl=24 * 60 * 60,
            failure_ttl=14 * 24 * 60 * 60,
        )

        _mark_success(job_id, queue_name, task_name, {
            **item,
            "file_hash": file_hash,
            "next_job_id": queued_job.id,
        })

        return {
            "ok": True,
            "file_hash": file_hash,
            "next_job_id": queued_job.id,
        }

    except Exception as exc:
        _mark_failure(job_id, queue_name, task_name, item, exc)
        raise


def parse_pdf(payload: dict[str, Any]) -> dict[str, Any]:
    """Parsing stage."""
    task_name = "parse_pdf"
    queue_name = "parsing"
    job_id = _mark_started(task_name, queue_name, payload)

    try:
        attachment_path = payload["attachment_path"]
        if not os.path.exists(attachment_path):
            raise FileNotFoundError(f"PDF does not exist: {attachment_path}")

        ingestion_job_id = payload.get("ingestion_job_id")
        parsed = parse_pdf_document(
            attachment_path,
            ingestion_job_id=int(ingestion_job_id) if ingestion_job_id is not None else None,
        )

        publish_payload = {
            **payload,
            "parsed": parsed,
            "incident_count": int(parsed.get("total_count") or 0),
        }

        queued_job = publishing_q.enqueue(
            "tasks.publish_incidents",
            publish_payload,
            job_timeout=30 * 60,
            retry=PUBLISHING_RETRY,
            result_ttl=24 * 60 * 60,
            failure_ttl=14 * 24 * 60 * 60,
        )

        _mark_success(job_id, queue_name, task_name, {
            **payload,
            "incident_count": int(parsed.get("total_count") or 0),
            "next_job_id": queued_job.id,
        })

        return {
            "ok": True,
            "incident_count": int(parsed.get("total_count") or 0),
            "next_job_id": queued_job.id,
        }

    except Exception as exc:
        ingestion_job_id = payload.get("ingestion_job_id")
        if ingestion_job_id is not None:
            increment_ingestion_retry(int(ingestion_job_id), str(exc))
            set_ingestion_job_status(int(ingestion_job_id), "failed", last_error=str(exc), finished=True)
            log_pipeline_event(
                int(ingestion_job_id),
                "parse",
                "error",
                {"error": str(exc)},
            )
        _mark_failure(job_id, queue_name, task_name, payload, exc)
        raise


def publish_incidents(payload: dict[str, Any]) -> dict[str, Any]:
    """Publishing stage (store normalized data + publish downstream outputs)."""
    task_name = "publish_incidents"
    queue_name = "publishing"
    job_id = _mark_started(task_name, queue_name, payload)

    try:
        attachment_path = payload["attachment_path"]
        parsed = payload.get("parsed") or {}
        source_document_id = payload.get("source_document_id")
        ingestion_job_id = payload.get("ingestion_job_id")

        blotter_id = store_parsed_pdf(
            attachment_path,
            parsed,
            source_document_id=int(source_document_id) if source_document_id is not None else None,
            ingestion_job_id=int(ingestion_job_id) if ingestion_job_id is not None else None,
        )
        post_count = publish_blotter(
            int(blotter_id),
            ingestion_job_id=int(ingestion_job_id) if ingestion_job_id is not None else None,
            label="blotter",
        )

        _mark_success(job_id, queue_name, task_name, {
            **payload,
            "blotter_id": int(blotter_id),
            "post_count": int(post_count),
        })

        return {
            "ok": True,
            "blotter_id": int(blotter_id),
            "post_count": int(post_count),
        }

    except Exception as exc:
        ingestion_job_id = payload.get("ingestion_job_id")
        if ingestion_job_id is not None:
            increment_ingestion_retry(int(ingestion_job_id), str(exc))
            set_ingestion_job_status(int(ingestion_job_id), "failed", last_error=str(exc), finished=True)
            log_pipeline_event(
                int(ingestion_job_id),
                "publish",
                "error",
                {"error": str(exc)},
            )
        _mark_failure(job_id, queue_name, task_name, payload, exc)
        raise


# Backward-compatible aliases for previously queued job names.
parse_pdf_task = parse_pdf
store_and_publish_task = publish_incidents
