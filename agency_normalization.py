import re
import sqlite3
from typing import Optional


_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_COUNTY_JOIN_RE = re.compile(r"(?i)\b([A-Za-z]+)county\b")
_TRAILING_POLICE_RE = re.compile(
    r"([A-Z][a-z]+(?: [A-Z][a-z]+){0,2} Police Department)$"
)
_TRAILING_SHERIFF_RE = re.compile(
    r"([A-Z][a-z]+(?: [A-Z][a-z]+){0,2} County Sheriff's Office)$"
)

_EXPLICIT_ALIASES = {
    "havre police department": ("Havre Police Department", "police"),
    "flathead sheriff's office": ("Flathead County Sheriff's Office", "sheriff"),
    "flathead county sheriff": ("Flathead County Sheriff's Office", "sheriff"),
    "flathead county sheriff's office": ("Flathead County Sheriff's Office", "sheriff"),
}

_JUNK_MARKERS = (
    "needed the phone number",
    "has not been served",
)
_LEADING_STOPWORDS = {"for", "the", "a", "an"}


def _clean_text(value: Optional[str]) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip()).strip(" -")


def _title_case_phrase(value: str) -> str:
    cleaned = _COUNTY_JOIN_RE.sub(r"\1 County", _clean_text(value))
    if not cleaned:
        return ""

    def _fix_word(match: re.Match[str]) -> str:
        word = match.group(0)
        lowered = word.lower()
        if lowered in {"pd", "fd", "mtu", "msu"}:
            return lowered.upper()
        return word[:1].upper() + word[1:].lower()

    titled = _TITLE_WORD_RE.sub(_fix_word, cleaned)
    titled = titled.replace("Sheriff'S", "Sheriff's")
    titled = titled.replace("Sheriffs Office", "Sheriff's Office")
    return titled


def _slugify_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _clean_text(value).lower()).strip("-")


def _county_police_slug_keys(county: str) -> set[str]:
    county_slug = _slugify_key(county)
    return {
        f"{county_slug}-police",
        f"{county_slug}-police-department",
        f"{county_slug}-county-police",
        f"{county_slug}-county-police-department",
    }


def _strip_leading_stopwords(value: str) -> str:
    words = value.split()
    while len(words) > 3 and words[0].lower() in _LEADING_STOPWORDS:
        words.pop(0)
    return " ".join(words)


def normalize_agency_identity(
    agency_name: Optional[str],
    agency_type: Optional[str] = None,
    *,
    county: Optional[str] = None,
    city: Optional[str] = None,
) -> tuple[str, str]:
    raw_name = _clean_text(agency_name)
    raw_type = (agency_type or "").strip().lower()
    county_name = _title_case_phrase(county or "")
    city_name = _title_case_phrase(city or "")

    alias_match = _EXPLICIT_ALIASES.get(raw_name.lower())
    if alias_match:
        return alias_match

    if raw_name:
        normalized_name = _title_case_phrase(raw_name)
        if any(marker in normalized_name.lower() for marker in _JUNK_MARKERS):
            police_match = _TRAILING_POLICE_RE.search(normalized_name)
            if police_match:
                normalized_name = _strip_leading_stopwords(police_match.group(1))
                raw_type = "police"
            else:
                sheriff_match = _TRAILING_SHERIFF_RE.search(normalized_name)
                if sheriff_match:
                    normalized_name = _strip_leading_stopwords(sheriff_match.group(1))
                    raw_type = "sheriff"
                else:
                    normalized_name = ""
        raw_name = normalized_name

    if raw_name:
        raw_slug = _slugify_key(raw_name)
        alias_match = _EXPLICIT_ALIASES.get(raw_name.lower())
        if alias_match:
            return alias_match

        if county_name and raw_slug in _county_police_slug_keys(county_name):
            if city_name:
                return f"{city_name} Police Department", "police"
            return f"{county_name} County Sheriff's Office", "sheriff"

        if county_name and "sheriff" in raw_name.lower():
            return f"{county_name} County Sheriff's Office", "sheriff"
        if city_name and "police" in raw_name.lower():
            return f"{city_name} Police Department", "police"

        inferred_type = raw_type
        if "sheriff" in raw_name.lower():
            inferred_type = "sheriff"
        elif "police" in raw_name.lower():
            inferred_type = "police"
        return raw_name, inferred_type or "other"

    if raw_type == "sheriff" and county_name:
        return f"{county_name} County Sheriff's Office", "sheriff"
    if raw_type == "police":
        if city_name:
            return f"{city_name} Police Department", "police"
        if county_name:
            return f"{county_name} County Sheriff's Office", "sheriff"

    return "", raw_type or "other"


def normalize_post_title(
    title: Optional[str],
    raw_agency_name: Optional[str],
    normalized_agency_name: str,
    *,
    county: Optional[str] = None,
    city: Optional[str] = None,
    agency_type: Optional[str] = None,
) -> str:
    cleaned_title = _clean_text(title)
    if not cleaned_title or not normalized_agency_name:
        return cleaned_title

    updated = cleaned_title
    raw_name = _clean_text(raw_agency_name)
    county_name = _title_case_phrase(county or "")
    city_name = _title_case_phrase(city or "")
    title_type = (agency_type or "").strip().lower()

    if raw_name:
        updated = re.sub(
            re.escape(raw_name),
            normalized_agency_name,
            updated,
            flags=re.IGNORECASE,
        )

    replacements = []
    if county_name:
        replacements.extend(
            [
                f"{county_name} Police",
                f"{county_name} Police Department",
                f"{county_name} County Police",
                f"{county_name} County Police Department",
                f"{county_name} Sheriff's Office",
            ]
        )
    if city_name and title_type == "police":
        replacements.append(f"{county_name} County Police")

    for source in replacements:
        if source:
            updated = re.sub(
                re.escape(source),
                normalized_agency_name,
                updated,
                flags=re.IGNORECASE,
            )

    if updated == cleaned_title and (
        any(marker in raw_name.lower() for marker in _JUNK_MARKERS)
        or raw_name.lower() != normalized_agency_name.lower()
    ):
        updated = re.sub(
            r"^(Daily(?: Police)? Activity Report)\s+[–-]\s+.*$",
            rf"\1 - {normalized_agency_name}",
            cleaned_title,
        )

    return updated


def normalize_existing_post_agencies(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT id, title, agency_name, agency_type, county, city
        FROM posts
        """
    ).fetchall()

    updates = []
    for row in rows:
        normalized_name, normalized_type = normalize_agency_identity(
            row["agency_name"],
            row["agency_type"],
            county=row["county"],
            city=row["city"],
        )
        normalized_title = normalize_post_title(
            row["title"],
            row["agency_name"],
            normalized_name,
            county=row["county"],
            city=row["city"],
            agency_type=normalized_type,
        )

        next_name = normalized_name if normalized_name else _clean_text(row["agency_name"])
        next_type = normalized_type or (row["agency_type"] or "other")
        next_title = normalized_title or _clean_text(row["title"])

        if (
            next_name != (row["agency_name"] or "")
            or next_type != (row["agency_type"] or "")
            or next_title != (row["title"] or "")
        ):
            updates.append((next_name, next_type, next_title, row["id"]))

    if not updates:
        return 0

    conn.executemany(
        """
        UPDATE posts
        SET agency_name = ?, agency_type = ?, title = ?
        WHERE id = ?
        """,
        updates,
    )
    return len(updates)
