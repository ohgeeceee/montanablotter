"""
great_falls_police_fetcher.py
=============================
Great Falls Police Department blotter fetcher (stub).

Great Falls police portal at greatfallsmt.gov needs endpoint discovery.
"""

import logging
import requests

logger = logging.getLogger(__name__)

GREAT_FALLS_BASE_URL = "https://www.greatfallsmt.gov"
GREAT_FALLS_POLICE_URL = f"{GREAT_FALLS_BASE_URL}/police"

POTENTIAL_ENDPOINTS = [
    "/police/incident-reports",
    "/police/daily-blotter",
    "/police/calls-for-service",
    "/api/police/calls",
]


def discover_blotter_endpoint():
    """Probe Great Falls police portal for dispatch feed."""
    logger.info("Discovering Great Falls Police blotter endpoint")
    
    for endpoint in POTENTIAL_ENDPOINTS:
        url = GREAT_FALLS_BASE_URL + endpoint
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code < 400:
                logger.info(f"  Found: {endpoint} ({resp.status_code})")
                return url
        except Exception as e:
            logger.debug(f"  {endpoint}: {e}")
    
    logger.warning("No blotter endpoint found")
    return None


def ingest_blotter(dry_run=False):
    """Ingest Great Falls Police blotter (not yet implemented)."""
    endpoint = discover_blotter_endpoint()
    if not endpoint:
        logger.warning("Great Falls blotter endpoint not yet available")
        return None, 0, 0
    
    logger.info(f"Would fetch from {endpoint}")
    # TODO: Implement once endpoint confirmed
    return None, 0, 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    discover_blotter_endpoint()
