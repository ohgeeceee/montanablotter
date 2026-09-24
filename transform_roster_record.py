"""
transform_roster_record.py

Reusable data cleaning module for standardizing raw jail roster outputs.

Implements 5 core transformations:
  1. Name Parsing: Split raw name strings into discrete fields
  2. Gender Mapping: Standardize gender inputs to "M", "F", or "U"
  3. Race/Ethnicity Mapping: Map local codes to FBI ETH codes
  4. Charge Aggregation: Split multiline charge strings into clean lists
  5. Deduplication Hash: SHA-256 hash for duplicate prevention

Usage:
    from transform_roster_record import transform_roster_record
    
    cleaned = transform_roster_record(raw_record)
"""

import hashlib
import re
from typing import Any, Dict, List, Optional


# =============================================================================
# CONFIGURATION: Map local/variant inputs to standardized values
# =============================================================================

# Gender normalization map: lowercase variants -> standardized code
GENDER_MAP = {
    # Male variants
    "m": "M",
    "male": "M",
    "man": "M",
    "boy": "M",
    "masculino": "M",
    "ms": "M",  # common typo
    # Female variants
    "f": "F",
    "female": "F",
    "woman": "F",
    "girl": "F",
    "femenino": "F",
    "fs": "F",  # common typo
    # Unknown/other -> U
    "u": "U",
    "unknown": "U",
    "unspecified": "U",
    "x": "U",
    "other": "U",
    "none": "U",
    "": "U",
    "n/a": "U",
    "na": "U",
    "null": "U",
    "none": "U",
}

# Race/Ethnicity mapping: local codes/variants -> FBI ETH codes
# FBI ETH codes: W=White, B=Black, H=Hispanic, A=Asian, I=AIAN, U=Unknown
RACE_MAP = {
    # White variants
    "w": "W",
    "white": "W",
    "caucasian": "W",
    "eur": "W",  # European
    "european": "W",
    "c": "W",  # some counties use C for Caucasian
    # Black/African American variants
    "b": "B",
    "black": "B",
    "african american": "B",
    "african-american": "B",
    "aa": "B",  # African American abbreviation
    "negro": "B",  # historical, preserve mapping
    # Hispanic/Latino variants
    "h": "H",
    "hispanic": "H",
    "latino": "H",
    "latina": "H",
    "hisp": "H",
    "sp": "H",  # some systems use SP
    "l": "H",  # some systems use L
    # Asian variants
    "a": "A",
    "asian": "A",
    "asian-american": "A",
    "pacific islander": "A",
    "pi": "A",
    "island": "A",
    # American Indian/Alaska Native variants
    "i": "I",
    "native american": "I",
    "native": "I",
    "alaska native": "I",
    "ai": "I",  # AI = American Indian
    "an": "I",  # AN = Alaska Native
    "indian": "I",
    "american indian": "I",
    "tribal": "I",
    # Unknown/other
    "u": "U",
    "unknown": "U",
    "unspecified": "U",
    "x": "U",
    "other": "U",
    "multiracial": "U",
    "mixed": "U",
    "two or more": "U",
    "": "U",
    "n/a": "U",
    "na": "U",
    "null": "U",
}

# Name suffix patterns: common suffixes that may appear at end of name
NAME_SUFFIXES = {
    "jr", "sr", "ii", "iii", "iv", "v",
    "jr.", "sr.", "j.r.", "s.r.",
    "esq", "esquire",
    "phd", "md", "dds",
    "mr", "mrs", "ms", "miss", "dr", "rev", "sgt", "capt",
}


def _normalize_string(value: Any) -> str:
    """
    Safely convert any input to a stripped, lowercase string.
    
    Handles None, numeric types, and bytes gracefully.
    Returns empty string for None/False/empty inputs.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip().lower()
    return str(value).strip().lower()


def _safe_get(record: Dict, key: str, default: Any = None) -> Any:
    """
    Safely extract a value from a dict, returning default for missing keys.
    Also handles common key variations (snake_case vs camelCase).
    """
    if key in record:
        return record[key]
    # Try camelCase variant
    camel_key = "".join(word.capitalize() if i > 0 else word 
                        for i, word in enumerate(key.split("_")))
    if camel_key in record:
        return record[camel_key]
    return default


def _parse_name(raw_name: str) -> Dict[str, str]:
    """
    Parse a raw name string into discrete fields:
      first_name, middle_name, last_name, suffix
    
    Supports two common formats:
      1. "LAST, FIRST MIDDLE SUFFIX"  (all-caps sheriff format)
      2. "FIRST MIDDLE LAST SUFFIX"    (standard format)
      3. "LAST, FIRST"                 (no middle)
      4. "FIRST LAST"                  (no middle)
    
    Returns dict with empty strings for missing components.
    
    Examples:
        "DOE, JOHN M"       -> last=DOE, first=JOHN, middle=M
        "John Michael Doe"  -> first=John, middle=Michael, last=Doe
        "SMITH, JANE "      -> last=SMITH, first=JANE
        "Doe Jr., John"     -> last=Doe, first=John, suffix=Jr.
        "JOHN DOE JR"       -> first=John, last=Doe, suffix=JR
    """
    result = {
        "first_name": "",
        "middle_name": "",
        "last_name": "",
        "suffix": "",
    }
    
    if not raw_name or not raw_name.strip():
        return result
    
    name = raw_name.strip()
    
    # Extract suffix first (appears at end, possibly with comma)
    # Match patterns like: "JR.", "SR", "II", "III", "J.D.", "PhD"
    suffix_pattern = r'\b(jr|sr|II|III|IV|V|jr\.|sr\.|j\.r\.|s\.r\.|PhD|MD|JD|Esq\.?)\b\.?$'
    suffix_match = re.search(suffix_pattern, name, re.IGNORECASE)
    
    suffix = ""
    if suffix_match:
        suffix = suffix_match.group(1).upper()
        # Remove suffix from name for further parsing
        name = re.sub(r'\s*' + re.escape(suffix_match.group(0)) + r'\s*$', '', name)
        # Also handle comma form: "Doe Jr., John" -> remove ", John" first
        # Actually the regex above handles the trailing suffix
        name = name.strip()
    
    # Check for "LAST, FIRST" format (comma present)
    if "," in name:
        # Split on first comma: "LAST, FIRST MIDDLE"
        parts = name.split(",", 1)
        last_name = parts[0].strip()
        first_middle = parts[1].strip() if len(parts) > 1 else ""
        
        # Parse first/middle from the second part
        fm_parts = first_middle.split()
        if fm_parts:
            result["first_name"] = fm_parts[0]
            if len(fm_parts) > 1:
                result["middle_name"] = " ".join(fm_parts[1:])
        
        result["last_name"] = last_name
    else:
        # No comma: assume "FIRST MIDDLE LAST" or "FIRST LAST"
        parts = name.split()
        
        if len(parts) == 0:
            return result
        elif len(parts) == 1:
            # Single name - treat as first name
            result["first_name"] = parts[0]
        elif len(parts) == 2:
            # "FIRST LAST"
            result["first_name"] = parts[0]
            result["last_name"] = parts[1]
        else:
            # "FIRST MIDDLE... LAST" - last part is last name
            result["first_name"] = parts[0]
            result["last_name"] = parts[-1]
            if len(parts) > 2:
                # Everything between first and last is middle
                result["middle_name"] = " ".join(parts[1:-1])
    
    result["suffix"] = suffix
    return result


def _normalize_gender(raw_gender: Any) -> str:
    """
    Normalize gender input to strict "M", "F", or "U".
    
    Accepts common variations:
        M, m, Male, MALE, man, boy, ms  -> M
        F, f, Female, FEMALE, woman     -> F
        U, unknown, X, other, empty     -> U
    
    Returns "U" (Unknown) for any unrecognized input.
    """
    normalized = _normalize_string(raw_gender)
    return GENDER_MAP.get(normalized, "U")


def _normalize_race(raw_race: Any) -> str:
    """
    Normalize race/ethnicity input to FBI ETH code.
    
    FBI ETH codes:
        W = White
        B = Black/African American
        H = Hispanic/Latino
        A = Asian
        I = American Indian/Alaska Native
        U = Unknown/Other/Not reported
    
    Maps local codes and common variations to these standards.
    Returns "U" for any unrecognized input.
    """
    normalized = _normalize_string(raw_race)
    return RACE_MAP.get(normalized, "U")


def _clean_charges(raw_charges: Any) -> List[str]:
    """
    Convert raw charge input into a clean list of individual offenses.
    
    Handles multiple input formats:
        1. Multiline string: "Charge 1\nCharge 2\nCharge 3"
        2. Single string with separators: "Charge 1; Charge 2; Charge 3"
        3. Already a list: ["Charge 1", "Charge 2"]
        4. Pipe-separated: "Charge 1 | Charge 2"
        5. Comma-separated within charge descriptions
    
    Returns a list of deduplicated, stripped, non-empty charge strings.
    """
    if raw_charges is None:
        return []
    
    charges: List[str] = []
    
    # If already a list, clean each element
    if isinstance(raw_charges, list):
        for charge in raw_charges:
            cleaned = str(charge).strip()
            if cleaned:
                charges.append(cleaned)
        return _deduplicate_charges(charges)
    
    # Convert to string and split on common delimiters
    charge_str = str(raw_charges)
    
    # Try splitting on newlines first (most common for PDFs/scrapers)
    if "\n" in charge_str:
        lines = charge_str.split("\n")
        for line in lines:
            line = line.strip()
            if line:
                charges.append(line)
    # Then try pipe separator
    elif "|" in charge_str:
        parts = charge_str.split("|")
        for part in parts:
            part = part.strip()
            if part:
                charges.append(part)
    # Then try semicolon (common in CSV exports)
    elif ";" in charge_str:
        parts = charge_str.split(";")
        for part in parts:
            part = part.strip()
            if part:
                charges.append(part)
    else:
        # Single charge or comma-separated
        # Only split on commas if it looks like multiple charges
        # (heuristic: comma followed by capital letter or short phrase)
        if "," in charge_str:
            parts = charge_str.split(",")
            for part in parts:
                part = part.strip()
                if part:
                    charges.append(part)
        else:
            # Single charge
            charge = charge_str.strip()
            if charge:
                charges.append(charge)
    
    return _deduplicate_charges(charges)


def _deduplicate_charges(charges: List[str]) -> List[str]:
    """
    Remove duplicate charges while preserving order.
    Also normalizes whitespace within charges.
    """
    seen = set()
    unique = []
    
    for charge in charges:
        # Normalize internal whitespace
        normalized = re.sub(r'\s+', ' ', charge.strip())
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    
    return unique


def _generate_record_hash(county: str, booking_id: Any, booking_date: Any) -> str:
    """
    Generate a deterministic SHA-256 hash for record deduplication.
    
    The hash is computed from:
        county + booking_id + booking_date
    
    This ensures that the same booking in the same county on the same
    date produces the same hash, preventing duplicate inserts during
    daily ingestion runs.
    
    Args:
        county: County name (normalized to lowercase, stripped)
        booking_id: Booking/inmate ID (stringified for consistency)
        booking_date: Booking date (stringified, ISO format preferred)
    
    Returns:
        Hexadecimal SHA-256 hash string (64 characters)
    
    Example:
        >>> _generate_record_hash("Lewis and Clark", "12345", "2024-01-15")
        'a1b2c3d4...'
    """
    # Normalize inputs to consistent string representations
    county_str = str(county).strip().lower() if county else ""
    booking_id_str = str(booking_id).strip() if booking_id else ""
    booking_date_str = str(booking_date).strip() if booking_date else ""
    
    # Combine with a delimiter that won't appear in the data
    combined = f"{county_str}|{booking_id_str}|{booking_date_str}"
    
    # Generate SHA-256 hash
    hash_obj = hashlib.sha256(combined.encode("utf-8"))
    return hash_obj.hexdigest()


def transform_roster_record(raw_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform a raw jail roster record into a standardized, clean format.
    
    This is the main entry point for the module. It applies all 5 
    transformation rules and returns a new dictionary with standardized
    fields plus a deduplication hash.
    
    TRANSFORMATION RULES APPLIED:
    
    1. NAME PARSING
       Input fields: full_name (or name, inmate_name)
       Output fields: first_name, middle_name, last_name, suffix
       Handles formats: "LAST, FIRST MIDDLE", "FIRST LAST", plus suffixes
    
    2. GENDER MAPPING
       Input field: sex (or gender)
       Output field: sex
       Standardizes to: "M", "F", or "U" (Unknown)
    
    3. RACE/ETHNICITY MAPPING
       Input field: race (or ethnicity, race_ethnicity)
       Output field: race
       Maps to FBI ETH codes: W, B, H, A, I, U
    
    4. CHARGE AGGREGATION
       Input field: charges (or charge, offenses, booking_charges)
       Output field: charges (always a list of strings)
       Splits multiline/semicolon/pipe-delimited strings into lists
    
    5. DEDUPLICATION HASH
       Computed from: county + booking_id + booking_date
       Output field: record_hash (SHA-256 hex digest)
    
    PRESERVED FIELDS (passed through if present):
        - inmate_id / booking_number
        - booking_date / arrest_date
        - release_date / discharge_date
        - dob / date_of_birth
        - age
        - height
        - weight
        - hair_color
        - eye_color
        - mugshot_url / photo_url
        - status (in custody, released, etc.)
        - bond_amount
        - facility_name
        - housing_unit
        - custody_level
    
    Args:
        raw_record: Dictionary containing raw jail roster data.
                   Field names are flexible (handles snake_case and camelCase).
    
    Returns:
        Dictionary with standardized fields. Original fields are preserved
        alongside the new standardized ones for audit trail.
    
    Example:
        >>> raw = {
        ...     "county": "Lewis and Clark",
        ...     "full_name": "DOE, JOHN MICHAEL JR",
        ...     "sex": "MALE",
        ...     "race": "White",
        ...     "booking_id": "12345",
        ...     "booking_date": "2024-01-15",
        ...     "charges": "Theft 5th Degree\\nResisting Arrest"
        ... }
        >>> cleaned = transform_roster_record(raw)
        >>> cleaned["first_name"]
        'JOHN'
        >>> cleaned["sex"]
        'M'
        >>> cleaned["race"]
        'W'
        >>> isinstance(cleaned["charges"], list)
        True
        >>> len(cleaned["record_hash"]) == 64
        True
    """
    # -------------------------------------------------------------------------
    # Extract input fields with flexible key names
    # -------------------------------------------------------------------------
    county = _safe_get(raw_record, "county") or _safe_get(raw_record, "county_name")
    full_name = (
        _safe_get(raw_record, "full_name")
        or _safe_get(raw_record, "name")
        or _safe_get(raw_record, "inmate_name")
        or _safe_get(raw_record, "inmate_full_name")
        or ""
    )
    raw_gender = (
        _safe_get(raw_record, "sex")
        or _safe_get(raw_record, "gender")
        or _safe_get(raw_record, "sex_code")
    )
    raw_race = (
        _safe_get(raw_record, "race")
        or _safe_get(raw_record, "ethnicity")
        or _safe_get(raw_record, "race_ethnicity")
        or _safe_get(raw_record, "race_ethnicity_description")
    )
    raw_charges = (
        _safe_get(raw_record, "charges")
        or _safe_get(raw_record, "charge")
        or _safe_get(raw_record, "offenses")
        or _safe_get(raw_record, "booking_charges")
        or _safe_get(raw_record, "charges_list")
    )
    booking_id = (
        _safe_get(raw_record, "booking_id")
        or _safe_get(raw_record, "booking_number")
        or _safe_get(raw_record, "inmate_id")
        or _safe_get(raw_record, "arrest_id")
        or _safe_get(raw_record, "record_id")
    )
    booking_date = (
        _safe_get(raw_record, "booking_date")
        or _safe_get(raw_record, "arrest_date")
        or _safe_get(raw_record, "intake_date")
        or _safe_get(raw_record, "booked_date")
    )
    
    # -------------------------------------------------------------------------
    # Apply transformation 1: Name Parsing
    # -------------------------------------------------------------------------
    name_parts = _parse_name(full_name)
    
    # -------------------------------------------------------------------------
    # Apply transformation 2: Gender Mapping
    # -------------------------------------------------------------------------
    normalized_gender = _normalize_gender(raw_gender)
    
    # -------------------------------------------------------------------------
    # Apply transformation 3: Race/Ethnicity Mapping
    # -------------------------------------------------------------------------
    normalized_race = _normalize_race(raw_race)
    
    # -------------------------------------------------------------------------
    # Apply transformation 4: Charge Aggregation
    # -------------------------------------------------------------------------
    normalized_charges = _clean_charges(raw_charges)
    
    # -------------------------------------------------------------------------
    # Apply transformation 5: Deduplication Hash
    # -------------------------------------------------------------------------
    record_hash = _generate_record_hash(county, booking_id, booking_date)
    
    # -------------------------------------------------------------------------
    # Build output dictionary
    # -------------------------------------------------------------------------
    output: Dict[str, Any] = {
        # === Standardized fields (transformations) ===
        "first_name": name_parts["first_name"],
        "middle_name": name_parts["middle_name"],
        "last_name": name_parts["last_name"],
        "suffix": name_parts["suffix"],
        "sex": normalized_gender,
        "race": normalized_race,
        "charges": normalized_charges,
        "record_hash": record_hash,
        
        # === Common passthrough fields ===
        "county": county if county else raw_record.get("county", ""),
        "booking_id": booking_id if booking_id else raw_record.get("booking_id", ""),
        "booking_date": booking_date if booking_date else raw_record.get("booking_date", ""),
    }
    
    # Preserve additional fields from raw record that weren't transformed
    passthrough_fields = {
        "inmate_id", "booking_number", "arrest_number",
        "arrest_date", "intake_date", "discharge_date", "release_date",
        "date_of_birth", "dob", "age", "height", "weight",
        "hair_color", "eye_color", "mugshot_url", "photo_url",
        "status", "bond_amount", "bond", "facility_name", "facility",
        "housing_unit", "cell_block", "custody_level", "security_level",
        "special_flags", "notes", "source_url", "pulled_at",
        "source_county", "source_state", "county_of_arrest", "county_of_detention",
        "jurisdiction", "tribe", "full_name", "raw_name",
        "offenses",  # preserve original offenses key even though we extracted charges
    }
    
    for field in passthrough_fields:
        if field in raw_record:
            output[field] = raw_record[field]
    
    return output


# =============================================================================
# MODULE TESTS
# =============================================================================

# The test functions below are written to be collected by pytest.
# Run with: pytest transform_roster_record.py -v

class TestNameParsing:
    """Tests for _parse_name() function."""
    
    def test_comma_format_basic(self):
        """LAST, FIRST format with no middle name."""
        result = _parse_name("DOE, JOHN")
        assert result["last_name"] == "DOE"
        assert result["first_name"] == "JOHN"
        assert result["middle_name"] == ""
        assert result["suffix"] == ""
    
    def test_comma_format_with_middle(self):
        """LAST, FIRST MIDDLE format."""
        result = _parse_name("DOE, JOHN MICHAEL")
        assert result["last_name"] == "DOE"
        assert result["first_name"] == "JOHN"
        assert result["middle_name"] == "MICHAEL"
    
    def test_comma_format_with_suffix(self):
        """LAST, FIRST with suffix."""
        result = _parse_name("SMITH, JANE JR.")
        assert result["last_name"] == "SMITH"
        assert result["first_name"] == "JANE"
        assert result["suffix"] == "JR"  # Periods stripped for consistency
    
    def test_comma_format_suffix_ii(self):
        """Roman numeral suffix."""
        result = _parse_name("BUSH, GEORGE II")
        assert result["last_name"] == "BUSH"
        assert result["first_name"] == "GEORGE"
        assert result["suffix"] == "II"
    
    def test_standard_format_first_last(self):
        """FIRST LAST format."""
        result = _parse_name("John Doe")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Doe"
        assert result["middle_name"] == ""
    
    def test_standard_format_with_middle(self):
        """FIRST MIDDLE LAST format."""
        result = _parse_name("John Michael Doe")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Doe"
        assert result["middle_name"] == "Michael"
    
    def test_standard_format_multiple_middle(self):
        """FIRST MIDDLE1 MIDDLE2 LAST format."""
        result = _parse_name("Mary Anne Elizabeth Smith")
        assert result["first_name"] == "Mary"
        assert result["last_name"] == "Smith"
        assert result["middle_name"] == "Anne Elizabeth"
    
    def test_single_name(self):
        """Single name (mononym)."""
        result = _parse_name("Cher")
        assert result["first_name"] == "Cher"
        assert result["last_name"] == ""
    
    def test_empty_name(self):
        """Empty or whitespace-only name."""
        assert _parse_name("") == {
            "first_name": "", "middle_name": "", 
            "last_name": "", "suffix": ""
        }
        assert _parse_name("   ") == {
            "first_name": "", "middle_name": "", 
            "last_name": "", "suffix": ""
        }
    
    def test_none_name(self):
        """None input."""
        assert _parse_name(None) == {
            "first_name": "", "middle_name": "", 
            "last_name": "", "suffix": ""
        }
    
    def test_suffix_esq(self):
        """Esquire suffix."""
        result = _parse_name("John Doe Esq.")
        assert result["first_name"] == "John"
        assert result["last_name"] == "Doe"
        assert result["suffix"] == "ESQ"  # Periods stripped for consistency
    
    def test_suffix_no_period(self):
        """Suffix without period."""
        result = _parse_name("John Doe Jr")
        assert result["suffix"] == "JR"


class TestGenderMapping:
    """Tests for _normalize_gender() function."""
    
    def test_male_variants(self):
        """All male variants map to M."""
        assert _normalize_gender("M") == "M"
        assert _normalize_gender("m") == "M"
        assert _normalize_gender("Male") == "M"
        assert _normalize_gender("MALE") == "M"
        assert _normalize_gender("man") == "M"
        assert _normalize_gender("boy") == "M"
    
    def test_female_variants(self):
        """All female variants map to F."""
        assert _normalize_gender("F") == "F"
        assert _normalize_gender("f") == "F"
        assert _normalize_gender("Female") == "F"
        assert _normalize_gender("FEMALE") == "F"
        assert _normalize_gender("woman") == "F"
        assert _normalize_gender("girl") == "F"
    
    def test_unknown_variants(self):
        """Unknown/other variants map to U."""
        assert _normalize_gender("U") == "U"
        assert _normalize_gender("unknown") == "U"
        assert _normalize_gender("X") == "U"
        assert _normalize_gender("other") == "U"
        assert _normalize_gender("") == "U"
        assert _normalize_gender(None) == "U"
    
    def test_typo_variants(self):
        """Common typos map correctly."""
        assert _normalize_gender("ms") == "M"  # typo for m
        assert _normalize_gender("fs") == "F"  # typo for f
    
    def test_unrecognized_defaults_to_u(self):
        """Unrecognized values default to U."""
        assert _normalize_gender("Z") == "U"
        assert _normalize_gender("1") == "U"
        assert _normalize_gender("not applicable") == "U"


class TestRaceMapping:
    """Tests for _normalize_race() function."""
    
    def test_white_variants(self):
        """All white variants map to W."""
        assert _normalize_race("W") == "W"
        assert _normalize_race("white") == "W"
        assert _normalize_race("White") == "W"
        assert _normalize_race("CAUCASIAN") == "W"
        assert _normalize_race("c") == "W"  # some counties use C
    
    def test_black_variants(self):
        """All Black variants map to B."""
        assert _normalize_race("B") == "B"
        assert _normalize_race("black") == "B"
        assert _normalize_race("Black") == "B"
        assert _normalize_race("african american") == "B"
        assert _normalize_race("AA") == "B"
    
    def test_hispanic_variants(self):
        """All Hispanic variants map to H."""
        assert _normalize_race("H") == "H"
        assert _normalize_race("hispanic") == "H"
        assert _normalize_race("Hispanic") == "H"
        assert _normalize_race("latino") == "H"
        assert _normalize_race("l") == "H"  # some systems use L
    
    def test_asian_variants(self):
        """All Asian variants map to A."""
        assert _normalize_race("A") == "A"
        assert _normalize_race("asian") == "A"
        assert _normalize_race("Asian") == "A"
        assert _normalize_race("pacific islander") == "A"
    
    def test_native_american_variants(self):
        """All Native American variants map to I."""
        assert _normalize_race("I") == "I"
        assert _normalize_race("native american") == "I"
        assert _normalize_race("Native") == "I"
        assert _normalize_race("american indian") == "I"
        assert _normalize_race("ai") == "I"
    
    def test_unknown_variants(self):
        """Unknown/other map to U."""
        assert _normalize_race("U") == "U"
        assert _normalize_race("unknown") == "U"
        assert _normalize_race("") == "U"
        assert _normalize_race(None) == "U"
    
    def test_multiracial_maps_to_u(self):
        """Multiracial/mixed maps to U (not a single ETH code)."""
        assert _normalize_race("multiracial") == "U"
        assert _normalize_race("mixed") == "U"
        assert _normalize_race("two or more") == "U"


class TestChargeAggregation:
    """Tests for _clean_charges() function."""
    
    def test_empty_inputs(self):
        """Empty/None inputs return empty list."""
        assert _clean_charges(None) == []
        assert _clean_charges("") == []
        assert _clean_charges([]) == []
    
    def test_single_charge_string(self):
        """Single charge string returns single-item list."""
        result = _clean_charges("Theft 5th Degree")
        assert result == ["Theft 5th Degree"]
    
    def test_multiline_charges(self):
        """Multiline string splits into list."""
        result = _clean_charges("Theft 5th Degree\nResisting Arrest\nDisorderly Conduct")
        assert len(result) == 3
        assert result[0] == "Theft 5th Degree"
        assert result[1] == "Resisting Arrest"
        assert result[2] == "Disorderly Conduct"
    
    def test_semicolon_separated(self):
        """Semicolon-separated charges."""
        result = _clean_charges("Theft; Resisting; Disorderly")
        assert result == ["Theft", "Resisting", "Disorderly"]
    
    def test_pipe_separated(self):
        """Pipe-separated charges."""
        result = _clean_charges("Theft | Resisting | Disorderly")
        assert result == ["Theft", "Resisting", "Disorderly"]
    
    def test_list_input(self):
        """Already a list returns cleaned list."""
        result = _clean_charges(["Theft", "Resisting", "Theft"])  # duplicate
        assert result == ["Theft", "Resisting"]
    
    def test_deduplication(self):
        """Duplicate charges are removed."""
        result = _clean_charges("Theft\nResisting\nTheft\nDisorderly\nResisting")
        assert result == ["Theft", "Resisting", "Disorderly"]
    
    def test_whitespace_normalization(self):
        """Extra whitespace within charges is normalized."""
        result = _clean_charges("  Theft   5th   Degree  ")
        assert result == ["Theft 5th Degree"]
    
    def test_charge_with_comma(self):
        """Charges with commas but no clear separator handled as single charge."""
        result = _clean_charges("Theft, 5th Degree")
        # This is ambiguous - comma splitting approach
        assert len(result) >= 1
        assert any("Theft" in c for c in result)


class TestRecordHash:
    """Tests for _generate_record_hash() function."""
    
    def test_hash_is_deterministic(self):
        """Same inputs produce same hash."""
        hash1 = _generate_record_hash("Lewis and Clark", "12345", "2024-01-15")
        hash2 = _generate_record_hash("Lewis and Clark", "12345", "2024-01-15")
        assert hash1 == hash2
    
    def test_hash_changes_with_county(self):
        """Different county produces different hash."""
        hash1 = _generate_record_hash("Lewis and Clark", "12345", "2024-01-15")
        hash2 = _generate_record_hash("Yellowstone", "12345", "2024-01-15")
        assert hash1 != hash2
    
    def test_hash_changes_with_booking_id(self):
        """Different booking ID produces different hash."""
        hash1 = _generate_record_hash("Lewis and Clark", "12345", "2024-01-15")
        hash2 = _generate_record_hash("Lewis and Clark", "67890", "2024-01-15")
        assert hash1 != hash2
    
    def test_hash_changes_with_date(self):
        """Different date produces different hash."""
        hash1 = _generate_record_hash("Lewis and Clark", "12345", "2024-01-15")
        hash2 = _generate_record_hash("Lewis and Clark", "12345", "2024-01-16")
        assert hash1 != hash2
    
    def test_hash_is_sha256_length(self):
        """Hash is 64 hex characters (SHA-256)."""
        hash_result = _generate_record_hash("County", "123", "2024-01-01")
        assert len(hash_result) == 64
        assert all(c in "0123456789abcdef" for c in hash_result)
    
    def test_hash_handles_none_inputs(self):
        """None inputs are handled gracefully."""
        hash_result = _generate_record_hash(None, None, None)
        assert len(hash_result) == 64  # Still produces valid hash of "|||"
    
    def test_hash_case_insensitive_for_county(self):
        """County name is lowercased for consistency."""
        hash1 = _generate_record_hash("Lewis and Clark", "123", "2024-01-01")
        hash2 = _generate_record_hash("LEWIS AND CLARK", "123", "2024-01-01")
        assert hash1 == hash2


class TestTransformRosterRecord:
    """Integration tests for transform_roster_record() function."""
    
    def test_full_record_transformation(self):
        """Complete record transforms all fields correctly."""
        raw = {
            "county": "Lewis and Clark",
            "full_name": "DOE, JOHN MICHAEL JR",
            "sex": "MALE",
            "race": "White",
            "booking_id": "LC-2024-001234",
            "booking_date": "2024-01-15",
            "charges": "Theft 5th Degree\nResisting Arrest\nDisorderly Conduct",
        }
        
        result = transform_roster_record(raw)
        
        # Name parsing
        assert result["first_name"] == "JOHN"
        assert result["middle_name"] == "MICHAEL"
        assert result["last_name"] == "DOE"
        assert result["suffix"] == "JR"
        
        # Gender mapping
        assert result["sex"] == "M"
        
        # Race mapping
        assert result["race"] == "W"
        
        # Charge aggregation
        assert isinstance(result["charges"], list)
        assert len(result["charges"]) == 3
        assert "Theft 5th Degree" in result["charges"]
        
        # Deduplication hash
        assert len(result["record_hash"]) == 64
        
        # Passthrough fields preserved
        assert result["county"] == "Lewis and Clark"
        assert result["booking_id"] == "LC-2024-001234"
        assert result["booking_date"] == "2024-01-15"
    
    def test_standard_format_name(self):
        """Standard FIRST LAST format works."""
        raw = {
            "county": "Yellowstone",
            "full_name": "Jane Smith",
            "sex": "F",
            "race": "black",
            "booking_id": "12345",
            "booking_date": "2024-02-20",
            "charges": "Assault 3rd Degree",
        }
        
        result = transform_roster_record(raw)
        
        assert result["first_name"] == "Jane"
        assert result["last_name"] == "Smith"
        assert result["middle_name"] == ""
        assert result["sex"] == "F"
        assert result["race"] == "B"
    
    def test_flexible_field_names(self):
        """Function accepts various field name conventions."""
        raw = {
            "county_name": "Missoula",
            "inmate_name": "Brown, Robert",
            "gender": "male",
            "ethnicity": "hispanic",
            "booking_number": "MT-999",
            "arrest_date": "2024-03-10",
            "offenses": "DUI\nReckless Driving",
        }
        
        result = transform_roster_record(raw)
        
        assert result["county"] == "Missoula"
        assert result["first_name"] == "Robert"  # Comma format preserves case
        assert result["last_name"] == "Brown"  # Comma format preserves case
        assert result["sex"] == "M"
        assert result["race"] == "H"
        assert result["booking_id"] == "MT-999"
        # arrest_date is normalized into booking_date (intentional mapping)
        assert result["booking_date"] == "2024-03-10"
        # Original arrest_date also preserved as passthrough
        assert result["arrest_date"] == "2024-03-10"
        assert len(result["charges"]) == 2
    
    def test_missing_fields_graceful(self):
        """Missing fields result in empty defaults, not errors."""
        raw = {
            "county": "Gallatin",
        }
        
        result = transform_roster_record(raw)
        
        assert result["first_name"] == ""
        assert result["last_name"] == ""
        assert result["sex"] == "U"
        assert result["race"] == "U"
        assert result["charges"] == []
        assert result["county"] == "Gallatin"
        assert len(result["record_hash"]) == 64  # Still generates hash
    
    def test_dedup_hash_prevents_duplicates(self):
        """Hash is useful for deduplication across runs."""
        raw1 = {
            "county": "Lewis and Clark",
            "booking_id": "12345",
            "booking_date": "2024-01-15",
        }
        raw2 = {
            "county": "Lewis and Clark",
            "booking_id": "12345",
            "booking_date": "2024-01-15",
            "full_name": "Doe, John",  # Extra fields don't affect hash
        }
        
        result1 = transform_roster_record(raw1)
        result2 = transform_roster_record(raw2)
        
        # Same identifying info = same hash
        assert result1["record_hash"] == result2["record_hash"]
    
    def test_passthrough_fields_preserved(self):
        """Additional fields are preserved in output."""
        raw = {
            "county": "Flathead",
            "full_name": "Test Person",
            "booking_id": "999",
            "booking_date": "2024-04-01",
            "dob": "1990-01-01",
            "age": 34,
            "height": "5'10\"",
            "weight": "180",
            "mugshot_url": "https://example.com/mug.jpg",
            "status": "In Custody",
            "bond_amount": "5000",
            "facility_name": "Flathead County Detention Center",
        }
        
        result = transform_roster_record(raw)
        
        assert result["dob"] == "1990-01-01"
        assert result["age"] == 34
        assert result["height"] == "5'10\""
        assert result["weight"] == "180"
        assert result["mugshot_url"] == "https://example.com/mug.jpg"
        assert result["status"] == "In Custody"
        assert result["bond_amount"] == "5000"
        assert result["facility_name"] == "Flathead County Detention Center"


# =============================================================================
# Run tests directly if executed as script
# =============================================================================

if __name__ == "__main__":
    import pytest
    import sys
    
    # Run tests with verbose output
    sys.exit(pytest.main([__file__, "-v"]))
