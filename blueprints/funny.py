"""Public /funniest feed — ranked humorous blotter entries, PII-sc rubbed.

Read-only. Eligible incident types are low-stakes (traffic, animal, theft of
cheap items, suspicious circumstances, welfare checks, public-intox). Sensitive
types are excluded by the scorer (humor_score = 0) and again here as a defen sive
SQL filter. Every displayed field is run through ``redact_text`` so no PII leaks.
"""
from __future__ import annotations

import math

from flask import Blueprint, abort, jsonify, request, url_for

from db import get_db
from services.blotter.humor import DENY_INCIDENT_TYPES, redact_text

funny_bp = Blueprint("funny", __name__)

PAGE_SIZE = 20
DENY_PLACEHOLDER = "•"  # never rendered; placeholder for safe SQL binding


def _build_query(limit: int, offset: int) -> tuple[str, tuple]:
    placeholders = ", ".join("?" for _ in DENY_INCIDENT_TYPES)
    sql = (
        "SELECT id, incident, details, location, incident_type, county, date, humor_score "
        "FROM records "
        "WHERE humor_score IS NOT NULL AND humor_score > 0 "
        f"  AND (incident_type IS NULL OR incident_type NOT IN ({placeholders})) "
        "ORDER BY humor_score DESC, date DESC, id DESC "
        "LIMIT ? OFFSET ?"
    )
    params = tuple(DENY_INCIDENT_TYPES) + (limit, offset)
    return sql, params


def _serialize(row) -> dict:
    return {
        "id": row["id"],
        "incident": redact_text(row["incident"] or ""),
        "details": redact_text(row["details"] or ""),
        "location": redact_text(row["location"] or ""),
        "incident_type": row["incident_type"],
        "county": row["county"],
        "date": row["date"],
        "humor_score": row["humor_score"],
        "share_url": url_for("funny.funniest_feed", _external=True) + f"#item-{row['id']}",
    }


def _fetch_page(page: int) -> dict:
    """Return {items, page, total_pages, total} for the given 1-based page."""
    if page < 1:
        page = 1
    conn = get_db()
    try:
        count_sql = (
            "SELECT COUNT(*) AS n FROM records "
            "WHERE humor_score IS NOT NULL AND humor_score > 0 "
            f"  AND (incident_type IS NULL OR incident_type NOT IN ({', '.join('?' for _ in DENY_INCIDENT_TYPES)}))"
        )
        total = conn.execute(count_sql, tuple(DENY_INCIDENT_TYPES)).fetchone()["n"]
        offset = (page - 1) * PAGE_SIZE
        sql, params = _build_query(PAGE_SIZE, offset)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    items = [_serialize(r) for r in rows]
    total_pages = max(1, math.ceil(total / PAGE_SIZE)) if total else 1
    return {
        "items": items,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "page_size": PAGE_SIZE,
    }


def _pagination_rels(page: int, total_pages: int) -> list[dict]:
    """SEO rel=prev/next hints for the base template's pagination_rels loop."""
    rels: list[dict] = []
    if page > 1:
        rels.append(
            {"rel": "prev", "href": url_for("funny.funniest_feed", page=page - 1, _external=True)}
        )
    if page < total_pages:
        rels.append(
            {"rel": "next", "href": url_for("funny.funniest_feed", page=page + 1, _external=True)}
        )
    return rels


@funny_bp.route("/funniest")
@funny_bp.route("/funniest/")
def funniest_feed():
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    data = _fetch_page(page)
    return _render_feed(data)


def _render_feed(data: dict):
    from flask import render_template

    prev_url = url_for("funny.funniest_feed", page=data["page"] - 1) if data["page"] > 1 else None
    next_url = (
        url_for("funny.funniest_feed", page=data["page"] + 1)
        if data["page"] < data["total_pages"]
        else None
    )
    canonical = url_for("funny.funniest_feed", _external=True)
    if data["page"] > 1:
        canonical = url_for("funny.funniest_feed", page=data["page"], _external=True)
    return render_template(
        "funniest.html",
        items=data["items"],
        page=data["page"],
        total_pages=data["total_pages"],
        total=data["total"],
        prev_url=prev_url,
        next_url=next_url,
        pagination_rels=_pagination_rels(data["page"], data["total_pages"]),
        canonical_url=canonical,
        page_title="Funniest Police Blotters",
        meta_description=(
            "The lighter side of Montana law enforcement — loose livestock, "
            "lawn gnomes, and other small-town absurdity, drawn from public records."
        ),
        active_nav="funniest",
        current_year=__import__("datetime").datetime.now().year,
    )


@funny_bp.route("/funniest.json")
def funniest_json():
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    data = _fetch_page(page)
    return jsonify(
        {
            "items": data["items"],
            "page": data["page"],
            "total_pages": data["total_pages"],
            "total": data["total"],
            "page_size": data["page_size"],
        }
    )
