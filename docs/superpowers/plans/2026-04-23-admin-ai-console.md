# Admin AI Console Implementation Plan

> **Status:** Completed. The `tests/test_kimi_sqlite_agent.py` file referenced below was removed in commit 7aa2b07b — the `kimi_sqlite_agent` module path no longer exists; tool registration consolidated elsewhere under the admin AI service module.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an admin-only `/admin/ai` console that uses Kimi 2.6 through a server-side tool registry for read queries and draft-only actions with confirmation and audit logging.

**Architecture:** Add a focused admin AI service module that owns tool registration, Kimi orchestration, pending-action signing, and execution boundaries. Add a dedicated admin blueprint module and template for the UI, keeping all write-intent tools behind a confirm step and reusing existing admin auth and `_log_admin_action` patterns.

**Tech Stack:** Flask, Flask-Login, Jinja, SQLite, existing admin blueprint modules, `openai` SDK with Moonshot-compatible `base_url`

---

## File Structure

- Create: `admin_ai.py`
  Server-side AI orchestration, tool registry, pending action helpers, and bounded tool execution.
- Create: `blueprints/admin/ai_console.py`
  Admin routes for page render, query submit, and confirmation submit.
- Create: `templates/admin_ai_console.html`
  Admin-only AI console UI using existing admin visual patterns.
- Create: `tests/test_admin_ai_console.py`
  Focused auth, confirmation, and execution-boundary tests.
- Modify: `blueprints/admin/__init__.py`
  Register the new admin AI route module.
- Modify: `kimi_sqlite_agent.py`
  Reuse shared tool execution from `admin_ai.py` instead of duplicating DB tool logic.
- Modify: `requirements.txt`
  Already updated to include `openai`; keep this file in the implementation diff if not yet merged in the target branch.
- Modify: `templates/admin_dashboard.html`
  Add a link into the new admin console if there is a natural admin navigation slot.

### Task 1: Build The First Failing Access-Control Test

**Files:**
- Create: `tests/test_admin_ai_console.py`
- Reference: `tests/test_missing_persons.py`

- [ ] **Step 1: Write the failing test**

```python
import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class AdminAiConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix='mb-admin-ai-', suffix='.db')
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config['TESTING'] = True

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            '''
        )
        bootstrap_conn.commit()
        bootstrap_conn.close()

        init_db.init_database()
        init_db.migrate()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_admin_ai_page_requires_login(self) -> None:
        client = app_module.app.test_client()

        response = client.get('/admin/ai')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login', response.headers['Location'])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_admin_ai_page_requires_login -q`

Expected: FAIL because `/admin/ai` is not registered yet.

- [ ] **Step 3: Write minimal implementation**

```python
# blueprints/admin/ai_console.py
from flask import render_template
from flask_login import login_required

from blueprints.admin import admin_bp


@admin_bp.route('/ai')
@login_required
def admin_ai_console():
    return render_template('admin_ai_console.html')
```

```python
# blueprints/admin/__init__.py
def register_admin_blueprint(app):
    from blueprints.admin import agents     # noqa: F401
    from blueprints.admin import ai_console # noqa: F401
    from blueprints.admin import audience   # noqa: F401
    ...
```

```html
<!-- templates/admin_ai_console.html -->
<!doctype html>
<html lang="en">
<body>
  <h1>Admin AI Console</h1>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_admin_ai_page_requires_login -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_admin_ai_console.py blueprints/admin/ai_console.py blueprints/admin/__init__.py templates/admin_ai_console.html
git commit -m "feat(admin): add ai console route scaffold"
```

### Task 2: Add Logged-In Admin Page Rendering

**Files:**
- Modify: `tests/test_admin_ai_console.py`
- Modify: `blueprints/admin/ai_console.py`
- Modify: `templates/admin_ai_console.html`

- [ ] **Step 1: Write the failing test**

```python
    def _create_admin_user(self) -> int:
        conn = app_module.get_db()
        cursor = conn.execute(
            """
            INSERT INTO users (username, password, email, role, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            ('ai-admin', 'not-used-in-tests', 'ai@example.com', 'ops', 1),
        )
        conn.commit()
        conn.close()
        return int(cursor.lastrowid)

    def _login_admin_session(self, client, user_id: int) -> None:
        with client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
            session['_csrf_token'] = 'test-csrf-token'

    def test_admin_ai_page_renders_for_logged_in_admin(self) -> None:
        admin_user_id = self._create_admin_user()
        client = app_module.app.test_client()
        self._login_admin_session(client, admin_user_id)

        response = client.get('/admin/ai')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Admin AI Console', html)
        self.assertIn('Ask Montana Blotter AI', html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_admin_ai_page_renders_for_logged_in_admin -q`

Expected: FAIL because the placeholder template does not contain the real page content.

- [ ] **Step 3: Write minimal implementation**

```python
# blueprints/admin/ai_console.py
@admin_bp.route('/ai')
@login_required
def admin_ai_console():
    return render_template(
        'admin_ai_console.html',
        transcript=[],
        pending_action=None,
        recent_actions=[],
        default_model='kimi-k2.6',
    )
```

```html
<!-- templates/admin_ai_console.html -->
<section>
  <p>Ask Montana Blotter AI</p>
  <h1>Admin AI Console</h1>
  <form method="post" action="/admin/ai/query">
    <textarea name="question"></textarea>
    <button type="submit">Ask</button>
  </form>
</section>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_admin_ai_page_renders_for_logged_in_admin -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_admin_ai_console.py blueprints/admin/ai_console.py templates/admin_ai_console.html
git commit -m "feat(admin): render ai console page for admins"
```

### Task 3: Extract Shared Tool Logic Into `admin_ai.py`

**Files:**
- Create: `admin_ai.py`
- Modify: `kimi_sqlite_agent.py`
- Test: `tests/test_kimi_sqlite_agent.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_search_records_filters_by_county_and_keyword(self) -> None:
        import admin_ai as agent

        result = agent.run_registered_tool(
            "search_records",
            {"county": "Yellowstone", "keyword": "theft", "limit": 5},
            db_path=self.db_path,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_kimi_sqlite_agent.py::KimiSqliteAgentTests::test_search_records_filters_by_county_and_keyword -q`

Expected: FAIL because `admin_ai.run_registered_tool` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# admin_ai.py
from __future__ import annotations

import sqlite3
from typing import Any

import config


def connect_db(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path, timeout=float(getattr(config, "DB_TIMEOUT_SECONDS", 30) or 30))
    conn.row_factory = sqlite3.Row
    return conn


def run_registered_tool(name: str, args: dict[str, Any], *, db_path: str | None = None) -> Any:
    conn = connect_db(db_path)
    try:
        if name == "search_records":
            ...
        if name == "get_missing_persons_summary":
            ...
        raise ValueError(f"Unknown tool: {name}")
    finally:
        conn.close()
```

```python
# kimi_sqlite_agent.py
from admin_ai import DEFAULT_BASE_URL, DEFAULT_MODEL, TOOLS, run_registered_tool

def run_tool(name: str, args: dict[str, Any], *, db_path: str | None = None):
    return run_registered_tool(name, args, db_path=db_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_kimi_sqlite_agent.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add admin_ai.py kimi_sqlite_agent.py tests/test_kimi_sqlite_agent.py
git commit -m "refactor(ai): share kimi sqlite tools with admin service"
```

### Task 4: Add Proposal-Only Query Endpoint

**Files:**
- Modify: `tests/test_admin_ai_console.py`
- Modify: `admin_ai.py`
- Modify: `blueprints/admin/ai_console.py`

- [ ] **Step 1: Write the failing test**

```python
from unittest import mock

    def test_query_endpoint_returns_read_only_answer(self) -> None:
        admin_user_id = self._create_admin_user()
        client = app_module.app.test_client()
        self._login_admin_session(client, admin_user_id)

        with mock.patch('blueprints.admin.ai_console.run_admin_ai_query') as mocked_query:
            mocked_query.return_value = {
                'answer': 'Yellowstone has 1 matching theft record.',
                'transcript': [{'role': 'assistant', 'content': 'Yellowstone has 1 matching theft record.'}],
                'pending_action': None,
            }
            response = client.post('/admin/ai/query', data={'question': 'Find theft records'})

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Yellowstone has 1 matching theft record.', html)
        self.assertNotIn('Confirm Draft Action', html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_query_endpoint_returns_read_only_answer -q`

Expected: FAIL because `/admin/ai/query` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# admin_ai.py
def run_admin_ai_query(question: str, *, db_path: str | None = None) -> dict[str, Any]:
    return {
        'answer': '',
        'transcript': [],
        'pending_action': None,
    }
```

```python
# blueprints/admin/ai_console.py
from admin_ai import run_admin_ai_query

@admin_bp.route('/ai/query', methods=['POST'])
@login_required
def admin_ai_query():
    question = (request.form.get('question') or '').strip()
    result = run_admin_ai_query(question)
    return render_template(
        'admin_ai_console.html',
        transcript=result['transcript'],
        pending_action=result['pending_action'],
        recent_actions=[],
        default_model='kimi-k2.6',
        question=question,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_query_endpoint_returns_read_only_answer -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_admin_ai_console.py admin_ai.py blueprints/admin/ai_console.py
git commit -m "feat(admin): add ai query endpoint"
```

### Task 5: Add Pending Action Proposal Storage Without Executing Writes

**Files:**
- Modify: `tests/test_admin_ai_console.py`
- Modify: `admin_ai.py`
- Modify: `blueprints/admin/ai_console.py`
- Modify: `templates/admin_ai_console.html`

- [ ] **Step 1: Write the failing test**

```python
    def test_query_endpoint_shows_pending_action_without_executing_it(self) -> None:
        admin_user_id = self._create_admin_user()
        client = app_module.app.test_client()
        self._login_admin_session(client, admin_user_id)

        with mock.patch('blueprints.admin.ai_console.run_admin_ai_query') as mocked_query:
            mocked_query.return_value = {
                'answer': 'I can draft that blog post.',
                'transcript': [{'role': 'assistant', 'content': 'I can draft that blog post.'}],
                'pending_action': {
                    'token': 'pending-token',
                    'tool_name': 'create_blog_draft',
                    'summary': 'Create a draft blog post about Yellowstone theft trends',
                    'arguments': {'title': 'Yellowstone theft trends', 'body': 'Draft body'},
                },
            }
            response = client.post('/admin/ai/query', data={'question': 'Draft a blog post'})

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Confirm Draft Action', html)
        self.assertIn('create_blog_draft', html)
        with client.session_transaction() as session:
            self.assertEqual(session['admin_ai_pending_action']['tool_name'], 'create_blog_draft')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_query_endpoint_shows_pending_action_without_executing_it -q`

Expected: FAIL because the route does not store or render pending actions yet.

- [ ] **Step 3: Write minimal implementation**

```python
# admin_ai.py
import time
import secrets

PENDING_ACTION_SESSION_KEY = 'admin_ai_pending_action'
PENDING_ACTION_TTL_SECONDS = 900

def build_pending_action(tool_name: str, summary: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        'token': secrets.token_urlsafe(24),
        'tool_name': tool_name,
        'summary': summary,
        'arguments': arguments,
        'created_at': int(time.time()),
    }
```

```python
# blueprints/admin/ai_console.py
if result['pending_action']:
    session['admin_ai_pending_action'] = result['pending_action']
```

```html
{% if pending_action %}
<section>
  <h2>Confirm Draft Action</h2>
  <p>{{ pending_action.tool_name }}</p>
  <p>{{ pending_action.summary }}</p>
  <form method="post" action="/admin/ai/confirm">
    <input type="hidden" name="token" value="{{ pending_action.token }}">
    <button type="submit">Confirm</button>
  </form>
</section>
{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_query_endpoint_shows_pending_action_without_executing_it -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_admin_ai_console.py admin_ai.py blueprints/admin/ai_console.py templates/admin_ai_console.html
git commit -m "feat(admin): stage ai draft actions for confirmation"
```

### Task 6: Add Confirmation Execution And Replay Protection

**Files:**
- Modify: `tests/test_admin_ai_console.py`
- Modify: `admin_ai.py`
- Modify: `blueprints/admin/ai_console.py`

- [ ] **Step 1: Write the failing tests**

```python
    def test_confirm_executes_matching_pending_action_once(self) -> None:
        admin_user_id = self._create_admin_user()
        client = app_module.app.test_client()
        self._login_admin_session(client, admin_user_id)

        with client.session_transaction() as session:
            session['admin_ai_pending_action'] = {
                'token': 'pending-token',
                'tool_name': 'create_blog_draft',
                'summary': 'Create a draft',
                'arguments': {'title': 'Draft title', 'body': 'Draft body'},
                'created_at': 4102444800,
            }

        with mock.patch('blueprints.admin.ai_console.execute_pending_admin_ai_action') as mocked_execute:
            mocked_execute.return_value = {'message': 'Draft created', 'target_id': 42}
            response = client.post('/admin/ai/confirm', data={'token': 'pending-token'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('Draft created', response.get_data(as_text=True))
        mocked_execute.assert_called_once()
        with client.session_transaction() as session:
            self.assertNotIn('admin_ai_pending_action', session)

    def test_confirm_rejects_mismatched_token(self) -> None:
        admin_user_id = self._create_admin_user()
        client = app_module.app.test_client()
        self._login_admin_session(client, admin_user_id)

        with client.session_transaction() as session:
            session['admin_ai_pending_action'] = {
                'token': 'expected-token',
                'tool_name': 'create_blog_draft',
                'summary': 'Create a draft',
                'arguments': {'title': 'Draft title', 'body': 'Draft body'},
                'created_at': 4102444800,
            }

        response = client.post('/admin/ai/confirm', data={'token': 'wrong-token'})

        self.assertEqual(response.status_code, 400)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py -q`

Expected: FAIL because `/admin/ai/confirm` and pending-action validation do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# admin_ai.py
import time

def get_pending_action(session_obj):
    return session_obj.get(PENDING_ACTION_SESSION_KEY)

def validate_pending_action(session_obj, token: str) -> dict[str, Any]:
    payload = get_pending_action(session_obj)
    if not payload:
        raise ValueError('No pending action found.')
    if payload.get('token') != token:
        raise ValueError('Pending action token mismatch.')
    created_at = int(payload.get('created_at') or 0)
    if time.time() - created_at > PENDING_ACTION_TTL_SECONDS:
        raise ValueError('Pending action expired.')
    return payload

def clear_pending_action(session_obj) -> None:
    session_obj.pop(PENDING_ACTION_SESSION_KEY, None)

def execute_pending_admin_ai_action(payload: dict[str, Any], *, db_path: str | None = None) -> dict[str, Any]:
    result = run_registered_tool(payload['tool_name'], payload['arguments'], db_path=db_path)
    return {'message': 'Action completed.', 'result': result}
```

```python
# blueprints/admin/ai_console.py
from flask import session
from admin_ai import clear_pending_action, execute_pending_admin_ai_action, validate_pending_action

@admin_bp.route('/ai/confirm', methods=['POST'])
@login_required
def admin_ai_confirm():
    token = (request.form.get('token') or '').strip()
    try:
        payload = validate_pending_action(session, token)
    except ValueError as exc:
        return str(exc), 400

    result = execute_pending_admin_ai_action(payload)
    clear_pending_action(session)
    return render_template(
        'admin_ai_console.html',
        transcript=[{'role': 'assistant', 'content': result['message']}],
        pending_action=None,
        recent_actions=[],
        default_model='kimi-k2.6',
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py -q`

Expected: PASS for the new confirm-path tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_admin_ai_console.py admin_ai.py blueprints/admin/ai_console.py
git commit -m "feat(admin): confirm ai draft actions safely"
```

### Task 7: Add Draft-Only Write Tools And Audit Logging

**Files:**
- Modify: `tests/test_admin_ai_console.py`
- Modify: `admin_ai.py`
- Modify: `blueprints/admin/ai_console.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_confirmed_blog_draft_action_creates_unpublished_post_and_logs_audit(self) -> None:
        import admin_ai

        result = admin_ai.execute_pending_admin_ai_action(
            {
                'tool_name': 'create_blog_draft',
                'arguments': {
                    'title': 'AI Draft Title',
                    'summary': 'AI Draft Summary',
                    'body': 'AI Draft Body',
                },
            },
            db_path=self.db_path,
        )

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT title, excerpt, body, published FROM blog_posts WHERE id = ?",
            (result['target_id'],),
        ).fetchone()
        conn.close()

        self.assertEqual(row['title'], 'AI Draft Title')
        self.assertEqual(row['excerpt'], 'AI Draft Summary')
        self.assertEqual(row['published'], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_confirmed_blog_draft_action_creates_unpublished_post_and_logs_audit -q`

Expected: FAIL because `create_blog_draft` execution does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# admin_ai.py
def _slugify(value: str) -> str:
    import re
    return re.sub(r'[^a-z0-9]+', '-', (value or '').strip().lower()).strip('-')

def _create_blog_draft(conn, args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get('title') or '').strip()
    summary = (args.get('summary') or '').strip()
    body = (args.get('body') or '').strip()
    if not title or not body:
        raise ValueError('Draft title and body are required.')
    cursor = conn.execute(
        '''
        INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
        VALUES (?, ?, ?, ?, ?, 0)
        ''',
        (title, _slugify(title), body, summary, 'Montana Blotter AI'),
    )
    conn.commit()
    return {'target_id': int(cursor.lastrowid), 'message': 'Draft created'}

def run_registered_tool(name: str, args: dict[str, Any], *, db_path: str | None = None) -> Any:
    ...
    if name == "create_blog_draft":
        return _create_blog_draft(conn, args)
```

```python
# blueprints/admin/ai_console.py
_log_admin_action(
    'admin_ai_action_executed',
    'admin_ai',
    metadata={
        'tool_name': payload['tool_name'],
        'arguments': payload['arguments'],
        'result': result,
    },
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_confirmed_blog_draft_action_creates_unpublished_post_and_logs_audit -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_admin_ai_console.py admin_ai.py blueprints/admin/ai_console.py
git commit -m "feat(admin): add ai draft action execution and audit logging"
```

### Task 8: Add The Kimi-Orchestrated Query Layer

**Files:**
- Modify: `admin_ai.py`
- Modify: `tests/test_admin_ai_console.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_run_admin_ai_query_returns_pending_action_for_write_intent(self) -> None:
        import admin_ai

        class FakeToolCall:
            id = 'tool-1'
            function = type('Fn', (), {
                'name': 'create_blog_draft',
                'arguments': '{"title":"AI Draft Title","summary":"AI Draft Summary","body":"AI Draft Body"}',
            })()

        class FakeMessage:
            content = 'I can prepare that draft.'
            tool_calls = [FakeToolCall()]

        fake_response = type('Resp', (), {
            'choices': [type('Choice', (), {'message': FakeMessage()})()]
        })()

        with mock.patch('admin_ai.create_kimi_client') as mocked_client:
            mocked_client.return_value.chat.completions.create.return_value = fake_response
            result = admin_ai.run_admin_ai_query('Draft a blog post', db_path=self.db_path)

        self.assertEqual(result['pending_action']['tool_name'], 'create_blog_draft')
        self.assertEqual(result['pending_action']['arguments']['title'], 'AI Draft Title')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_run_admin_ai_query_returns_pending_action_for_write_intent -q`

Expected: FAIL because `run_admin_ai_query` is still a placeholder.

- [ ] **Step 3: Write minimal implementation**

```python
# admin_ai.py
READ_ONLY_TOOLS = {'search_records', 'get_missing_persons_summary', 'get_recent_posts', 'get_subscriber_counts'}
WRITE_INTENT_TOOLS = {'create_blog_draft', 'update_blog_draft', 'create_facebook_draft', 'create_email_draft'}

def create_kimi_client():
    from openai import OpenAI
    ...

def run_admin_ai_query(question: str, *, model: str = DEFAULT_MODEL, db_path: str | None = None) -> dict[str, Any]:
    client = create_kimi_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': 'Use tools for Montana Blotter admin tasks. Never assume a write action is already approved.'},
            {'role': 'user', 'content': question},
        ],
        tools=TOOLS,
    )
    assistant_message = response.choices[0].message
    transcript = [{'role': 'assistant', 'content': assistant_message.content or ''}]

    if not getattr(assistant_message, 'tool_calls', None):
        return {'answer': assistant_message.content or '', 'transcript': transcript, 'pending_action': None}

    tool_call = assistant_message.tool_calls[0]
    tool_name = tool_call.function.name
    tool_args = json.loads(tool_call.function.arguments or '{}')

    if tool_name in WRITE_INTENT_TOOLS:
        pending_action = build_pending_action(tool_name, assistant_message.content or tool_name, tool_args)
        return {'answer': assistant_message.content or '', 'transcript': transcript, 'pending_action': pending_action}

    tool_result = run_registered_tool(tool_name, tool_args, db_path=db_path)
    transcript.append({'role': 'tool', 'content': json.dumps(tool_result, default=str)})
    return {'answer': assistant_message.content or '', 'transcript': transcript, 'pending_action': None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py -q`

Expected: PASS for the orchestration test and prior console tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_admin_ai_console.py admin_ai.py
git commit -m "feat(admin): wire kimi orchestration into ai console"
```

### Task 9: Finish The Admin Template And Navigation

**Files:**
- Modify: `templates/admin_ai_console.html`
- Modify: `templates/admin_dashboard.html`
- Modify: `tests/test_admin_ai_console.py`

- [ ] **Step 1: Write the failing test**

```python
    def test_admin_ai_console_template_shows_pending_action_controls(self) -> None:
        admin_user_id = self._create_admin_user()
        client = app_module.app.test_client()
        self._login_admin_session(client, admin_user_id)

        with client.session_transaction() as session:
            session['admin_ai_pending_action'] = {
                'token': 'pending-token',
                'tool_name': 'create_blog_draft',
                'summary': 'Create a blog draft',
                'arguments': {'title': 'Draft title'},
                'created_at': 4102444800,
            }

        response = client.get('/admin/ai')
        html = response.get_data(as_text=True)

        self.assertIn('Confirm Draft Action', html)
        self.assertIn('Cancel Pending Action', html)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py::AdminAiConsoleTests::test_admin_ai_console_template_shows_pending_action_controls -q`

Expected: FAIL because the full template controls are not implemented yet.

- [ ] **Step 3: Write minimal implementation**

```html
<!-- templates/admin_ai_console.html -->
<main class="max-w-6xl mx-auto px-6 py-10 space-y-6">
  <section class="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
    <p class="text-sm font-semibold uppercase tracking-[0.14em] text-slate-500">Internal Use Only</p>
    <h1 class="mt-2 text-3xl font-black text-slate-950">Admin AI Console</h1>
    <p class="mt-3 text-sm text-slate-600">Ask questions about records, missing persons, posts, and subscribers. Draft actions always require confirmation before anything is written.</p>
    <form method="post" action="/admin/ai/query" class="mt-6 space-y-3">
      <textarea name="question" class="min-h-[140px] w-full rounded-2xl border border-slate-300 px-4 py-3">{{ question or '' }}</textarea>
      <button type="submit" class="rounded-xl bg-amber-400 px-4 py-2.5 text-sm font-black text-slate-950">Ask Montana Blotter AI</button>
    </form>
  </section>

  {% if transcript %}
  <section class="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
    {% for item in transcript %}
      <div class="mb-4">
        <p class="text-xs font-black uppercase tracking-[0.12em] text-slate-500">{{ item.role }}</p>
        <pre class="mt-2 whitespace-pre-wrap text-sm text-slate-800">{{ item.content }}</pre>
      </div>
    {% endfor %}
  </section>
  {% endif %}

  {% if pending_action %}
  <section class="rounded-3xl border border-amber-300 bg-amber-50 p-6 shadow-sm">
    <h2 class="text-xl font-black text-slate-950">Confirm Draft Action</h2>
    <p class="mt-2 text-sm text-slate-700">{{ pending_action.summary }}</p>
    <pre class="mt-3 whitespace-pre-wrap text-xs text-slate-700">{{ pending_action.arguments | tojson(indent=2) }}</pre>
    <div class="mt-4 flex gap-3">
      <form method="post" action="/admin/ai/confirm">
        <input type="hidden" name="token" value="{{ pending_action.token }}">
        <button type="submit" class="rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-black text-white">Confirm Draft Action</button>
      </form>
      <form method="post" action="/admin/ai/cancel">
        <button type="submit" class="rounded-xl border border-slate-300 px-4 py-2.5 text-sm font-semibold text-slate-700">Cancel Pending Action</button>
      </form>
    </div>
  </section>
  {% endif %}
</main>
```

```html
<!-- templates/admin_dashboard.html -->
<a href="/admin/ai" class="inline-flex items-center justify-center rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition">AI Console</a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_admin_ai_console.py templates/admin_ai_console.html templates/admin_dashboard.html
git commit -m "feat(admin): finish ai console interface"
```

### Task 10: Final Focused Verification

**Files:**
- Verify only

- [ ] **Step 1: Run focused admin AI tests**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py -q`

Expected: PASS

- [ ] **Step 2: Run shared Kimi tool tests**

Run: `./venv/bin/python -m pytest tests/test_kimi_sqlite_agent.py -q`

Expected: PASS

- [ ] **Step 3: Run a combined smoke subset**

Run: `./venv/bin/python -m pytest tests/test_admin_ai_console.py tests/test_kimi_sqlite_agent.py tests/test_missing_persons.py -q`

Expected: PASS

- [ ] **Step 4: Manual admin smoke check**

Run: `./venv/bin/python -c "import app; print('/admin/ai' in [str(rule) for rule in app.app.url_map.iter_rules()])"`

Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add admin_ai.py blueprints/admin/ai_console.py blueprints/admin/__init__.py templates/admin_ai_console.html templates/admin_dashboard.html tests/test_admin_ai_console.py kimi_sqlite_agent.py requirements.txt
git commit -m "feat(admin): add kimi-powered ai console"
```

## Self-Review

- Spec coverage:
  - admin-only page and endpoints: Tasks 1, 2, 4, 6
  - shared server-side tool registry: Task 3
  - draft-only action tools with confirmation: Tasks 5, 6, 7, 8
  - audit logging: Task 7
  - admin template and explicit action UI: Task 9
  - focused verification: Task 10
- Placeholder scan:
  - no `TODO`, `TBD`, or implicit “handle later” steps remain
- Type consistency:
  - session key is consistently `admin_ai_pending_action`
  - route names stay `/admin/ai`, `/admin/ai/query`, `/admin/ai/confirm`
  - service entry points stay `run_registered_tool`, `run_admin_ai_query`, and `execute_pending_admin_ai_action`
