from __future__ import annotations

from urllib.parse import urlparse


APPROVED_NEWS_SOURCES = [
    {"source_type": "montanablotter", "domains": {"montanablotter.com", "www.montanablotter.com"}},
    {
        "source_type": "montana_public_records",
        "domains": {
            "courts.mt.gov",
            "juddocumentservice.mt.gov",
            "webapps.missoulacounty.us",
            "www.missoulacounty.us",
        },
    },
]


def is_allowed_source_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return False
    return any(hostname in entry["domains"] for entry in APPROVED_NEWS_SOURCES)
