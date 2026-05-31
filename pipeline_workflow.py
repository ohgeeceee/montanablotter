"""
pipeline_workflow.py — Dynamic blotter pipeline orchestrator.

Stages (in order):
  parse     — extract records from a PDF attachment
  store     — normalize + write blotter/records to DB
  summarize — generate AI post drafts via Claude
  audit     — PII scan + SEO metadata via blotter_auditor
  alerts    — dispatch email alerts to subscribers
  facebook  — auto-queue clean posts to Facebook

Usage:
  # Full run from a raw PDF
  python pipeline_workflow.py --attachment /root/montanablotter/uploads/foo.pdf

  # Re-run downstream stages on an already-stored blotter
  python pipeline_workflow.py --blotter-id 42

  # Only specific stages
  python pipeline_workflow.py --blotter-id 42 --stages summarize,audit

  # Skip a stage
  python pipeline_workflow.py --blotter-id 42 --skip facebook

Can also be called from code:
  from pipeline_workflow import run_workflow, WorkflowContext
  ctx = WorkflowContext(blotter_id=42)
  result = run_workflow(ctx, skip=["facebook"])
  print(result.summary())
"""

import argparse
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import config
from core.pipeline_state import log_pipeline_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class StageResult:
    name: str
    ok: bool
    skipped: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed_s: float = 0.0

    def __str__(self) -> str:
        if self.skipped:
            return f"  [SKIP]  {self.name}"
        tag = "OK  " if self.ok else "FAIL"
        suffix = f"  ({self.elapsed_s:.1f}s)"
        if not self.ok:
            suffix += f"  — {self.error}"
        return f"  [{tag}]  {self.name}{suffix}"


@dataclass
class WorkflowResult:
    stages: list[StageResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok or s.skipped for s in self.stages)

    @property
    def failed_stages(self) -> list[StageResult]:
        return [s for s in self.stages if not s.ok and not s.skipped]

    def summary(self) -> str:
        lines = ["", "── Pipeline result ──────────────────────────────"]
        for s in self.stages:
            lines.append(str(s))
        lines.append("─────────────────────────────────────────────────")
        if self.ok:
            lines.append("  Overall: SUCCESS")
        else:
            failed = ", ".join(s.name for s in self.failed_stages)
            lines.append(f"  Overall: FAILED  (stages: {failed})")
        lines.append("")
        return "\n".join(lines)


# ── Shared context ─────────────────────────────────────────────────────────────

@dataclass
class WorkflowContext:
    """Mutable state shared across all pipeline stages."""
    blotter_id: Optional[int] = None
    attachment_path: Optional[str] = None
    ingestion_job_id: Optional[int] = None
    source_document_id: Optional[int] = None
    # Populated by parse stage, consumed by store stage
    parsed: Optional[dict] = None
    # Populated by summarize stage, used for reporting
    post_count: int = 0
    # Populated by audit stage, consumed by facebook stage
    audit_results: list = field(default_factory=list)


# ── Stage definition ──────────────────────────────────────────────────────────

@dataclass
class Stage:
    name: str
    fn: Callable[[WorkflowContext], dict[str, Any]]
    # If True, pipeline halts on failure; if False, logs and continues
    abort_on_failure: bool = True


# ── Stage implementations ─────────────────────────────────────────────────────

def _stage_parse(ctx: WorkflowContext) -> dict[str, Any]:
    if not ctx.attachment_path:
        raise ValueError("attachment_path required for parse stage")
    from services.blotter.processor import parse_pdf
    ctx.parsed = parse_pdf(
        ctx.attachment_path,
        ingestion_job_id=ctx.ingestion_job_id,
    )
    return {"incident_count": int(ctx.parsed.get("total_count") or 0)}


def _stage_store(ctx: WorkflowContext) -> dict[str, Any]:
    if not ctx.attachment_path or ctx.parsed is None:
        raise ValueError("attachment_path and parsed data required for store stage")
    from services.blotter.processor import store_parsed_pdf
    ctx.blotter_id = int(store_parsed_pdf(
        ctx.attachment_path,
        ctx.parsed,
        source_document_id=ctx.source_document_id,
        ingestion_job_id=ctx.ingestion_job_id,
    ))
    return {"blotter_id": ctx.blotter_id}


def _stage_summarize(ctx: WorkflowContext) -> dict[str, Any]:
    if ctx.blotter_id is None:
        raise ValueError("blotter_id required for summarize stage")
    import services.summarizer.engine as summarizer
    ctx.post_count = int(summarizer.generate_posts(
        ctx.blotter_id,
        ingestion_job_id=ctx.ingestion_job_id,
    ))
    return {"post_count": ctx.post_count}


def _stage_audit(ctx: WorkflowContext) -> dict[str, Any]:
    if ctx.blotter_id is None:
        raise ValueError("blotter_id required for audit stage")
    import services.blotter.auditor as auditor
    ctx.audit_results = auditor.audit_blotter_posts(ctx.blotter_id)
    flagged = [r for r in ctx.audit_results if not r.audit_passed]
    return {"audited": len(ctx.audit_results), "flagged": len(flagged)}


def _stage_alerts(ctx: WorkflowContext) -> dict[str, Any]:
    if ctx.blotter_id is None:
        raise ValueError("blotter_id required for alerts stage")
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT county FROM blotters WHERE id=?", (ctx.blotter_id,)).fetchone()
    county = (row["county"] if row else None) or "Unknown"
    conn.close()
    from services.alerts.dispatcher import dispatch_alerts
    sent = dispatch_alerts(ctx.blotter_id, county) or 0
    return {"alerts_sent": sent}


def _stage_facebook(ctx: WorkflowContext) -> dict[str, Any]:
    if not ctx.audit_results:
        return {"queued": 0, "note": "no audit results — run audit stage first"}
    try:
        from facebook_publisher import auto_queue_post_if_enabled
    except ImportError:
        return {"queued": 0, "note": "facebook_publisher not available"}
    queued = sum(
        1 for r in ctx.audit_results
        if r.audit_passed and r.post_id is not None
        and auto_queue_post_if_enabled(int(r.post_id)).get("queued")
    )
    return {"queued": queued}


# ── Stage registry (canonical order) ─────────────────────────────────────────

ALL_STAGES: list[Stage] = [
    Stage("parse",     _stage_parse,     abort_on_failure=True),
    Stage("store",     _stage_store,     abort_on_failure=True),
    Stage("summarize", _stage_summarize, abort_on_failure=False),
    Stage("audit",     _stage_audit,     abort_on_failure=False),
    Stage("alerts",    _stage_alerts,    abort_on_failure=False),
    Stage("facebook",  _stage_facebook,  abort_on_failure=False),
]

STAGE_NAMES = [s.name for s in ALL_STAGES]
_STAGE_MAP = {s.name: s for s in ALL_STAGES}


# ── Workflow runner ───────────────────────────────────────────────────────────

def run_workflow(
    ctx: WorkflowContext,
    *,
    stages: Optional[list[str]] = None,
    skip: Optional[list[str]] = None,
) -> WorkflowResult:
    """
    Run the blotter pipeline stages in canonical order.

    Args:
        ctx:    Shared mutable context; populate blotter_id OR attachment_path.
        stages: If given, only run these named stages (still in canonical order).
        skip:   Stage names to exclude.

    Returns:
        WorkflowResult with per-stage outcomes.
    """
    skip_set = set(skip or [])
    if stages is not None:
        unknown = set(stages) - set(STAGE_NAMES)
        if unknown:
            raise ValueError(f"Unknown stage(s): {', '.join(sorted(unknown))}. Valid: {STAGE_NAMES}")
        wanted = set(stages)
        active = {s.name for s in ALL_STAGES if s.name in wanted and s.name not in skip_set}
    else:
        active = {s.name for s in ALL_STAGES if s.name not in skip_set}

    result = WorkflowResult()
    for stage in ALL_STAGES:
        if stage.name not in active:
            result.stages.append(StageResult(name=stage.name, ok=True, skipped=True))
            continue

        t0 = time.monotonic()
        try:
            data = stage.fn(ctx) or {}
            elapsed = time.monotonic() - t0
            sr = StageResult(name=stage.name, ok=True, data=data, elapsed_s=elapsed)
            result.stages.append(sr)
            log.info("stage %s ok — %s", stage.name, data)
            if ctx.ingestion_job_id is not None:
                log_pipeline_event(ctx.ingestion_job_id, stage.name, "ok", data)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            sr = StageResult(name=stage.name, ok=False, error=str(exc), elapsed_s=elapsed)
            result.stages.append(sr)
            log.error("stage %s failed — %s", stage.name, exc)
            if ctx.ingestion_job_id is not None:
                log_pipeline_event(ctx.ingestion_job_id, stage.name, "error", {"error": str(exc)})
            if stage.abort_on_failure:
                log.error("aborting pipeline — stage '%s' is required", stage.name)
                # Mark remaining stages as skipped
                seen = {s.name for s in result.stages}
                for s in ALL_STAGES:
                    if s.name not in seen:
                        result.stages.append(StageResult(name=s.name, ok=True, skipped=True))
                break

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Montana Blotter dynamic pipeline runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available stages (in order): {', '.join(STAGE_NAMES)}",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--blotter-id", type=int, metavar="N",
                     help="Run on an existing stored blotter (skips parse+store by default)")
    src.add_argument("--attachment", metavar="PATH",
                     help="PDF path — full pipeline from parse stage")
    p.add_argument("--stages", metavar="STAGES",
                   help="Comma-separated stages to run, e.g. summarize,audit")
    p.add_argument("--skip", metavar="STAGES",
                   help="Comma-separated stages to skip")
    p.add_argument("--ingestion-job-id", type=int, metavar="N")
    p.add_argument("--source-document-id", type=int, metavar="N")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    ctx = WorkflowContext(
        blotter_id=args.blotter_id,
        attachment_path=args.attachment,
        ingestion_job_id=args.ingestion_job_id,
        source_document_id=args.source_document_id,
    )

    stages = [s.strip() for s in args.stages.split(",")] if args.stages else None
    skip = [s.strip() for s in args.skip.split(",")] if args.skip else None

    # When starting from an existing blotter with no explicit stage selection,
    # skip parse+store — they already ran.
    if args.blotter_id and stages is None and skip is None:
        skip = ["parse", "store"]

    result = run_workflow(ctx, stages=stages, skip=skip)
    print(result.summary())
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
