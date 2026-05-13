from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Any

from db import connect_db
from services.ingestion.civil_filings import ingest_civil_filings


def parse_import_file(file_path: str, *, file_format: str | None = None) -> list[dict[str, Any]]:
    ext = (file_format or os.path.splitext(file_path)[1].lstrip('.').lower()).lower()
    if ext == 'json':
        with open(file_path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if isinstance(data, dict) and 'records' in data:
            return data['records']
        if isinstance(data, list):
            return data
        return [data]
    if ext == 'csv':
        with open(file_path, newline='', encoding='utf-8') as handle:
            return list(csv.DictReader(handle))
    raise ValueError(f'Unsupported format: {ext}')


def main() -> None:
    parser = argparse.ArgumentParser(description='Import civil filing records')
    parser.add_argument('--source', required=True)
    parser.add_argument('--display-name', required=True)
    parser.add_argument('--county', required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--format', choices=['json', 'csv'])
    args = parser.parse_args()

    rows = parse_import_file(args.file, file_format=args.format)
    conn = connect_db()
    try:
        result = ingest_civil_filings(
            conn,
            source_key=args.source,
            display_name=args.display_name,
            adapter_type=f'import_{args.format or os.path.splitext(args.file)[1].lstrip(".").lower() or "json"}',
            county=args.county,
            records=rows,
        )
        print(f"Inserted: {result['inserted']}, Updated: {result['updated']}")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
