"""City -> Montana county resolver for address-derived county tags.

Deliberately a curated static map, not a geocoder call: prospecting runs
in bulk and the largest MT towns cover the vast majority of service
businesses. Unknown cities fall back to a county-name substring match on
the raw address, then to '' (untagged, still importable). Never guesses.
"""
from __future__ import annotations

import re

MT_COUNTIES = [
    'Beaverhead', 'Big Horn', 'Blaine', 'Broadwater', 'Carbon', 'Carter',
    'Cascade', 'Chouteau', 'Custer', 'Daniels', 'Dawson', 'Deer Lodge',
    'Fallon', 'Fergus', 'Flathead', 'Gallatin', 'Garfield', 'Glacier',
    'Golden Valley', 'Granite', 'Hill', 'Jefferson', 'Judith Basin', 'Lake',
    'Lewis and Clark', 'Liberty', 'Lincoln', 'McCone', 'Madison', 'Meagher',
    'Mineral', 'Missoula', 'Musselshell', 'Park', 'Petroleum', 'Phillips',
    'Pondera', 'Powder River', 'Powell', 'Ravalli', 'Richland', 'Roosevelt',
    'Rosebud', 'Sanders', 'Sheridan', 'Silver Bow', 'Stillwater',
    'Sweet Grass', 'Teton', 'Toole', 'Treasure', 'Valley', 'Wheatland',
    'Wibaux', 'Yellowstone',
]
_MT_COUNTY_SET = set(MT_COUNTIES)
_COUNTY_RE = re.compile(r'\b(' + '|'.join(c.casefold() for c in MT_COUNTIES) + r')\b', re.I)

# city (casefolded, punctuation-stripped) -> county
CITY_COUNTY = {
    'billings': 'Yellowstone', 'laurel': 'Yellowstone', 'lockwood': 'Yellowstone',
    'columbus': 'Stillwater',
    'missoula': 'Missoula',
    'polson': 'Lake', 'ovando': 'Lake', 'st ignatius': 'Lake', 'salish': 'Lake',
    'moore': 'Lake', 'cezanne': 'Lake', 'drummond': 'Flathead',
    'bozeman': 'Gallatin', 'belgrade': 'Gallatin', 'manhattan': 'Gallatin',
    'three forks': 'Gallatin', 'simms': 'Gallatin', 'cardwell': 'Jefferson',
    'livingston': 'Park', 'gardiner': 'Park', 'wilsall': 'Park', 'pryor': 'Big Horn',
    'great falls': 'Cascade', 'sun river': 'Cascade',
    'shelby': 'Toole', 'glacier': 'Toole', 'kila': 'Toole', 'north field': 'Toole',
    'china': 'Hill', 'havre': 'Hill', 'winnett': 'Hill',
    'helena': 'Lewis and Clark', 'canyon creek': 'Lewis and Clark',
    'clancy': 'Lewis and Clark', 'lincoln': 'Lewis and Clark',
    'augusta': 'Lewis and Clark', 'sussex': 'Lewis and Clark',
    'butte': 'Silver Bow', 'walkerville': 'Silver Bow', 'meaderville': 'Silver Bow',
    'anaconda': 'Deer Lodge',
    'kalispell': 'Flathead', 'whitefish': 'Flathead', 'columbia falls': 'Flathead',
    'polesetter': 'Flathead', 'somers': 'Flathead', 'bigfork': 'Flathead',
    'big fork': 'Flathead', 'rollins': 'Flathead', 'olney': 'Flathead',
    'charlo': 'Flathead', 'kings beach': 'Flathead', 'st regis': 'Lincoln',
    'dillon': 'Beaverhead', 'wisdom': 'Beaverhead', 'seeley lake': 'Beaverhead',
    'miles city': 'Custer', 'fromberg': 'Custer', 'joliet': 'Carbon',
    'red lodge': 'Carbon', 'rocky point': 'Judith Basin', 'gate': 'Judith Basin',
    'glendive': 'Dawson', 'lewistown': 'Fergus', 'winifred': 'Fergus',
    'virginia city': 'Madison', 'sidney': 'Richland', 'oplevna': 'Fallon',
    'plevna': 'Fallon', 'choteau': 'Teton', 'fairfield': 'Teton',
    'wolf point': 'Rosebud', 'custer': 'Rosebud', 'ebrington': 'Rosebud',
    'scobey': 'Daniels', 'glasgow': 'Valley', 'malta': 'Phillips', 'eagar': 'Phillips',
    'hardin': 'Big Horn', 'crow agency': 'Big Horn', 'lodge grass': 'Big Horn',
    'pray': 'Park', 'emblem': 'Park', 'boulder': 'Jefferson',
    'whitehall': 'Jefferson', 'white sulphur springs': 'Meagher',
    'libby': 'Lincoln', 'troy': 'Lincoln', 'dia': 'Lincoln', 'elmo': 'Lake',
    'warm springs': 'Sanders', 'hot springs': 'Sanders', 'plans': 'Sanders',
    'moran': 'Powell', 'martin city': 'Blaine', 'bench': 'Blaine', 'inzell': 'Lake',
    'baggs': 'Carbon', 'ryegate': 'Carbon', 'hysham': 'Musselshell',
    'foreman': 'Carbon', 'stanford': 'Gallatin', 'twin bridges': 'Broadwater',
    'dutton': 'Teton', 'valentine': 'Phillips', 'kinross': 'Golden Valley',
    
}


def normalize_city(city: str) -> str:
    c = re.sub(r'[^\w\s]', ' ', (city or '').casefold())
    return re.sub(r'\s+', ' ', c).strip()


def county_for(city: str = '', address: str = '', state: str = 'MT') -> str:
    """Return a Montana county name ('' if unresolvable)."""
    if (state or '').strip().upper() not in ('MT', 'MONTANA', ''):
        return ''
    hit = CITY_COUNTY.get(normalize_city(city))
    if hit:
        return hit
    m = _COUNTY_RE.search(address or '')
    if m:
        return m.group(0).title().replace(' And ', ' and ')
    return ''
