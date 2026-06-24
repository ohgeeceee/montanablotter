from __future__ import annotations

import re
from urllib.parse import urljoin

from .base import MontanaScraper
from .models import AgendaDocument, MeetingRecord


class SelectorDrivenProvider(MontanaScraper):
    default_selectors: dict[str, str] = {
        "row": "table tbody tr",
        "title": "td:nth-child(1), .title, .meeting-name",
        "date": "td:nth-child(2), .date, .meeting-date",
        "agenda_link": "a[href*='agenda'], a:has-text('Agenda')",
        "packet_link": "a[href*='packet'], a:has-text('Packet')",
        "minutes_link": "a[href*='minutes'], a:has-text('Minutes')",
    }

    def selectors(self) -> dict[str, str]:
        merged = dict(self.default_selectors)
        merged.update(self.config.selectors)
        return merged

    def _normalized_keywords(self, key: str) -> tuple[str, ...]:
        configured = self.config.metadata.get(key) or []
        if isinstance(configured, str):
            configured = [configured]
        return tuple(str(item).strip().lower() for item in configured if str(item).strip())

    def _allow_fallback_link(self) -> bool:
        value = self.config.metadata.get("disable_fallback_link")
        return not bool(value)

    def _matches_title_filters(self, title_text: str) -> bool:
        normalized = (title_text or "").strip().lower()
        if not normalized:
            return False
        include_keywords = self._normalized_keywords("include_keywords")
        if include_keywords and not any(keyword in normalized for keyword in include_keywords):
            return False
        exclude_keywords = self._normalized_keywords("exclude_keywords")
        if exclude_keywords and any(keyword in normalized for keyword in exclude_keywords):
            return False
        return True

    def extract_meetings(self, page) -> list[MeetingRecord]:
        selectors = self.selectors()
        rows = page.locator(selectors["row"]).all()
        meetings: list[MeetingRecord] = []

        for row in rows:
            title_text = self._clean_text(row.locator(selectors["title"]).first.inner_text()) if row.locator(selectors["title"]).count() else ""
            date_text = self._clean_text(row.locator(selectors["date"]).first.inner_text()) if row.locator(selectors["date"]).count() else ""
            if not title_text and not date_text:
                continue
            if not self._matches_title_filters(title_text):
                continue

            meeting = MeetingRecord(
                title=title_text or self.config.name,
                starts_at=date_text,
                source_url=self.config.url,
                body_name=self.config.name,
            )

            agenda_links = row.locator(selectors["agenda_link"]).all()
            for anchor in agenda_links:
                href = (anchor.get_attribute("href") or "").strip()
                if not href:
                    continue
                label = self._clean_text(anchor.inner_text()) or "Agenda"
                meeting.documents.append(self._document(page, href, label=label, document_type="agenda"))

            packet_selector = selectors.get("packet_link", "")
            if packet_selector:
                packet_links = row.locator(packet_selector).all()
                for anchor in packet_links:
                    href = (anchor.get_attribute("href") or "").strip()
                    if not href:
                        continue
                    label = self._clean_text(anchor.inner_text()) or "Packet"
                    meeting.documents.append(self._document(page, href, label=label, document_type="packet"))

            minutes_links = row.locator(selectors["minutes_link"]).all()
            for anchor in minutes_links:
                href = (anchor.get_attribute("href") or "").strip()
                if not href:
                    continue
                label = self._clean_text(anchor.inner_text()) or "Minutes"
                meeting.documents.append(self._document(page, href, label=label, document_type="minutes"))

            if not meeting.documents and self._allow_fallback_link():
                first_anchor = row.locator("a[href]").first
                if first_anchor.count():
                    href = (first_anchor.get_attribute("href") or "").strip()
                    label = self._clean_text(first_anchor.inner_text()) or "Agenda"
                    if href:
                        meeting.documents.append(self._document(page, href, label=label, document_type="agenda"))

            meetings.append(meeting)

        return meetings


class GranicusProvider(SelectorDrivenProvider):
    provider_name = "granicus"
    default_selectors = {
        "row": "table tbody tr, .listingRow",
        "title": ".title, td:nth-child(1), .views-field-title",
        "date": ".date, td:nth-child(2), .views-field-field-date",
        "agenda_link": "a[href*='agenda'], a:has-text('Agenda'), a:has-text('View Details')",
        "packet_link": "a[href*='packet'], a:has-text('Packet')",
        "minutes_link": "a[href*='minutes'], a:has-text('Minutes')",
    }


class MuniCodeProvider(SelectorDrivenProvider):
    provider_name = "municode"
    default_selectors = {
        "row": ".meeting-row, table tbody tr, .agenda-row",
        "title": ".meeting-title, td:nth-child(1), .title",
        "date": ".meeting-date, td:nth-child(2), .date",
        "agenda_link": "a[href*='agenda'], a:has-text('Agenda'), a:has-text('Packet')",
        "packet_link": "a[href*='packet'], a:has-text('Packet')",
        "minutes_link": "a[href*='minutes'], a:has-text('Minutes')",
    }


class CustomHTMLProvider(SelectorDrivenProvider):
    provider_name = "custom_html"
    LEGACY_ROW_KEYWORDS = ("agenda", "minutes", "packet", "notice")
    LEGACY_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

    def legacy_row_keywords(self) -> tuple[str, ...]:
        configured = self.config.metadata.get("legacy_row_keywords")
        if not configured:
            return self.LEGACY_ROW_KEYWORDS
        if isinstance(configured, str):
            configured = [configured]
        values = [str(item).strip().lower() for item in configured if str(item).strip()]
        return tuple(values) or self.LEGACY_ROW_KEYWORDS

    @classmethod
    def _row_matches_legacy_pattern(cls, text: str, keywords: tuple[str, ...]) -> bool:
        normalized = " ".join((text or "").split()).lower()
        if not normalized:
            return False
        has_keyword = any(keyword in normalized for keyword in keywords)
        has_year = bool(cls.LEGACY_YEAR_RE.search(normalized))
        return has_keyword and has_year

    def _looks_like_legacy_meeting_row(self, text: str) -> bool:
        return self._row_matches_legacy_pattern(text, self.legacy_row_keywords())

    def _extract_legacy_table_meetings(self, page) -> list[MeetingRecord]:
        rows = page.locator("table tr").all()
        meetings: list[MeetingRecord] = []
        keywords = self.legacy_row_keywords()

        for row in rows:
            row_text = row.inner_text().strip()
            if not self._row_matches_legacy_pattern(row_text, keywords):
                continue

            title_text = self._clean_text(row_text)
            meeting = MeetingRecord(
                title=title_text,
                starts_at="",
                source_url=self.config.url,
                body_name=self.config.name,
            )

            anchors = row.locator("a[href]").all()
            for anchor in anchors:
                href = (anchor.get_attribute("href") or "").strip()
                label = self._clean_text(anchor.inner_text()) or "Agenda"
                if not href:
                    continue
                lowered = label.lower()
                if not any(keyword in lowered for keyword in keywords):
                    if "agenda" not in row_text.lower() and "minutes" not in row_text.lower():
                        continue
                document_type = "minutes" if "minutes" in lowered else "packet" if "packet" in lowered else "agenda"
                meeting.documents.append(self._document(page, href, label=label, document_type=document_type))

            if meeting.documents:
                meetings.append(meeting)

        return meetings

    def extract_meetings(self, page) -> list[MeetingRecord]:
        meetings = super().extract_meetings(page)
        if meetings:
            return meetings

        legacy_table_meetings = self._extract_legacy_table_meetings(page)
        if legacy_table_meetings:
            return legacy_table_meetings

        fallback_links = page.locator("a[href]").all()
        fallback_meetings: list[MeetingRecord] = []
        for anchor in fallback_links:
            label = anchor.inner_text().strip()
            label = self._clean_text(label)
            href = (anchor.get_attribute("href") or "").strip()
            if not href or not label or "agenda" not in label.lower():
                continue
            fallback_meetings.append(
                MeetingRecord(
                    title=label,
                    source_url=self.config.url,
                    body_name=self.config.name,
                    documents=[self._document(page, href, label=label, document_type="agenda")],
                )
            )
        return fallback_meetings


class MunicipalImpactProvider(MontanaScraper):
    provider_name = "municipalimpact"
    DEFAULT_LINK_SELECTOR = "main a[href*='/documents/'], #site_main a[href*='/documents/'], a[href*='/documents/']"
    DATE_PATTERNS = (
        re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{2,4})\b"),
        re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"),
        re.compile(r"\b([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\b"),
    )

    def _wait_for_listing(self, page) -> None:
        page.wait_for_selector(self.link_selector(), state="attached", timeout=self.timeout_ms)

    def link_selector(self) -> str:
        return str(self.config.selectors.get("link") or self.DEFAULT_LINK_SELECTOR)

    def _document_type(self) -> str:
        value = str(self.config.metadata.get("document_type") or "agenda").strip().lower()
        if value in {"agenda", "minutes", "packet", "attachment"}:
            return value
        return "agenda"

    def _include_keywords(self) -> tuple[str, ...]:
        configured = self.config.metadata.get("include_keywords") or []
        if isinstance(configured, str):
            configured = [configured]
        return tuple(str(item).strip().lower() for item in configured if str(item).strip())

    def _exclude_keywords(self) -> tuple[str, ...]:
        configured = self.config.metadata.get("exclude_keywords") or []
        if isinstance(configured, str):
            configured = [configured]
        return tuple(str(item).strip().lower() for item in configured if str(item).strip())

    def _matches_filters(self, label: str) -> bool:
        normalized = (label or "").strip().lower()
        if not normalized:
            return False
        include_keywords = self._include_keywords()
        if include_keywords and not any(keyword in normalized for keyword in include_keywords):
            return False
        exclude_keywords = self._exclude_keywords()
        if exclude_keywords and any(keyword in normalized for keyword in exclude_keywords):
            return False
        return True

    @classmethod
    def _extract_date(cls, label: str) -> str:
        text = (label or "").strip()
        if not text:
            return ""

        for pattern in cls.DATE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            if len(match.groups()) == 3 and match.group(1).isalpha():
                month_name, day, year = match.groups()
                return f"{month_name} {int(day)}, {year}"
            month, day, year = match.groups()
            year = f"20{year}" if len(year) == 2 else year
            return f"{int(month):02d}/{int(day):02d}/{year}"
        return ""

    def extract_meetings(self, page) -> list[MeetingRecord]:
        anchors = page.locator(self.link_selector()).all()
        meetings: list[MeetingRecord] = []
        document_type = self._document_type()

        for anchor in anchors:
            href = (anchor.get_attribute("href") or "").strip()
            if not href:
                continue
            label = self._clean_text(anchor.get_attribute("title") or anchor.inner_text())
            if not self._matches_filters(label):
                continue
            meetings.append(
                MeetingRecord(
                    title=label,
                    starts_at=self._extract_date(label),
                    source_url=self.config.url,
                    body_name=self.config.name,
                    documents=[
                        AgendaDocument(
                            label=label,
                            url=urljoin(page.url, href),
                            document_type=document_type,
                            target_type="pdf",
                        )
                    ],
                )
            )

        deduped: list[MeetingRecord] = []
        seen: set[tuple[str, str]] = set()
        for meeting in meetings:
            key = ((meeting.title or "").lower(), meeting.documents[0].url if meeting.documents else "")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(meeting)
        return deduped


class PrimeGovProvider(MontanaScraper):
    provider_name = "primegov"
    DEFAULT_ROW_SELECTOR = "table tbody tr"

    def _wait_for_listing(self, page) -> None:
        page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        page.wait_for_selector(self.DEFAULT_ROW_SELECTOR, state="attached", timeout=self.timeout_ms)

    def extract_meetings(self, page) -> list[MeetingRecord]:
        rows = page.locator(self.DEFAULT_ROW_SELECTOR).all()
        meetings: list[MeetingRecord] = []

        for row in rows:
            cells = row.locator("td").all()
            if len(cells) < 3:
                continue
            title = self._clean_text(cells[0].inner_text())
            starts_at = self._clean_text(cells[1].inner_text())
            if not title:
                continue

            documents: list[AgendaDocument] = []
            for anchor in row.locator("a[href]").all():
                href = (anchor.get_attribute("href") or "").strip()
                label = self._clean_text(anchor.inner_text())
                if not href or not label:
                    continue
                lowered = label.lower()
                if lowered not in {"agenda", "packet", "minutes"}:
                    continue
                document_type = "minutes" if lowered == "minutes" else "packet" if lowered == "packet" else "agenda"
                documents.append(
                    AgendaDocument(
                        label=label,
                        url=urljoin(page.url, href),
                        document_type=document_type,
                        target_type="pdf",
                    )
                )

            if not documents:
                continue

            meetings.append(
                MeetingRecord(
                    title=title,
                    starts_at=starts_at,
                    source_url=self.config.url,
                    body_name=self.config.name,
                    documents=self._dedupe_documents(documents),
                )
            )

        return meetings


class CivicClerkProvider(MontanaScraper):
    provider_name = "civicclerk"
    EVENT_ID_RE = re.compile(r"eventListRow-(\d+)")
    DEFAULT_ROW_SELECTOR = '[data-testid="row"]'
    DEFAULT_ALLOWED_KEYWORDS = ("commission", "council")

    def _wait_for_listing(self, page) -> None:
        page.wait_for_load_state("networkidle", timeout=self.timeout_ms)
        page.wait_for_function(
            """
            selector => {
                const rows = Array.from(document.querySelectorAll(selector));
                return rows.some(row => {
                    const id = (row.getAttribute('id') || '').trim();
                    const text = (row.innerText || '').trim();
                    return id && id !== 'eventListRow-undefined' && text.length > 20;
                });
            }
            """,
            arg=self.DEFAULT_ROW_SELECTOR,
            timeout=self.timeout_ms,
        )

    @classmethod
    def _event_id_from_row_id(cls, row_id: str) -> str:
        match = cls.EVENT_ID_RE.search((row_id or "").strip())
        return match.group(1) if match else ""

    def _allowed_keywords(self) -> tuple[str, ...]:
        configured = self.config.metadata.get("include_keywords")
        if configured is None:
            configured = self.DEFAULT_ALLOWED_KEYWORDS
        if isinstance(configured, str):
            configured = [configured]
        values = [str(item).strip().lower() for item in configured if str(item).strip()]
        return tuple(values)

    def _category_allowlist(self) -> tuple[str, ...]:
        configured = self.config.metadata.get("category_allowlist") or []
        if isinstance(configured, str):
            configured = [configured]
        return tuple(str(item).strip().lower() for item in configured if str(item).strip())

    def _max_documents(self) -> int:
        value = self.config.metadata.get("max_documents", 8)
        try:
            return max(int(value), 1)
        except (TypeError, ValueError):
            return 8

    def _matches_filters(self, *, title: str, category: str) -> bool:
        haystacks = [title.lower(), category.lower()]
        allowed_keywords = self._allowed_keywords()
        if allowed_keywords and not any(keyword in haystack for haystack in haystacks for keyword in allowed_keywords):
            return False
        category_allowlist = self._category_allowlist()
        if category_allowlist and not any(item in category.lower() for item in category_allowlist):
            return False
        return True

    @staticmethod
    def _nonempty_lines(value: str) -> list[str]:
        return [line.strip() for line in (value or "").splitlines() if line.strip()]

    @classmethod
    def _parse_event_details(cls, value: str) -> tuple[str, str]:
        lines = cls._nonempty_lines(value)
        if not lines:
            return ("", "")
        title = lines[0]
        category = lines[-1] if len(lines) > 1 else ""
        if category.lower().startswith("agenda posted on:"):
            category = ""
        return (title, category)

    @classmethod
    def _parse_date_details(cls, value: str) -> str:
        lines = cls._nonempty_lines(value)
        if len(lines) < 2:
            return ""
        date_text = " ".join(lines[:-1])
        date_text = re.sub(r"^[A-Za-z]+\s+", "", date_text).strip()
        time_text = re.sub(r"\b[A-Z]{2,4}\b$", "", lines[-1]).strip()
        return " ".join(part for part in (date_text, time_text) if part).strip()

    @classmethod
    def _document_type_for_label(cls, label: str) -> str:
        lowered = (label or "").strip().lower()
        if "minute" in lowered:
            return "minutes"
        if "packet" in lowered or "report" in lowered:
            return "packet"
        if "agenda" in lowered:
            return "agenda"
        return "attachment"

    def extract_meetings(self, page) -> list[MeetingRecord]:
        rows = page.locator(self.DEFAULT_ROW_SELECTOR).all()
        meetings: list[MeetingRecord] = []

        for row in rows:
            row_id = (row.get_attribute("id") or "").strip()
            event_id = self._event_id_from_row_id(row_id)
            if not event_id:
                continue

            title = ""
            category = ""
            starts_at = ""

            event_details = row.locator('[data-testid="eventDetails"]')
            if event_details.count():
                title, category = self._parse_event_details(event_details.first.inner_text())

            date_details = row.locator('[data-testid="dateDetails"]')
            if date_details.count():
                starts_at = self._parse_date_details(date_details.first.inner_text())

            if not title or not self._matches_filters(title=title, category=category):
                continue

            meeting_page_url = urljoin(page.url, f"/event/{event_id}")
            meetings.append(
                MeetingRecord(
                    title=title,
                    starts_at=starts_at,
                    source_url=self.config.url,
                    meeting_page_url=meeting_page_url,
                    body_name=self.config.name,
                )
            )

        return meetings

    def extract_nested_documents(self, context, url: str) -> list[AgendaDocument]:
        page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            page.wait_for_timeout(1000)
            anchors = page.locator('[data-testid="itemAttachmentLink"]').all()
            documents: list[AgendaDocument] = []
            for anchor in anchors:
                href = (anchor.get_attribute("href") or "").strip()
                label = self._clean_text(anchor.inner_text()) or "Meeting attachment"
                if not href:
                    continue
                documents.append(
                    AgendaDocument(
                        label=label,
                        url=urljoin(page.url, href),
                        document_type=self._document_type_for_label(label),
                        target_type="pdf",
                    )
                )
            deduped = self._dedupe_documents(documents)
            return deduped[: self._max_documents()]
        finally:
            page.close()
