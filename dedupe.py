import re
from datetime import datetime


def _get_value(item, key, default=""):
    if item is None:
        return default
    if isinstance(item, dict):
        return item.get(key, default)
    try:
        return item[key]
    except (KeyError, IndexError, TypeError):
        return getattr(item, key, default)


def normalize_text(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_date(value: str) -> str:
    raw = (value or "").strip()
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    fallback = normalize_text(raw)
    return fallback if len(fallback) >= 4 else ""


def normalize_time(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue
    match = re.search(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\b", raw)
    if match:
        return f"{int(match.group(1)):02d}:{match.group(2)}"
    fallback = normalize_text(raw)
    return fallback if len(fallback) >= 3 else ""


def incident_keys(item, county: str | None = None) -> set[str]:
    county_value = county if county is not None else _get_value(item, "county", "")
    county_key = normalize_text(county_value)
    cfs_number = normalize_text(_get_value(item, "cfs_number", ""))
    date_key = normalize_date(_get_value(item, "date", ""))
    time_key = normalize_time(_get_value(item, "time", ""))
    incident_type = normalize_text(
        _get_value(item, "incident_type", "") or _get_value(item, "incident", "")
    )
    location = normalize_text(_get_value(item, "location", ""))
    details = normalize_text(_get_value(item, "details", ""))

    keys = set()
    if cfs_number:
        keys.add(f"cfs|{county_key}|{cfs_number}")

    if county_key and date_key and incident_type and location:
        keys.add(f"sig|{county_key}|{date_key}|{time_key}|{incident_type}|{location}")
        if not time_key and details:
            keys.add(f"sigd|{county_key}|{date_key}|{incident_type}|{location}|{details[:80]}")

    return keys


def incident_key_set(items, county: str | None = None) -> set[str]:
    if not isinstance(items, (list, tuple)):
        return set()
    keys = set()
    for item in items or []:
        keys.update(incident_keys(item, county=county))
    return keys
