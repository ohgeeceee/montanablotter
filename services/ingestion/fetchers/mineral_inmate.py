"""
mineral_inmate.py — Mineral County jail roster fetcher.
Stub adapter. Update once endpoint is confirmed.
"""
import logging
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.co.mineral.mt.us"
FACILITY_NAME = "Mineral County Detention Center"


def fetch_mineral_bookings(roster_html_or_url=None) -> list[JailBookingRecord]:
    """Fetch Mineral County jail roster (not yet implemented)."""
    logger.info("Mineral County roster adapter not yet implemented")
    return []
