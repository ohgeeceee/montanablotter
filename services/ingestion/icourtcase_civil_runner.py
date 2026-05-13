from __future__ import annotations

import argparse
import json

from db import connect_db
from services.ingestion.icourtcase_civil import run_daily_county_ingest


DEFAULT_COUNTIES = [
    'Beaverhead', 'Big Horn', 'Blaine', 'Broadwater', 'Carbon', 'Carter', 'Cascade',
    'Chouteau', 'Custer', 'Daniels', 'Dawson', 'Deer Lodge', 'Fallon', 'Fergus',
    'Flathead', 'Gallatin', 'Garfield', 'Glacier', 'Golden Valley', 'Granite', 'Hill',
    'Jefferson', 'Judith Basin', 'Lake', 'Lewis and Clark', 'Liberty', 'Lincoln',
    'Madison', 'McCone', 'Meagher', 'Mineral', 'Missoula', 'Musselshell', 'Park',
    'Petroleum', 'Phillips', 'Pondera', 'Powder River', 'Powell', 'Prairie',
    'Ravalli', 'Richland', 'Roosevelt', 'Rosebud', 'Sanders', 'Sheridan', 'Silver Bow',
    'Stillwater', 'Sweet Grass', 'Teton', 'Toole', 'Treasure', 'Valley', 'Wheatland',
    'Wibaux', 'Yellowstone',
]


def main() -> None:
    parser = argparse.ArgumentParser(description='Daily iCourtCase civil filing ingest')
    parser.add_argument('--county', action='append', default=[], help='County name (repeatable)')
    parser.add_argument('--max-pages', type=int, default=10)
    parser.add_argument('--delay-seconds', type=float, default=0.5)
    args = parser.parse_args()

    counties = args.county or DEFAULT_COUNTIES
    conn = connect_db()
    try:
        results: list[dict] = []
        for county in counties:
            result = run_daily_county_ingest(
                conn,
                county=county,
                max_pages=args.max_pages,
                delay_seconds=args.delay_seconds,
            )
            results.append(result)
        print(json.dumps({'ok': True, 'results': results}, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
