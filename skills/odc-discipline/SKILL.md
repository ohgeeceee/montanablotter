---
name: odc-discipline
description: "Reusable pattern for ingesting monthly/periodic government discipline/public-list PDFs or HTML pages (ODC attorney discipline as the canonical example). Use when building a new monthly-drop ingest for a government list — PDF version-stamped file, HTML page with a download link, or a scrapeable table — that needs dedup by version + fingerprint, upsert into a dedicated table, and a public search/filter page."
metadata: {"openclaw": {"skillKey": "odc-discipline"}}
---

# ODC Discipline / Government List Ingestion — Reusable Pattern

## Trigger

```json
{
  "activation": {
    "anyPhrases": [
      "ODC discipline",
      "government list ingest",
      "monthly discipline PDF",
      "attorney discipline list",
      "board disciplinary actions",
      "new government list ingestion",
      "rewrite the ODC fetcher for X"
    ]
  },
  "movement": {
    "target": "desk",
    "skipIfAlreadyThere": true
  }
}
```

Use this skill whenever the task is: *"ingest a periodic government list (PDF or HTML) into a Montana Blotter table, with versioned dedup and a public-facing page."* The ODC attorney discipline list (`services/ingestion/odc_discipline.py`) is the canonical worked example, but the pattern generalizes to any board/agency list that drops on a schedule.

## What's already in the repo

| Artifact | Where | Notes |
|----------|-------|-------|
| Ingest script (canonical) | `services/ingestion/odc_discipline.py` | Fetch ODC page → extract PDF URL → version from filename → download → pdfplumber column parse → upsert. Monthly cron. |
| Public page template | `templates/odc_discipline_index.html` | Search-by-name, filter-by-discipline, pagination, sidebar discipline filter. **Not yet wired to a Flask route in `app.py`.** |
| DB table (created) | `odc_discipline` in `blotter.db` | `id, attorney_name, cause_no, discipline, date_ordered, pdf_version, source_url, fetched_at`. Unique index on `(pdf_version, attorney_name, cause_no)`. |
| Verification script | `scripts/ops/verify_odc_discipline.py` | Seeds a fake older version to confirm skip + dedup paths. |
| Cron entry | `crontab.txt` line ~111 | `0 4 1 * *` — 1st of month at 04:00, after 03:00 DB backup. Runs via `job_runner.py --name odc_discipline_monthly`. |
| Current data | `odc_discipline` has 431 rows, 1 version (`2026.07.24`). Latest ordered date 2026-07-21. | |

Related pattern that's *not* the same but is adjacent: `license_sanction_ingest.py` — weekly HTML-board scrape using `professional_boards.py` + Kimi LLM extraction (JSON-out-of-HTML). Different parse strategy (LLM vs. column positions), same upsert/dedup shape.

## Skeleton for a new government-list ingestion

### 1. Decide the source type

Two common shapes — pick the parser strategy that fits:

- **Version-stamped PDF drop** (ODC model): a PDF with a date in the filename, downloaded from a URL found on an HTML landing page. Use pdfplumber column parsing or pdfplumber + LLM as a fallback. *This is the ODC pattern.*
- **Scrapeable HTML table/list** (license sanctions model): a board page with rows or list items. Use BeautifulSoup + `professional_boards.py` helpers, optionally with LLM extraction for messy pages.

### 2. Table design

Create a dedicated table per list. Minimal safe shape:

```sql
CREATE TABLE new_list (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,   -- the entity being disciplined/tracked
    reference_no    TEXT,               -- cause no., license no., case no., etc.
    action          TEXT,               -- discipline / action text, possibly long
    date_effective  TEXT,               -- ISO YYYY-MM-DD or raw if unparseable
    list_version    TEXT    NOT NULL,   -- version from filename/URL/page (e.g. 2026.07.24)
    source_url      TEXT    NOT NULL,
    fetched_at      TEXT    NOT NULL    -- ISO timestamp of when this run pulled the source
);
CREATE INDEX idx_<name>_name         ON new_list(name);
CREATE INDEX idx_<name>_date        ON new_list(date_effective);
CREATE INDEX idx_<name>_version     ON new_list(list_version);
CREATE UNIQUE INDEX uidx_<name>_v_n_r
    ON new_list(list_version, name, reference_no);
```

**Dedup key rule:** unique index on `(list_version, name, reference_no)` so re-runs of the same version are safe and a new version inserts fresh rows.

### 3. Version detection

Pick one:

- **From filename**: regex the date stamp out of the PDF filename (ODC: `20\d{2}\.\d{2}\.\d{2}`).
- **From HTML page**: if the page itself shows a "as of" date, extract and use it.
- **From content**: as a last resort, hash a slice of the source and treat a changed hash as a new version — but version-from-filename/URL is preferred because it's stable and readable.

Skip the whole run if `SELECT COUNT(*) FROM new_list WHERE list_version = ?` is already > 0.

### 4. Fetch + parse + upsert (the ODC walkthrough)

The ODC script does this:

1. **Discover the download URL** — GET the landing page, regex the `img1.wsimg.com` link containing the date-stamped PDF filename. Fallback to protocol-relative URL.
2. **Extract version** — regex the date stamp from the URL. This is the `list_version`.
3. **Skip-if-present** — check `odc_discipline` for that version. If found, log and return early.
4. **Download** — stream to `/tmp/odc_discipline_<version>.pdf`, clean up in a `finally`.
5. **Parse** — pdfplumber `extract_words()`, group by top position into visual lines, split each line into 4 columns by x0 thresholds (`_NAME_MAX_X0=180`, `_CAUSE_MAX_X0=275`, `_DISC_MAX_X0=490`). Continuation lines (no name, no cause) append to the previous row's discipline text and may update date_ordered.
6. **Skip boilerplate** — header lines and fragments like "The following", "Although", "Please note", "State Bar of", "P.O. Box", "Helena, MT", "Copyright", "Powered by", "Home", "Filing a", "Resources", "Annual Reports", "FAQ".
7. **Upsert** — INSERT per row; IntegrityError on the unique index = duplicate → skip. Count inserted vs skipped.
8. **Return a summary** — `{version, total_rows, inserted, skipped, source_url, fetched_at}` suitable for logging and cron output.

### 5. Column boundaries are PDF-specific — re-derive when layout changes

The ODC column boundaries (`_NAME_MAX_X0`, etc.) were derived from the July 2026 PDF. If parsing starts dropping rows, re-derive by inspecting `extract_words()` on the new PDF — don't guess. Add a note in the script docstring with the new values and the PDF version they were derived from.

### 6. LLM fallback for non-PDF or messy sources

For HTML boards or PDFs that don't parse cleanly with column positions, the `license_sanction_ingest.py` pattern works:

- Fetch the page.
- Pass HTML/text to an LLM with a structured extraction prompt that returns a JSON array of records with the fields you need.
- Dedup/fingerprint by a hash of `(name, reference, board, date, action)`.
- Upsert by fingerprint into the table.

The ODC script does *not* use an LLM — it uses deterministic column positions, which is faster and cheaper for a clean table layout. Use the LLM path when the layout is irregular or when the source is HTML.

### 7. Cron scheduling

Add to `crontab.txt` with a comment block explaining the source, the schedule rationale, and the skip/dedup behavior. Run *after* the 03:00 DB backup so the new version lands in the snapshot. Prefer `job_runner.py --name <name>` so the run is logged under a named job.

### 8. Public page (if the list is public-facing)

The `odc_discipline_index.html` template is a reusable base for any discipline/public-list page:

- Search by name or reference number.
- Filter by action/discipline type (with a sidebar list of types).
- Pagination.
- "Latest version" header so readers know how fresh the data is.
- Source link back to the agency.

**Note:** as of this writing the template exists but is not wired to a route in `app.py`. Wiring it is a separate step — render with `rows`, `total`, `latest_version`, `q`, `discipline_filter`, `discipline_types`, `page`, `total_pages`.

### 9. Verification

Seed a fake older version (as `verify_odc_discipline.py` does) and run the ingest to confirm:
- skip path fires when the version is already present,
- dedup fires on re-insert of the same `(version, name, reference_no)`,
- dry-run path works,
- fresh version inserts new rows.

## Canonical worked example

`services/ingestion/odc_discipline.py` is the reference implementation. Key constants and helpers:

- `_HOMEPAGE = "https://montanaodc.org/attorney-discipline"`
- `_PDF_HOST = "img1.wsimg.com"`
- `_NAME_MAX_X0 = 180`, `_CAUSE_MAX_X0 = 275`, `_DISC_MAX_X0 = 490`
- `_DATE_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$")` — parses the date-ordered column (MM/DD/YY or MM/DD/YYYY).
- `run_monthly(dry_run, pdf_url, pdf_version)` is the main entry point; `main()` adds CLI flags `--dry-run`, `--url`, `--version`.

## Common failure modes

- **PDF layout changed** — columns shift, rows dropped. Re-derive column boundaries from the new PDF and update the constants + docstring.
- **Download link moved** — the regex for the `img1.wsimg.com` link fails. Inspect the new landing page HTML and update the regex.
- **Date parsing fails** — the date-ordered column format changed. Update `_DATE_RE` or fall back to raw text in `date_ordered`.
- **No version in filename** — if the next drop doesn't date-stamp the filename, switch version detection to the HTML page "as of" date or the content hash approach.
- **Duplicate rows across versions** — the unique index prevents exact duplicates within a version; if the same attorney appears in multiple versions that's expected and correct (keep them — each version is a snapshot).

## Environment / secrets

- No API key required for the ODC path (plain HTTP GET to the landing page + the PDF blob link).
- LLM-based board scraping (`license_sanction_ingest.py`) needs `KIMI_API_KEY` / `KIMI_API_BASE` / `KIMI_MODEL` — confirm those are in `.env` before running that path.
- ODC PDF is hosted on a WebStringer blob endpoint (`img1.wsimg.com`); the User-Agent header is set to a browser string on both the page fetch and the PDF download.

## Related skills / files

- `license_sanction_ingest.py` + `license_sanction_sources.py` + `scrapers/professional_boards.py` — the HTML-board / LLM-extraction mirror pattern.
- `crontab.txt` — canonical schedule; add new list cron entries here.
- `init_db.py` `migrate()` — add the new table's `CREATE TABLE IF NOT EXISTS` + indexes here so it's created on startup.
