from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from .models import AgendaTargetType

PDF_SUFFIXES = (".pdf",)
PDF_HOST_PATTERNS = (
    "/documentcenter/view/",
    "/viewfile/",
    "/download/",
    "/agenda_packet/",
    "/agenda/",
)
NESTED_PAGE_PATTERNS = (
    "/agendacenter",
    "/calendar",
    "/meetings",
    "/agendas",
    "/minutes",
    "/view",
    "/detail",
)


def detect_target_type(url: str, *, label: str = "", content_type: str = "") -> AgendaTargetType:
    if not url:
        return "unknown"

    lowered_url = url.strip().lower()
    lowered_label = (label or "").strip().lower()
    lowered_content_type = (content_type or "").strip().lower()
    parsed = urlparse(lowered_url)
    query = parse_qs(parsed.query)

    if lowered_content_type:
        if "application/pdf" in lowered_content_type:
            return "pdf"
        if "text/html" in lowered_content_type or "application/xhtml+xml" in lowered_content_type:
            return "nested_page"

    if parsed.path.endswith(PDF_SUFFIXES):
        return "pdf"
    if any(pattern in parsed.path for pattern in PDF_HOST_PATTERNS):
        return "pdf"
    if any(value.lower().endswith(".pdf") for values in query.values() for value in values):
        return "pdf"
    if "pdf" in lowered_label and "calendar" not in lowered_label:
        return "pdf"

    if any(pattern in parsed.path for pattern in NESTED_PAGE_PATTERNS):
        return "nested_page"
    if any(token in lowered_label for token in ("agenda", "calendar", "details", "meeting page")):
        return "nested_page"

    return "unknown"
