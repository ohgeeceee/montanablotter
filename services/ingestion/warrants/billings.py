"""Billings PD / Municipal Court warrant list adapter."""

from __future__ import annotations

import logging

import requests

from services.ingestion.warrants.html_table import parse_warrant_table_page

logger = logging.getLogger(__name__)

BILLINGS_LIST_URL = "https://www.billingsmt.gov/2874/Warrants"
_COUNTY = "Yellowstone"
_CITY = "Billings"
_ISSUED_BY = "Billings Municipal Court"

_INFO_ONLY_MARKERS = (
    "please call the billings municipal court",
    "montana public access portal",
    "please select billings municipal court in the court dropdown",
)


def parse_billings_warrant_page(
    page_html: str,
    *,
    source_url: str = BILLINGS_LIST_URL,
):
    return parse_warrant_table_page(
        page_html,
        county=_COUNTY,
        city=_CITY,
        source_url=source_url,
        source_prefix="billings-pd-warrant",
        issued_by=_ISSUED_BY,
        info_only_markers=_INFO_ONLY_MARKERS,
    )


def fetch_billings_warrants(session: requests.Session):
    try:
        resp = session.get(BILLINGS_LIST_URL, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch Billings warrant page %s: %s", BILLINGS_LIST_URL, exc)
        return []

    return parse_billings_warrant_page(resp.text, source_url=BILLINGS_LIST_URL)
