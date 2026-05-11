#!/usr/bin/env python3
"""
Montana Blotter Data Sanitizer
Transforms messy jail roster / police blotter data into clean structured records.

Usage:
    python3 sanitize.py /path/to/blotter.db [--dry-run] [--verbose]
"""

import sqlite3
import re
import sys
import json
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

# ── Montana County FIPS Codes ──────────────────────────────────────────
COUNTY_FIPS = {
    '1': 'Beaverhead', '01': 'Beaverhead',
    '3': 'Big Horn', '03': 'Big Horn',
    '5': 'Blaine', '05': 'Blaine',
    '7': 'Broadwater', '07': 'Broadwater',
    '9': 'Carbon', '09': 'Carbon',
    '11': 'Carter', '13': 'Cascade', '15': 'Chouteau',
    '17': 'Custer', '19': 'Daniels', '21': 'Dawson',
    '23': 'Deer Lodge', '25': 'Fallon', '27': 'Fergus',
    '29': 'Flathead', '31': 'Gallatin', '32': 'Gallatin',
    '33': 'Garfield', '35': 'Glacier', '37': 'Golden Valley',
    '39': 'Granite', '41': 'Hill', '43': 'Jefferson',
    '45': 'Judith Basin', '47': 'Lake', '49': 'Lewis and Clark',
    '51': 'Liberty', '53': 'Lincoln', '55': 'McCone',
    '57': 'Madison', '59': 'Meagher', '61': 'Mineral',
    '63': 'Missoula', '65': 'Musselshell', '67': 'Park',
    '69': 'Petroleum', '71': 'Phillips', '73': 'Pondera',
    '75': 'Powder River', '77': 'Powell', '79': 'Prairie',
    '81': 'Ravalli', '83': 'Richland', '85': 'Roosevelt',
    '87': 'Rosebud', '89': 'Sanders', '91': 'Sheridan',
    '93': 'Silver Bow', '95': 'Stillwater', '97': 'Sweet Grass',
    '99': 'Teton', '101': 'Toole', '103': 'Treasure',
    '105': 'Valley', '107': 'Wheatland', '109': 'Wibaux',
    '111': 'Yellowstone', '113': 'Yellowstone',
}

VALID_COUNTIES = set(COUNTY_FIPS.values())

# ── Statute Code Patterns ──────────────────────────────────────────────
STATUTE_PATTERNS = [
    (r'45-5-20[12]', 'ASSAULT', 'Felony'),
    (r'45-5-206', 'ASSAULT', 'Felony'),
    (r'45-5-210', 'ASSAULT', 'Felony'),
    (r'45-5-220', 'ASSAULT', 'Felony'),
    (r'45-5-30[234]', 'HOMICIDE', 'Felony'),
    (r'45-5-40[12]', 'ROBBERY', 'Felony'),
    (r'45-6-30[12345]', 'THEFT', 'Felony'),
    (r'45-6-310', 'THEFT', 'Misdemeanor'),
    (r'45-6-311', 'FRAUD', 'Misdemeanor'),
    (r'45-6-32[01]', 'BURGLARY', 'Felony'),
    (r'45-6-4', 'FRAUD', 'Felony'),
    (r'45-7-30[1234]', 'WEAPONS', 'Felony'),
    (r'45-7-40[12]', 'VANDALISM', 'Misdemeanor'),
    (r'45-5-50', 'SEX CRIME', 'Felony'),
    (r'45-9-10', 'NARCOTICS', 'Felony'),
    (r'45-9-130', 'NARCOTICS', 'Misdemeanor'),
    (r'61-8-40[16]', 'TRAFFIC', 'Misdemeanor'),
    (r'61-8-410', 'TRAFFIC', 'Felony'),
    (r'61-8-30', 'TRAFFIC', 'Misdemeanor'),
    (r'61-8-71', 'TRAFFIC', 'Misdemeanor'),
    (r'61-8-714', 'TRAFFIC', 'Felony'),
]

# ── Incident Type Keyword Mapping ──────────────────────────────────────
TYPE_KEYWORDS = {
    'THEFT': ['theft', 'shoplift', 'embezzle', 'larceny', 'steal', 'stolen'],
    'ASSAULT': ['assault', 'battery', 'strangulation', 'domestic'],
    'TRAFFIC': ['dui', 'driving', 'traffic', 'speed', 'way /', 'way/'],
    'NARCOTICS': ['drug', 'marijuana', 'meth', 'dangerous drug', 'possession of dangerous', 'heroin', 'cocaine', 'fentanyl'],
    'WEAPONS': ['weapon', 'firearm', 'gun', 'knife', 'ammunition'],
    'BURGLARY': ['burglary', 'break', 'enter', 'b & e'],
    'ROBBERY': ['robbery'],
    'FRAUD': ['forgery', 'fraud', 'credit card', 'deceptive', 'identity theft', 'counterfeit'],
    'SEX CRIME': ['sexual', 'rape', 'prostitution', 'indecent', 'sodomy', 'molest'],
    'WARRANT': ['warrant', 'fugitive', 'extradition', 'hold for'],
    'HOMICIDE': ['homicide', 'murder', 'manslaughter'],
    'DISTURBANCE': ['disturbance', 'disorderly', 'noise', 'harassment'],
    'VANDALISM': ['mischief', 'vandalism', 'damage', 'graffiti'],
    'WELFARE': ['welfare check', 'missing', 'runaway', 'mental health', 'suicide', 'overdose'],
    'PATROL': ['patrol', 'follow up', 'suspicious', 'check', 'hang up'],
}

# ── Location Corrections ───────────────────────────────────────────────
LOCATION_FIXES = {
    'st wpd': 'St. Ignatius',
    'st. wpd': 'St. Ignatius',
    'way just incase she took more': 'Whitefish',
    'unknown': '',
    'n/a': '',
    'mt': '',
}

# ── Bond Normalization ─────────────────────────────────────────────────
BOND_PATTERNS = [
    (r'\$?([\d,]+)(?:\.\d{2})?\s*(?:C/S|S/B|CASH|SURETY)?', 'dollar'),
    (r'\$?([\d]+)K', 'thousands'),
    (r'(?:NO\s*BOND|CASH\s*ONLY|NO\s*BAIL)', 'no_bond'),
    (r'(?:ROR|PR|P\.R\.|RELEASED|OR)', 'ror'),
]


def normalize_county(county: Optional[str]) -> str:
    """Convert county to proper name."""
    if not county:
        return 'Unknown'
    county = county.strip()
    if county.isdigit():
        return COUNTY_FIPS.get(county, 'Unknown')
    if county in VALID_COUNTIES:
        return county
    upper = county.upper()
    for valid in VALID_COUNTIES:
        if upper == valid.upper():
            return valid
    # Common misspellings
    fixes = {
        'GALLITIN': 'Gallatin', 'SWEETGRASS': 'Sweet Grass',
        'SILVERBOW': 'Silver Bow', 'DEERLODGE': 'Deer Lodge',
        'GOLDENVALLEY': 'Golden Valley', 'JUDITHBASIN': 'Judith Basin',
        'POWDERRIVER': 'Powder River', 'LEWIS & CLARK': 'Lewis and Clark',
    }
    return fixes.get(upper, county if len(county) > 2 else 'Unknown')


def normalize_location(location: Optional[str], county: str = '') -> str:
    """Clean location string."""
    if not location:
        return county or 'Unknown'
    loc = location.strip()
    lower = loc.lower()
    if lower in LOCATION_FIXES:
        loc = LOCATION_FIXES[lower]
    if loc.isupper() and len(loc) > 5:
        loc = loc.title()
    loc = re.sub(r'\s+', ' ', loc)
    loc = re.sub(r'\s*,\s*', ', ', loc)
    loc = re.sub(r'^[^\w]+', '', loc)
    return loc or county or 'Unknown'


def normalize_bond(bond_str: Optional[str]) -> Tuple[Optional[int], str]:
    """Extract numeric bond amount from raw string. Returns (amount, raw_type)."""
    if not bond_str:
        return None, ''
    raw = bond_str.upper().strip()

    # No bond patterns
    if re.search(r'NO\s*BOND|CASH\s*ONLY|NO\s*BAIL|DENIED', raw):
        return None, 'no_bond'
    if re.search(r'ROR|PR\b|P\.R\.|RELEASED|OR\b|OWN\s*RECOGNIZANCE', raw):
        return 0, 'ror'

    # Thousands shorthand: $5K
    m = re.search(r'\$?([\d,]+)\s*K\b', raw, re.I)
    if m:
        return int(m.group(1).replace(',', '')) * 1000, 'cash'

    # Standard dollar amount
    m = re.search(r'\$?([\d,]+(?:\.\d{2})?)', raw)
    if m:
        val = int(float(m.group(1).replace(',', '')))
        bond_type = 'surety' if 'S' in raw.replace('$', '') else 'cash'
        return val, bond_type

    # Plain number
    if raw.isdigit():
        return int(raw), 'cash'

    return None, 'unknown'


def normalize_name(name: Optional[str]) -> str:
    """Format name: Title Case, strip suffixes, clean commas."""
    if not name:
        return 'Unknown'
    n = name.strip()
    # Strip common suffixes
    n = re.sub(r',?\s*(JR|SR|III|II|IV|V)\b\.?$', '', n, flags=re.I).strip()
    n = re.sub(r',?\s*(JUNIOR|SENIOR)\b\.?$', '', n, flags=re.I).strip()
    # If ALL CAPS, convert to Title Case
    if n.isupper():
        n = n.title()
    # Clean double spaces
    n = re.sub(r'\s+', ' ', n)
    # Ensure Last, First format
    if ',' not in n and ' ' in n:
        parts = n.split()
        if len(parts) >= 2:
            n = f"{parts[-1]}, {' '.join(parts[:-1])}"
    return n or 'Unknown'


def parse_statute(text: Optional[str]) -> Tuple[str, str, str]:
    """Extract statute code, category, and severity from charge text."""
    if not text:
        return '', 'OTHER', ''

    lower = text.lower()

    # Try direct statute pattern match
    for pattern, category, severity in STATUTE_PATTERNS:
        if re.search(pattern, text):
            code = re.search(r'\d{2}-\d+-\d+', text)
            return (code.group(0) if code else ''), category, severity

    # Keyword fallback
    for category, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                # Try to find severity
                severity = 'Felony' if any(f in lower for f in ['felony', 'aggravated']) else 'Misdemeanor'
                code = re.search(r'\d{2}-\d+-\d+', text)
                return (code.group(0) if code else ''), category, severity

    # Extract any statute code even without category match
    code = re.search(r'\d{2}-\d+-\d+', text)
    return (code.group(0) if code else ''), 'OTHER', ''


def normalize_incident_type(incident_type: Optional[str], incident: Optional[str] = '') -> str:
    """Categorize incident from type field or incident description."""
    if not incident_type and not incident:
        return 'OTHER'

    source = f"{incident_type or ''} {incident or ''}".lower()

    for category, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in source:
                return category

    # Statute code check
    if re.search(r'45-5-2', source):
        return 'ASSAULT'
    elif re.search(r'45-6-3', source):
        return 'THEFT'
    elif re.search(r'45-6-32', source):
        return 'BURGLARY'
    elif re.search(r'45-9-10', source):
        return 'NARCOTICS'
    elif re.search(r'45-7-', source):
        return 'WEAPONS'
    elif re.search(r'45-5-5', source):
        return 'SEX CRIME'
    elif re.search(r'45-5-40', source):
        return 'ROBBERY'
    elif re.search(r'45-5-30', source):
        return 'HOMICIDE'
    elif re.search(r'45-6-4', source):
        return 'FRAUD'
    elif re.search(r'61-8-', source):
        return 'TRAFFIC'

    return 'OTHER'


def normalize_date(date_str: Optional[str]) -> str:
    """Standardize date to ISO 8601 (YYYY-MM-DD)."""
    if not date_str:
        return ''
    d = date_str.strip()
    # MM/DD/YY or M/D/YY
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', d)
    if m:
        month, day, year = m.groups()
        year_int = int(year)
        if year_int < 100:
            year_int += 2000 if year_int < 50 else 1900
        return f"{year_int}-{int(month):02d}-{int(day):02d}"
    # YYYY-MM-DD
    if re.match(r'\d{4}-\d{2}-\d{2}', d):
        return d
    return d


def normalize_time(time_str: Optional[str]) -> str:
    """Standardize time to HH:MM."""
    if not time_str:
        return ''
    t = time_str.strip()
    m = re.match(r'(\d{1,2}):(\d{2})', t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return t


def sanitize_database(db_path: str, dry_run: bool = False, verbose: bool = False) -> Dict[str, int]:
    """Run full sanitization on blotter.db."""
    print(f"{'[DRY RUN] ' if dry_run else ''}Connecting to {db_path}...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    stats = {
        'total': 0, 'counties_fixed': 0, 'locations_cleaned': 0,
        'types_normalized': 0, 'dates_fixed': 0, 'times_fixed': 0,
        'statutes_extracted': 0, 'bonds_parsed': 0,
    }

    cur.execute("SELECT COUNT(*) FROM records")
    stats['total'] = cur.fetchone()[0]
    print(f"Total records: {stats['total']:,}")

    # Process batches
    batch = 1000
    offset = 0
    while True:
        cur.execute(
            "SELECT id, county, location, incident_type, incident, date, time, status, summary, details "
            "FROM records ORDER BY id LIMIT ? OFFSET ?", (batch, offset)
        )
        rows = cur.fetchall()
        if not rows:
            break

        for row in rows:
            updates = {}

            # County
            nc = normalize_county(row['county'])
            if nc != row['county']:
                updates['county'] = nc
                stats['counties_fixed'] += 1

            # Location
            nl = normalize_location(row['location'], nc)
            if nl != row['location']:
                updates['location'] = nl
                stats['locations_cleaned'] += 1

            # Incident type (use both fields)
            nt = normalize_incident_type(row['incident_type'], row['incident'])
            if nt != row['incident_type']:
                updates['incident_type'] = nt
                stats['types_normalized'] += 1

            # Date
            nd = normalize_date(row['date'])
            if nd and nd != row['date']:
                updates['date'] = nd
                stats['dates_fixed'] += 1

            # Time
            ntime = normalize_time(row['time'])
            if ntime and ntime != row['time']:
                updates['time'] = ntime
                stats['times_fixed'] += 1

            # Apply
            if updates and not dry_run:
                sets = ', '.join(f"{k} = ?" for k in updates)
                cur.execute(f"UPDATE records SET {sets} WHERE id = ?",
                          list(updates.values()) + [row['id']])

        offset += batch
        if offset % 5000 == 0:
            print(f"  Processed {offset:,}...")
            if not dry_run:
                conn.commit()

    if not dry_run:
        conn.commit()
    conn.close()
    return stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 sanitize.py <db_path> [--dry-run] [--verbose]")
        sys.exit(1)

    db_path = sys.argv[1]
    dry_run = '--dry-run' in sys.argv
    verbose = '--verbose' in sys.argv

    if dry_run:
        print("=== DRY RUN — no changes will be made ===\n")

    stats = sanitize_database(db_path, dry_run, verbose)

    print("\n" + "=" * 50)
    print("SANITIZATION SUMMARY")
    print("=" * 50)
    for k, v in stats.items():
        print(f"  {k:25s}: {v:>10,}")
    print("=" * 50)
    print(f"{'Preview complete' if dry_run else 'Database updated'}.")


if __name__ == '__main__':
    main()
