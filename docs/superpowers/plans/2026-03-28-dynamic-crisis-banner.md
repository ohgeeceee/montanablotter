# Dynamic Crisis Banner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each morning, after the daily briefing runs, Claude scans yesterday's blotter posts and auto-updates the top-of-site banner with a crisis-specific or evergreen message.

**Architecture:** A single new function `_update_crisis_banner(posts)` is added to `morning_briefing.py` and called at the end of `run_briefing()`. It calls Claude API, parses JSON output, and writes three settings to the `app_settings` table via `_save_app_setting`. The banner label default in `app.py` is renamed to "Public Safety Alert".

**Tech Stack:** Python 3.12, `anthropic` SDK (`claude-sonnet-4-6`), SQLite via existing `_save_app_setting`, `pytest` for tests.

---

## File Map

| File | Change |
|------|--------|
| `morning_briefing.py` | Add `_update_crisis_banner(posts)`, call it at end of `run_briefing()`, add imports |
| `app.py` | Change label default from `"Winter Storm Support"` → `"Public Safety Alert"` |
| `tests/test_crisis_banner.py` | New — unit tests for `_update_crisis_banner` |

---

### Task 1: Rename banner label to "Public Safety Alert"

**Files:**
- Modify: `app.py:71`

- [ ] **Step 1: Edit the label default**

In `app.py`, find `WINTER_STORM_SUPPORT_BANNER_DEFAULTS` (around line 69) and change:

```python
'label': 'Winter Storm Support',
```
to:
```python
'label': 'Public Safety Alert',
```

- [ ] **Step 2: Verify the change**

```bash
grep -n "label" /root/montanablotter/app.py | grep -A1 -B1 "Public Safety"
```

Expected output includes: `'label': 'Public Safety Alert',`

- [ ] **Step 3: Commit**

```bash
cd /root/montanablotter
git add app.py
git commit -m "feat: rename banner label to Public Safety Alert"
```

---

### Task 2: Write failing tests for `_update_crisis_banner`

**Files:**
- Create: `tests/test_crisis_banner.py`

- [ ] **Step 1: Create the tests directory and test file**

```bash
mkdir -p /root/montanablotter/tests
```

Create `/root/montanablotter/tests/test_crisis_banner.py`:

```python
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

from morning_briefing import _update_crisis_banner  # noqa: E402


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

    def test_writes_evergreen_when_posts_empty(self):
        conn = _make_db()

        with patch("morning_briefing.anthropic") as mock_anthropic:
            _update_crisis_banner(EMPTY_POSTS, conn=conn)
            # Claude should NOT be called for empty posts
            mock_anthropic.Anthropic.assert_not_called()

        assert _read_setting(conn, "winter_storm_banner_enabled") == "1"
        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert headline is not None and len(headline) > 0


class TestUpdateCrisisBannerApiFailure(unittest.TestCase):
    def test_leaves_settings_unchanged_on_api_error(self):
        conn = _make_db()
        # Pre-set a value that should survive the failure
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("winter_storm_banner_headline", "Existing headline"),
        )
        conn.commit()

        with patch("morning_briefing.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = Exception("API down")
            mock_anthropic.Anthropic.return_value = mock_client
            # Should not raise
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert headline == "Existing headline"

    def test_leaves_settings_unchanged_on_bad_json(self):
        conn = _make_db()
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("winter_storm_banner_headline", "Keep this"),
        )
        conn.commit()

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="not valid json")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("morning_briefing.anthropic") as mock_anthropic:
            mock_anthropic.Anthropic.return_value = mock_client
            _update_crisis_banner(SAMPLE_POSTS, conn=conn)

        headline = _read_setting(conn, "winter_storm_banner_headline")
        assert headline == "Keep this"


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests — verify they fail with ImportError (function not yet defined)**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/test_crisis_banner.py -v 2>&1 | head -30
```

Expected: ImportError or AttributeError — `_update_crisis_banner` does not exist yet.

- [ ] **Step 3: Commit the failing tests**

```bash
cd /root/montanablotter
git add tests/test_crisis_banner.py
git commit -m "test: add failing tests for _update_crisis_banner"
```

---

### Task 3: Implement `_update_crisis_banner` in `morning_briefing.py`

**Files:**
- Modify: `morning_briefing.py`

- [ ] **Step 1: Add imports at the top of `morning_briefing.py`**

After `import sqlite3` (around line 14), add `import json`. After `import config` (around line 16), add the remaining two imports:

```python
import json          # add after import sqlite3

import anthropic     # add after import config
from utils.app_settings import _save_app_setting
```

`config` is already imported — do not add it again.

- [ ] **Step 2: Add the `_update_crisis_banner` function**

Add this function before `run_briefing()` (i.e. after `send_email()` and before `def run_briefing()`):

```python
_CRISIS_BANNER_EVERGREEN_HEADLINE = "Support Montana public safety journalism"
_CRISIS_BANNER_EVERGREEN_BODY = (
    "Help fund ongoing dispatch monitoring, records coverage, "
    "and county-by-county reporting across Montana."
)

_CRISIS_BANNER_PROMPT = """\
You are an editor for Montana Blotter, a Montana public safety news site.

Review these law enforcement blotter summaries from yesterday and determine if \
there is an active public safety crisis that readers should know about. \
Crises include: wildfires, floods, winter storms, major search-and-rescue \
operations, missing persons, or other significant public safety emergencies \
affecting Montana communities.

Respond ONLY with valid JSON in this exact format:
{{
  "crisis_detected": true or false,
  "crisis_type": "brief crisis type or null",
  "headline": "Banner headline, max 80 characters",
  "body": "Banner body, max 160 characters"
}}

If no crisis is detected, set crisis_detected to false and write an evergreen \
message encouraging readers to support Montana public safety coverage.

Blotter summaries:
{summaries}"""


def _update_crisis_banner(posts, conn=None):
    """Call Claude to detect crises in yesterday's posts and update the banner settings.

    If posts is empty, writes the evergreen message without calling the API.
    If the API call fails or returns bad JSON, leaves existing settings unchanged.
    Always sets winter_storm_banner_enabled to '1'.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_db()

    try:
        if not posts:
            _save_app_setting(conn, "winter_storm_banner_enabled", "1")
            _save_app_setting(conn, "winter_storm_banner_headline", _CRISIS_BANNER_EVERGREEN_HEADLINE)
            _save_app_setting(conn, "winter_storm_banner_body", _CRISIS_BANNER_EVERGREEN_BODY)
            conn.commit()
            print("Banner updated: no posts — wrote evergreen message.")
            return

        # Build compact text blob (cap at 3000 chars to stay within token budget)
        lines = []
        for p in posts:
            title = (p["title"] or "").strip()
            summary = (p["summary"] or "").strip()
            if title or summary:
                lines.append(f"- {title}: {summary}" if title else f"- {summary}")
        summaries_text = "\n".join(lines)[:3000]

        api_key = getattr(config, "ANTHROPIC_API_KEY", None)
        if not api_key:
            print("Banner update skipped: no ANTHROPIC_API_KEY configured.")
            return

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": _CRISIS_BANNER_PROMPT.format(summaries=summaries_text),
                }
            ],
        )
        raw = message.content[0].text.strip()
        data = json.loads(raw)

        headline = str(data.get("headline") or _CRISIS_BANNER_EVERGREEN_HEADLINE)[:80]
        body = str(data.get("body") or _CRISIS_BANNER_EVERGREEN_BODY)[:160]

        _save_app_setting(conn, "winter_storm_banner_enabled", "1")
        _save_app_setting(conn, "winter_storm_banner_headline", headline)
        _save_app_setting(conn, "winter_storm_banner_body", body)
        conn.commit()

        crisis_type = data.get("crisis_type") or "none"
        print(f"Banner updated: crisis_detected={data.get('crisis_detected')}, type={crisis_type}")

    except Exception as e:
        print(f"Banner update failed (settings unchanged): {e}")
    finally:
        if own_conn:
            conn.close()
```

- [ ] **Step 3: Call `_update_crisis_banner` at the end of `run_briefing()`**

In `run_briefing()`, after `run_conn.close()` and the final `print(f"Subscriber briefings: ...")` line, add:

```python
    # Update the top-of-site banner based on today's blotter content
    _update_crisis_banner(posts)
```

The full end of `run_briefing()` should look like:

```python
    run_conn.commit()
    run_conn.close()

    print(f"Subscriber briefings: {sent} sent, {skipped} skipped, {failed} failed")

    # Update the top-of-site banner based on today's blotter content
    _update_crisis_banner(posts)
```

- [ ] **Step 4: Run the tests — verify they pass**

```bash
cd /root/montanablotter && source venv/bin/activate && python -m pytest tests/test_crisis_banner.py -v
```

Expected output:
```
tests/test_crisis_banner.py::TestUpdateCrisisBannerCrisisDetected::test_writes_crisis_headline_and_body PASSED
tests/test_crisis_banner.py::TestUpdateCrisisBannerCrisisDetected::test_headline_truncated_to_80_chars PASSED
tests/test_crisis_banner.py::TestUpdateCrisisBannerCrisisDetected::test_body_truncated_to_160_chars PASSED
tests/test_crisis_banner.py::TestUpdateCrisisBannerNoCrisis::test_writes_evergreen_when_no_crisis PASSED
tests/test_crisis_banner.py::TestUpdateCrisisBannerNoCrisis::test_writes_evergreen_when_posts_empty PASSED
tests/test_crisis_banner.py::TestUpdateCrisisBannerApiFailure::test_leaves_settings_unchanged_on_api_error PASSED
tests/test_crisis_banner.py::TestUpdateCrisisBannerApiFailure::test_leaves_settings_unchanged_on_bad_json PASSED
7 passed
```

- [ ] **Step 5: Commit**

```bash
cd /root/montanablotter
git add morning_briefing.py
git commit -m "feat: add Claude-powered crisis banner auto-update to morning briefing"
```

---

### Task 4: Smoke test and deploy

- [ ] **Step 1: Check the app imports cleanly**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "import morning_briefing; print('OK')"
```

Expected: `OK`

- [ ] **Step 2: Verify the DB settings after a dry run**

```bash
cd /root/montanablotter && source venv/bin/activate && python -c "
import morning_briefing
import sqlite3, config
conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute(\"SELECT key, value FROM app_settings WHERE key LIKE 'winter_storm%'\").fetchall()
for r in rows:
    print(r['key'], '=', r['value'])
conn.close()
"
```

Expected: three rows — `winter_storm_banner_enabled`, `winter_storm_banner_headline`, `winter_storm_banner_body`.

- [ ] **Step 3: Restart production**

```bash
systemctl restart montanablotter && systemctl status montanablotter | head -5
```

Expected: `Active: active (running)`

- [ ] **Step 4: Check banner on the live site**

Visit `https://montanablotter.com` and confirm the banner shows current text (either crisis-specific or evergreen). The label should now read "Public Safety Alert".
