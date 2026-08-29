"""
pondera_inmate.py — Pondera County jail roster fetcher.
Stub adapter. Update once endpoint is confirmed.
"""
import logging
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.co.pondera.mt.us"
FACILITY_NAME = "Pondera County Detention Center"


def fetch_pondera_bookings(roster_html_or_url=None) -> list[JailBookingRecord]:
    """Fetch Pondera County jail roster (not yet implemented)."""
    logger.info("Pondera County roster adapter not yet implemented")
    return []
