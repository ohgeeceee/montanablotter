from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AgendaTargetType = Literal["pdf", "nested_page", "unknown"]


@dataclass
class AgendaDocument:
    label: str
    url: str
    document_type: str = "agenda"
    target_type: AgendaTargetType = "unknown"


@dataclass
class MeetingRecord:
    title: str
    starts_at: str = ""
    source_url: str = ""
    meeting_page_url: str = ""
    location_name: str = ""
    body_name: str = ""
    documents: list[AgendaDocument] = field(default_factory=list)
