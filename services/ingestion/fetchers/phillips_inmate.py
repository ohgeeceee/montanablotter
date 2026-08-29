"""
phillips_inmate.py — Phillips County jail roster fetcher.
Stub adapter. Update once endpoint is confirmed.
"""
import logging
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

ROSTER_URL = "https://www.phillipscosheriff.com"
FACILITY_NAME = "Phillips County Detention Center"


def fetch_phillips_bookings(roster_html_or_url=None) -> list[JailBookingRecord]:
    """Fetch Phillips County jail roster (not yet implemented)."""
    logger.info("Phillips County roster adapter not yet implemented")
    return []
