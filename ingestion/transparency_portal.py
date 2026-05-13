"""
MT Transparency Portal CSV Ingestion Pipeline
Handles public employee salaries and government contracts/spending.
Source: transparency.mt.gov
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db

logger = logging.getLogger("transparency_portal")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(handler)

BASE_URL = "https://transparency.mt.gov"
SALARY_PATH = "/State-Employee-Salary-and-Compensation/State-Employee-Salary-Compensation/3h5r-2d5q/data"
CONTRACT_PATH = "/Contracts/Contracts/7k5k-2d5q/data"
EXPENDITURE_PATH = "/Expenditures/Expenditures/8l6l-3e6r/data"

HEADERS = {
    "User-Agent": "MontanaBlotterBot/1.0 (transparency data ingestion)",
    "Accept": "text/csv,application/json,text/plain,*/*",
}


@dataclass
class SalaryRecord:
    employee_name: str
    agency: str
    position: str
    salary: float | None
    county: str | None
    fiscal_year: str | None
    raw_row: dict[str, str]

    def fingerprint(self) -> str:
        payload = f"{self.employee_name}|{self.agency}|{self.position}|{self.salary}|{self.fiscal_year}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


@dataclass
class ContractRecord:
    agency: str
    vendor: str
    amount: float | None
    contract_type: str | None
    date: str | None
    description: str | None
    county: str | None
    raw_row: dict[str, str]

    def fingerprint(self) -> str:
        payload = f"{self.agency}|{self.vendor}|{self.amount}|{self.date}|{self.description}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


@dataclass
class ExpenditureRecord:
    agency: str
    vendor: str
    amount: float | None
    expenditure_type: str | None
    date: str | None
    description: str | None
    county: str | None
    raw_row: dict[str, str]

    def fingerprint(self) -> str:
        payload = f"{self.agency}|{self.vendor}|{self.amount}|{self.date}|{self.description}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]


def ensure_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS public_salaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_name TEXT NOT NULL,
            agency TEXT NOT NULL,
            position TEXT,
            salary REAL,
            county TEXT,
            fiscal_year TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS government_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency TEXT NOT NULL,
            vendor TEXT NOT NULL,
            amount REAL,
            contract_type TEXT,
            contract_date TEXT,
            description TEXT,
            county TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            analysis_summary TEXT,
            analysis_flags TEXT,
            vendor_record_count INTEGER DEFAULT 0,
            agency_large_threshold REAL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS government_expenditures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency TEXT NOT NULL,
            vendor TEXT NOT NULL,
            amount REAL,
            expenditure_type TEXT,
            expenditure_date TEXT,
            description TEXT,
            county TEXT,
            fingerprint TEXT NOT NULL UNIQUE,
            raw_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS watchdog_spending_alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_record_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(subscription_id, source_type, source_record_id)
        )
        """
    )

    # Safe additive migrations
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_contracts_agency ON government_contracts(agency)",
        "CREATE INDEX IF NOT EXISTS idx_contracts_vendor ON government_contracts(vendor)",
        "CREATE INDEX IF NOT EXISTS idx_contracts_date ON government_contracts(contract_date)",
        "CREATE INDEX IF NOT EXISTS idx_expenditures_agency ON government_expenditures(agency)",
        "CREATE INDEX IF NOT EXISTS idx_expenditures_vendor ON government_expenditures(vendor)",
        "CREATE INDEX IF NOT EXISTS idx_expenditures_date ON government_expenditures(expenditure_date)",
    ):
        cursor.execute(sql)

    for col, col_type in (
        ("analysis_summary", "TEXT"),
        ("analysis_flags", "TEXT"),
        ("vendor_record_count", "INTEGER DEFAULT 0"),
        ("agency_large_threshold", "REAL"),
    ):
        try:
            cursor.execute(f"ALTER TABLE government_contracts ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    conn.commit()


def _fetch_csv(url: str, timeout: int = 120) -> list[dict[str, str]]:
    logger.info("Fetching %s", url)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    text = resp.text
    if text.strip().startswith("{"):
        logger.warning("Endpoint returned JSON instead of CSV; skipping parse.")
        return []
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _clean_money(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.replace("$", "").replace(",", "").replace(" ", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_string(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip() or None


def parse_salary_rows(rows: list[dict[str, str]]) -> list[SalaryRecord]:
    records: list[SalaryRecord] = []
    for row in rows:
        name = _clean_string(row.get("Employee Name") or row.get("NAME") or row.get("name"))
        agency = _clean_string(row.get("Agency") or row.get("AGENCY") or row.get("agency"))
        position = _clean_string(row.get("Position") or row.get("POSITION") or row.get("position") or row.get("Job Title"))
        salary = _clean_money(row.get("Salary") or row.get("SALARY") or row.get("salary") or row.get("Total Salary"))
        county = _clean_string(row.get("County") or row.get("COUNTY") or row.get("county"))
        year = _clean_string(row.get("Fiscal Year") or row.get("YEAR") or row.get("fiscal_year") or row.get("Year"))
        if not name or not agency:
            continue
        records.append(SalaryRecord(name, agency, position or "", salary, county, year, row))
    return records


def parse_contract_rows(rows: list[dict[str, str]]) -> list[ContractRecord]:
    records: list[ContractRecord] = []
    for row in rows:
        agency = _clean_string(row.get("Agency") or row.get("AGENCY") or row.get("agency"))
        vendor = _clean_string(row.get("Vendor") or row.get("VENDOR") or row.get("vendor") or row.get("Contractor"))
        amount = _clean_money(row.get("Amount") or row.get("AMOUNT") or row.get("amount") or row.get("Contract Amount"))
        ctype = _clean_string(row.get("Contract Type") or row.get("TYPE") or row.get("contract_type"))
        date = _clean_string(row.get("Date") or row.get("DATE") or row.get("date") or row.get("Contract Date"))
        desc = _clean_string(row.get("Description") or row.get("DESCRIPTION") or row.get("description") or row.get("Purpose"))
        county = _clean_string(row.get("County") or row.get("COUNTY") or row.get("county"))
        if not agency or not vendor:
            continue
        records.append(ContractRecord(agency, vendor, amount, ctype, date, desc, county, row))
    return records


def parse_expenditure_rows(rows: list[dict[str, str]]) -> list[ExpenditureRecord]:
    records: list[ExpenditureRecord] = []
    for row in rows:
        agency = _clean_string(row.get("Agency") or row.get("AGENCY") or row.get("agency"))
        vendor = _clean_string(row.get("Vendor") or row.get("VENDOR") or row.get("vendor"))
        amount = _clean_money(row.get("Amount") or row.get("AMOUNT") or row.get("amount"))
        etype = _clean_string(row.get("Expenditure Type") or row.get("TYPE") or row.get("expenditure_type"))
        date = _clean_string(row.get("Date") or row.get("DATE") or row.get("date"))
        desc = _clean_string(row.get("Description") or row.get("DESCRIPTION") or row.get("description"))
        county = _clean_string(row.get("County") or row.get("COUNTY") or row.get("county"))
        if not agency or not vendor:
            continue
        records.append(ExpenditureRecord(agency, vendor, amount, etype, date, desc, county, row))
    return records


def _analyze_contract(conn: sqlite3.Connection, rec: ContractRecord) -> tuple[str, list[str], int, float | None]:
    flags: list[str] = []
    ctype = (rec.contract_type or "").lower()
    desc = (rec.description or "").lower()

    if "sole" in ctype or "single source" in ctype or "sole source" in desc:
        flags.append("sole-source")
    if "emergency" in ctype or "emergency" in desc:
        flags.append("emergency")

    vendor_count = 0
    if rec.vendor:
        vendor_count = conn.execute(
            """
            SELECT (
                (SELECT COUNT(1) FROM government_contracts WHERE lower(vendor)=lower(?)) +
                (SELECT COUNT(1) FROM government_expenditures WHERE lower(vendor)=lower(?))
            ) AS c
            """,
            (rec.vendor, rec.vendor),
        ).fetchone()["c"] or 0
        if vendor_count > 1:
            flags.append("vendor-in-other-records")

    p95 = None
    if rec.amount is not None:
        rows = conn.execute(
            "SELECT amount FROM government_contracts WHERE lower(agency)=lower(?) AND amount IS NOT NULL ORDER BY amount",
            (rec.agency,),
        ).fetchall()
        amounts = [float(r["amount"]) for r in rows]
        if len(amounts) >= 10:
            idx = int(0.95 * (len(amounts) - 1))
            p95 = amounts[idx]
            if rec.amount >= p95:
                flags.append("unusually-large-for-agency")

    summary = (
        f"{rec.agency} awarded {rec.vendor} "
        f"for ${rec.amount:,.2f} " if rec.amount is not None else f"{rec.agency} awarded {rec.vendor} "
    )
    if rec.contract_type:
        summary += f"as a {rec.contract_type} contract"
    else:
        summary += "under a state contract"
    if rec.date:
        summary += f" on {rec.date}"
    if rec.description:
        summary += f". Purpose: {rec.description[:220]}"
    if flags:
        summary += f". Flags: {', '.join(flags)}"

    return summary.strip(), flags, vendor_count, p95


def _queue_watchdog_spending_alerts(
    conn: sqlite3.Connection,
    source_type: str,
    source_record_id: int,
    agency: str,
    vendor: str,
    amount: float | None,
) -> None:
    alerts = conn.execute(
        """
        SELECT id, subscription_id, alert_type, vendor_name, agency_name, threshold_amount
        FROM watchdog_spending_alerts
        WHERE is_active = 1
        """
    ).fetchall()
    if not alerts:
        return

    for alert in alerts:
        message = None
        if alert["alert_type"] == "vendor" and alert["vendor_name"]:
            if vendor.lower() == str(alert["vendor_name"]).strip().lower():
                message = f"Vendor alert: {vendor} received a new {source_type.replace('_', ' ')} from {agency}."
        elif alert["alert_type"] == "agency_threshold" and alert["agency_name"] and amount is not None:
            threshold = float(alert["threshold_amount"] or 0)
            if agency.lower() == str(alert["agency_name"]).strip().lower() and amount >= threshold:
                message = (
                    f"Agency threshold alert: {agency} posted a {source_type.replace('_', ' ')} "
                    f"of ${amount:,.2f} (threshold ${threshold:,.2f})."
                )

        if message:
            conn.execute(
                """
                INSERT OR IGNORE INTO watchdog_spending_alert_events
                (subscription_id, source_type, source_record_id, message)
                VALUES (?, ?, ?, ?)
                """,
                (alert["subscription_id"], source_type, source_record_id, message),
            )


def import_salaries(conn: sqlite3.Connection, records: list[SalaryRecord]) -> dict[str, int]:
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for rec in records:
        fp = rec.fingerprint()
        cursor.execute("SELECT id FROM public_salaries WHERE fingerprint = ?", (fp,))
        existing = cursor.fetchone()
        raw_json = json.dumps(rec.raw_row)
        if existing:
            cursor.execute(
                """
                UPDATE public_salaries
                SET employee_name = ?, agency = ?, position = ?, salary = ?, county = ?,
                    fiscal_year = ?, raw_json = ?, updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (rec.employee_name, rec.agency, rec.position, rec.salary, rec.county, rec.fiscal_year, raw_json, fp),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO public_salaries
                (employee_name, agency, position, salary, county, fiscal_year, fingerprint, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rec.employee_name, rec.agency, rec.position, rec.salary, rec.county, rec.fiscal_year, fp, raw_json),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated}


def import_contracts(conn: sqlite3.Connection, records: list[ContractRecord]) -> dict[str, int]:
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for rec in records:
        fp = rec.fingerprint()
        cursor.execute("SELECT id FROM government_contracts WHERE fingerprint = ?", (fp,))
        existing = cursor.fetchone()
        summary, flags, vendor_count, p95 = _analyze_contract(conn, rec)
        raw_json = json.dumps(rec.raw_row)

        if existing:
            cursor.execute(
                """
                UPDATE government_contracts
                SET agency = ?, vendor = ?, amount = ?, contract_type = ?, contract_date = ?, description = ?,
                    county = ?, raw_json = ?, analysis_summary = ?, analysis_flags = ?, vendor_record_count = ?,
                    agency_large_threshold = ?, updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (rec.agency, rec.vendor, rec.amount, rec.contract_type, rec.date, rec.description, rec.county,
                 raw_json, summary, json.dumps(flags), vendor_count, p95, fp),
            )
            row_id = existing["id"]
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO government_contracts
                (agency, vendor, amount, contract_type, contract_date, description, county, fingerprint, raw_json,
                 analysis_summary, analysis_flags, vendor_record_count, agency_large_threshold)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rec.agency, rec.vendor, rec.amount, rec.contract_type, rec.date, rec.description, rec.county,
                 fp, raw_json, summary, json.dumps(flags), vendor_count, p95),
            )
            row_id = cursor.lastrowid
            inserted += 1

        _queue_watchdog_spending_alerts(conn, "government_contract", row_id, rec.agency, rec.vendor, rec.amount)

    conn.commit()
    return {"inserted": inserted, "updated": updated}


def import_expenditures(conn: sqlite3.Connection, records: list[ExpenditureRecord]) -> dict[str, int]:
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for rec in records:
        fp = rec.fingerprint()
        cursor.execute("SELECT id FROM government_expenditures WHERE fingerprint = ?", (fp,))
        existing = cursor.fetchone()
        raw_json = json.dumps(rec.raw_row)

        if existing:
            cursor.execute(
                """
                UPDATE government_expenditures
                SET agency = ?, vendor = ?, amount = ?, expenditure_type = ?, expenditure_date = ?, description = ?,
                    county = ?, raw_json = ?, updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (rec.agency, rec.vendor, rec.amount, rec.expenditure_type, rec.date, rec.description, rec.county, raw_json, fp),
            )
            row_id = existing["id"]
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO government_expenditures
                (agency, vendor, amount, expenditure_type, expenditure_date, description, county, fingerprint, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rec.agency, rec.vendor, rec.amount, rec.expenditure_type, rec.date, rec.description, rec.county, fp, raw_json),
            )
            row_id = cursor.lastrowid
            inserted += 1

        _queue_watchdog_spending_alerts(conn, "government_expenditure", row_id, rec.agency, rec.vendor, rec.amount)

    conn.commit()
    return {"inserted": inserted, "updated": updated}


def run_salary_ingest(csv_url: str | None = None, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    url = csv_url or urljoin(BASE_URL, SALARY_PATH)
    rows = _fetch_csv(url)
    if not rows:
        return {"status": "no_data", "message": "No rows returned from endpoint"}
    records = parse_salary_rows(rows)
    if not records:
        return {"status": "parse_failure", "message": "Could not parse any rows"}
    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)
    stats = import_salaries(conn, records)
    if close_conn:
        conn.close()
    return {"status": "ok", **stats, "total_parsed": len(records)}


def run_contract_ingest(csv_url: str | None = None, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    url = csv_url or urljoin(BASE_URL, CONTRACT_PATH)
    rows = _fetch_csv(url)
    if not rows:
        return {"status": "no_data", "message": "No rows returned from endpoint"}
    records = parse_contract_rows(rows)
    if not records:
        return {"status": "parse_failure", "message": "Could not parse any rows"}
    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)
    stats = import_contracts(conn, records)
    if close_conn:
        conn.close()
    return {"status": "ok", **stats, "total_parsed": len(records)}


def run_expenditure_ingest(csv_url: str | None = None, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    url = csv_url or urljoin(BASE_URL, EXPENDITURE_PATH)
    rows = _fetch_csv(url)
    if not rows:
        return {"status": "no_data", "message": "No rows returned from endpoint"}
    records = parse_expenditure_rows(rows)
    if not records:
        return {"status": "parse_failure", "message": "Could not parse any rows"}
    close_conn = conn is None
    if conn is None:
        conn = db.connect_db()
    ensure_schema(conn)
    stats = import_expenditures(conn, records)
    if close_conn:
        conn.close()
    return {"status": "ok", **stats, "total_parsed": len(records)}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest MT Transparency Portal CSVs")
    parser.add_argument("--salary-url", help="Override salary CSV URL")
    parser.add_argument("--contract-url", help="Override contract CSV URL")
    parser.add_argument("--expenditure-url", help="Override expenditure CSV URL")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    args = parser.parse_args()

    conn = db.connect_db() if not args.dry_run else None
    if conn:
        ensure_schema(conn)

    result = run_salary_ingest(args.salary_url, conn)
    logger.info("Salary ingest: %s", result)
    result = run_contract_ingest(args.contract_url, conn)
    logger.info("Contract ingest: %s", result)
    result = run_expenditure_ingest(args.expenditure_url, conn)
    logger.info("Expenditure ingest: %s", result)

    if conn:
        conn.close()
