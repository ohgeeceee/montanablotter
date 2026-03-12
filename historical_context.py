import re
from typing import Iterable, Mapping, Optional


_HISTORICAL_HEADING = "// Historical Perspective:"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _field_value(record: Mapping[str, object], field: str) -> str:
    if record is None:
        return ""
    try:
        value = record[field]
    except Exception:
        value = getattr(record, field, "")
    return str(value or "")


def _combined_record_text(records: Iterable[Mapping[str, object]]) -> str:
    parts = []
    for record in records or ():
        parts.append(
            " ".join(
                _field_value(record, field)
                for field in ("incident_type", "incident", "location", "details", "county", "officer", "cfs_number")
            )
        )
    return _normalize_text(" ".join(parts))


def _is_rural_context(text: str, county: str, agency_name: str, agency_type: str) -> bool:
    rural_markers = (
        "ranch",
        "pasture",
        "grazing",
        "county road",
        "forest service",
        "state land",
        "blm",
        "trailhead",
        "farm",
        "barn",
        "gate",
        "fence",
        "acre",
    )
    county_text = _normalize_text(county)
    agency_text = _normalize_text(agency_name)
    return (
        _contains_any(text, rural_markers)
        or agency_type == "sheriff"
        or "sheriff" in agency_text
        or "county" in county_text
    )


def _historical_perspective_text(text: str, county: str = "", agency_name: str = "", agency_type: str = "") -> str:
    rural = _is_rural_context(text, county, agency_name, agency_type)

    if _contains_any(text, ("fraud", "forgery", "counterfeit", "embezzl", "brib", "corrupt", "scam", "deceptive", "bad check")):
        return (
            "Fraud and public-integrity cases in Montana are often read against the Copper Kings era, "
            "when mining money distorted state politics and helped produce the 1912 Corrupt Practices Act."
        )

    if _contains_any(text, ("dui", "d.u.i", "driving under the influence", "drunk driving", "driving while intoxicated", "bac", "alcohol", "intoxicated driver")):
        return (
            "Montana's alcohol enforcement history is unusual: the state stepped back from Prohibition enforcement in 1926, "
            "and impaired driving was often treated as a 'folk crime' until stricter 1983 reforms reshaped DUI law."
        )

    if _contains_any(text, ("trespass", "criminal trespass", "property dispute", "land dispute", "unlawful entry", "fence", "gate", "grazing")):
        if rural:
            return (
                "This property-access report echoes Montana's long shift from the 1864 Territorial open-range assumptions to later multiple-use land rules, "
                "and in rural counties it also sits against the state's checkerboard pattern of railroad, state, and federal parcels."
            )
        return (
            "This property-access report echoes Montana's long shift from the 1864 Territorial open-range assumptions "
            "to the later land-use rules that hardened trespass and access boundaries."
        )

    if _contains_any(text, ("disorderly", "disturbance", "public order", "breach of peace", "riot", "affray", "unlawful assembly", "noise complaint", "fight", "public intoxication")):
        return (
            "Public-order enforcement in Montana developed partly in reaction to the 1863-1864 Vigilante era, "
            "and the arrival of Territorial judges such as Hezekiah Hosmer helped move those disputes from vigilante action into formal courts."
        )

    theft_keywords = ("theft", "larceny", "stolen", "vehicle theft", "motor vehicle theft", "unauthorized use", "livestock theft")
    if _contains_any(text, theft_keywords):
        if _contains_any(text, ("cattle", "livestock", "horse", "calf", "brand inspection", "ranch stock")):
            return (
                "Montana theft law was shaped early by stock-stealing conflicts associated with outfits such as Stuart's Stranglers, "
                "when livestock theft was treated as a frontier threat to the territorial economy."
            )
        if _contains_any(text, ("vehicle", "car", "truck", "pickup", "atv", "motorcycle", "trailer")):
            return (
                "Montana theft anxieties moved from livestock to motor vehicles in the twentieth century, "
                "including early headline cases such as Missoula's 1936 police-car theft."
            )

    return ""


def append_historical_perspective(
    summary: str,
    *,
    records: Optional[Iterable[Mapping[str, object]]] = None,
    raw_text: str = "",
    county: str = "",
    agency_name: str = "",
    agency_type: str = "",
) -> str:
    original = (summary or "").strip()
    base = original
    existing_perspective = ""
    if _HISTORICAL_HEADING in original:
        base, existing_perspective = original.split(_HISTORICAL_HEADING, 1)
        base = base.rstrip()
        existing_perspective = existing_perspective.strip()

    combined_text = _normalize_text(raw_text)
    if not combined_text and records is not None:
        combined_text = _combined_record_text(records)
    perspective = _historical_perspective_text(
        combined_text,
        county=county,
        agency_name=agency_name,
        agency_type=agency_type,
    )
    if not perspective:
        return original
    if existing_perspective:
        perspective = existing_perspective
    if not base:
        return f"{_HISTORICAL_HEADING} {perspective}"
    return f"{base}\n\n{_HISTORICAL_HEADING} {perspective}"
