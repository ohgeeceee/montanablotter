from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urljoin

from .config import CityScrapeConfig
from .link_detection import detect_target_type
from .models import AgendaDocument, AgendaTargetType, MeetingRecord


class MontanaScraper(ABC):
    provider_name = "base"
    default_timeout_ms = 30_000

    def __init__(self, config: CityScrapeConfig) -> None:
        self.config = config

    def scrape(self, browser) -> list[MeetingRecord]:
        context = browser.new_context()
        page = context.new_page()
        page.goto(self.config.url, wait_until="domcontentloaded", timeout=self.default_timeout_ms)
        self._wait_for_listing(page)
        meetings = self.extract_meetings(page)

        hydrated: list[MeetingRecord] = []
        for meeting in meetings:
            hydrated.append(self._hydrate_meeting(context, meeting))

        context.close()
        return hydrated

    def _wait_for_listing(self, page) -> None:
        listing_selector = self.config.selectors.get("row", "")
        if listing_selector:
            page.wait_for_selector(listing_selector, timeout=self.default_timeout_ms)

    def _disable_nested_document_hydration(self) -> bool:
        return bool(self.config.metadata.get("disable_nested_document_hydration"))

    def _hydrate_meeting(self, context, meeting: MeetingRecord) -> MeetingRecord:
        normalized_docs: list[AgendaDocument] = []
        nested_page_url = meeting.meeting_page_url
        fallback_nested_agenda: AgendaDocument | None = None

        for document in meeting.documents:
            target_type = document.target_type
            if target_type == "unknown":
                target_type = self.classify_link_target(context, document.url, label=document.label)
            document.target_type = target_type

            if document.document_type == "agenda" and target_type == "nested_page":
                nested_page_url = nested_page_url or document.url
                if fallback_nested_agenda is None:
                    fallback_nested_agenda = document
                continue
            normalized_docs.append(document)

        if nested_page_url and self._disable_nested_document_hydration():
            if fallback_nested_agenda is not None:
                normalized_docs.append(fallback_nested_agenda)
        elif nested_page_url:
            nested_documents = self.extract_nested_documents(context, nested_page_url)
            for document in nested_documents:
                if document.target_type == "unknown":
                    document.target_type = self.classify_link_target(context, document.url, label=document.label)
                normalized_docs.append(document)
            if not nested_documents and fallback_nested_agenda is not None:
                normalized_docs.append(fallback_nested_agenda)

        meeting.meeting_page_url = nested_page_url
        meeting.documents = self._dedupe_documents(normalized_docs)
        return meeting

    def classify_link_target(self, context, url: str, *, label: str = "") -> AgendaTargetType:
        hinted = detect_target_type(url, label=label)
        if hinted != "unknown":
            return hinted

        try:
            response = context.request.fetch(url, method="HEAD", fail_on_status_code=False, timeout=self.default_timeout_ms)
            content_type = response.headers.get("content-type", "")
            resolved_url = response.url or url
            return detect_target_type(resolved_url, label=label, content_type=content_type)
        except Exception:
            return hinted

    def extract_nested_documents(self, context, url: str) -> list[AgendaDocument]:
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=self.default_timeout_ms)
        documents = self.extract_documents_from_page(page)
        page.close()
        return documents

    def extract_documents_from_page(self, page) -> list[AgendaDocument]:
        anchors = page.locator("a[href]").all()
        documents: list[AgendaDocument] = []
        for anchor in anchors:
            href = (anchor.get_attribute("href") or "").strip()
            if not href:
                continue
            label = self._clean_text(anchor.inner_text()) or "Agenda document"
            lowered = label.lower()
            if any(token in lowered for token in ("print agenda", "wcag", "conformance")):
                continue
            if "agenda" not in lowered and "minutes" not in lowered and "packet" not in lowered:
                continue
            document_type = "minutes" if "minutes" in lowered else "packet" if "packet" in lowered else "agenda"
            absolute_url = urljoin(page.url, href)
            if "w3.org" in absolute_url.lower():
                continue
            documents.append(
                AgendaDocument(
                    label=label,
                    url=absolute_url,
                    document_type=document_type,
                    target_type=detect_target_type(absolute_url, label=label),
                )
            )
        return self._dedupe_documents(documents)

    def _document(self, page, href: str, *, label: str, document_type: str = "agenda") -> AgendaDocument:
        absolute_url = urljoin(page.url, href)
        return AgendaDocument(
            label=self._clean_text(label),
            url=absolute_url,
            document_type=document_type,
            target_type=detect_target_type(absolute_url, label=self._clean_text(label)),
        )

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join((value or "").split()).strip()

    @staticmethod
    def _dedupe_documents(documents: list[AgendaDocument]) -> list[AgendaDocument]:
        seen: set[tuple[str, str]] = set()
        deduped: list[AgendaDocument] = []
        for document in documents:
            key = (document.document_type, document.url)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(document)
        return deduped

    @abstractmethod
    def extract_meetings(self, page) -> list[MeetingRecord]:
        raise NotImplementedError
