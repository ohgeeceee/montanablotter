"""
billings_police_fetcher.py
==========================
Billings Police Department blotter fetcher (stub).

Billings uses a Granicus/CivicPlus portal. When we identify the exact
dispatch/blotter feed endpoint, this fetcher will pull it hourly.

For now, this is a scaffold that verifies connectivity and logs available
data endpoints.
"""

import logging
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BILLINGS_BASE_URL = "https://www.billingsmt.gov"
BILLINGS_POLICE_URL = f"{BILLINGS_BASE_URL}/Police"

# Known Granicus endpoint patterns (to be verified)
POTENTIAL_ENDPOINTS = [
    "/Police/Calls-for-Service",
    "/Police/Daily-Blotter",
    "/Police/Incident-Reports",
    "/api/v1/police/calls",
    "/api/public/police/calls",
]


def discover_blotter_endpoint():
    """
    Probe Billings police portal to find live dispatch/blotter endpoint.
    
    Returns:
        str or None: URL to the blotter feed if found
    """
    logger.info("Discovering Billings Police blotter endpoint")
    
    for endpoint in POTENTIAL_ENDPOINTS:
        url = BILLINGS_BASE_URL + endpoint
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code < 400:
                logger.info(f"  Found: {endpoint} ({resp.status_code})")
                return url
        except Exception as e:
            logger.debug(f"  {endpoint}: {e}")
    
    logger.warning("No blotter endpoint found; manual discovery needed")
    return None


def ingest_blotter(dry_run=False):
    """
    Ingest Billings Police blotter (not yet implemented).
    
    Args:
        dry_run (bool): Preview only
    
    Returns:
        tuple: (blotter_id, fetched_count, post_count) or (None, 0, 0) if not ready
    """
    endpoint = discover_blotter_endpoint()
    if not endpoint:
        logger.warning("Billings blotter endpoint not yet available")
        return None, 0, 0
    
    logger.info(f"Would fetch from {endpoint}")
    # TODO: Implement once endpoint is confirmed
    return None, 0, 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    discover_blotter_endpoint()
