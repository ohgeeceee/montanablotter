"""
whitefish_blotter_fetcher.py
============================
Daily Whitefish PD blotter puller.

Fetches the Whitefish Police Blotter page, downloads recent PDF logs, and
pushes them into the standard montanablotter ingestion pipeline.
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

import requests

import config
from pipeline_state import (
    ensure_ingestion_job,
    ensure_source_document,
    get_ingestion_job_status,
    increment_ingestion_retry,
    log_pipeline_event,
    set_ingestion_job_status,
    sha256_bytes,
)
from processor import process_new_blotter

logger = logging.getLogger(__name__)

DEFAULT_PAGE_URL = "https://www.cityofwhitefish.gov/688/Police-Blotter"
DEFAULT_MAX_LINKS = 12
SOURCE_TYPE = "whitefish_pdf"
SOURCE_SENDER = "City of Whitefish Police Department"

LINK_PATTERN = re.compile(
    r"/DocumentCenter/View/(?P<doc_id>\d+)(?:/(?P<slug>[^/?#\"<]+))?",
    re.IGNORECASE,
)
SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class BlotterLink:
    doc_id: int
    href: str
    slug: str
    label: str

    @property
    def canonical_href(self) -> str:
        return f"/DocumentCenter/View/{self.doc_id}/{self.slug}" if self.slug else f"/DocumentCenter/View/{self.doc_id}"

    @property
    def document_url(self) -> str:
        return urljoin(DEFAULT_PAGE_URL, self.canonical_href)


@dataclass
class FetchStats:
    discovered: int = 0
    selected: int = 0
    skipped_published: int = 0
    skipped_missing: int = 0
    skipped_parse_errors: int = 0
    processed: int = 0
    created_blotters: int = 0
    duplicate_only: int = 0
    failed: int = 0


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = {name.lower(): (value or "") for name, value in attrs}
        href = attr_map.get("href", "")
        if not href:
            return
        self._current = {
            "href": href.strip(),
            "aria_hidden": attr_map.get("aria-hidden", "").strip().lower(),
        }
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current is None:
            return
        text = " ".join(part.strip() for part in self._text_parts if part and part.strip())
        item = {
            "href": self._current.get("href", ""),
            "text": html.unescape(text).strip(),
            "aria_hidden": self._current.get("aria_hidden", ""),
        }
        self.items.append(item)
        self._current = None
        self._text_parts = []


def _normalise_slug(value: str) -> str:
    slug = SAFE_NAME_PATTERN.sub("-", value.strip().lower())
    slug = slug.strip(".-_")
    return slug or "log"


def _is_log_link(label: str, slug: str) -> bool:
    token = f"{label} {slug}".upper()
    return "LOG" in token or "BLOTTER" in token or "MONDAY" in token


def _extract_links(page_html: str) -> list[BlotterLink]:
    parser = _AnchorCollector()
    parser.feed(page_html)
    by_doc: dict[int, BlotterLink] = {}

    for item in parser.items:
        if item.get("aria_hidden") == "true":
            continue
        href = (item.get("href") or "").strip()
        if not href:
            continue
        match = LINK_PATTERN.search(href)
        if not match:
            continue

        doc_id = int(match.group("doc_id"))
        slug = (match.group("slug") or "").strip()
        label = (item.get("text") or "").strip()
        if not label:
            label = slug or f"log-{doc_id}"
        if not _is_log_link(label, slug):
            continue

        link = BlotterLink(
            doc_id=doc_id,
            href=href,
            slug=_normalise_slug(slug or label),
            label=label,
        )
        existing = by_doc.get(doc_id)
        # Prefer entries with a visible text label over slug-only fallbacks.
        if existing is None or (existing.label == existing.slug and link.label != link.slug):
            by_doc[doc_id] = link

    return sorted(by_doc.values(), key=lambda row: row.doc_id)


def _select_recent_links(links: Iterable[BlotterLink], max_links: int) -> list[BlotterLink]:
    all_links = list(links)
    if max_links <= 0 or len(all_links) <= max_links:
        return all_links
    return all_links[-max_links:]


def _download_pdf(session: requests.Session, url: str, timeout_seconds: int = 45) -> bytes:
    response = session.get(url, timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.content
    if len(payload) < 1024:
        raise ValueError(f"Unexpectedly small payload from {url}")
    return payload


def ingest_whitefish_blotters(page_url: str, max_links: int, dry_run: bool = False) -> FetchStats:
    stats = FetchStats()

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )

    logger.info("Fetching Whitefish blotter page: %s", page_url)
    page_response = session.get(page_url, timeout=45)
    page_response.raise_for_status()

    discovered_links = _extract_links(page_response.text)
    stats.discovered = len(discovered_links)
    selected_links = _select_recent_links(discovered_links, max_links=max_links)
    stats.selected = len(selected_links)

    if dry_run:
        logger.info("Dry run: discovered=%s selected=%s", stats.discovered, stats.selected)
        for row in selected_links:
            print(f"{row.doc_id}\t{row.label}\t{row.document_url}")
        return stats

    if not selected_links:
        logger.info("No Whitefish blotter links discovered")
        return stats

    whitefish_upload_dir = os.path.join(config.UPLOAD_DIR, "whitefish")
    os.makedirs(whitefish_upload_dir, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()

    for link in selected_links:
        source_subject = f"Whitefish Police Blotter {link.label}"
        filename = f"whitefish_{link.doc_id}_{link.slug}.pdf"
        storage_path = os.path.join(whitefish_upload_dir, filename)
        document_url = link.document_url

        try:
            payload = _download_pdf(session, document_url)
            digest = sha256_bytes(payload)

            source_document_id = ensure_source_document(
                source_type=SOURCE_TYPE,
                source_sender=SOURCE_SENDER,
                source_subject=source_subject,
                source_received_at=now_iso,
                filename=filename,
                content_sha256=digest,
                storage_path=storage_path,
                raw_text=None,
                extraction_method="web_download",
                extraction_warnings=[],
            )
            ingestion_job_id = ensure_ingestion_job(source_document_id)
            existing_status = get_ingestion_job_status(source_document_id)
            if existing_status == "published":
                stats.skipped_published += 1
                logger.info("Skipping already published Whitefish source doc #%s", source_document_id)
                continue

            with open(storage_path, "wb") as handle:
                handle.write(payload)

            set_ingestion_job_status(ingestion_job_id, "extracted")
            log_pipeline_event(
                ingestion_job_id,
                "extract",
                "ok",
                {
                    "filename": filename,
                    "storage_path": storage_path,
                    "source_url": document_url,
                },
            )

            blotter_id = process_new_blotter(
                storage_path,
                county="Flathead",
                source_document_id=source_document_id,
                ingestion_job_id=ingestion_job_id,
            )
            stats.processed += 1
            if blotter_id:
                stats.created_blotters += 1
                logger.info("Whitefish log %s ingested as blotter #%s", link.label, blotter_id)
            else:
                stats.duplicate_only += 1
                logger.info("Whitefish log %s had no new incidents (duplicate-only)", link.label)
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 404:
                stats.skipped_missing += 1
                logger.warning("Skipping missing Whitefish link %s (%s)", link.label, document_url)
                continue

            stats.failed += 1
            logger.exception("Failed ingest for Whitefish link %s (%s)", link.label, document_url)

            # Best effort pipeline status update for failed jobs.
            try:
                if "ingestion_job_id" in locals():
                    increment_ingestion_retry(ingestion_job_id, str(exc))
                    set_ingestion_job_status(ingestion_job_id, "failed", last_error=str(exc), finished=True)
                    log_pipeline_event(
                        ingestion_job_id,
                        "publish",
                        "error",
                        {"error": str(exc), "source_url": document_url},
                    )
            except Exception:
                stats.skipped_parse_errors += 1
        except Exception as exc:
            stats.failed += 1
            logger.exception("Failed ingest for Whitefish link %s (%s)", link.label, document_url)

            # Best effort pipeline status update for failed jobs.
            try:
                if "ingestion_job_id" in locals():
                    increment_ingestion_retry(ingestion_job_id, str(exc))
                    set_ingestion_job_status(ingestion_job_id, "failed", last_error=str(exc), finished=True)
                    log_pipeline_event(
                        ingestion_job_id,
                        "publish",
                        "error",
                        {"error": str(exc), "source_url": document_url},
                    )
            except Exception:
                stats.skipped_parse_errors += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull Whitefish Police Blotter PDFs into Montana Blotter")
    parser.add_argument("--page-url", default=DEFAULT_PAGE_URL, help=f"Whitefish blotter page URL (default: {DEFAULT_PAGE_URL})")
    parser.add_argument("--max-links", type=int, default=DEFAULT_MAX_LINKS, help=f"Number of newest links to process (default: {DEFAULT_MAX_LINKS})")
    parser.add_argument("--dry-run", action="store_true", help="Print selected links without downloading or ingesting")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    stats = ingest_whitefish_blotters(
        page_url=args.page_url,
        max_links=args.max_links,
        dry_run=args.dry_run,
    )
    print(
        "Whitefish fetch complete "
        f"(discovered={stats.discovered}, selected={stats.selected}, processed={stats.processed}, "
        f"created={stats.created_blotters}, duplicate_only={stats.duplicate_only}, "
        f"skipped_published={stats.skipped_published}, skipped_missing={stats.skipped_missing}, "
        f"failed={stats.failed})"
    )
    successful = stats.processed + stats.created_blotters + stats.duplicate_only + stats.skipped_published + stats.skipped_missing
    if stats.selected and stats.failed and successful == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
