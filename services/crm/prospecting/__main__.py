"""`Find Leads` CLI.

Examples:
    # dry run against Google Places for Cascade County bail bonds
    python3 -m services.crm.prospecting --vertical bail --county Cascade --dry-run

    # ingest a saved DLI/SOS licensee export for private investigators
    python3 -m services.crm.prospecting --vertical pi \
        --import data/prospecting/dli_pi_licensed.csv
"""
from __future__ import annotations

import argparse
import sqlite3
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python3 -m services.crm.prospecting')
    ap.add_argument('--vertical', default='all',
                    choices=['all', 'bail', 'legal', 'pi', 'process_server'])
    ap.add_argument('--county', default=None, help='restrict Google queries')
    ap.add_argument('--limit', type=int, default=60, help='max records per provider run')
    ap.add_argument('--dry-run', action='store_true',
                    help='fetch + dedupe-report only; write nothing')
    ap.add_argument('--import', dest='import_file', default=None,
                    help='CSV/JSON/HTML-table export from a state registry')
    ap.add_argument('--no-google', action='store_true',
                    help='skip Google Places provider (no key / offline)')
    ap.add_argument('--db', default=None,
                    help='db path (default: app config DB_PATH; use a copy!)')
    args = ap.parse_args(argv)

    if args.db:
        db_path = args.db
    else:
        sys.path.insert(0, '.')
        import config  # live repo config when run from /root/montanablotter
        db_path = config.DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')

    # additive + idempotent; safe to run before every ingest. In production
    # this call lives in init_db.migrate() once the draft is applied.
    from pathlib import Path
    try:
        from init_db import ensure_crm_schema
    except ImportError:
        import importlib.util
        _p = Path(__file__).resolve().parents[4] / 'migration/crm_migration.py'
        _spec = importlib.util.spec_from_file_location('crm_migration', _p)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        ensure_crm_schema = _mod.ensure_crm_schema
    ensure_crm_schema(conn)

    from services.crm.prospecting.engine import run_ingest
    from services.crm.prospecting.providers import VERTICALS, ProviderUnavailable

    verticals = list(VERTICALS) if args.vertical == 'all' else [args.vertical]
    grand = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'skipped': 0}

    for v in verticals:
        records = []
        if args.import_file:
            from services.crm.prospecting.providers import directory_import
            records.extend(directory_import.fetch_file(
                args.import_file, v, limit=args.limit))
        if not args.no_google and not args.import_file:
            from services.crm.prospecting.providers import google_local
            try:
                records.extend(google_local.fetch(v, county=args.county,
                                                  limit=args.limit))
            except ProviderUnavailable as exc:
                print(f'[skip] {exc}', file=sys.stderr)
            except Exception as exc:  # network/parse — never kill the run
                print(f'[error] google_local/{v}: {exc}', file=sys.stderr)
        if not records:
            print(f'{v}: 0 records fetched')
            continue
        counts = run_ingest(conn, records, dry_run=args.dry_run)
        for k in grand:
            grand[k] += counts[k]
        print(f'{v}: {counts}' + (' (dry-run)' if args.dry_run else ''))

    print(f'TOTAL: {grand}')
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
