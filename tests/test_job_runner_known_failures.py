"""Tests for job_runner's known-failure signature suppression.

The court_refresh job periodically fails because the dcportal|coljportal
WAF IP-blocks this server. The system already records the failure in
court_sources.last_error, but the one-shot failure email re-fires every
time scheduled_job_state is reset (DB migration, service restart, etc.).

These tests verify that _is_known_failure and _maybe_send_alert cooperate
to suppress the email when the failure mode is fully known, while still
alerting on novel failure modes.
"""

import unittest
from unittest import mock

import job_runner


class IsKnownFailureTests(unittest.TestCase):
    def test_returns_false_for_unknown_job(self) -> None:
        output = 'ERROR Page.goto: net::ERR_CONNECTION_RESET at https://example.com'
        self.assertFalse(job_runner._is_known_failure('nonexistent_job', output))

    def test_returns_false_for_empty_output(self) -> None:
        self.assertFalse(job_runner._is_known_failure('court_refresh', ''))

    def test_returns_false_for_output_with_no_error_lines(self) -> None:
        output = 'montana-supreme-court-oral-arguments: cases=1 events=1 filings=0'
        self.assertFalse(job_runner._is_known_failure('court_refresh', output))

    def test_returns_true_for_dcportal_connection_reset(self) -> None:
        output = (
            'montana_district_court_calendar: ERROR Page.goto: net::ERR_CONNECTION_RESET '
            'at https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do\n'
            'Call log:\n'
            '  - navigating to "https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do"'
        )
        self.assertTrue(job_runner._is_known_failure('court_refresh', output))

    def test_returns_true_for_coljportal_connection_reset(self) -> None:
        output = (
            'montana_colj_calendar: ERROR Page.goto: net::ERR_CONNECTION_RESET '
            'at https://coljportal.pubcourts.mt.gov/fullcourtweb/start.do\n'
            'Call log:\n'
            '  - navigating to "https://coljportal.pubcourts.mt.gov/fullcourtweb/start.do"'
        )
        self.assertTrue(job_runner._is_known_failure('court_refresh', output))

    def test_returns_true_for_waf_request_rejected_path(self) -> None:
        # The 34b53690 fix made colj return 0 events with last_error set
        # when the WAF rejects every login, but the per-source failure
        # still bumps exit_code to 1 — this is also a known signature.
        output = (
            'montana_colj_calendar: cases=0 events=0 filings=0\n'
            '  Unexpected page after login: Request Rejected'
        )
        self.assertTrue(job_runner._is_known_failure('court_refresh', output))

    def test_returns_true_for_mixed_known_signatures(self) -> None:
        output = (
            'montana_district_court_calendar: ERROR Page.goto: net::ERR_CONNECTION_RESET '
            'at https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do\n'
            'montana_colj_calendar: ERROR Page.goto: net::ERR_CONNECTION_RESET '
            'at https://coljportal.pubcourts.mt.gov/fullcourtweb/start.do\n'
            '[2026-06-12T06:50:07+00:00] job_finish status=failed exit_code=1'
        )
        self.assertTrue(job_runner._is_known_failure('court_refresh', output))

    def test_returns_false_when_unknown_error_appears_alongside_known(self) -> None:
        # If a new failure mode appears in the same run, the email MUST
        # fire — otherwise we'd silently swallow real bugs.
        output = (
            'montana_district_court_calendar: ERROR Page.goto: net::ERR_CONNECTION_RESET '
            'at https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do\n'
            'sqlite3.OperationalError: database is locked\n'
            '[2026-06-12T06:50:07+00:00] job_finish status=failed exit_code=1'
        )
        self.assertFalse(job_runner._is_known_failure('court_refresh', output))

    def test_returns_false_for_unrelated_job_with_known_signature_text(self) -> None:
        # The signature table is job-scoped; the same text in another
        # job's output should not be classified as a known failure.
        output = (
            'ERROR Page.goto: net::ERR_CONNECTION_RESET '
            'at https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do'
        )
        self.assertFalse(job_runner._is_known_failure('email_worker', output))


class MaybeSendAlertSuppressionTests(unittest.TestCase):
    """Verify _maybe_send_alert suppresses the email only when the
    failure mode is fully known.
    """

    @mock.patch('job_runner._fallback_recipients', return_value=['alerts@example.com'])
    @mock.patch('job_runner.send_plaintext_email')
    def test_suppresses_email_for_known_court_refresh_failure(self, send_email, _recipients) -> None:
        output = (
            'montana_district_court_calendar: ERROR Page.goto: net::ERR_CONNECTION_RESET '
            'at https://dcportal.pubcourts.mt.gov/fullcourtweb/start.do\n'
            'montana_colj_calendar: ERROR Page.goto: net::ERR_CONNECTION_RESET '
            'at https://coljportal.pubcourts.mt.gov/fullcourtweb/start.do\n'
            '[2026-06-12T06:50:07+00:00] job_finish status=failed exit_code=1'
        )
        job_runner._maybe_send_alert(
            conn=None,
            previous_state={'last_status': 'ok'},
            job_name='court_refresh',
            command_text='python3 -m services.court.refresh',
            status='failed',
            exit_code=1,
            started_at='2026-06-12T06:50:00+00:00',
            finished_at='2026-06-12T06:50:07+00:00',
            duration_seconds=7.0,
            output_excerpt=output,
        )
        send_email.assert_not_called()

    @mock.patch('job_runner._fallback_recipients', return_value=['alerts@example.com'])
    @mock.patch('job_runner.send_plaintext_email')
    def test_sends_email_for_unknown_failure_in_court_refresh(self, send_email, _recipients) -> None:
        output = (
            'sqlite3.OperationalError: database is locked\n'
            '[2026-06-12T06:50:07+00:00] job_finish status=failed exit_code=1'
        )
        job_runner._maybe_send_alert(
            conn=None,
            previous_state={'last_status': 'ok'},
            job_name='court_refresh',
            command_text='python3 -m services.court.refresh',
            status='failed',
            exit_code=1,
            started_at='2026-06-12T06:50:00+00:00',
            finished_at='2026-06-12T06:50:07+00:00',
            duration_seconds=7.0,
            output_excerpt=output,
        )
        send_email.assert_called_once()
        self.assertIn('Scheduled job failed: court_refresh', send_email.call_args.args[1])

    @mock.patch('job_runner._fallback_recipients', return_value=['alerts@example.com'])
    @mock.patch('job_runner.send_plaintext_email')
    def test_sends_email_for_unknown_job_failure(self, send_email, _recipients) -> None:
        # The signature table is job-scoped; other jobs' failures
        # always send (no known-signature gate).
        job_runner._maybe_send_alert(
            conn=None,
            previous_state={'last_status': 'ok'},
            job_name='email_worker',
            command_text='python3 email_worker.py',
            status='failed',
            exit_code=2,
            started_at='2026-06-12T12:00:00+00:00',
            finished_at='2026-06-12T12:00:05+00:00',
            duration_seconds=5.0,
            output_excerpt='Traceback (most recent call last):\n  ...',
        )
        send_email.assert_called_once()

    @mock.patch('job_runner._fallback_recipients', return_value=['alerts@example.com'])
    @mock.patch('job_runner.send_plaintext_email')
    def test_still_sends_recovery_email_for_known_failing_job(self, send_email, _recipients) -> None:
        # When the IP block lifts and the job recovers, we DO want the
        # recovery email — that's actionable news for the operator.
        job_runner._maybe_send_alert(
            conn=None,
            previous_state={'last_status': 'failed'},
            job_name='court_refresh',
            command_text='python3 -m services.court.refresh',
            status='ok',
            exit_code=0,
            started_at='2026-06-12T12:50:00+00:00',
            finished_at='2026-06-12T12:50:05+00:00',
            duration_seconds=5.0,
            output_excerpt='montana-supreme-court-oral-arguments: cases=1 events=1',
        )
        send_email.assert_called_once()
        self.assertIn('Scheduled job recovered: court_refresh', send_email.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
