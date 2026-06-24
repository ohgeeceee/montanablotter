"""
Recidivism leaderboard — top people who have been released and re-booked.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from services.persons.profiles import _pretty_name


def recidivism_leaderboard_context(
    conn: sqlite3.Connection,
    *,
    limit: int = 10,
    county_slug: str = '',
) -> dict[str, Any]:
    """Return context for the repeat-booking leaderboard page.

    Qualifying individuals must have at least two bookings where a later
    booking started after an earlier booking had a release date. This filters
    out people who simply have multiple concurrent or unknown-status bookings.

    Args:
        conn: SQLite connection.
        limit: Maximum number of leaderboard entries.
        county_slug: Optional county slug to restrict results to.
    """
    selected_county = (county_slug or '').strip().lower()

    county_filter = ""
    params: list = []
    if selected_county:
        county_filter = "AND county_slug = ?"
        params.append(selected_county)

    rows = conn.execute(
        f"""
        WITH person_bookings AS (
            SELECT
                name_slug,
                person_name,
                county_name,
                county_slug,
                booking_at,
                release_at
            FROM jail_bookings
            WHERE name_slug IS NOT NULL AND name_slug != ''
                {county_filter}
        ),
        qualifiers AS (
            SELECT DISTINCT pb1.name_slug
            FROM person_bookings pb1
            JOIN person_bookings pb2
                ON pb1.name_slug = pb2.name_slug
                AND pb1.booking_at < pb2.booking_at
                AND pb1.release_at IS NOT NULL
                AND pb2.booking_at > pb1.release_at
        ),
        stats AS (
            SELECT
                name_slug,
                person_name,
                COUNT(*) AS booking_count,
                GROUP_CONCAT(DISTINCT county_name) AS county_names,
                MIN(booking_at) AS first_booking_at,
                MAX(booking_at) AS last_booking_at,
                MAX(release_at) AS last_release_at
            FROM person_bookings
            GROUP BY name_slug
        )
        SELECT
            s.name_slug,
            s.person_name,
            s.booking_count,
            s.county_names,
            s.first_booking_at,
            s.last_booking_at,
            s.last_release_at
        FROM stats s
        JOIN qualifiers q ON s.name_slug = q.name_slug
        ORDER BY s.booking_count DESC, s.last_booking_at DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()

    leaderboard: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        county_names = sorted({c.strip() for c in (row["county_names"] or "").split(",") if c.strip()})
        leaderboard.append(
            {
                "rank": idx,
                "name_slug": row["name_slug"],
                "person_name": row["person_name"],
                "display_name": _pretty_name(row["person_name"]),
                "booking_count": row["booking_count"],
                "county_names": county_names,
                "counties_label": ", ".join(county_names) if county_names else "",
                "first_booking_at": row["first_booking_at"],
                "last_booking_at": row["last_booking_at"],
                "last_release_at": row["last_release_at"],
                "profile_url": f"/person/{row['name_slug']}",
            }
        )

    qualifier_filter = ""
    qualifier_params: list = []
    if selected_county:
        qualifier_filter = "AND jb.county_slug = ?"
        qualifier_params.append(selected_county)

    total_qualifiers = conn.execute(
        f"""
        SELECT COUNT(DISTINCT jb.name_slug) AS n
        FROM jail_bookings jb
        WHERE EXISTS (
            SELECT 1
            FROM jail_bookings earlier
            WHERE earlier.name_slug = jb.name_slug
              AND earlier.county_slug = jb.county_slug
              AND earlier.release_at IS NOT NULL
              AND earlier.booking_at < jb.booking_at
              AND jb.booking_at > earlier.release_at
        )
        {qualifier_filter}
        """,
        qualifier_params,
    ).fetchone()["n"]

    counties = [
        dict(r)
        for r in conn.execute(
            """
            SELECT DISTINCT county_name, county_slug
            FROM jail_bookings
            WHERE county_name IS NOT NULL AND county_name != ''
            ORDER BY county_name
            """
        ).fetchall()
    ]

    return {
        "leaderboard": leaderboard,
        "total_qualifiers": total_qualifiers or 0,
        "total_bookings_in_leaderboard": sum(r["booking_count"] for r in leaderboard),
        "limit": limit,
        "counties": counties,
        "selected_county": selected_county,
    }
