"""
powell_inmate.py — Powell County jail roster fetcher.
Stub adapter. Update once endpoint is confirmed.
"""
import logging
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.co.powell.mt.us"
FACILITY_NAME = "Powell County Detention Center"


def fetch_powell_bookings(roster_html_or_url=None) -> list[JailBookingRecord]:
    """Fetch Powell County jail roster (not yet implemented)."""
    logger.info("Powell County roster adapter not yet implemented")
    return []
