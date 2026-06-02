#!/usr/bin/env python3
"""
Disposition watcher — link new jail bookings to court cases and detect
court-outcome changes for existing links.

Run from cron (see crontab.txt):
  */15 * * * * /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/scripts/ops/disposition_watcher.py \\
      >> /root/montanablotter/logs/disposition_watcher.log 2>&1

The script is import-safe and idempotent — re-running it is a no-op for
already-linked bookings. If anything raises, the entire run is wrapped
in a try/except so a transient DB hiccup can't kill the cron loop.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.chdir(PROJECT_ROOT)

# Activate venv if not already (cron runs without sourcing venv).
VENV = os.path.join(PROJECT_ROOT, 'venv')
if VENV not in sys.path and os.path.isdir(VENV):
    sys.path.insert(0, os.path.join(VENV, 'lib', 'python3.12', 'site-packages'))

import logging

import config
import init_db
from app import get_db
from services.disposition.watcher import run_all

LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, 'disposition_watcher.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger('disposition_watcher')


def main() -> int:
    t0 = time.monotonic()
    log.info('starting disposition_watcher run')
    conn = get_db()
    try:
        # Ensure schema is present (idempotent — only adds missing columns/tables).
        init_db.migrate()
        stats = run_all(conn)
    except Exception as e:  # noqa: BLE001
        log.error('watcher run failed: %s\n%s', e, traceback.format_exc())
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass
    elapsed = time.monotonic() - t0
    log.info('finished in %.2fs — %s', elapsed, json.dumps(stats, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
