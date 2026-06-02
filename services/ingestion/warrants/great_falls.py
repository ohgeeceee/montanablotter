"""Great Falls Municipal Court warrant list adapter."""

from __future__ import annotations

import logging

import requests

from services.ingestion.warrants.html_table import parse_warrant_table_page

logger = logging.getLogger(__name__)

GREAT_FALLS_LIST_URL = "https://greatfallsmt.gov/579/Warrants-List"
_COUNTY = "Cascade"
_CITY = "Great Falls"
_ISSUED_BY = "Great Falls Municipal Court"

_INFO_ONLY_MARKERS = (
    "court public access portal",
    "montana public access portal",
    "please contact the court",
    "please call the municipal court",
)


def fetch_great_falls_warrants(session: requests.Session):
    try:
        resp = session.get(GREAT_FALLS_LIST_URL, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch Great Falls warrant page %s: %s", GREAT_FALLS_LIST_URL, exc)
        return []

    return parse_warrant_table_page(
        resp.text,
        county=_COUNTY,
        city=_CITY,
        source_url=GREAT_FALLS_LIST_URL,
        source_prefix="great-falls-muni-warrant",
        issued_by=_ISSUED_BY,
        info_only_markers=_INFO_ONLY_MARKERS,
    )
