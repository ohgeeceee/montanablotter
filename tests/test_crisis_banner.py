"""Tests for _update_crisis_banner in morning_briefing.py."""
import json
import sqlite3
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal stubs so morning_briefing.py imports without real config/db
# ---------------------------------------------------------------------------

# Stub services.alerts.legacy (morning_briefing imports collect_alert_recipients and
# send_plaintext_email from there now, not from a top-level alerting module).
alerting_stub = types.ModuleType("services.alerts.legacy")
alerting_stub.collect_alert_recipients = lambda conn: []
alerting_stub.send_plaintext_email = lambda *a, **kw: None
sys.modules.setdefault("services.alerts.legacy", alerting_stub)
# Back-compat: some legacy code paths may still try `from alerting import ...`.
alerting_legacy_alias = types.ModuleType("alerting")
alerting_legacy_alias.collect_alert_recipients = lambda conn: []
alerting_legacy_alias.send_plaintext_email = lambda *a, **kw: None
sys.modules.setdefault("alerting", alerting_legacy_alias)

from services.publishing.morning_briefing import _update_crisis_banner  # noqa: E402
import services.publishing.morning_briefing as _mb_module


class _PaidLLMEnabledTestCase(unittest.TestCase):
    """Base test case that forces the paid-LLM gate to True for Claude paths."""

    def setUp(self):
        self._orig_use_paid_llm = getattr(_mb_module.config, "USE_PAID_LLM", None)
        _mb_module.config.USE_PAID_LLM = True

    def tearDown(self):
        if self._orig_use_paid_llm is None:
            if hasattr(_mb_module.config, "USE_PAID_LLM"):
                delattr(_mb_module.config, "USE_PAID_LLM")
        else:
            _mb_module.config.USE_PAID_LLM = self._orig_use_paid_llm


def _make_db():
    """Create an in-memory app_settings table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)"
    )
    conn.commit()
    return conn


def _read_setting(conn, key):
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


SAMPLE_POSTS = [
    {
        "title": "Cascade County Sheriff Daily Report",
        "summary": "Wildfire reported near Canyon Ferry. Multiple structures threatened. Evacuation orders issued for residents in the area.",
        "agency_name": "Cascade County Sheriff",
        "county": "Cascade",
    },
]

EMPTY_POSTS = []

EVERGREEN_POSTS = [
    {
        "title": "Cascade County Sheriff Daily Report",
        "summary": "Three arrests for DUI. One theft report. Traffic stop on I-15.",
        "agency_name": "Cascade County Sheriff",
        "county": "Cascade",
    },
]


class TestUpdateCrisisBannerCrisisDetected(_PaidLLMEnabledTestCase):
    def test_writes_crisis_headline_and_body(self):
        conn = _make_db()
        api_response = json.dumps({
            "crisis_detected": True,
            "crisis_type": "wildfire",
            "headline": "Wildfire threatens Canyon Ferry area",
            "body": "Evacuation orders in effect. Follow Cascade County Sheriff for updates.",
        })
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=api_response)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("services.publishing.morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        headline = _read_setting(conn, "winter_storm_banner_headline")
        body = _read_setting(conn, "winter_storm_banner_body")
        assert headline == "Wildfire threatens Canyon Ferry area"
        assert "Evacuation" in body

    def test_headline_truncated_to_80_chars(self):
        conn = _make_db()
        long_headline = "A" * 100
        api_response = json.dumps({
            "crisis_detected": True,
            "crisis_type": "wildfire",
            "headline": long_headline,
            "body": "Short body.",
        })
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=api_response)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("services.publishing.morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert len(headline) <= 80

    def test_body_truncated_to_160_chars(self):
        conn = _make_db()
        long_body = "B" * 200
        api_response = json.dumps({
            "crisis_detected": True,
            "crisis_type": "flood",
            "headline": "Flooding in Missoula",
            "body": long_body,
        })
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=api_response)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("services.publishing.morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        body = _read_setting(conn, "winter_storm_banner_body")
        assert len(body) <= 160


class TestUpdateCrisisBannerNoCrisis(_PaidLLMEnabledTestCase):
    def test_writes_evergreen_when_no_crisis(self):
        conn = _make_db()
        api_response = json.dumps({
            "crisis_detected": False,
            "crisis_type": None,
            "headline": "Support Montana public safety journalism",
            "body": "Help fund ongoing dispatch monitoring and county-by-county reporting across Montana.",
        })
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=api_response)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("services.publishing.morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(EVERGREEN_POSTS, conn=conn)

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert headline == "Support Montana public safety journalism"
        body = _read_setting(conn, "winter_storm_banner_body")
        assert "dispatch monitoring" in body

    def test_writes_evergreen_when_posts_empty(self):
        conn = _make_db()

        with patch("services.publishing.morning_briefing.anthropic") as mock_anthropic:
            _update_crisis_banner(EMPTY_POSTS, conn=conn)
            # Claude should NOT be called for empty posts
            mock_anthropic.Anthropic.assert_not_called()

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert headline == "Support Montana public safety journalism"


class TestUpdateCrisisBannerApiFailure(_PaidLLMEnabledTestCase):
    def test_leaves_settings_unchanged_on_api_error(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("winter_storm_banner_enabled", "1"),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("winter_storm_banner_headline", "Existing headline"),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("winter_storm_banner_body", "Existing body"),
        )
        conn.commit()

        with patch("services.publishing.morning_briefing.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = Exception("API down")
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        assert _read_setting(conn, "winter_storm_banner_headline") == "Existing headline"
        assert _read_setting(conn, "winter_storm_banner_body") == "Existing body"

    def test_leaves_settings_unchanged_on_bad_json(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("winter_storm_banner_enabled", "1"),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("winter_storm_banner_headline", "Keep this"),
        )
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("winter_storm_banner_body", "Keep this body"),
        )
        conn.commit()

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="not valid json")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("services.publishing.morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        assert _read_setting(conn, "winter_storm_banner_headline") == "Keep this"
        assert _read_setting(conn, "winter_storm_banner_body") == "Keep this body"


class TestUpdateCrisisBannerPaidLLMFlag(unittest.TestCase):
    def test_writes_evergreen_when_paid_llm_disabled(self):
        conn = _make_db()
        with patch.object(_mb_module.config, "USE_PAID_LLM", False):
            with patch("services.publishing.morning_briefing.anthropic") as mock_anthropic:
                _update_crisis_banner(SAMPLE_POSTS, conn=conn)
                mock_anthropic.Anthropic.assert_not_called()

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert headline == "Support Montana public safety journalism"


if __name__ == "__main__":
    unittest.main()
