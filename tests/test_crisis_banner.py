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

# Stub config
config_stub = types.ModuleType("config")
config_stub.DB_PATH = ":memory:"
config_stub.ANTHROPIC_API_KEY = "test-key"
config_stub.EMAIL_USER = "test@example.com"
config_stub.EMAIL_PASSWORD = "password"
config_stub.SMTP_USER = "test@example.com"
config_stub.SMTP_PASSWORD = "password"
config_stub.SMTP_SERVER = "smtp.example.com"
config_stub.SMTP_PORT = 587
sys.modules.setdefault("config", config_stub)

# Stub alerting
alerting_stub = types.ModuleType("alerting")
alerting_stub.collect_alert_recipients = lambda conn: []
alerting_stub.send_plaintext_email = lambda *a, **kw: None
sys.modules.setdefault("alerting", alerting_stub)

from services.publishing.morning_briefing import _update_crisis_banner  # noqa: E402


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


class TestUpdateCrisisBannerCrisisDetected(unittest.TestCase):
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

        with patch("morning_briefing.anthropic") as mock_anthropic:
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

        with patch("morning_briefing.anthropic") as mock_anthropic:
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

        with patch("morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        body = _read_setting(conn, "winter_storm_banner_body")
        assert len(body) <= 160


class TestUpdateCrisisBannerNoCrisis(unittest.TestCase):
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

        with patch("morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(EVERGREEN_POSTS, conn=conn)

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert headline == "Support Montana public safety journalism"
        body = _read_setting(conn, "winter_storm_banner_body")
        assert "dispatch monitoring" in body

    def test_writes_evergreen_when_posts_empty(self):
        conn = _make_db()

        with patch("morning_briefing.anthropic") as mock_anthropic:
            _update_crisis_banner(EMPTY_POSTS, conn=conn)
            # Claude should NOT be called for empty posts
            mock_anthropic.Anthropic.assert_not_called()

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert headline == "Support Montana public safety journalism"


class TestUpdateCrisisBannerApiFailure(unittest.TestCase):
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

        with patch("morning_briefing.anthropic") as mock_anthropic:
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

        with patch("morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        assert _read_setting(conn, "winter_storm_banner_headline") == "Keep this"
        assert _read_setting(conn, "winter_storm_banner_body") == "Keep this body"


if __name__ == "__main__":
    unittest.main()
