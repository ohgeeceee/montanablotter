"""
Orchestrator for all scrapers and ingestion pipelines.
Run via cron: python ingestion/run_all_scrapers.py
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db

from services.alerts.engine import process_new_record
from ingestion.alert_engine import run_alert_delivery
from services.alerts.watchdog_spending import dispatch_watchdog_spending_alerts
from services.ingestion.civil_violations_pipeline import run_pipeline as run_civil_violations_pipeline
from transparency_portal import run_contract_ingest, run_expenditure_ingest, run_salary_ingest

# Scrapers
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scrapers"))
from fwp_violations import run_fwp_scrape
from mt_511_crashes import run_511_scrape
from professional_boards import run_professional_boards_scrape
from federal_court_cases import run_federal_scrape

logger = logging.getLogger("run_all_scrapers")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)


def run_all():
    conn = db.connect_db()
    results = {}

    # 1. MT Transparency Portal (CSV ingestion)
    logger.info("=== Transparency Portal ===")
    try:
        results["salaries"] = run_salary_ingest(conn=conn)
        logger.info("Salaries: %s", results["salaries"])
    except Exception as exc:
        logger.error("Salary ingest failed: %s", exc)
        results["salaries"] = {"status": "error", "message": str(exc)}

    monthly_only = (os.getenv("MB_TRANSPARENCY_MONTHLY_ONLY", "1").strip().lower() in {"1", "true", "yes", "on"})
    should_run_monthly = (not monthly_only) or datetime.utcnow().day == 1
    if should_run_monthly:
        try:
            results["contracts"] = run_contract_ingest(conn=conn)
            logger.info("Contracts: %s", results["contracts"])
        except Exception as exc:
            logger.error("Contract ingest failed: %s", exc)
            results["contracts"] = {"status": "error", "message": str(exc)}

        try:
            results["expenditures"] = run_expenditure_ingest(conn=conn)
            logger.info("Expenditures: %s", results["expenditures"])
        except Exception as exc:
            logger.error("Expenditure ingest failed: %s", exc)
            results["expenditures"] = {"status": "error", "message": str(exc)}
    else:
        results["contracts"] = {"status": "skipped", "message": "Configured for monthly runs only"}
        results["expenditures"] = {"status": "skipped", "message": "Configured for monthly runs only"}

    # 2. FWP Violations
    logger.info("=== FWP Violations ===")
    try:
        results["fwp"] = run_fwp_scrape(max_articles=50, conn=conn)
        logger.info("FWP: %s", results["fwp"])
    except Exception as exc:
        logger.error("FWP scrape failed: %s", exc)
        results["fwp"] = {"status": "error", "message": str(exc)}

    # 3. 511 Crashes
    logger.info("=== 511 Crashes ===")
    try:
        results["511"] = run_511_scrape(conn=conn)
        logger.info("511: %s", results["511"])
    except Exception as exc:
        logger.error("511 scrape failed: %s", exc)
        results["511"] = {"status": "error", "message": str(exc)}

    # 4. Professional Boards
    logger.info("=== Professional Boards ===")
    try:
        results["boards"] = run_professional_boards_scrape(conn=conn)
        logger.info("Boards: %s", results["boards"])
    except Exception as exc:
        logger.error("Board scrape failed: %s", exc)
        results["boards"] = {"status": "error", "message": str(exc)}

    # 5. Federal Court Cases
    logger.info("=== Federal Court Cases ===")
    try:
        results["federal"] = run_federal_scrape(conn=conn)
        logger.info("Federal: %s", results["federal"])
    except Exception as exc:
        logger.error("Federal scrape failed: %s", exc)
        results["federal"] = {"status": "error", "message": str(exc)}

    # 6. Civil filings + code violations (live first, then import/demo fallback)
    logger.info("=== Civil + Code Violations Pipeline ===")
    try:
        civil_import_dir = (os.getenv("MB_CIVIL_IMPORT_DIR", "") or "").strip()
        code_import_dir = (os.getenv("MB_CODE_IMPORT_DIR", "") or "").strip()
        seed_demo = (os.getenv("MB_CIVIL_SEED_DEMO", "") or "").strip().lower() in {"1", "true", "yes", "on"}
        results["civil_violations_pipeline"] = run_civil_violations_pipeline(
            civil_import_dir=civil_import_dir,
            code_import_dir=code_import_dir,
            live=True,
            seed_demo=seed_demo,
            max_pages=10,
            delay_seconds=0.5,
        )
    except Exception as exc:
        logger.error("Civil + code violations pipeline failed: %s", exc)
        results["civil_violations_pipeline"] = {"status": "error", "message": str(exc)}

    conn.close()

    # 7. Deliver pending alerts
    logger.info("=== Alert Delivery ===")
    try:
        delivery = run_alert_delivery()
        logger.info("Alert delivery: %s", delivery)
    except Exception as exc:
        logger.error("Alert delivery failed: %s", exc)
    try:
        spending_delivery = dispatch_watchdog_spending_alerts(limit=200)
        logger.info("Watchdog spending alert delivery: %s", spending_delivery)
    except Exception as exc:
        logger.error("Watchdog spending alert delivery failed: %s", exc)

    logger.info("=== All scrapers complete ===")
    return results


if __name__ == "__main__":
    run_all()
