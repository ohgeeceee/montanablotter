"""
MT Transparency Portal CSV Ingestion Pipeline
Handles public employee salaries and government contracts/spending.
Source: transparency.mt.gov
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import sqlite3
import sys
import tempfile
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

DB_PATH = os.getenv("MB_DB_PATH", "/root/montanablotter/blotter.db")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------
def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables for salaries, contracts, and expenditures if missing."""
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
        "CREATE INDEX IF NOT EXISTS idx_salaries_name ON public_salaries(employee_name)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_salaries_agency ON public_salaries(agency)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_salaries_county ON public_salaries(county)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_salaries_year ON public_salaries(fiscal_year)"
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
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_contracts_agency ON government_contracts(agency)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_contracts_vendor ON government_contracts(vendor)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_contracts_date ON government_contracts(contract_date)"
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
        "CREATE INDEX IF NOT EXISTS idx_expenditures_agency ON government_expenditures(agency)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_expenditures_vendor ON government_expenditures(vendor)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_expenditures_date ON government_expenditures(expenditure_date)"
    )

    conn.commit()


# ---------------------------------------------------------------------------
# CSV fetch + parse
# ---------------------------------------------------------------------------
def _fetch_csv(url: str, timeout: int = 120) -> list[dict[str, str]]:
    """Download a CSV and return rows as dicts."""
    logger.info("Fetching %s", url)
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    text = resp.text
    # Some Socrata endpoints return JSON even when CSV requested; handle gracefully.
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
        records.append(
            SalaryRecord(
                employee_name=name,
                agency=agency,
                position=position or "",
                salary=salary,
                county=county,
                fiscal_year=year,
                raw_row=row,
            )
        )
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
        records.append(
            ContractRecord(
                agency=agency,
                vendor=vendor,
                amount=amount,
                contract_type=ctype,
                date=date,
                description=desc,
                county=county,
                raw_row=row,
            )
        )
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
        records.append(
            ExpenditureRecord(
                agency=agency,
                vendor=vendor,
                amount=amount,
                expenditure_type=etype,
                date=date,
                description=desc,
                county=county,
                raw_row=row,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Database insert (upsert by fingerprint)
# ---------------------------------------------------------------------------
def import_salaries(conn: sqlite3.Connection, records: list[SalaryRecord]) -> dict[str, int]:
    cursor = conn.cursor()
    inserted = 0
    updated = 0
    for rec in records:
        fp = rec.fingerprint()
        cursor.execute("SELECT id FROM public_salaries WHERE fingerprint = ?", (fp,))
        existing = cursor.fetchone()
        raw_json = str(rec.raw_row)
        if existing:
            cursor.execute(
                """
                UPDATE public_salaries
                SET employee_name = ?, agency = ?, position = ?, salary = ?,
                    county = ?, fiscal_year = ?, raw_json = ?, updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (rec.employee_name, rec.agency, rec.position, rec.salary,
                 rec.county, rec.fiscal_year, raw_json, fp),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO public_salaries
                (employee_name, agency, position, salary, county, fiscal_year, fingerprint, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rec.employee_name, rec.agency, rec.position, rec.salary,
                 rec.county, rec.fiscal_year, fp, raw_json),
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
        raw_json = str(rec.raw_row)
        if existing:
            cursor.execute(
                """
                UPDATE government_contracts
                SET agency = ?, vendor = ?, amount = ?, contract_type = ?,
                    contract_date = ?, description = ?, county = ?, raw_json = ?, updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (rec.agency, rec.vendor, rec.amount, rec.contract_type,
                 rec.date, rec.description, rec.county, raw_json, fp),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO government_contracts
                (agency, vendor, amount, contract_type, contract_date, description, county, fingerprint, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rec.agency, rec.vendor, rec.amount, rec.contract_type,
                 rec.date, rec.description, rec.county, fp, raw_json),
            )
            inserted += 1
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
        raw_json = str(rec.raw_row)
        if existing:
            cursor.execute(
                """
                UPDATE government_expenditures
                SET agency = ?, vendor = ?, amount = ?, expenditure_type = ?,
                    expenditure_date = ?, description = ?, county = ?, raw_json = ?, updated_at = datetime('now')
                WHERE fingerprint = ?
                """,
                (rec.agency, rec.vendor, rec.amount, rec.expenditure_type,
                 rec.date, rec.description, rec.county, raw_json, fp),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO government_expenditures
                (agency, vendor, amount, expenditure_type, expenditure_date, description, county, fingerprint, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rec.agency, rec.vendor, rec.amount, rec.expenditure_type,
                 rec.date, rec.description, rec.county, fp, raw_json),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "updated": updated}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_salary_ingest(csv_url: str | None = None, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Fetch and ingest salary data."""
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
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

    if args.salary_url or not any([args.contract_url, args.expenditure_url]):
        result = run_salary_ingest(args.salary_url, conn)
        logger.info("Salary ingest: %s", result)

    if args.contract_url:
        result = run_contract_ingest(args.contract_url, conn)
        logger.info("Contract ingest: %s", result)

    if args.expenditure_url:
        result = run_expenditure_ingest(args.expenditure_url, conn)
        logger.info("Expenditure ingest: %s", result)

    if conn:
        conn.close()
