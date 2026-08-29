"""
helena_police_fetcher.py
========================
Helena Police Department blotter fetcher (stub).

Helena has a known police portal at helenamt.gov/police but the exact
dispatch/blotter feed endpoint needs discovery.
"""

import logging
import requests

logger = logging.getLogger(__name__)

HELENA_BASE_URL = "https://www.helenamt.gov"
HELENA_POLICE_URL = f"{HELENA_BASE_URL}/police"

POTENTIAL_ENDPOINTS = [
    "/police/daily-incident-reports",
    "/police/calls-for-service",
    "/police/public-blotter",
    "/api/police/incidents",
]


def discover_blotter_endpoint():
    """Probe Helena police portal for dispatch feed."""
    logger.info("Discovering Helena Police blotter endpoint")
    
    for endpoint in POTENTIAL_ENDPOINTS:
        url = HELENA_BASE_URL + endpoint
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
    """Ingest Helena Police blotter (not yet implemented)."""
    endpoint = discover_blotter_endpoint()
    if not endpoint:
        logger.warning("Helena blotter endpoint not yet available")
        return None, 0, 0
    
    logger.info(f"Would fetch from {endpoint}")
    # TODO: Implement once endpoint confirmed
    return None, 0, 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    discover_blotter_endpoint()
