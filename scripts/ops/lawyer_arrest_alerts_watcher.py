#!/usr/bin/env python3
"""
Lawyer arrest alerts watcher — dispatches new-post alerts to matching paid
lawyer advertisers and checks claimed cases for newly-scraped dispositions.

Run from cron (see crontab.txt):
  */15 * * * * /root/montanablotter/venv/bin/python3 \\
      /root/montanablotter/scripts/ops/lawyer_arrest_alerts_watcher.py \\
      >> /root/montanablotter/logs/lawyer_arrest_alerts.log 2>&1

Idempotent — posts.lawyer_alert_dispatched_at and
lawyer_arrest_alert_deliveries.outcome_notified_at gate re-sends.
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

VENV = os.path.join(PROJECT_ROOT, 'venv')
if VENV not in sys.path and os.path.isdir(VENV):
    sys.path.insert(0, os.path.join(VENV, 'lib', 'python3.12', 'site-packages'))

import logging

import init_db
from app import get_db
from services.alerts.lawyer_arrest_alerts import check_claimed_outcomes, dispatch_pending_alerts

LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, 'lawyer_arrest_alerts.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger('lawyer_arrest_alerts_watcher')


def main() -> int:
    t0 = time.monotonic()
    log.info('starting lawyer_arrest_alerts_watcher run')
    conn = get_db()
    try:
        init_db.migrate()
        dispatch_stats = dispatch_pending_alerts(conn)
        outcome_stats = check_claimed_outcomes(conn)
    except Exception as e:  # noqa: BLE001
        log.error('watcher run failed: %s\n%s', e, traceback.format_exc())
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass
    elapsed = time.monotonic() - t0
    log.info(
        'finished in %.2fs — dispatch=%s outcomes=%s',
        elapsed, json.dumps(dispatch_stats), json.dumps(outcome_stats),
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
