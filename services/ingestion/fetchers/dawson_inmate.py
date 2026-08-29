"""
dawson_inmate.py — Dawson County jail roster fetcher.
Stub adapter. Update roster_url + parse logic once endpoint is confirmed.
"""
import logging
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

# Update these once the public roster URL is confirmed
ROSTER_URL = "https://www.co.dawson.mt.us"  # TODO: find actual roster endpoint
FACILITY_NAME = "Dawson County Detention Center"


def fetch_dawson_bookings(roster_html_or_url=None) -> list[JailBookingRecord]:
    """
    Fetch Dawson County jail roster.
    
    Args:
        roster_html_or_url: HTML string or URL (for testing/override)
    
    Returns:
        list[JailBookingRecord]
    """
    logger.info("Dawson County roster adapter not yet implemented")
    return []
