import tempfile
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import services.ops.watchdog as watchdog
from services.ops.watchdog import MonitoredJob, MonitoredStateJob, _check_job


class WatchdogFirstRunTests(unittest.TestCase):
    def test_missing_log_is_not_due_only_before_first_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            due = datetime(2026, 10, 1, 6, 15, tzinfo=timezone.utc)
            job = MonitoredJob('seasonal', Path(directory) / 'job.log',
                               2256, 'seasonal', due)
            self.assertEqual(_check_job(job, due - timedelta(seconds=1))['status'], 'not_due')
            self.assertEqual(_check_job(job, due)['status'], 'missing')
            self.assertEqual(_check_job(job, due + timedelta(days=1))['status'], 'missing')
            ordinary = MonitoredJob('daily', job.log_path, 26, 'daily')
            self.assertEqual(_check_job(ordinary, due)['status'], 'missing')

    def test_recent_failed_run_is_not_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / 'state.db')
            now = datetime.now(timezone.utc)
            with sqlite3.connect(db_path) as conn:
                conn.execute('''CREATE TABLE scheduled_job_state
                    (job_name TEXT, last_finished_at TEXT, last_status TEXT,
                     last_exit_code INTEGER)''')
                conn.execute('INSERT INTO scheduled_job_state VALUES (?, ?, ?, ?)',
                             ('missoula_public_report', now.isoformat(), 'failed', 1))
            job = MonitoredStateJob('missoula_public_report', 2, 'hourly')
            with patch.object(watchdog.config, 'DB_PATH', db_path):
                self.assertEqual(watchdog._check_state_job(job, now)['status'], 'error')
                with sqlite3.connect(db_path) as conn:
                    conn.execute("UPDATE scheduled_job_state SET last_status='ok', last_exit_code=0")
                self.assertEqual(watchdog._check_state_job(job, now)['status'], 'ok')
