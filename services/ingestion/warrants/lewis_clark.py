"""Lewis and Clark County Justice Court arrest warrant list adapter."""

from __future__ import annotations

import logging

import requests

from services.ingestion.warrants.list_items import parse_li_warrant_list

logger = logging.getLogger(__name__)

LC_JC_WARRANT_URL = (
    "https://www.lccountymt.gov/Government/Justice-Court/Arrest-Warrant-List"
)
_COUNTY = "Lewis and Clark"
_ISSUED_BY = "Lewis and Clark County Justice Court"


def fetch_lewis_clark_warrants(session: requests.Session) -> list:
    try:
        resp = session.get(LC_JC_WARRANT_URL, timeout=90)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning(
            "Failed to fetch Lewis and Clark warrant list %s: %s",
            LC_JC_WARRANT_URL,
            exc,
        )
        return []

    return parse_li_warrant_list(
        resp.text,
        county=_COUNTY,
        source_url=LC_JC_WARRANT_URL,
        source_prefix="lewis-clark-jc-warrant",
        issued_by=_ISSUED_BY,
    )
