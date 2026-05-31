# Datasets Pages + Daily Cron Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/datasets` + three MT-only dataset landing pages (Jail Bookings, Public Meetings, Police Calls) with explorer links and a daily system cron job that refreshes cached metrics and “last updated” stamps.

**Architecture:** Implement dataset pages as curated views on existing tables (`jail_bookings`, `public_meetings`, `records`). Add a small SQLite cache table `dataset_metrics` for fast landing-page stats, refreshed by a daily cron-driven script. Explorer pages reuse existing public pages when possible via redirects.

**Tech Stack:** Flask + Jinja templates, SQLite, existing `job_runner.py` cron wrapper, unittest/pytest style tests in `tests/`.

---

## File Map (locked-in)

**Create**
- `blueprints/datasets.py` — `/datasets` directory + `/datasets/<slug>` landing pages + `/datasets/<slug>/records` explorer routes.
- `services/datasets/schema.py` — `ensure_dataset_metrics_schema(conn)` for SQLite table creation.
- `services/datasets/metrics.py` — metric refresh functions per dataset slug.
- `services/datasets/refresh.py` — orchestration: refresh all dataset slugs, lock handling, timestamps.
- `scripts/refresh_datasets.py` — CLI entrypoint for cron (uses venv python).
- `templates/datasets_index.html` — datasets directory page.
- `templates/dataset_landing.html` — generic dataset landing page template.
- `templates/police_calls_records.html` — police calls explorer (records table filtered by `cfs_number`).
- `tests/test_datasets_pages.py` — route smoke tests + metrics refresh tests.

**Modify**
- `app.py` — register the datasets blueprint + add nav link if appropriate.
- `crontab.txt` — add daily job_runner entry for dataset refresh.

---

### Task 1: Add dataset metrics schema + refresh functions

**Files:**
- Create: `services/datasets/schema.py`
- Create: `services/datasets/metrics.py`
- Create: `services/datasets/refresh.py`
- Test: `tests/test_datasets_pages.py`

- [ ] **Step 1: Create `dataset_metrics` schema helper**

Create `services/datasets/schema.py`:

```python
from __future__ import annotations

import sqlite3


def ensure_dataset_metrics_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_metrics (
            dataset_slug TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            window_1d_count INTEGER NOT NULL DEFAULT 0,
            window_7d_count INTEGER NOT NULL DEFAULT 0,
            window_30d_count INTEGER NOT NULL DEFAULT 0,
            trend_30d_json TEXT NOT NULL DEFAULT '[]',
            top_categories_json TEXT NOT NULL DEFAULT '[]',
            coverage_json TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dataset_metrics_updated_at ON dataset_metrics(updated_at)"
    )
```

- [ ] **Step 2: Add per-dataset query functions**

Create `services/datasets/metrics.py`:

```python
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any, Dict, List, Tuple


DATASET_SLUG_JAIL_BOOKINGS = "jail-bookings"
DATASET_SLUG_PUBLIC_MEETINGS = "public-meetings"
DATASET_SLUG_POLICE_CALLS = "police-calls"


def _utc_now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _date_days_ago(days: int) -> str:
    return (_dt.date.today() - _dt.timedelta(days=days)).isoformat()


def _trend_series_from_counts(rows: List[sqlite3.Row]) -> str:
    # rows: [{day: 'YYYY-MM-DD', cnt: int}, ...]
    payload = [{"date": (r["day"] or ""), "count": int(r["cnt"] or 0)} for r in rows]
    return json.dumps(payload, separators=(",", ":"))


def _json_list(items: List[Dict[str, Any]]) -> str:
    return json.dumps(items, separators=(",", ":"))


def compute_jail_bookings_metrics(conn: sqlite3.Connection) -> Dict[str, Any]:
    # Booking timestamp: prefer booking_at, then first_seen_at, then created_at.
    updated_at = _utc_now_iso()

    count_1d = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM jail_bookings
        WHERE is_current = 1
          AND datetime(COALESCE(booking_at, first_seen_at, created_at)) >= datetime('now', '-1 day')
        """
    ).fetchone()["cnt"]
    count_7d = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM jail_bookings
        WHERE is_current = 1
          AND datetime(COALESCE(booking_at, first_seen_at, created_at)) >= datetime('now', '-7 day')
        """
    ).fetchone()["cnt"]
    count_30d = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM jail_bookings
        WHERE is_current = 1
          AND datetime(COALESCE(booking_at, first_seen_at, created_at)) >= datetime('now', '-30 day')
        """
    ).fetchone()["cnt"]

    trend_rows = conn.execute(
        """
        SELECT substr(date(COALESCE(booking_at, first_seen_at, created_at)), 1, 10) AS day,
               COUNT(*) AS cnt
        FROM jail_bookings
        WHERE is_current = 1
          AND datetime(COALESCE(booking_at, first_seen_at, created_at)) >= datetime('now', '-30 day')
        GROUP BY day
        ORDER BY day ASC
        """
    ).fetchall()

    # Coverage: by county_name where available via sources table, else county on booking if present.
    coverage_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(jb.county_name, ''), NULLIF(jbs.county_name, ''), '(unknown)') AS county,
               COUNT(*) AS cnt
        FROM jail_bookings jb
        LEFT JOIN jail_booking_sources jbs ON jbs.id = jb.source_id
        WHERE jb.is_current = 1
          AND datetime(COALESCE(jb.booking_at, jb.first_seen_at, jb.created_at)) >= datetime('now', '-30 day')
        GROUP BY county
        ORDER BY cnt DESC, county ASC
        LIMIT 12
        """
    ).fetchall()
    coverage_json = _json_list([{"label": r["county"], "count": int(r["cnt"] or 0)} for r in coverage_rows])

    return {
        "updated_at": updated_at,
        "window_1d_count": int(count_1d or 0),
        "window_7d_count": int(count_7d or 0),
        "window_30d_count": int(count_30d or 0),
        "trend_30d_json": _trend_series_from_counts(trend_rows),
        "top_categories_json": "[]",
        "coverage_json": coverage_json,
    }


def compute_public_meetings_metrics(conn: sqlite3.Connection) -> Dict[str, Any]:
    updated_at = _utc_now_iso()

    upcoming_14d = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM public_meetings
        WHERE is_current = 1
          AND status = 'upcoming'
          AND COALESCE(meeting_date, '') != ''
          AND date(meeting_date) >= date('now')
          AND date(meeting_date) <= date('now', '+14 day')
        """
    ).fetchone()["cnt"]
    past_30d = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM public_meetings
        WHERE is_current = 1
          AND COALESCE(meeting_date, '') != ''
          AND date(meeting_date) >= date('now', '-30 day')
        """
    ).fetchone()["cnt"]

    trend_rows = conn.execute(
        """
        SELECT substr(date(meeting_date), 1, 10) AS day,
               COUNT(*) AS cnt
        FROM public_meetings
        WHERE is_current = 1
          AND COALESCE(meeting_date, '') != ''
          AND date(meeting_date) >= date('now', '-30 day')
        GROUP BY day
        ORDER BY day ASC
        """
    ).fetchall()

    coverage_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(ml.county_name, ''), '(unknown)') AS county,
               COUNT(*) AS cnt
        FROM public_meetings pm
        LEFT JOIN meeting_locations ml ON ml.id = pm.location_id
        WHERE pm.is_current = 1
          AND COALESCE(pm.meeting_date, '') != ''
          AND date(pm.meeting_date) >= date('now', '-30 day')
        GROUP BY county
        ORDER BY cnt DESC, county ASC
        LIMIT 12
        """
    ).fetchall()
    coverage_json = _json_list([{"label": r["county"], "count": int(r["cnt"] or 0)} for r in coverage_rows])

    # Map upcoming_14d into window_1d_count slot for directory consistency (template labels will differ).
    return {
        "updated_at": updated_at,
        "window_1d_count": int(upcoming_14d or 0),
        "window_7d_count": 0,
        "window_30d_count": int(past_30d or 0),
        "trend_30d_json": _trend_series_from_counts(trend_rows),
        "top_categories_json": "[]",
        "coverage_json": coverage_json,
    }


def compute_police_calls_metrics(conn: sqlite3.Connection) -> Dict[str, Any]:
    updated_at = _utc_now_iso()

    count_1d = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM records
        WHERE COALESCE(NULLIF(cfs_number, ''), '') != ''
          AND date(date) >= date('now', '-1 day')
        """
    ).fetchone()["cnt"]
    count_7d = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM records
        WHERE COALESCE(NULLIF(cfs_number, ''), '') != ''
          AND date(date) >= date('now', '-7 day')
        """
    ).fetchone()["cnt"]
    count_30d = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM records
        WHERE COALESCE(NULLIF(cfs_number, ''), '') != ''
          AND date(date) >= date('now', '-30 day')
        """
    ).fetchone()["cnt"]

    trend_rows = conn.execute(
        """
        SELECT substr(date(date), 1, 10) AS day,
               COUNT(*) AS cnt
        FROM records
        WHERE COALESCE(NULLIF(cfs_number, ''), '') != ''
          AND date(date) >= date('now', '-30 day')
        GROUP BY day
        ORDER BY day ASC
        """
    ).fetchall()

    category_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(incident_type, ''), '(unknown)') AS label,
               COUNT(*) AS cnt
        FROM records
        WHERE COALESCE(NULLIF(cfs_number, ''), '') != ''
          AND date(date) >= date('now', '-30 day')
        GROUP BY label
        ORDER BY cnt DESC, label ASC
        LIMIT 10
        """
    ).fetchall()
    top_categories_json = _json_list([{"label": r["label"], "count": int(r["cnt"] or 0)} for r in category_rows])

    coverage_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(county, ''), '(unknown)') AS county,
               COUNT(*) AS cnt
        FROM records
        WHERE COALESCE(NULLIF(cfs_number, ''), '') != ''
          AND date(date) >= date('now', '-30 day')
        GROUP BY county
        ORDER BY cnt DESC, county ASC
        LIMIT 12
        """
    ).fetchall()
    coverage_json = _json_list([{"label": r["county"], "count": int(r["cnt"] or 0)} for r in coverage_rows])

    return {
        "updated_at": updated_at,
        "window_1d_count": int(count_1d or 0),
        "window_7d_count": int(count_7d or 0),
        "window_30d_count": int(count_30d or 0),
        "trend_30d_json": _trend_series_from_counts(trend_rows),
        "top_categories_json": top_categories_json,
        "coverage_json": coverage_json,
    }
```

- [ ] **Step 3: Add refresh orchestration with a lock file**

Create `services/datasets/refresh.py`:

```python
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, Iterable, List

from services.datasets.schema import ensure_dataset_metrics_schema
from services.datasets.metrics import (
    DATASET_SLUG_JAIL_BOOKINGS,
    DATASET_SLUG_PUBLIC_MEETINGS,
    DATASET_SLUG_POLICE_CALLS,
    compute_jail_bookings_metrics,
    compute_public_meetings_metrics,
    compute_police_calls_metrics,
)


DEFAULT_DATASET_SLUGS: List[str] = [
    DATASET_SLUG_JAIL_BOOKINGS,
    DATASET_SLUG_PUBLIC_MEETINGS,
    DATASET_SLUG_POLICE_CALLS,
]


@contextmanager
def file_lock(lock_path: str, stale_seconds: int = 6 * 60 * 60):
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    now = time.time()
    if os.path.exists(lock_path):
        try:
            age = now - os.path.getmtime(lock_path)
            if age < stale_seconds:
                raise RuntimeError(f"lock exists: {lock_path}")
        except OSError:
            pass
    with open(lock_path, "w", encoding="utf-8") as fh:
        fh.write(str(int(now)))
    try:
        yield
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def refresh_dataset_metrics(conn: sqlite3.Connection, dataset_slug: str) -> Dict[str, object]:
    if dataset_slug == DATASET_SLUG_JAIL_BOOKINGS:
        metrics = compute_jail_bookings_metrics(conn)
    elif dataset_slug == DATASET_SLUG_PUBLIC_MEETINGS:
        metrics = compute_public_meetings_metrics(conn)
    elif dataset_slug == DATASET_SLUG_POLICE_CALLS:
        metrics = compute_police_calls_metrics(conn)
    else:
        raise ValueError(f"unknown dataset slug: {dataset_slug}")

    conn.execute(
        """
        INSERT INTO dataset_metrics (
            dataset_slug, updated_at,
            window_1d_count, window_7d_count, window_30d_count,
            trend_30d_json, top_categories_json, coverage_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset_slug) DO UPDATE SET
            updated_at=excluded.updated_at,
            window_1d_count=excluded.window_1d_count,
            window_7d_count=excluded.window_7d_count,
            window_30d_count=excluded.window_30d_count,
            trend_30d_json=excluded.trend_30d_json,
            top_categories_json=excluded.top_categories_json,
            coverage_json=excluded.coverage_json
        """,
        (
            dataset_slug,
            str(metrics["updated_at"]),
            int(metrics["window_1d_count"]),
            int(metrics["window_7d_count"]),
            int(metrics["window_30d_count"]),
            str(metrics["trend_30d_json"]),
            str(metrics["top_categories_json"]),
            str(metrics["coverage_json"]),
        ),
    )
    return metrics


def refresh_all_dataset_metrics(
    conn: sqlite3.Connection,
    dataset_slugs: Iterable[str] = DEFAULT_DATASET_SLUGS,
) -> Dict[str, Dict[str, object]]:
    ensure_dataset_metrics_schema(conn)
    results: Dict[str, Dict[str, object]] = {}
    for slug in dataset_slugs:
        results[slug] = refresh_dataset_metrics(conn, slug)
    return results
```

- [ ] **Step 4: Write tests for schema + refresh logic**

Create `tests/test_datasets_pages.py` (initial version focuses on schema + metrics refresh; routes added in later tasks):

```python
import os
import tempfile
import unittest

import app as app_module
import config
import init_db
from services.datasets.schema import ensure_dataset_metrics_schema
from services.datasets.refresh import refresh_all_dataset_metrics


class DatasetMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-datasets-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_ensure_dataset_metrics_schema_creates_table(self) -> None:
        conn = app_module.get_db()
        ensure_dataset_metrics_schema(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dataset_metrics'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_refresh_all_dataset_metrics_writes_rows(self) -> None:
        conn = app_module.get_db()
        ensure_dataset_metrics_schema(conn)
        refresh_all_dataset_metrics(conn)
        rows = conn.execute("SELECT dataset_slug FROM dataset_metrics").fetchall()
        conn.close()
        self.assertGreaterEqual(len(rows), 3)
```

- [ ] **Step 5: Run targeted tests**

Run: `./venv/bin/python3 -m pytest tests/test_datasets_pages.py -q`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/datasets/schema.py services/datasets/metrics.py services/datasets/refresh.py tests/test_datasets_pages.py
git commit -m "feat: add dataset metrics cache and refresh helpers"
```

---

### Task 2: Add datasets blueprint + templates

**Files:**
- Create: `blueprints/datasets.py`
- Create: `templates/datasets_index.html`
- Create: `templates/dataset_landing.html`
- Create: `templates/police_calls_records.html`
- Modify: `app.py`
- Test: `tests/test_datasets_pages.py`

- [ ] **Step 1: Implement datasets blueprint**

Create `blueprints/datasets.py`:

```python
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from flask import Blueprint, abort, redirect, render_template, request, url_for

from services.datasets.schema import ensure_dataset_metrics_schema


DATASETS = {
    "jail-bookings": {
        "title": "Jail Bookings",
        "subtitle": "Daily jail booking activity by county.",
        "browse_href": "/jail-bookings",
        "sources": [
            {"label": "County detention rosters (varies by county)", "href": "/jail-rosters"},
        ],
        "methodology": [
            "Counts are derived from jail booking ingestion sources that Montana Blotter monitors.",
            "Coverage varies by county and by date.",
        ],
    },
    "public-meetings": {
        "title": "Public Meetings / Agendas",
        "subtitle": "Upcoming Montana public meetings with official agenda links.",
        "browse_href": "/meetings",
        "sources": [
            {"label": "Official city/county agenda listings", "href": "/meetings"},
        ],
        "methodology": [
            "Meetings are scraped from official agenda systems and linked back to the original source URLs.",
            "Times/dates reflect what the source provides; labels are standardized when possible.",
        ],
    },
    "police-calls": {
        "title": "Police Calls / Calls-for-Service",
        "subtitle": "Call-for-service records where official CFS numbers are present.",
        "browse_href": "/datasets/police-calls/records",
        "sources": [
            {"label": "Official agency call logs and activity reports (varies by agency)", "href": "/"},
        ],
        "methodology": [
            "This dataset includes records with a non-empty CFS number in the `records` table.",
            "Not all agencies publish CFS numbers or call logs in a structured format.",
        ],
    },
}


def register_datasets_blueprint(app, get_db, base_url: str = "https://montanablotter.com") -> None:
    datasets_bp = Blueprint("datasets", __name__, template_folder="templates")

    def _load_metrics(dataset_slug: str) -> Optional[Dict[str, Any]]:
        conn = get_db()
        try:
            ensure_dataset_metrics_schema(conn)
            row = conn.execute(
                "SELECT * FROM dataset_metrics WHERE dataset_slug = ?",
                (dataset_slug,),
            ).fetchone()
            if not row:
                return None
            payload = dict(row)
            for key in ("trend_30d_json", "top_categories_json", "coverage_json"):
                try:
                    payload[key] = json.loads(payload.get(key) or "[]")
                except Exception:
                    payload[key] = []
            return payload
        finally:
            conn.close()

    @datasets_bp.route("/datasets")
    def datasets_index():
        entries = []
        for slug, meta in DATASETS.items():
            metrics = _load_metrics(slug)
            entries.append(
                {
                    "slug": slug,
                    "title": meta["title"],
                    "subtitle": meta["subtitle"],
                    "href": url_for("datasets.dataset_landing", slug=slug),
                    "updated_at": (metrics or {}).get("updated_at"),
                    "window_1d_count": (metrics or {}).get("window_1d_count", 0),
                    "window_7d_count": (metrics or {}).get("window_7d_count", 0),
                    "window_30d_count": (metrics or {}).get("window_30d_count", 0),
                }
            )
        return render_template(
            "datasets_index.html",
            datasets=entries,
            page_title="Datasets",
            meta_description="Montana-only public datasets directory: jail bookings, public meetings, and police calls.",
            canonical_url=f"{base_url}/datasets",
            current_year=__import__("datetime").datetime.now().year,
        )

    @datasets_bp.route("/datasets/<slug>")
    def dataset_landing(slug: str):
        meta = DATASETS.get(slug)
        if not meta:
            abort(404)
        metrics = _load_metrics(slug)
        return render_template(
            "dataset_landing.html",
            dataset_slug=slug,
            dataset=meta,
            metrics=metrics,
            browse_href=meta["browse_href"],
            page_title=f"{meta['title']} Dataset",
            meta_description=meta["subtitle"],
            canonical_url=f"{base_url}/datasets/{slug}",
            current_year=__import__("datetime").datetime.now().year,
        )

    @datasets_bp.route("/datasets/<slug>/records")
    def dataset_records(slug: str):
        if slug == "jail-bookings":
            return redirect("/jail-bookings", code=302)
        if slug == "public-meetings":
            return redirect("/meetings", code=302)
        if slug == "police-calls":
            return _police_calls_records(get_db)
        abort(404)

    def _police_calls_records(get_db):
        county = (request.args.get("county") or "").strip()
        q = (request.args.get("q") or "").strip()
        page = max(1, request.args.get("page", 1, type=int))
        per_page = 25

        conn = get_db()
        try:
            sql = """
                SELECT records.*,
                       COALESCE(blotters.filename, '') AS filename
                FROM records
                LEFT JOIN blotters ON records.blotter_id = blotters.id
                WHERE COALESCE(NULLIF(records.cfs_number, ''), '') != ''
            """
            params = []
            if county:
                sql += " AND records.county = ?"
                params.append(county)
            if q:
                st = f"%{q}%"
                sql += " AND (records.incident_type LIKE ? OR records.details LIKE ? OR records.location LIKE ? OR records.cfs_number LIKE ?)"
                params.extend([st, st, st, st])

            total = conn.execute(
                sql.replace(
                    "SELECT records.*,\n                       COALESCE(blotters.filename, '') AS filename",
                    "SELECT COUNT(*)",
                ),
                params,
            ).fetchone()[0]
            total_pages = max(1, (total + per_page - 1) // per_page)

            sql += " ORDER BY records.created_at DESC LIMIT ? OFFSET ?"
            params.extend([per_page, (page - 1) * per_page])
            records = conn.execute(sql, params).fetchall()

            counties = [
                r["county"]
                for r in conn.execute(
                    "SELECT DISTINCT county FROM records WHERE COALESCE(NULLIF(cfs_number, ''), '') != '' ORDER BY county"
                ).fetchall()
            ]
        finally:
            conn.close()

        return render_template(
            "police_calls_records.html",
            records=records,
            total=total,
            total_pages=total_pages,
            page=page,
            counties=counties,
            county=county,
            q=q,
            current_year=__import__("datetime").datetime.now().year,
        )

    app.register_blueprint(datasets_bp)
```

- [ ] **Step 2: Register blueprint in `app.py`**

Modify `app.py` near other imports/registrations:

```python
from blueprints.datasets import register_datasets_blueprint
```

And register (near the other `register_*_blueprint` calls):

```python
register_datasets_blueprint(app, get_db=get_db, base_url=BASE_URL)
```

- [ ] **Step 3: Add templates**

Create `templates/datasets_index.html`:

```html
{% extends "public_page_base.html" %}
{% block body %}
<section class="public-hero">
  <h1 class="public-title">Datasets</h1>
  <p class="public-subtitle">Montana-only public datasets with standardized filters and source links.</p>
</section>

<section class="public-section">
  <div class="grid gap-4 md:grid-cols-3">
    {% for ds in datasets %}
    <a href="{{ ds.href }}" class="rounded-2xl border border-slate-200 bg-white p-5 hover:bg-slate-50 transition">
      <div class="text-xs font-black uppercase tracking-widest text-slate-500">Dataset</div>
      <div class="mt-2 text-lg font-black text-slate-900">{{ ds.title }}</div>
      <div class="mt-1 text-sm text-slate-600">{{ ds.subtitle }}</div>
      <div class="mt-4 flex flex-wrap gap-2 text-xs">
        <span class="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700">24h: {{ ds.window_1d_count or 0 }}</span>
        <span class="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700">7d: {{ ds.window_7d_count or 0 }}</span>
        <span class="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700">30d: {{ ds.window_30d_count or 0 }}</span>
      </div>
      <div class="mt-3 text-xs text-slate-500">
        Last updated: {{ ds.updated_at or "pending" }}
      </div>
    </a>
    {% endfor %}
  </div>
</section>
{% endblock %}
```

Create `templates/dataset_landing.html`:

```html
{% extends "public_page_base.html" %}
{% block body %}
<section class="public-hero">
  <div class="flex flex-wrap items-end justify-between gap-4">
    <div>
      <h1 class="public-title">{{ dataset.title }}</h1>
      <p class="public-subtitle">{{ dataset.subtitle }}</p>
      <p class="mt-3 text-xs text-slate-500">Last updated: {{ metrics.updated_at if metrics else "pending" }}</p>
    </div>
    <a href="{{ browse_href }}" class="public-btn public-btn--primary">Browse records</a>
  </div>
</section>

<section class="public-section">
  {% if metrics %}
  <div class="grid gap-4 md:grid-cols-3">
    <div class="rounded-2xl border border-slate-200 bg-white p-5">
      <div class="text-xs font-black uppercase tracking-widest text-slate-500">24h</div>
      <div class="mt-2 text-3xl font-black text-slate-900">{{ metrics.window_1d_count or 0 }}</div>
    </div>
    <div class="rounded-2xl border border-slate-200 bg-white p-5">
      <div class="text-xs font-black uppercase tracking-widest text-slate-500">7d</div>
      <div class="mt-2 text-3xl font-black text-slate-900">{{ metrics.window_7d_count or 0 }}</div>
    </div>
    <div class="rounded-2xl border border-slate-200 bg-white p-5">
      <div class="text-xs font-black uppercase tracking-widest text-slate-500">30d</div>
      <div class="mt-2 text-3xl font-black text-slate-900">{{ metrics.window_30d_count or 0 }}</div>
    </div>
  </div>
  {% else %}
    <div class="rounded-2xl border border-dashed border-slate-200 bg-white p-6 text-sm text-slate-600">
      Metrics are being generated. Check back soon.
    </div>
  {% endif %}
</section>

<section class="public-section">
  <h2 class="text-lg font-black text-slate-900">Sources</h2>
  <ul class="mt-2 list-disc pl-6 text-sm text-slate-700">
    {% for s in dataset.sources %}
      <li><a href="{{ s.href }}" class="font-semibold text-sky-700 hover:text-sky-900">{{ s.label }}</a></li>
    {% endfor %}
  </ul>
</section>

<section class="public-section">
  <h2 class="text-lg font-black text-slate-900">Methodology</h2>
  <ul class="mt-2 list-disc pl-6 text-sm text-slate-700">
    {% for line in dataset.methodology %}
      <li>{{ line }}</li>
    {% endfor %}
  </ul>
  <p class="mt-4 text-xs text-slate-500">
    This page summarizes public records. It does not imply guilt or wrongdoing and may be incomplete.
  </p>
</section>
{% endblock %}
```

Create `templates/police_calls_records.html`:

```html
{% extends "public_page_base.html" %}
{% block body %}
<section class="public-hero">
  <h1 class="public-title">Police Calls / Calls-for-Service</h1>
  <p class="public-subtitle">Records with a non-empty CFS number.</p>
  <a class="mt-3 inline-block text-sm font-bold text-sky-700 hover:text-sky-900" href="/datasets/police-calls">Back to dataset</a>
</section>

<section class="public-section">
  <form method="get" class="flex flex-wrap gap-2">
    <input type="text" name="q" value="{{ q }}" placeholder="Search type/location/CFS" class="rounded-xl border border-slate-300 px-4 py-3 text-sm" />
    <select name="county" class="rounded-xl border border-slate-300 px-4 py-3 text-sm">
      <option value="">All counties</option>
      {% for c in counties %}
        <option value="{{ c }}" {% if county == c %}selected{% endif %}>{{ c }}</option>
      {% endfor %}
    </select>
    <button class="public-btn public-btn--primary" type="submit">Filter</button>
  </form>

  <div class="mt-5 space-y-3">
    {% for record in records %}
      <a href="/record/{{ record.id }}" class="block rounded-2xl border border-slate-200 bg-white p-5 hover:bg-slate-50 transition">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="text-sm font-black text-slate-900">{{ record.incident_type or "Call" }}</div>
          <div class="text-xs text-slate-500">{{ record.date }} {{ record.time or "" }}</div>
        </div>
        <div class="mt-2 text-sm text-slate-700">{{ record.location or "" }}</div>
        {% if record.cfs_number %}
          <div class="mt-2 text-xs font-mono text-slate-500">CFS {{ record.cfs_number }}</div>
        {% endif %}
      </a>
    {% else %}
      <div class="rounded-2xl border border-dashed border-slate-200 bg-white p-6 text-sm text-slate-600">
        No matching records.
      </div>
    {% endfor %}
  </div>
</section>
{% endblock %}
```

- [ ] **Step 4: Extend tests to cover routes**

Append to `tests/test_datasets_pages.py`:

```python
class DatasetRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-datasets-routes-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True
        init_db.migrate()
        self.client = app_module.app.test_client()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = config.DB_PATH
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_datasets_index_renders(self) -> None:
        res = self.client.get("/datasets")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Datasets", res.data)

    def test_dataset_landing_renders(self) -> None:
        res = self.client.get("/datasets/jail-bookings")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Jail Bookings", res.data)
```

- [ ] **Step 5: Run targeted tests**

Run: `./venv/bin/python3 -m pytest tests/test_datasets_pages.py -q`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add blueprints/datasets.py templates/datasets_index.html templates/dataset_landing.html templates/police_calls_records.html app.py tests/test_datasets_pages.py
git commit -m "feat: add datasets directory and landing pages"
```

---

### Task 3: Add cron entrypoint script + wire into crontab

**Files:**
- Create: `scripts/refresh_datasets.py`
- Modify: `crontab.txt`
- Test: `tests/test_datasets_pages.py`

- [ ] **Step 1: Add cron entrypoint**

Create `scripts/refresh_datasets.py`:

```python
from __future__ import annotations

import argparse
import os
import sys
import time

import app as app_module
from services.datasets.refresh import DEFAULT_DATASET_SLUGS, file_lock, refresh_all_dataset_metrics


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", default="/root/montanablotter/logs/datasets_refresh.lock")
    args = parser.parse_args(argv)

    started = time.time()
    conn = app_module.get_db()
    try:
        with file_lock(args.lock):
            refresh_all_dataset_metrics(conn, DEFAULT_DATASET_SLUGS)
            conn.commit()
    finally:
        conn.close()

    elapsed = time.time() - started
    print(f"[datasets] refreshed in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 2: Add daily cron job**

Modify `crontab.txt` (place near other daily jobs):

```cron
# Datasets: refresh landing-page metrics daily at 6:10am
10 6 * * * /root/montanablotter/venv/bin/python3 /root/montanablotter/job_runner.py --name datasets_refresh --log /root/montanablotter/logs/datasets_refresh.log --workdir /root/montanablotter -- /root/montanablotter/venv/bin/python3 /root/montanablotter/scripts/refresh_datasets.py
```

- [ ] **Step 3: Add a test that the script can run**

Append to `tests/test_datasets_pages.py`:

```python
    def test_refresh_script_runs(self) -> None:
        from scripts.refresh_datasets import main

        rc = main(["--lock", os.path.join(tempfile.gettempdir(), "datasets_refresh.lock")])
        self.assertEqual(rc, 0)
```

- [ ] **Step 4: Run targeted tests**

Run: `./venv/bin/python3 -m pytest tests/test_datasets_pages.py -q`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/refresh_datasets.py crontab.txt tests/test_datasets_pages.py
git commit -m "chore: add daily datasets refresh cron"
```

---

### Task 4: Nav link + SEO/canonical sanity

**Files:**
- Modify: `templates/includes/mobile_tab_bar.html` (if desired)
- Modify: `templates/public_page_base.html` or another nav include (if applicable)

- [ ] **Step 1: Add a “Datasets” link in the public nav**

If `templates/includes/mobile_tab_bar.html` is the primary public nav on mobile, add:

```jinja2
('/datasets', public_nav_menu_labels_by_href.get('/datasets', 'Datasets'), public_nav_full_labels_by_href.get('/datasets', 'Datasets')),
```

- [ ] **Step 2: Verify canonical URLs on `/datasets` + dataset pages**

Manual check (local): open `/datasets` and `/datasets/jail-bookings` and ensure the `<link rel="canonical">` reflects `https://montanablotter.com/...`.

- [ ] **Step 3: Commit**

```bash
git add templates/includes/mobile_tab_bar.html templates/public_page_base.html
git commit -m "feat: add datasets nav link"
```

---

## Self-Review Checklist (plan)

- Spec coverage:
  - `/datasets` directory: Task 2.
  - `/datasets/<slug>` landing pages: Task 2.
  - `/datasets/<slug>/records` explorer: Task 2 (redirects for jail/meetings; native explorer for police calls).
  - Daily system cron updater: Task 3.
  - Metrics caching: Task 1.
  - View-only: no download endpoints added.
- Placeholder scan: no TBD/TODO in code steps.
- Type consistency: dataset slugs centralized in `services/datasets/metrics.py`.

---

## Execution Handoff

Plan complete and saved to `montanablotter/docs/superpowers/plans/2026-05-19-datasets-pages-and-cron.md`.

Two execution options:
1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks.
2. **Inline Execution** — execute tasks in this session in order.

Which approach?
