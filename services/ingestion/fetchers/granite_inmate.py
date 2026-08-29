"""
granite_inmate.py — Granite County jail roster fetcher.
Stub adapter. Update once endpoint is confirmed.
"""
import logging
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.co.granite.mt.us"
FACILITY_NAME = "Granite County Detention Center"


def fetch_granite_bookings(roster_html_or_url=None) -> list[JailBookingRecord]:
    """Fetch Granite County jail roster (not yet implemented)."""
    logger.info("Granite County roster adapter not yet implemented")
    return []
