#!/usr/bin/env python3
"""
Unified smoke checks for all Montana Blotter ingest sources.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
from dataclasses import asdict, dataclass
from typing import Callable

from services.ingestion.fetchers.bozeman import DATASETS, ingest_dataset
from services.ingestion.fetchers.crime_mapping import MONTANA_AGENCIES, smoke_check_agency
from email_worker import EmailWorker
from services.ingestion.fetchers.missoula import ingest_missoula_public_report
from services.ingestion.fetchers.whitefish import DEFAULT_MAX_LINKS, DEFAULT_PAGE_URL, ingest_whitefish_blotters

logger = logging.getLogger(__name__)


@dataclass
class SmokeCheckResult:
    key: str
    status: str
    summary: str
    details: dict


def _run_check(key: str, func: Callable[[], tuple[str, str, dict]]) -> SmokeCheckResult:
    try:
        stdout_buffer = io.StringIO()
        with contextlib.redirect_stdout(stdout_buffer):
            status, summary, details = func()
        captured_output = stdout_buffer.getvalue().strip()
        if captured_output:
            details = {**details, "captured_output": captured_output}
        return SmokeCheckResult(key=key, status=status, summary=summary, details=details)
    except Exception as exc:
        return SmokeCheckResult(
            key=key,
            status="fail",
            summary=str(exc),
            details={"error": str(exc)},
        )


def _check_whitefish() -> tuple[str, str, dict]:
    stats = ingest_whitefish_blotters(DEFAULT_PAGE_URL, DEFAULT_MAX_LINKS, dry_run=True)
    summary = f"discovered {stats.discovered} links, selected {stats.selected}"
    return "pass", summary, asdict(stats)


def _check_bozeman_calls() -> tuple[str, str, dict]:
    _, fetched, normalized = ingest_dataset(DATASETS["calls"], DATASETS["calls"].default_days_back, dry_run=True)
    return "pass", f"fetched {fetched} rows", {"fetched": fetched, "normalized": normalized}


def _check_bozeman_crime() -> tuple[str, str, dict]:
    _, fetched, normalized = ingest_dataset(DATASETS["crime"], DATASETS["crime"].default_days_back, dry_run=True)
    return "pass", f"fetched {fetched} rows", {"fetched": fetched, "normalized": normalized}


def _check_missoula() -> tuple[str, str, dict]:
    _, stats = ingest_missoula_public_report("https://webapps.missoulacounty.us/dailypublicreport/", dry_run=True)
    return "pass", f"parsed {stats.fetched_incidents} incidents", asdict(stats)


def _check_email() -> tuple[str, str, dict]:
    details = EmailWorker().smoke_check_connection()
    return "pass", f"connected to {details['mailbox']} ({details['unread_count']} unread)", details


def _check_crimemapping(max_agencies: int | None = None) -> tuple[str, str, dict]:
    agencies = MONTANA_AGENCIES[:max_agencies] if max_agencies else MONTANA_AGENCIES
    probes = [smoke_check_agency(agency) for agency in agencies]
    total_map = sum(item["map_total"] for item in probes)
    return "pass", f"probed {len(probes)} agencies ({total_map} map incidents in window)", {"agencies": probes}


def run_smoke_checks(*, skip_email: bool = False, skip_crimemapping: bool = False, max_crimemapping_agencies: int | None = None) -> list[SmokeCheckResult]:
    checks: list[tuple[str, Callable[[], tuple[str, str, dict]]]] = [
        ("whitefish", _check_whitefish),
        ("bozeman_calls", _check_bozeman_calls),
        ("bozeman_crime", _check_bozeman_crime),
        ("missoula", _check_missoula),
    ]
    if not skip_email:
        checks.append(("email_imap", _check_email))
    if not skip_crimemapping:
        checks.append(("crimemapping", lambda: _check_crimemapping(max_crimemapping_agencies)))
    return [_run_check(key, func) for key, func in checks]


def _format_results(results: list[SmokeCheckResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"[{result.status.upper()}] {result.key}: {result.summary}")
    passed = sum(1 for result in results if result.status == "pass")
    failed = sum(1 for result in results if result.status == "fail")
    lines.append(f"Summary: {passed} passed, {failed} failed")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Montana Blotter ingestion smoke checks.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of plain text")
    parser.add_argument("--skip-email", action="store_true", help="Skip IMAP connectivity check")
    parser.add_argument("--skip-crimemapping", action="store_true", help="Skip CrimeMapping probe")
    parser.add_argument("--max-crimemapping-agencies", type=int, default=None, help="Probe only the first N CrimeMapping agencies")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    results = run_smoke_checks(
        skip_email=args.skip_email,
        skip_crimemapping=args.skip_crimemapping,
        max_crimemapping_agencies=args.max_crimemapping_agencies,
    )

    if args.json:
        print(json.dumps([asdict(item) for item in results], indent=2, sort_keys=True))
    else:
        print(_format_results(results))

    return 1 if any(item.status == "fail" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
