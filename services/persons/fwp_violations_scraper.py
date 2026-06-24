"""
Montana FWP Violations Scraper

Scrapes enforcement press releases from fwp.mt.gov and uses Claude (Haiku)
to extract structured violation records from the narrative text.

CLI usage:
    python -m services.persons.fwp_violations_scraper --months 3
    python -m services.persons.fwp_violations_scraper --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from datetime import date

import requests

import config
from db import connect_db

FWP_NEWS_BASE = 'https://fwp.mt.gov/homepage/news'
USER_AGENT = 'MontanaBlotter/1.0 (news@montanablotter.com)'

_ENFORCEMENT_KEYWORDS = (
    'citation', 'violat', 'poach', 'convict', 'sentence', 'plead', 'guilty',
    'fine', 'restitution', 'suspend', 'arrest', 'charge', 'enforcement',
    'illegal', 'unlawful', 'conservation officer',
)
_MONTH_NAMES = [
    'january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december',
]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def ensure_fwp_schema(conn: sqlite3.Connection) -> None:
    conn.execute('''
        CREATE TABLE IF NOT EXISTS fwp_violations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url          TEXT UNIQUE NOT NULL,
            article_title       TEXT,
            article_date        TEXT,
            defendant_name      TEXT,
            defendant_city      TEXT,
            violation_types     TEXT,
            county              TEXT,
            species             TEXT,
            fines_dollars       REAL,
            restitution_dollars REAL,
            suspension_months   INTEGER,
            court               TEXT,
            case_date           TEXT,
            raw_text            TEXT,
            extracted_json      TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_fwp_violations_url  ON fwp_violations(source_url)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_fwp_violations_date ON fwp_violations(article_date)')
    conn.commit()


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def _fetch_article_links(year: int, month: int) -> list[dict[str, str]]:
    month_name = _MONTH_NAMES[month - 1]
    url = f"{FWP_NEWS_BASE}/{year}/{month_name}"
    try:
        resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=20)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
    except Exception as exc:
        print(f"  fetch failed {url}: {exc}")
        return []

    seen: set[str] = set()
    links = []
    for m in re.finditer(
        r'href="(/homepage/news/\d{4}/[a-z]+/[^"]+)"[^>]*>([^<]+)<',
        resp.text, re.IGNORECASE,
    ):
        href = 'https://fwp.mt.gov' + m.group(1).rstrip('/')
        title = m.group(2).strip()
        if href not in seen:
            seen.add(href)
            links.append({'url': href, 'title': title})
    return links


def _is_enforcement(title: str, url: str) -> bool:
    text = (title + ' ' + url).lower()
    return any(kw in text for kw in _ENFORCEMENT_KEYWORDS)


def _fetch_text(url: str) -> str:
    try:
        resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=20)
        resp.raise_for_status()
    except Exception:
        return ''
    text = re.sub(r'<[^>]+>', ' ', resp.text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:4000]


# ---------------------------------------------------------------------------
# Claude extraction
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a structured data extractor for Montana FWP enforcement press releases. "
    "Return ONLY valid JSON with these keys (null for unknown): "
    "defendant_name, defendant_city, violation_types (array), county, species (array), "
    "fines_dollars (number), restitution_dollars (number), suspension_months (integer), "
    "court, case_date (YYYY-MM-DD or null)."
)


def _extract(text: str) -> dict:
    if not getattr(config, 'USE_PAID_LLM', False):
        return {}
    api_key = getattr(config, 'ANTHROPIC_API_KEY', None)
    if not api_key:
        return {}
    try:
        import anthropic
        msg = anthropic.Anthropic(api_key=api_key).messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=512,
            system=_SYSTEM,
            messages=[{'role': 'user', 'content': text}],
            timeout=20.0,
        )
        raw = re.sub(r'^```json\s*|```$', '', msg.content[0].text.strip(), flags=re.MULTILINE)
        return json.loads(raw)
    except Exception as exc:
        print(f"  Claude error: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Ingest one article
# ---------------------------------------------------------------------------

def _ingest(conn: sqlite3.Connection, url: str, title: str,
            article_date: str, dry_run: bool) -> bool:
    if conn.execute('SELECT id FROM fwp_violations WHERE source_url=?', (url,)).fetchone():
        return False
    text = _fetch_text(url)
    if not text:
        return False
    extracted = _extract(text)
    if dry_run:
        print(f"  [dry-run] {title[:70]}\n    → {extracted}")
        return True
    conn.execute(
        '''INSERT OR IGNORE INTO fwp_violations
           (source_url,article_title,article_date,defendant_name,defendant_city,
            violation_types,county,species,fines_dollars,restitution_dollars,
            suspension_months,court,case_date,raw_text,extracted_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (url, title, article_date,
         extracted.get('defendant_name'), extracted.get('defendant_city'),
         json.dumps(extracted.get('violation_types') or []),
         extracted.get('county'),
         json.dumps(extracted.get('species') or []),
         extracted.get('fines_dollars'), extracted.get('restitution_dollars'),
         extracted.get('suspension_months'), extracted.get('court'),
         extracted.get('case_date'), text[:2000], json.dumps(extracted)),
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape_fwp_violations(months_back: int = 3, dry_run: bool = False) -> int:
    conn = connect_db()
    ensure_fwp_schema(conn)
    today = date.today()
    ingested = 0
    for i in range(months_back):
        year = today.year if (today.month - i) > 0 else today.year - 1
        month = ((today.month - 1 - i) % 12) + 1
        print(f"Scanning FWP news {year}/{month:02d}")
        for link in _fetch_article_links(year, month):
            if not _is_enforcement(link['title'], link['url']):
                continue
            print(f"  {link['title'][:70]}")
            if _ingest(conn, link['url'], link['title'], f"{year}-{month:02d}", dry_run):
                ingested += 1
            time.sleep(1)
        time.sleep(1)
    conn.close()
    print(f"Done — {ingested} new FWP violation(s)")
    return ingested


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--months', type=int, default=3)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    scrape_fwp_violations(args.months, args.dry_run)


if __name__ == '__main__':
    main()
