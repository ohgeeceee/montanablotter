from __future__ import annotations

import json
import os
import sqlite3
import time
import secrets
import re
from typing import Any

import config
from utils.app_settings import _save_app_setting


PENDING_ACTION_SESSION_KEY = "admin_ai_pending_action"
PENDING_ACTION_TTL_SECONDS = 900


DEFAULT_MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a Montana Blotter admin AI assistant. "
    "Use the available tools for factual answers. "
    "Never assume a write action has already been approved."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_records",
        "description": "Search Montana blotter records by county and keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "county": {"type": "string", "description": "Optional county filter."},
                "keyword": {"type": "string", "description": "Optional keyword filter."},
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of rows to return.",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["limit"],
        },
    },
    {
        "name": "get_missing_persons_summary",
        "description": "Get current missing-person counts and source stats.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_recent_posts",
        "description": "Get recent blog post metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of rows to return.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["limit"],
        },
    },
    {
        "name": "get_subscriber_counts",
        "description": "Get bounded subscriber totals and recent signup stats.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_blog_draft",
        "description": "Create a draft blog post. This requires admin confirmation before execution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "body": {"type": "string"},
                "author": {"type": "string"},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "update_blog_draft",
        "description": "Update an existing blog draft. This requires admin confirmation before execution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "integer"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "body": {"type": "string"},
                "author": {"type": "string"},
            },
            "required": ["post_id"],
        },
    },
    {
        "name": "create_facebook_draft",
        "description": "Create a queued Facebook draft for an existing post. This requires admin confirmation before execution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "integer"},
                "custom_message": {"type": "string"},
            },
            "required": ["post_id"],
        },
    },
    {
        "name": "create_email_draft",
        "description": "Save a sponsor email draft in app settings. This requires admin confirmation before execution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "sponsor_name": {"type": "string"},
                "headline": {"type": "string"},
                "body": {"type": "string"},
                "cta_label": {"type": "string"},
                "cta_url": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["subject", "headline", "body"],
        },
    },
]

READ_ONLY_TOOLS = {
    "search_records",
    "get_missing_persons_summary",
    "get_recent_posts",
    "get_subscriber_counts",
}

WRITE_INTENT_TOOLS = {
    "create_blog_draft",
    "update_blog_draft",
    "create_facebook_draft",
    "create_email_draft",
}


def connect_db(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path, timeout=float(getattr(config, "DB_TIMEOUT_SECONDS", 30) or 30))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(getattr(config, 'DB_BUSY_TIMEOUT_MS', 30000) or 30000)}")
    return conn


def _search_records(conn: sqlite3.Connection, args: dict[str, Any]) -> list[dict[str, Any]]:
    county = str(args.get("county") or "").strip()
    keyword = str(args.get("keyword") or "").strip()
    limit = max(1, min(int(args.get("limit", 10)), 20))

    sql = """
        SELECT id, county, incident_type, incident, location, created_at
        FROM records
        WHERE 1 = 1
    """
    params: list[Any] = []

    if county:
        sql += " AND county = ?"
        params.append(county)

    if keyword:
        token = f"%{keyword}%"
        sql += """
            AND (
                COALESCE(incident_type, '') LIKE ?
                OR COALESCE(incident, '') LIKE ?
                OR COALESCE(details, '') LIKE ?
                OR COALESCE(location, '') LIKE ?
            )
        """
        params.extend([token, token, token, token])

    sql += " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def _get_missing_persons_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = conn.execute(
        """
        SELECT status, COUNT(*) AS total
        FROM missing_persons
        GROUP BY status
        ORDER BY status
        """
    ).fetchall()
    stats = conn.execute(
        """
        SELECT total_active, children, indigenous, synced_at
        FROM missing_person_source_stats
        WHERE id = 1
        """
    ).fetchone()
    return {
        "counts_by_status": [dict(row) for row in counts],
        "source_stats": dict(stats) if stats else None,
    }


def _get_recent_posts(conn: sqlite3.Connection, args: dict[str, Any]) -> list[dict[str, Any]]:
    limit = max(1, min(int(args.get("limit", 5)), 10))
    rows = conn.execute(
        """
        SELECT id, title, slug, published, created_at, updated_at
        FROM blog_posts
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _get_subscriber_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM subscribers WHERE COALESCE(active, 1) = 1"
    ).fetchone()
    new_7d = conn.execute(
        "SELECT COUNT(*) AS total FROM subscribers WHERE datetime(created_at) >= datetime('now', '-7 days')"
    ).fetchone()
    return {
        "active_subscribers": int((total["total"] if total else 0) or 0),
        "new_subscribers_7d": int((new_7d["total"] if new_7d else 0) or 0),
    }


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")


def _create_blog_draft(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    title = (args.get("title") or "").strip()[:255]
    summary = (args.get("summary") or "").strip()[:1000]
    body = (args.get("body") or "").strip()[:20000]
    author = (args.get("author") or "Montana Blotter AI").strip()[:120] or "Montana Blotter AI"
    if not title or not body:
        raise ValueError("Draft title and body are required.")
    slug = _slugify(title) or f"draft-{int(time.time())}"
    cursor = conn.execute(
        """
        INSERT INTO blog_posts (title, slug, body, excerpt, author, published)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (title, slug, body, summary, author),
    )
    conn.commit()
    return {
        "target_id": int(cursor.lastrowid or 0),
        "message": "Draft created",
    }


def _update_blog_draft(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    post_id = int(args.get("post_id") or 0)
    row = conn.execute(
        "SELECT id, title, body, excerpt, author, published FROM blog_posts WHERE id = ?",
        (post_id,),
    ).fetchone()
    if not row:
        raise ValueError("Draft post not found.")
    if int(row["published"] or 0) != 0:
        raise ValueError("Only draft posts can be updated.")
    title = (args.get("title") or row["title"] or "").strip()[:255]
    summary = (args.get("summary") if "summary" in args else row["excerpt"]) or ""
    summary = str(summary).strip()[:1000]
    body = (args.get("body") if "body" in args else row["body"]) or ""
    body = str(body).strip()[:20000]
    author = (args.get("author") if "author" in args else row["author"]) or "Montana Blotter AI"
    author = str(author).strip()[:120]
    if not title or not body:
        raise ValueError("Draft title and body are required.")
    conn.execute(
        """
        UPDATE blog_posts
        SET title = ?, slug = ?, body = ?, excerpt = ?, author = ?, updated_at = datetime('now')
        WHERE id = ? AND published = 0
        """,
        (title, _slugify(title) or row["title"], body, summary, author, post_id),
    )
    conn.commit()
    return {
        "target_id": post_id,
        "message": "Draft updated",
    }


def _create_facebook_draft(conn: sqlite3.Connection, args: dict[str, Any], *, acting_user_id: int | None = None) -> dict[str, Any]:
    from facebook_publisher import queue_post

    post_id = int(args.get("post_id") or 0)
    custom_message = (args.get("custom_message") or "").strip()[:5000] or None
    result = queue_post(
        post_id,
        created_by_user_id=acting_user_id,
        enqueue_source="admin_ai",
        custom_message=custom_message,
        conn=conn,
    )
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "Could not queue Facebook draft."))
    conn.commit()
    return {
        "target_id": int(result.get("queue_id") or 0),
        "message": "Facebook draft queued",
        "status": result.get("status") or "queued",
    }


def _create_email_draft(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    field_specs = {
        "subject": ("email_ops.sponsor_announcement.subject", 255),
        "sponsor_name": ("email_ops.sponsor_announcement.sponsor_name", 120),
        "headline": ("email_ops.sponsor_announcement.headline", 220),
        "body": ("email_ops.sponsor_announcement.body", 4000),
        "cta_label": ("email_ops.sponsor_announcement.cta_label", 80),
        "cta_url": ("email_ops.sponsor_announcement.cta_url", 500),
        "notes": ("email_ops.sponsor_announcement.notes", 500),
    }
    draft: dict[str, str] = {}
    for field, (key, max_length) in field_specs.items():
        value = str(args.get(field) or "").strip()[:max_length]
        draft[field] = value
        _save_app_setting(conn, key, value)
    if not draft["subject"] or not draft["headline"] or not draft["body"]:
        raise ValueError("Email draft subject, headline, and body are required.")
    conn.commit()
    return {
        "target_id": draft["subject"][:120] or "email-draft",
        "message": "Email draft saved",
    }


def run_registered_tool(
    name: str,
    args: dict[str, Any],
    *,
    db_path: str | None = None,
    acting_user_id: int | None = None,
) -> Any:
    conn = connect_db(db_path)
    try:
        if name == "search_records":
            return _search_records(conn, args)
        if name == "get_missing_persons_summary":
            return _get_missing_persons_summary(conn)
        if name == "get_recent_posts":
            return _get_recent_posts(conn, args)
        if name == "get_subscriber_counts":
            return _get_subscriber_counts(conn)
        if name == "create_blog_draft":
            return _create_blog_draft(conn, args)
        if name == "update_blog_draft":
            return _update_blog_draft(conn, args)
        if name == "create_facebook_draft":
            return _create_facebook_draft(conn, args, acting_user_id=acting_user_id)
        if name == "create_email_draft":
            return _create_email_draft(conn, args)
        raise ValueError(f"Unknown tool: {name}")
    finally:
        conn.close()


def build_pending_action(tool_name: str, summary: str, arguments: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    return {
        "token": secrets.token_urlsafe(24),
        "tool_name": tool_name,
        "summary": (summary or tool_name).strip()[:500],
        "arguments": arguments,
        "created_at": now,
        "expires_at": now + PENDING_ACTION_TTL_SECONDS,
    }


def save_pending_action(
    user_id: int | None,
    pending_action: dict[str, Any],
    *,
    db_path: str | None = None,
) -> None:
    """Write pending action to the DB and return; session stores only the token."""
    conn = connect_db(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO admin_ai_pending_actions
                (token, user_id, tool_name, summary, arguments_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pending_action["token"],
                user_id,
                pending_action["tool_name"],
                pending_action.get("summary", ""),
                json.dumps(pending_action.get("arguments") or {}, default=str),
                int(pending_action.get("created_at") or time.time()),
                int(pending_action.get("expires_at") or time.time() + PENDING_ACTION_TTL_SECONDS),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_pending_action_from_db(token: str, *, db_path: str | None = None) -> dict[str, Any] | None:
    conn = connect_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM admin_ai_pending_actions WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return None
        return {
            "token": row["token"],
            "tool_name": row["tool_name"],
            "summary": row["summary"] or "",
            "arguments": json.loads(row["arguments_json"] or "{}"),
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }
    finally:
        conn.close()


def get_pending_action(session_obj, *, db_path: str | None = None) -> dict[str, Any] | None:
    token = session_obj.get(PENDING_ACTION_SESSION_KEY)
    if not token or not isinstance(token, str):
        return None
    return _fetch_pending_action_from_db(token, db_path=db_path)


def validate_pending_action(session_obj, token: str, *, db_path: str | None = None) -> dict[str, Any]:
    session_token = session_obj.get(PENDING_ACTION_SESSION_KEY)
    if not session_token:
        raise ValueError("No pending action found.")
    if session_token != token:
        raise ValueError("Pending action token mismatch.")
    payload = _fetch_pending_action_from_db(token, db_path=db_path)
    if not payload:
        raise ValueError("Pending action not found or already consumed.")
    if int(time.time()) > int(payload.get("expires_at") or 0):
        raise ValueError("Pending action expired.")
    return payload


def clear_pending_action(session_obj, *, db_path: str | None = None) -> None:
    token = session_obj.pop(PENDING_ACTION_SESSION_KEY, None)
    if not token or not isinstance(token, str):
        return
    conn = connect_db(db_path)
    try:
        conn.execute("DELETE FROM admin_ai_pending_actions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def execute_pending_admin_ai_action(
    payload: dict[str, Any],
    *,
    db_path: str | None = None,
    acting_user_id: int | None = None,
) -> dict[str, Any]:
    result = run_registered_tool(
        payload["tool_name"],
        payload["arguments"],
        db_path=db_path,
        acting_user_id=acting_user_id,
    )
    if isinstance(result, dict):
        return result
    return {
        "message": "Action completed.",
        "result": result,
    }


def create_claude_client():
    import anthropic
    api_key = getattr(config, "ANTHROPIC_API_KEY", None) or os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=api_key)


def run_admin_ai_query(
    question: str,
    *,
    model: str = DEFAULT_MODEL,
    db_path: str | None = None,
) -> dict[str, Any]:
    question = (question or "").strip()
    if not question:
        return {"answer": "", "transcript": [], "pending_action": None}

    import anthropic as _anthropic

    client = create_claude_client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    transcript: list[dict[str, Any]] = []

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=messages,
    )

    first_text = next((b.text for b in response.content if hasattr(b, "text")), "")
    if first_text:
        transcript.append({"role": "assistant", "content": first_text})

    if response.stop_reason != "tool_use":
        return {"answer": first_text, "transcript": transcript, "pending_action": None}

    tool_use = next(b for b in response.content if isinstance(b, _anthropic.types.ToolUseBlock))
    tool_name = tool_use.name
    tool_args = dict(tool_use.input or {})

    if tool_name in WRITE_INTENT_TOOLS:
        return {
            "answer": first_text,
            "transcript": transcript,
            "pending_action": build_pending_action(tool_name, first_text or tool_name, tool_args),
        }

    tool_result = run_registered_tool(tool_name, tool_args, db_path=db_path)
    transcript.append({"role": "tool", "content": json.dumps(tool_result, indent=2, default=str)})

    final_response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=TOOLS,
        messages=[
            {"role": "user", "content": question},
            {"role": "assistant", "content": response.content},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": json.dumps(tool_result, default=str),
                    }
                ],
            },
        ],
    )
    final_text = next((b.text for b in final_response.content if hasattr(b, "text")), "")
    if final_text:
        transcript.append({"role": "assistant", "content": final_text})
    return {"answer": final_text, "transcript": transcript, "pending_action": None}


def ask_claude(question: str, *, model: str = DEFAULT_MODEL, db_path: str | None = None) -> str:
    import anthropic as _anthropic

    client = create_claude_client()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system="You are a Montana Blotter data assistant. Use tools when database facts are needed. Do not invent database results.",
        tools=TOOLS,
        messages=[{"role": "user", "content": question}],
    )

    if response.stop_reason != "tool_use":
        return next((b.text for b in response.content if hasattr(b, "text")), "")

    tool_results: list[dict[str, Any]] = []
    for block in response.content:
        if not isinstance(block, _anthropic.types.ToolUseBlock):
            continue
        result = run_registered_tool(block.name, dict(block.input or {}), db_path=db_path)
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": json.dumps(result, default=str),
        })

    final_response = client.messages.create(
        model=model,
        max_tokens=2048,
        system="You are a Montana Blotter data assistant. Use tools when database facts are needed. Do not invent database results.",
        tools=TOOLS,
        messages=[
            {"role": "user", "content": question},
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": tool_results},
        ],
    )
    return next((b.text for b in final_response.content if hasattr(b, "text")), "")


# Backward-compat aliases for sqlite_agent.py and any CLI scripts
ask_kimi = ask_claude
create_kimi_client = create_claude_client
