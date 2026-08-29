"""Paid name-removal / privacy-suppression helpers.

A one-time $999 payment triggers a verified privacy review. On approval the
person's name is REDACTED (not deleted) across public records — jail bookings,
warrants, and posts. Deletion of underlying government records never happens
automatically; suppression only masks the name in public presentation.

The gate uses a normalized name lookup so "Last, First" and "First Last" forms
both match. County is optional and, when supplied, narrows the match.
"""

from __future__ import annotations

import re
import sqlite3

from db import get_db

WITHHELD_LABEL = "Name withheld per privacy request"


def _normalize_name(name: str) -> str:
    """Normalize a person name for matching.

    Lowercases, strips whitespace, collapses internal whitespace, and sorts
    comma/space-separated tokens so "Doe, John" and "John Doe" match.
    """
    if not name:
        return ""
    s = name.strip().lower()
    # split on commas and spaces, drop empties
    tokens = [t for t in re.split(r"[,\s]+", s) if t]
    if not tokens:
        return ""
    return " ".join(sorted(tokens))


def is_name_suppressed(name: str, county: str | None = None) -> bool:
    """Return True if the given person name is currently suppressed."""
    norm = _normalize_name(name)
    if not norm:
        return False
    conn = get_db()
    try:
        if county:
            row = conn.execute(
                "SELECT 1 FROM suppressed_names "
                "WHERE person_name_normalized = ? AND (county IS NULL OR county = ?) "
                "LIMIT 1",
                (norm, county),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM suppressed_names "
                "WHERE person_name_normalized = ? LIMIT 1",
                (norm,),
            ).fetchone()
        return row is not None
    finally:
        conn.close()


def redact_person_name(name: str | None, county: str | None = None,
                        withheld_label: str = WITHHELD_LABEL) -> str:
    """Return the displayed value for a person name, redacting if suppressed.

    Non-string/empty input passes through unchanged (None stays None).
    """
    if name is None:
        return None
    if not name:
        return name
    if is_name_suppressed(name, county):
        return withheld_label
    return name


def _active_suppressions(county: str | None = None) -> list[tuple[str, str | None]]:
    """Return list of (normalized_name, county) currently suppressed."""
    conn = get_db()
    try:
        if county:
            rows = conn.execute(
                "SELECT person_name_normalized, county FROM suppressed_names "
                "WHERE county IS NULL OR county = ?",
                (county,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT person_name_normalized, county FROM suppressed_names"
            ).fetchall()
        return [(r["person_name_normalized"], r["county"]) for r in rows]
    finally:
        conn.close()


def redact_text(text: str | None, county: str | None = None,
                withheld_label: str = WITHHELD_LABEL) -> str:
    """Mask any suppressed person name that appears inside free text.

    Used for post titles/summaries where the name is embedded in prose. Each
    suppressed name is replaced (case-insensitively, whole-word, any token order)
    with the withheld label. Returns the text unchanged if no suppression applies.

    Matching is order-independent: a name stored as "doe john" (normalized) will
    still mask "John Doe", "Doe, John", or "Doe John" in the source text.
    """
    if not text:
        return text if text is not None else ""
    for norm, _ in _active_suppressions(county):
        tokens = norm.split(" ")
        if len(tokens) == 1:
            pattern = re.compile(r"\b" + re.escape(tokens[0]) + r"\b", re.IGNORECASE)
        else:
            # match the tokens in either order, allowing punctuation/whitespace
            # between them (covers "John Doe", "Doe, John", "Doe John")
            pattern = re.compile(
                r"(?:" + ".*?".join(r"\b" + re.escape(t) + r"\b" for t in tokens)
                + r")|(?:" + ".*?".join(r"\b" + re.escape(t) + r"\b" for t in reversed(tokens)) + r")",
                re.IGNORECASE,
            )
        text = pattern.sub(withheld_label, text)
    return text


def apply_suppression(request_id: int, person_name: str, county: str | None,
                     applied_by: int | None = None) -> bool:
    """Write a suppression entry for an approved request.

    Idempotent: re-applying the same normalized name + county is a no-op.
    Returns True if a new row was inserted.
    """
    norm = _normalize_name(person_name)
    if not norm:
        return False
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT 1 FROM suppressed_names "
            "WHERE person_name_normalized = ? AND (county IS NULL OR county = ?) LIMIT 1",
            (norm, county),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE name_suppression_requests SET applied_at=datetime('now') "
                "WHERE id = ?",
                (request_id,),
            )
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO suppressed_names "
            "(person_name_normalized, county, request_id, applied_by) "
            "VALUES (?, ?, ?, ?)",
            (norm, county, request_id, applied_by),
        )
        conn.execute(
            "UPDATE name_suppression_requests SET status='applied', "
            "applied_at=datetime('now') WHERE id = ?",
            (request_id,),
        )
        conn.commit()
        return True
    finally:
        conn.close()
