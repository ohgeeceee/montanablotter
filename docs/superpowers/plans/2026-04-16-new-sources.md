# New Source Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add jail roster adapters for Gallatin, Cascade, Lewis & Clark, Silver Bow, and Ravalli counties, plus a Helena PD police incident fetcher.

**Architecture:** All jail adapters live in `jail_booking_ingest.py` — add to `TRACKED_SOURCES`, `SUPPORTED_ADAPTERS`, implement a `fetch_COUNTY_bookings()` function, and add an `elif` branch in `_run_source()`. Helena PD gets its own `helena_police_fetcher.py` modeled after `bozeman_police_fetcher.py`, writing to the `records` table.

**Tech Stack:** Python 3.12, requests, pdfplumber (for PDF counties), SQLite, pytest

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `jail_booking_ingest.py` | All 5 new county adapters + dispatch |
| Create | `helena_police_fetcher.py` | Helena PD CFS ingestion |
| Modify | `crontab.txt` | 5 new cron entries (Gallatin cron already exists) |
| Create | `tests/test_new_county_adapters.py` | Unit tests for new jail parsers |
| Create | `tests/test_helena_police_fetcher.py` | Unit tests for Helena PD fetcher |

---

## Task 1: Gallatin County jail adapter (Zuercher portal)

Gallatin uses `gallatin-so-mt.zuercherportal.com` — same portal software as Jefferson. Extract a shared internal helper so both counties use identical logic.

**Files:**
- Modify: `jail_booking_ingest.py:42` (`SUPPORTED_ADAPTERS`), `jail_booking_ingest.py:828-914` (after existing Zuercher code), `jail_booking_ingest.py:1340-1353` (`_run_source` dispatch)
- Modify: `tests/test_new_county_adapters.py` (create file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_new_county_adapters.py`:

```python
import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch

from jail_booking_ingest import (
    fetch_gallatin_bookings,
    JailBookingRecord,
)


ZUERCHER_RESPONSE = {
    "total_record_count": 2,
    "records": [
        {
            "name": "DOE, JOHN",
            "sex": "M",
            "race": "W",
            "arrest_date": "2026-04-15",
            "held_for_agency": "Gallatin County Sheriff",
            "hold_reasons": "Assault, Felony<br/>DUI",
        },
        {
            "name": "SMITH, JANE",
            "sex": "F",
            "race": "W",
            "arrest_date": "2026-04-14",
            "held_for_agency": "Bozeman PD",
            "hold_reasons": "",
        },
    ],
}


class GallatinAdapterTests(unittest.TestCase):
    @patch("jail_booking_ingest.requests.Session")
    def test_fetch_gallatin_returns_records(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        page1 = MagicMock()
        page1.json.return_value = ZUERCHER_RESPONSE
        page1.raise_for_status = MagicMock()
        session.post.return_value = page1

        records = fetch_gallatin_bookings("https://gallatin-so-mt.zuercherportal.com/#/inmates")

        assert len(records) == 2
        assert records[0].person_name == "Doe, John"
        assert "Assault" in records[0].charges_summary

    @patch("jail_booking_ingest.requests.Session")
    def test_fetch_gallatin_empty_hold_reasons_fallback(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session
        response = MagicMock()
        response.json.return_value = {
            "total_record_count": 1,
            "records": [
                {
                    "name": "SMITH, JANE",
                    "sex": "F",
                    "race": "W",
                    "arrest_date": "2026-04-14",
                    "held_for_agency": "Bozeman PD",
                    "hold_reasons": "",
                }
            ],
        }
        response.raise_for_status = MagicMock()
        session.post.return_value = response

        records = fetch_gallatin_bookings("https://gallatin-so-mt.zuercherportal.com/#/inmates")

        assert len(records) == 1
        assert "Charge details available" in records[0].charges_summary

    @patch("jail_booking_ingest.requests.Session")
    def test_fetch_gallatin_paginates(self, mock_session_cls):
        session = MagicMock()
        mock_session_cls.return_value = session

        page1_data = {
            "total_record_count": 51,
            "records": [
                {
                    "name": f"PERSON, {i:02d}",
                    "sex": "M",
                    "race": "W",
                    "arrest_date": "2026-04-15",
                    "held_for_agency": "Gallatin SO",
                    "hold_reasons": "DUI",
                }
                for i in range(50)
            ],
        }
        page2_data = {
            "total_record_count": 51,
            "records": [
                {
                    "name": "LAST, PERSON",
                    "sex": "F",
                    "race": "W",
                    "arrest_date": "2026-04-15",
                    "held_for_agency": "Gallatin SO",
                    "hold_reasons": "Theft",
                }
            ],
        }
        r1 = MagicMock()
        r1.json.return_value = page1_data
        r1.raise_for_status = MagicMock()
        r2 = MagicMock()
        r2.json.return_value = page2_data
        r2.raise_for_status = MagicMock()
        session.post.side_effect = [r1, r2]

        records = fetch_gallatin_bookings("https://gallatin-so-mt.zuercherportal.com/#/inmates")

        assert len(records) == 51
        assert session.post.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /root/montanablotter && source venv/bin/activate
python -m pytest tests/test_new_county_adapters.py::GallatinAdapterTests -v
```

Expected: `ImportError: cannot import name 'fetch_gallatin_bookings'`

- [ ] **Step 3: Add Gallatin to TRACKED_SOURCES (it's already there) and SUPPORTED_ADAPTERS**

In `jail_booking_ingest.py`, line 42, update `SUPPORTED_ADAPTERS`:

```python
SUPPORTED_ADAPTERS = {"broadwater", "flathead", "gallatin", "jefferson", "missoula", "sanders", "yellowstone"}
```

- [ ] **Step 4: Add `fetch_gallatin_bookings()` after the existing `fetch_jefferson_bookings()` function (around line 914)**

```python
def fetch_gallatin_bookings(source_url: str) -> list[JailBookingRecord]:
    api_base = source_url.rstrip("/")
    if api_base.endswith("#/inmates"):
        api_base = api_base[:-9].rstrip("/")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)",
            "Accept": "application/json,text/plain,*/*",
            "Referer": f"{api_base}/#/inmates",
        }
    )

    records: list[JailBookingRecord] = []
    offset = 0
    page_size = 50
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00.000Z")

    while True:
        response = session.post(
            f"{api_base}/api/portal/inmates/load",
            json={
                "name": "",
                "race": "all",
                "sex": "all",
                "cell_block": "all",
                "held_for_agency": "any",
                "in_custody": today,
                "paging": {"start": offset, "count": page_size},
                "sorting": {"sort_by_column_tag": "name", "sort_descending": False},
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json() or {}
        rows = payload.get("records") or []

        for row in rows:
            charges_summary = _summarize_zuercher_hold_reasons(row.get("hold_reasons", ""))
            arrest_date = _normalize_datetime(f"{row.get('arrest_date', '')} 00:00")
            identity_parts = [
                (row.get("name") or "").strip().upper(),
                (row.get("held_for_agency") or "").strip().upper(),
                (row.get("sex") or "").strip().upper(),
                (row.get("arrest_date") or "").strip(),
                charges_summary,
            ]
            source_record_id = hashlib.sha1("|".join(identity_parts).encode("utf-8")).hexdigest()[:20]
            booking_number = source_record_id[:12]

            records.append(
                JailBookingRecord(
                    source_record_id=source_record_id,
                    person_name=(row.get("name") or "").title(),
                    age=None,
                    booking_number=booking_number,
                    booking_at=arrest_date,
                    charges_summary=charges_summary,
                    source_url=f"{api_base}/#/inmates",
                )
            )

        offset += len(rows)
        total = int(payload.get("total_record_count") or 0)
        if not rows or offset >= total:
            break

    return records
```

- [ ] **Step 5: Add `elif` branch in `_run_source()` (around line 1344)**

Add after the `elif county_slug == "flathead":` branch:

```python
    elif county_slug == "gallatin":
        records = fetch_gallatin_bookings(roster_url)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python -m pytest tests/test_new_county_adapters.py::GallatinAdapterTests -v
```

Expected: All 3 tests PASS

- [ ] **Step 7: Commit**

```bash
git add jail_booking_ingest.py tests/test_new_county_adapters.py
git commit -m "feat: add Gallatin County jail roster adapter (Zuercher portal)"
```

---

## Task 2: Cascade County jail adapter (HTML)

Cascade County's page at `cascadecountymt.gov/314/Inmate-Roster` serves an HTML table updated every 4 hours. Before writing the parser, inspect the live page to confirm the HTML structure.

**Files:**
- Modify: `jail_booking_ingest.py` (TRACKED_SOURCES, SUPPORTED_ADAPTERS, parser, fetch fn, dispatch)
- Modify: `tests/test_new_county_adapters.py`

- [ ] **Step 1: Inspect the live page**

```bash
curl -s "https://www.cascadecountymt.gov/314/Inmate-Roster" | grep -A5 -i "inmate\|table\|roster" | head -60
```

Look for: the HTML table structure, column headers (Name, Booking Date, Charges, Bond), and any form/iframe wrapping. Note the exact tag names and class names you'll need to match.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_new_county_adapters.py`:

```python
from jail_booking_ingest import (
    _parse_cascade_roster,
    fetch_cascade_bookings,
    fetch_gallatin_bookings,
    JailBookingRecord,
)

# Update the import at top to include _parse_cascade_roster, fetch_cascade_bookings


CASCADE_SAMPLE_HTML = """
<html><body>
<table>
<thead><tr><th>Name</th><th>Age</th><th>Booking Date</th><th>Charges</th><th>Bond</th></tr></thead>
<tbody>
<tr><td>DOE, JOHN A</td><td>35</td><td>04/15/2026 08:30 AM</td><td>Assault; DUI</td><td>$5,000</td></tr>
<tr><td>SMITH, JANE</td><td>28</td><td>04/14/2026 22:15 PM</td><td>Theft</td><td>$1,500</td></tr>
</tbody>
</table>
</body></html>
"""


class CascadeAdapterTests(unittest.TestCase):
    def test_parse_cascade_returns_records(self):
        records = _parse_cascade_roster(CASCADE_SAMPLE_HTML, "https://www.cascadecountymt.gov/314/Inmate-Roster")
        assert len(records) == 2
        assert records[0].person_name == "Doe, John A"
        assert records[0].age == 35
        assert "Assault" in records[0].charges_summary

    def test_parse_cascade_normalizes_name(self):
        records = _parse_cascade_roster(CASCADE_SAMPLE_HTML, "https://www.cascadecountymt.gov/314/Inmate-Roster")
        assert records[1].person_name == "Smith, Jane"

    def test_parse_cascade_empty_table(self):
        html = "<html><body><table><thead><tr><th>Name</th></tr></thead><tbody></tbody></table></body></html>"
        records = _parse_cascade_roster(html, "https://www.cascadecountymt.gov/314/Inmate-Roster")
        assert records == []

    @patch("jail_booking_ingest.requests.get")
    def test_fetch_cascade_calls_correct_url(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = CASCADE_SAMPLE_HTML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        records = fetch_cascade_bookings("https://www.cascadecountymt.gov/314/Inmate-Roster")

        mock_get.assert_called_once()
        assert "cascadecountymt.gov" in mock_get.call_args[0][0]
        assert len(records) == 2
```

**Note:** The sample HTML above uses a generic table structure. After inspecting the live page in Step 1, update `CASCADE_SAMPLE_HTML` to match the actual HTML structure you found. The test logic stays the same.

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m pytest tests/test_new_county_adapters.py::CascadeAdapterTests -v
```

Expected: `ImportError: cannot import name '_parse_cascade_roster'`

- [ ] **Step 4: Add Cascade to TRACKED_SOURCES**

Cascade is already in `TRACKED_SOURCES` (lines 79-86). No change needed there.

Add `"cascade"` to `SUPPORTED_ADAPTERS`:

```python
SUPPORTED_ADAPTERS = {"broadwater", "cascade", "flathead", "gallatin", "jefferson", "missoula", "sanders", "yellowstone"}
```

- [ ] **Step 5: Implement `_parse_cascade_roster()` and `fetch_cascade_bookings()`**

Add after `fetch_gallatin_bookings()`. Adjust column indices based on what you found in Step 1:

```python
def _parse_cascade_roster(page_html: str, source_url: str) -> list[JailBookingRecord]:
    records: list[JailBookingRecord] = []
    table_match = re.search(
        r"<table[^>]*>(.*?)</table>",
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        logger.warning("Cascade roster: no table found")
        return records

    rows = re.findall(
        r"<tr[^>]*>(.*?)</tr>",
        table_match.group(1),
        re.IGNORECASE | re.DOTALL,
    )
    for row_html in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.IGNORECASE | re.DOTALL)
        if len(cells) < 4:
            continue
        raw_name = _text_from_html(cells[0]).strip()
        if not raw_name or not re.search(r"[A-Z]", raw_name):
            continue
        person_name = raw_name.title()
        age_text = _text_from_html(cells[1]).strip() if len(cells) > 1 else ""
        age = int(age_text) if age_text.isdigit() else None
        booking_date_text = _text_from_html(cells[2]).strip() if len(cells) > 2 else ""
        booking_at = _normalize_datetime(booking_date_text)
        charges_text = _text_from_html(cells[3]).strip() if len(cells) > 3 else ""
        bond_text = _text_from_html(cells[4]).strip() if len(cells) > 4 else ""
        charges_summary = charges_text or "Charge details available on the official Cascade County inmate page."
        if bond_text:
            charges_summary = f"{charges_summary}; Bond {bond_text}"
        source_record_id = f"cascade:{person_name.lower().replace(' ', '-')}:{booking_date_text}"
        records.append(
            JailBookingRecord(
                source_record_id=source_record_id,
                person_name=person_name,
                age=age,
                booking_number="",
                booking_at=booking_at,
                charges_summary=charges_summary,
                source_url=source_url,
            )
        )
    return records


def fetch_cascade_bookings(source_url: str) -> list[JailBookingRecord]:
    page_html = _fetch_html(source_url)
    return _parse_cascade_roster(page_html, source_url)
```

**Adjustment note:** After inspecting the live page in Step 1, you may need to adjust:
- The table selector regex (add class name if the page has multiple tables)
- Cell indices (0=Name, 1=Age, 2=Booking Date, 3=Charges, 4=Bond — confirm against actual columns)
- The `source_record_id` formula if a stable booking number is available in the HTML

- [ ] **Step 6: Add `elif` branch in `_run_source()`**

```python
    elif county_slug == "cascade":
        records = fetch_cascade_bookings(roster_url)
```

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/test_new_county_adapters.py::CascadeAdapterTests -v
```

Expected: All 4 tests PASS

- [ ] **Step 8: Commit**

```bash
git add jail_booking_ingest.py tests/test_new_county_adapters.py
git commit -m "feat: add Cascade County jail roster adapter (HTML scraper)"
```

---

## Task 3: PDF adapter — Lewis & Clark + Silver Bow

Both counties publish standard MT jail roster PDFs. Lewis & Clark's PDF URL changes with each update (scrape the detention page to find the current link). Silver Bow's URL is stable-ish (link from detention center page). Shared `_parse_mt_jail_roster_pdf()` handles both.

**Files:**
- Modify: `jail_booking_ingest.py`
- Modify: `tests/test_new_county_adapters.py`

- [ ] **Step 1: Inspect live pages to confirm PDF structure**

```bash
# Lewis & Clark — find current PDF link
curl -s "https://www.lccountymt.gov/Sheriff/Detention-Center" | grep -i "jail-roster\|pinmates\|\.pdf" | head -10

# Download and inspect a Lewis & Clark PDF (replace URL with current one from above)
curl -sL "https://www.lccountymt.gov/files/assets/county/v/1856/sheriff/documents/jail-roster.pdf" -o /tmp/lc_roster.pdf
python3 -c "
import pdfplumber
with pdfplumber.open('/tmp/lc_roster.pdf') as pdf:
    for page in pdf.pages[:2]:
        print(page.extract_text()[:800])
"

# Silver Bow — find current PDF link
curl -s "https://co.silverbow.mt.us/3274/Detention-Center" | grep -i "jail.roster\|\.pdf" | head -10
```

Note the column layout. Lewis & Clark PDFs typically look like:
```
Jail Roster Printed on April 14, 2026 Name Age Sex ...
LASTNAME, FIRSTNAME  42  M  ...booking date...  charges...
```

- [ ] **Step 2: Add Lewis & Clark and Silver Bow to TRACKED_SOURCES**

In `jail_booking_ingest.py`, add after the `"cascade"` entry in `TRACKED_SOURCES`:

```python
    "lewisclark": {
        "county_name": "Lewis & Clark",
        "facility_name": "Lewis & Clark County Detention Center",
        "roster_url": "https://www.lccountymt.gov/Sheriff/Detention-Center",
        "phone": "406-447-8235",
        "coverage_tier": "major",
        "is_featured": 1,
    },
    "silverbow": {
        "county_name": "Silver Bow",
        "facility_name": "Butte-Silver Bow Detention Center",
        "roster_url": "https://co.silverbow.mt.us/3274/Detention-Center",
        "phone": "406-497-1048",
        "coverage_tier": "standard",
        "is_featured": 0,
    },
```

Add to `SUPPORTED_ADAPTERS`:

```python
SUPPORTED_ADAPTERS = {"broadwater", "cascade", "flathead", "gallatin", "jefferson", "lewisclark", "missoula", "sanders", "silverbow", "yellowstone"}
```

- [ ] **Step 3: Write the failing tests**

Add to `tests/test_new_county_adapters.py`:

```python
from jail_booking_ingest import (
    _parse_cascade_roster,
    _parse_mt_jail_roster_pdf,
    _discover_lewisclark_pdf_url,
    fetch_cascade_bookings,
    fetch_gallatin_bookings,
    fetch_lewisclark_bookings,
    fetch_silverbow_bookings,
    JailBookingRecord,
)


LC_DETENTION_PAGE_HTML = """
<html><body>
<a href="/files/assets/county/v/1856/sheriff/documents/jail-roster.pdf">Current Jail Roster</a>
<a href="/files/assets/county/v/100/sheriff/documents/old-roster.pdf">Old Roster</a>
</body></html>
"""

SAMPLE_PDF_TEXT = """Jail Roster Printed on April 14, 2026 Name Age Sex Race
DOE, JOHN 42 M W Booking: 04/12/2026 Charges: Assault
SMITH, JANE 29 F W Booking: 04/13/2026 Charges: DUI; Reckless Driving
"""


class LewisClarkAdapterTests(unittest.TestCase):
    def test_discover_lewisclark_pdf_url_finds_latest(self):
        url = _discover_lewisclark_pdf_url(LC_DETENTION_PAGE_HTML, "https://www.lccountymt.gov")
        assert url == "https://www.lccountymt.gov/files/assets/county/v/1856/sheriff/documents/jail-roster.pdf"

    def test_discover_lewisclark_pdf_url_returns_none_when_not_found(self):
        url = _discover_lewisclark_pdf_url("<html><body>no links</body></html>", "https://www.lccountymt.gov")
        assert url is None

    def test_parse_mt_jail_roster_pdf_returns_records(self):
        # Test the text-line parser with representative text from a real roster
        records = _parse_mt_jail_roster_pdf(SAMPLE_PDF_TEXT, "https://example.com/roster.pdf")
        assert len(records) >= 1
        names = [r.person_name for r in records]
        assert any("Doe" in n or "John" in n for n in names)


class SilverBowAdapterTests(unittest.TestCase):
    @patch("jail_booking_ingest.requests.get")
    def test_fetch_silverbow_discovers_and_downloads_pdf(self, mock_get):
        detention_page = MagicMock()
        detention_page.text = """<html><body>
        <a href="/DocumentCenter/View/28943/Jail-Roster">Current Jail Roster</a>
        </body></html>"""
        detention_page.raise_for_status = MagicMock()

        pdf_response = MagicMock()
        pdf_response.content = b"%PDF fake content"
        pdf_response.raise_for_status = MagicMock()

        mock_get.side_effect = [detention_page, pdf_response]

        # Should not raise even with fake PDF content (pdfplumber will fail gracefully)
        try:
            fetch_silverbow_bookings("https://co.silverbow.mt.us/3274/Detention-Center")
        except Exception:
            pass  # graceful failure on fake PDF bytes is acceptable

        assert mock_get.call_count >= 1
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
python -m pytest tests/test_new_county_adapters.py::LewisClarkAdapterTests tests/test_new_county_adapters.py::SilverBowAdapterTests -v
```

Expected: ImportError for `_parse_mt_jail_roster_pdf`, `_discover_lewisclark_pdf_url`, etc.

- [ ] **Step 5: Implement the shared PDF parser and county fetch functions**

Add after `fetch_cascade_bookings()`:

```python
def _discover_lewisclark_pdf_url(page_html: str, base_url: str) -> str | None:
    matches = re.findall(
        r'href="(/files/assets/county/v/\d+/sheriff/documents/[^"]*(?:jail-roster|pinmates)[^"]*\.pdf)"',
        page_html,
        re.IGNORECASE,
    )
    if not matches:
        return None
    # Pick the one with the highest version number (largest v/ integer)
    def _version(href: str) -> int:
        m = re.search(r"/v/(\d+)/", href)
        return int(m.group(1)) if m else 0
    latest = max(matches, key=_version)
    return urljoin(base_url, latest)


def _parse_mt_jail_roster_pdf(pdf_text: str, source_url: str) -> list[JailBookingRecord]:
    """Parse plain text extracted from a standard Montana jail roster PDF.

    Montana counties (Lewis & Clark, Silver Bow, Lake) use a consistent columnar
    format: LASTNAME, FIRSTNAME  Age  Sex  Race  [Booking info]  Charges
    """
    records: list[JailBookingRecord] = []
    lines = [line.strip() for line in pdf_text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        # Skip header lines
        if re.match(r"^(Jail Roster|Name|Age|Sex|Race|Printed|Page \d)", line, re.IGNORECASE):
            continue
        # Name lines: LASTNAME, FIRSTNAME or LASTNAME, FIRSTNAME MIDDLE
        name_match = re.match(r"^([A-Z][A-Z' -]+),\s+([A-Z][A-Z' -]+(?:\s+[A-Z][A-Z' -]+)*)\s*(\d{2,3})?\s*", line)
        if not name_match:
            continue
        last = name_match.group(1).strip()
        first = name_match.group(2).strip()
        age_str = name_match.group(3)
        person_name = f"{last.title()}, {first.title()}"
        age = int(age_str) if age_str and age_str.isdigit() else None

        # Look for booking date on same line or next 2 lines
        booking_at = None
        charges_parts: list[str] = []
        context = line + " " + " ".join(lines[idx + 1 : idx + 3])
        date_match = re.search(
            r"Booking:?\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})",
            context,
            re.IGNORECASE,
        )
        if date_match:
            booking_at = _normalize_datetime(date_match.group(1))

        charges_match = re.search(r"Charges?:?\s*(.+?)(?:Bond|$)", context, re.IGNORECASE | re.DOTALL)
        if charges_match:
            raw_charges = charges_match.group(1).strip().rstrip(";, ")
            if raw_charges:
                charges_parts.append(raw_charges[:300])

        charges_summary = (
            "; ".join(charges_parts)
            if charges_parts
            else f"Charge details available on the official inmate roster."
        )
        source_record_id = f"{source_url.split('/')[-2]}:{person_name.lower().replace(' ', '-')}:{booking_at or idx}"
        records.append(
            JailBookingRecord(
                source_record_id=source_record_id,
                person_name=person_name,
                age=age,
                booking_number="",
                booking_at=booking_at,
                charges_summary=charges_summary,
                source_url=source_url,
            )
        )
    return records


def _fetch_pdf_text(pdf_url: str) -> str:
    import pdfplumber
    import io
    response = requests.get(
        pdf_url,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)"},
    )
    response.raise_for_status()
    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages)


def fetch_lewisclark_bookings(source_url: str) -> list[JailBookingRecord]:
    page_html = _fetch_html(source_url)
    pdf_url = _discover_lewisclark_pdf_url(page_html, "https://www.lccountymt.gov")
    if not pdf_url:
        logger.warning("Lewis & Clark: could not discover PDF URL from %s", source_url)
        return []
    pdf_text = _fetch_pdf_text(pdf_url)
    return _parse_mt_jail_roster_pdf(pdf_text, pdf_url)


def fetch_silverbow_bookings(source_url: str) -> list[JailBookingRecord]:
    page_html = _fetch_html(source_url)
    # Silver Bow links PDF from their DocumentCenter
    pdf_match = re.search(
        r'href="(/DocumentCenter/View/\d+/[^"]*)"',
        page_html,
        re.IGNORECASE,
    )
    if not pdf_match:
        logger.warning("Silver Bow: could not find PDF link on %s", source_url)
        return []
    pdf_url = urljoin("https://co.silverbow.mt.us", pdf_match.group(1))
    pdf_text = _fetch_pdf_text(pdf_url)
    return _parse_mt_jail_roster_pdf(pdf_text, pdf_url)
```

Also add `from urllib.parse import urljoin` to imports if not already imported (check line ~29).

- [ ] **Step 6: Add `elif` branches in `_run_source()`**

```python
    elif county_slug == "lewisclark":
        records = fetch_lewisclark_bookings(roster_url)
    elif county_slug == "silverbow":
        records = fetch_silverbow_bookings(roster_url)
```

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/test_new_county_adapters.py::LewisClarkAdapterTests tests/test_new_county_adapters.py::SilverBowAdapterTests -v
```

Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add jail_booking_ingest.py tests/test_new_county_adapters.py
git commit -m "feat: add Lewis & Clark and Silver Bow jail adapters (PDF parser)"
```

---

## Task 4: Ravalli County jail adapter

Ravalli County's detention center at `ravallicounty.gov/239/Adult-Detention-Center` has an online inmate lookup. Inspect the live page first to determine the mechanism.

**Files:**
- Modify: `jail_booking_ingest.py`
- Modify: `tests/test_new_county_adapters.py`

- [ ] **Step 1: Inspect the live page**

```bash
curl -s "https://ravallicounty.gov/239/Adult-Detention-Center" | grep -i "inmate\|roster\|pdf\|iframe\|form\|action" | head -20
```

Three likely outcomes:
- **HTML table / list** → write a parser similar to Cascade
- **PDF link** → use `_fetch_pdf_text` + `_parse_mt_jail_roster_pdf` (same as Lewis & Clark)
- **Iframe or JS redirect** → log warning and return empty list with a note in SKIPPED_SOURCES

If outcome is PDF, follow the same pattern as `fetch_silverbow_bookings`. If outcome is HTML, follow Cascade pattern.

- [ ] **Step 2: Add Ravalli to TRACKED_SOURCES**

In `TRACKED_SOURCES`, add:

```python
    "ravalli": {
        "county_name": "Ravalli",
        "facility_name": "Ravalli County Detention Center",
        "roster_url": "https://ravallicounty.gov/239/Adult-Detention-Center",
        "phone": "406-375-4080",
        "coverage_tier": "standard",
        "is_featured": 0,
    },
```

Add `"ravalli"` to `SUPPORTED_ADAPTERS`.

- [ ] **Step 3: Write the failing test**

Add to `tests/test_new_county_adapters.py`:

```python
from jail_booking_ingest import (
    _parse_cascade_roster,
    _parse_mt_jail_roster_pdf,
    _discover_lewisclark_pdf_url,
    fetch_cascade_bookings,
    fetch_gallatin_bookings,
    fetch_lewisclark_bookings,
    fetch_ravalli_bookings,
    fetch_silverbow_bookings,
    JailBookingRecord,
)


class RavalliAdapterTests(unittest.TestCase):
    @patch("jail_booking_ingest.requests.get")
    def test_fetch_ravalli_returns_list(self, mock_get):
        """fetch_ravalli_bookings returns a list (possibly empty) without raising."""
        response = MagicMock()
        response.text = "<html><body><p>No current inmates.</p></body></html>"
        response.raise_for_status = MagicMock()
        mock_get.return_value = response

        result = fetch_ravalli_bookings("https://ravallicounty.gov/239/Adult-Detention-Center")

        assert isinstance(result, list)
```

- [ ] **Step 4: Run test to verify it fails**

```bash
python -m pytest tests/test_new_county_adapters.py::RavalliAdapterTests -v
```

Expected: ImportError for `fetch_ravalli_bookings`

- [ ] **Step 5: Implement `fetch_ravalli_bookings()` based on Step 1 findings**

**If the page links a PDF:**

```python
def fetch_ravalli_bookings(source_url: str) -> list[JailBookingRecord]:
    page_html = _fetch_html(source_url)
    pdf_match = re.search(r'href="([^"]+\.pdf)"', page_html, re.IGNORECASE)
    if not pdf_match:
        logger.warning("Ravalli: could not find PDF link on %s", source_url)
        return []
    pdf_url = urljoin("https://ravallicounty.gov", pdf_match.group(1))
    pdf_text = _fetch_pdf_text(pdf_url)
    return _parse_mt_jail_roster_pdf(pdf_text, pdf_url)
```

**If the page has an HTML table (adjust selectors based on Step 1 inspection):**

```python
def fetch_ravalli_bookings(source_url: str) -> list[JailBookingRecord]:
    page_html = _fetch_html(source_url)
    return _parse_cascade_roster(page_html, source_url)  # reuse if same table structure
```

**If the page requires JS or is an iframe and cannot be scraped:**

```python
def fetch_ravalli_bookings(source_url: str) -> list[JailBookingRecord]:
    logger.info("Ravalli: live roster requires JavaScript; returning empty list")
    return []
```

And add to `SKIPPED_SOURCES`:
```python
SKIPPED_SOURCES = {
    "broadwater": "Official roster host is timing out from the ingest machine.",
    "ravalli": "Roster portal requires JavaScript rendering; not yet supported.",
}
```

- [ ] **Step 6: Add `elif` branch in `_run_source()`**

```python
    elif county_slug == "ravalli":
        records = fetch_ravalli_bookings(roster_url)
```

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/test_new_county_adapters.py::RavalliAdapterTests -v
```

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add jail_booking_ingest.py tests/test_new_county_adapters.py
git commit -m "feat: add Ravalli County jail roster adapter"
```

---

## Task 5: Helena PD police incident fetcher

New file `helena_police_fetcher.py` modeled after `bozeman_police_fetcher.py`. Writes to `records` table with `source = "helena_pd"`.

**Files:**
- Create: `helena_police_fetcher.py`
- Create: `tests/test_helena_police_fetcher.py`

- [ ] **Step 1: Inspect the Helena PD records page**

```bash
curl -s "https://www.helenamt.gov/Departments/Police-Department/Support-Services-Records" | grep -i "call\|incident\|csv\|json\|download\|arcgis\|esri\|dataset" | head -20
```

Three likely outcomes:
- **ArcGIS/Socrata dataset** (like Bozeman) → use REST API, model closely after `bozeman_police_fetcher.py`
- **Downloadable CSV/file** → download, parse rows into `records`
- **No machine-readable feed** → return empty list, log warning; add `helena_pd` as a stub for future

- [ ] **Step 2: Write the failing test**

Create `tests/test_helena_police_fetcher.py`:

```python
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import helena_police_fetcher


class HelenaFetcherTests(unittest.TestCase):
    def _make_db(self) -> tuple[str, sqlite3.Connection]:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                source_type TEXT,
                county TEXT,
                city TEXT,
                agency_name TEXT,
                incident_type TEXT,
                incident TEXT,
                location TEXT,
                date TEXT,
                time TEXT,
                cfs_number TEXT,
                details TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        return path, conn

    def test_module_has_main_function(self):
        assert hasattr(helena_police_fetcher, "fetch_helena_incidents")

    def test_fetch_returns_list(self):
        """fetch_helena_incidents returns a list without raising."""
        with patch.object(helena_police_fetcher, "_get_incidents", return_value=[]):
            result = helena_police_fetcher.fetch_helena_incidents()
        assert isinstance(result, list)

    def test_write_incidents_deduplicates(self):
        path, conn = self._make_db()
        try:
            incident = {
                "cfs_number": "HP-2026-001",
                "date": "2026-04-15",
                "time": "08:30",
                "incident_type": "Assault",
                "location": "123 Main St",
                "details": "",
            }
            helena_police_fetcher._write_incidents(conn, [incident])
            helena_police_fetcher._write_incidents(conn, [incident])  # second write = no duplicate
            count = conn.execute("SELECT COUNT(*) FROM records WHERE source = 'helena_pd'").fetchone()[0]
            assert count == 1
        finally:
            conn.close()
            os.unlink(path)
```

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m pytest tests/test_helena_police_fetcher.py -v
```

Expected: `ModuleNotFoundError: No module named 'helena_police_fetcher'`

- [ ] **Step 4: Create `helena_police_fetcher.py`**

Create `/root/montanablotter/helena_police_fetcher.py`:

```python
"""
helena_police_fetcher.py
========================
Pull Helena Police Department calls-for-service into Montana Blotter.

Writes to the records table with source='helena_pd'. Does not go through
the blotter summarizer pipeline.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import requests

import config

logger = logging.getLogger(__name__)

SOURCE = "helena_pd"
COUNTY = "Lewis & Clark"
CITY = "Helena"
AGENCY_NAME = "Helena Police Department"

DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))

# Update this URL after inspecting the live page in Step 1.
# If Helena PD publishes an ArcGIS feed, it will look like:
#   "https://services.arcgis.com/.../FeatureServer/0"
# If no machine-readable feed exists, set to None.
CFS_SERVICE_URL: str | None = None  # Set after Step 1 inspection


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn


def _get_incidents() -> list[dict[str, Any]]:
    """Fetch incident rows from Helena PD's data source.

    Replace the body of this function based on what you found in Step 1:

    ArcGIS example:
        response = requests.get(
            f"{CFS_SERVICE_URL}/query",
            params={"where": "1=1", "outFields": "*", "f": "json", "resultRecordCount": 1000},
            timeout=45,
        )
        response.raise_for_status()
        features = response.json().get("features") or []
        return [f["attributes"] for f in features]

    CSV example:
        response = requests.get(CSV_URL, timeout=60)
        response.raise_for_status()
        import csv, io
        reader = csv.DictReader(io.StringIO(response.text))
        return list(reader)
    """
    if CFS_SERVICE_URL is None:
        logger.info("Helena PD: no CFS_SERVICE_URL configured yet, returning empty list")
        return []
    # Placeholder — replace with actual fetch once CFS_SERVICE_URL is known
    response = requests.get(
        f"{CFS_SERVICE_URL}/query",
        params={
            "where": "1=1",
            "outFields": "*",
            "orderByFields": "DATE DESC",
            "resultRecordCount": 1000,
            "f": "json",
        },
        timeout=45,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)"},
    )
    response.raise_for_status()
    features = response.json().get("features") or []
    return [f["attributes"] for f in features]


def _normalize_incident(raw: dict[str, Any]) -> dict[str, Any]:
    """Map raw API/CSV fields to records table columns.

    Update the field names (DATE, TIME, INCIDENT_TYPE, LOCATION, CFS_NUMBER)
    to match the actual column names from the Helena PD data source.
    """
    date_raw = str(raw.get("DATE") or raw.get("date") or "")
    time_raw = str(raw.get("TIME") or raw.get("time") or "")
    incident_type = str(raw.get("INCIDENT_TYPE") or raw.get("incident_type") or raw.get("CALL_TYPE") or "")
    location = str(raw.get("LOCATION") or raw.get("location") or raw.get("ADDRESS") or "")
    cfs_number = str(raw.get("CFS_NUMBER") or raw.get("cfs_number") or raw.get("INCIDENT_NUMBER") or "")
    details = str(raw.get("DETAILS") or raw.get("NARRATIVE") or "")
    return {
        "cfs_number": cfs_number,
        "date": date_raw[:10],
        "time": time_raw[:8],
        "incident_type": incident_type[:200],
        "location": location[:300],
        "details": details[:500],
    }


def _incident_hash(incident: dict[str, Any]) -> str:
    key = "|".join([
        incident.get("cfs_number") or "",
        incident.get("date") or "",
        incident.get("incident_type") or "",
        incident.get("location") or "",
    ])
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def _write_incidents(conn: sqlite3.Connection, incidents: list[dict[str, Any]]) -> int:
    written = 0
    for raw in incidents:
        inc = _normalize_incident(raw) if any(k.isupper() for k in raw) else raw
        key_hash = _incident_hash(inc)
        cfs = inc.get("cfs_number") or ""
        date = inc.get("date") or ""
        # Dedup: skip if already exists by cfs_number or (date + incident_type + location hash)
        exists = conn.execute(
            """
            SELECT 1 FROM records
            WHERE source = ?
              AND (
                (cfs_number != '' AND cfs_number = ?)
                OR (cfs_number = '' AND date = ? AND incident_type = ? AND location = ?)
              )
            LIMIT 1
            """,
            (SOURCE, cfs, date, inc.get("incident_type", ""), inc.get("location", "")),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO records (
                source, source_type, county, city, agency_name,
                incident_type, incident, location, date, time, cfs_number, details,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                SOURCE,
                "helena_cfs",
                COUNTY,
                CITY,
                AGENCY_NAME,
                inc.get("incident_type", ""),
                inc.get("incident_type", ""),
                inc.get("location", ""),
                inc.get("date", ""),
                inc.get("time", ""),
                cfs,
                inc.get("details", ""),
            ),
        )
        written += 1
    conn.commit()
    return written


def fetch_helena_incidents() -> list[dict[str, Any]]:
    return _get_incidents()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Helena PD calls-for-service")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    incidents = _get_incidents()
    logger.info("Helena PD: fetched %d incidents", len(incidents))

    if args.dry_run:
        for inc in incidents[:5]:
            logger.info("DRY RUN: %s", inc)
        return

    conn = _connect_db()
    try:
        written = _write_incidents(conn, incidents)
        logger.info("Helena PD: wrote %d new records", written)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_helena_police_fetcher.py -v
```

Expected: All 3 tests PASS

- [ ] **Step 6: Update `CFS_SERVICE_URL` based on Step 1 findings**

If Helena PD has an ArcGIS endpoint, set it:

```python
CFS_SERVICE_URL = "https://services.arcgis.com/<hash>/arcgis/rest/services/Helena_PD_CFS/FeatureServer/0"
```

Update `_normalize_incident()` field names to match the actual API columns. Then run a dry-run to confirm:

```bash
source venv/bin/activate && python helena_police_fetcher.py --dry-run
```

- [ ] **Step 7: Commit**

```bash
git add helena_police_fetcher.py tests/test_helena_police_fetcher.py
git commit -m "feat: add Helena PD calls-for-service fetcher"
```

---

## Task 6: Cron entries

Add 5 new cron entries to `crontab.txt`. Gallatin's entry already exists at `:40 */2` — do not add another.

**Files:**
- Modify: `crontab.txt`

- [ ] **Step 1: Verify Gallatin cron entry exists**

```bash
grep "gallatin" /root/montanablotter/crontab.txt
```

Expected: one line containing `jail_booking_ingest.py --county gallatin`

- [ ] **Step 2: Add 5 new entries to crontab.txt**

Append after the existing Gallatin entry:

```cron
# Cascade County jail roster — every 2 hours
25 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_cascade --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county cascade

# Lewis & Clark County jail roster — every 2 hours
30 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_lewisclark --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county lewisclark

# Silver Bow County jail roster — every 2 hours
45 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_silverbow --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county silverbow

# Ravalli County jail roster — every 2 hours
55 */2 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name jail_booking_ingest_ravalli --log /root/montanablotter/jail_booking_ingest.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/jail_booking_ingest.py --county ravalli

# Helena PD calls for service — hourly
15 * * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name helena_police_fetcher --log /root/montanablotter/worker.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/helena_police_fetcher.py
```

- [ ] **Step 3: Install updated crontab**

```bash
crontab /root/montanablotter/crontab.txt
crontab -l | grep -E "cascade|lewisclark|silverbow|ravalli|helena_police"
```

Expected: 5 matching lines

- [ ] **Step 4: Commit**

```bash
git add crontab.txt
git commit -m "chore: add cron entries for new county jail rosters and Helena PD"
```

---

## Post-Implementation Smoke Test

After all tasks are committed, run a dry-run for each new county to verify adapters can fetch without errors:

```bash
cd /root/montanablotter && source venv/bin/activate

python jail_booking_ingest.py --county gallatin --dry-run
python jail_booking_ingest.py --county cascade --dry-run
python jail_booking_ingest.py --county lewisclark --dry-run
python jail_booking_ingest.py --county silverbow --dry-run
python jail_booking_ingest.py --county ravalli --dry-run
python helena_police_fetcher.py --dry-run
```

Expected for each: no Python exception, fetched count logged (0 is acceptable if live site is temporarily unavailable).

Run the full test suite to check for regressions:

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
