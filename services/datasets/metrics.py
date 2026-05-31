from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any, Dict, List


DATASET_SLUG_JAIL_BOOKINGS = "jail-bookings"
DATASET_SLUG_PUBLIC_MEETINGS = "public-meetings"
DATASET_SLUG_POLICE_CALLS = "police-calls"


def _utc_now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _trend_series_from_counts(rows: List[sqlite3.Row]) -> str:
    payload = [{"date": (r["day"] or ""), "count": int(r["cnt"] or 0)} for r in rows]
    return json.dumps(payload, separators=(",", ":"))


def _json_list(items: List[Dict[str, Any]]) -> str:
    return json.dumps(items, separators=(",", ":"))


def compute_jail_bookings_metrics(conn: sqlite3.Connection) -> Dict[str, Any]:
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
    coverage_json = _json_list(
        [{"label": r["county"], "count": int(r["cnt"] or 0)} for r in coverage_rows]
    )

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
    coverage_json = _json_list(
        [{"label": r["county"], "count": int(r["cnt"] or 0)} for r in coverage_rows]
    )

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
    top_categories_json = _json_list(
        [{"label": r["label"], "count": int(r["cnt"] or 0)} for r in category_rows]
    )

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
    coverage_json = _json_list(
        [{"label": r["county"], "count": int(r["cnt"] or 0)} for r in coverage_rows]
    )

    return {
        "updated_at": updated_at,
        "window_1d_count": int(count_1d or 0),
        "window_7d_count": int(count_7d or 0),
        "window_30d_count": int(count_30d or 0),
        "trend_30d_json": _trend_series_from_counts(trend_rows),
        "top_categories_json": top_categories_json,
        "coverage_json": coverage_json,
    }

