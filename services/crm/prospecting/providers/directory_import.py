"""File-based provider for state registries & licensee directories.

Reality check (verified 2026-09-24 from this VPS):
  * biz.sosmt.gov business search -> 403 "Security Check" for non-browser
    agents. No documented public JSON API.
  * boards.bsd.dli.mt.gov "Licensee Lookup System" -> JS portal (PI /
    bail enforcement licensee lists).
  * mtbar.org LawFinder -> JS-driven member search.

Rather than ship a fragile scraper behind three WAFs, this provider
ingests the exports those sites DO offer (CSV download, print-to-table,
or a saved .html of the results page) dropped into data/prospecting/ by
the operator. Column mapping is header-name heuristic, so exports from
different sites all work. A later revision can add a Playwright path;
the engine upsert layer will not care where records came from.

Usage:
    python3 -m services.crm.prospecting --vertical bail \
        --import data/prospecting/sos_bail_export.csv
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from services.crm.prospecting.engine import ProspectRecord

# header fragment -> record field (first match wins, case-insensitive)
_HEADER_MAP = [
    ('business_name', ('business name', 'entity name', 'firm name', 'name',
                       'licensee', 'agency name', 'dba')),
    ('contact_name', ('contact', 'owner', 'principal', 'officer', 'first name',
                      'attorney name', 'agent name')),
    ('email', ('email', 'e-mail')),
    ('phone', ('phone', 'telephone', 'voice')),
    ('website', ('website', 'web site', 'url')),
    ('license_number', ('license', 'licence', 'bar number', 'bar #', 'reg number',
                        'registration number', 'account number')),
    ('city', ('city', 'mailing city', 'physical city', 'town')),
    ('county', ('county',)),
    ('address', ('address', 'street', 'physical')),
    ('state', ('state',)),
]


def _map_columns(headers: list[str]) -> dict:
    mapping = {}
    for i, h in enumerate(headers):
        hl = re.sub(r'[^a-z# ]', ' ', h.lower()).strip()
        for field_name, frags in _HEADER_MAP:
            if field_name in mapping:
                continue
            if any(f in hl for f in frags):
                mapping[field_name] = i
                break
    return mapping


def _rows_from_html(path: Path, limit: int):
    """Parse the first <table> of a saved results page. Stdlib-only."""
    from html.parser import HTMLParser

    class TableGrab(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self._row, self._cell, self._in_tbl = [], None, None, 0
        def handle_starttag(self, tag, attrs):
            if tag == 'table':
                self._in_tbl += 1
            elif tag == 'tr' and self._in_tbl:
                self._row = []
            elif tag in ('td', 'th') and self._row is not None:
                self._cell = []
        def handle_endtag(self, tag):
            if tag == 'table':
                self._in_tbl -= 1
                if self._in_tbl == 0 and self.rows:
                    raise StopIteration
            elif tag == 'tr' and self._row is not None:
                self.rows.append(self._row); self._row = None
            elif tag in ('td', 'th') and self._cell is not None:
                self._row.append(' '.join(''.join(self._cell).split()))
                self._cell = None
        def handle_data(self, data):
            if self._cell is not None:
                self._cell.append(data)

    p = TableGrab()
    try:
        p.feed(path.read_text(errors='replace'))
    except StopIteration:
        pass
    rows = p.rows[:limit + 1]
    if not rows:
        return
    yield rows[0]
    yield from rows[1:]


def fetch_file(path: str, vertical: str, limit: int = 500):
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(src)
    text = src.read_text(errors='replace')
    if src.suffix == '.json':
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get('results') or data.get('records') or []
        if data:
            mapping = _map_columns([str(h) for h in data[0].keys()])
            headers = list(data[0].keys())
            for row in data[:limit]:
                yield _to_record({f: str(row.get(headers[i], '') or '')
                                  for f, i in mapping.items()},
                                 vertical, src.name)
        return
    if src.suffix == '.html':
        it = _rows_from_html(src, limit)
    else:
        rdr = csv.reader(io.StringIO(text))
        it = (r for r in rdr if any(c.strip() for c in r))
    try:
        headers = next(it)
    except StopIteration:
        return
    mapping = _map_columns(list(headers))
    for row in it:
        yield _to_record({f: (row[i] if i < len(row) else '')
                          for f, i in mapping.items()}, vertical, src.name)


def _to_record(fields: dict, vertical: str, fname: str) -> ProspectRecord:
    return ProspectRecord(
        business_name=(fields.get('business_name') or '').strip(),
        category=vertical,
        contact_name=(fields.get('contact_name') or '').strip(),
        email=(fields.get('email') or '').strip(),
        phone=(fields.get('phone') or '').strip(),
        website=(fields.get('website') or '').strip(),
        license_number=(fields.get('license_number') or '').strip(),
        city=(fields.get('city') or '').strip(),
        county=(fields.get('county') or '').strip(),
        address=(fields.get('address') or '').strip(),
        state=(fields.get('state') or 'MT').strip(),
        source_provider='state_directory',
        source_ref=f'{fname}:{fields.get("license_number") or fields.get("business_name")}',
        raw={'imported_from': str(fname)})
