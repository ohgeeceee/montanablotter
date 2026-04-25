from __future__ import annotations

from flask import render_template, request, session
from flask_login import login_required
from flask_login import current_user

from admin_ai import (
    DEFAULT_MODEL,
    clear_pending_action,
    execute_pending_admin_ai_action,
    get_pending_action,
    run_admin_ai_query,
    validate_pending_action,
)
from blueprints.admin import admin_bp, _log_admin_action, require_role
from db import get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES


def _recent_ai_actions(limit: int = 8) -> list[dict[str, object]]:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, action, target_type, target_id, metadata_json, timestamp
            FROM audit_logs
            WHERE action LIKE 'admin_ai_%'
            ORDER BY datetime(timestamp) DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _friendly_ai_error_message(exc: Exception) -> str:
    message = str(exc or "").strip()
    normalized = message.lower()
    if "invalid authentication" in normalized or "invalid_authentication_error" in normalized:
        return "Kimi API key is invalid."
    if "401" in normalized and "auth" in normalized:
        return "Kimi authentication failed."
    if "set moonshot_api_key or kimi_api_key" in normalized:
        return "Kimi API key is missing from the server environment."
    return f"AI query failed: {message[:300]}" if message else "AI query failed."


@admin_bp.route('/ai')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_ai_console():
    return render_template(
        'admin_ai_console.html',
        transcript=[],
        pending_action=get_pending_action(session),
        recent_actions=_recent_ai_actions(),
        default_model=DEFAULT_MODEL,
        question='',
    )


@admin_bp.route('/ai/query', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_ai_query():
    question = (request.form.get('question') or request.form.get('query') or '').strip()
    try:
        result = run_admin_ai_query(question)
    except Exception as exc:
        result = {
            'answer': '',
            'transcript': [
                {
                    'role': 'assistant',
                    'content': _friendly_ai_error_message(exc),
                }
            ],
            'pending_action': None,
        }
    pending_action = result.get('pending_action')
    if pending_action:
        session['admin_ai_pending_action'] = pending_action
        _log_admin_action(
            'admin_ai_action_proposed',
            'admin_ai',
            metadata={
                'tool_name': pending_action.get('tool_name'),
                'arguments': pending_action.get('arguments'),
                'summary': pending_action.get('summary'),
                'model': DEFAULT_MODEL,
            },
        )
    else:
        clear_pending_action(session)
    return render_template(
        'admin_ai_console.html',
        transcript=result.get('transcript') or [],
        pending_action=pending_action,
        recent_actions=_recent_ai_actions(),
        default_model=DEFAULT_MODEL,
        question=question,
    )


@admin_bp.route('/ai/confirm', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_ai_confirm():
    token = (request.form.get('token') or '').strip()
    try:
        payload = validate_pending_action(session, token)
    except ValueError as exc:
        return str(exc), 400

    result = execute_pending_admin_ai_action(payload, acting_user_id=current_user.id)
    clear_pending_action(session)
    _log_admin_action(
        'admin_ai_action_executed',
        'admin_ai',
        target_id=result.get('target_id') if isinstance(result, dict) else None,
        metadata={
            'tool_name': payload.get('tool_name'),
            'arguments': payload.get('arguments'),
            'result': result,
            'model': DEFAULT_MODEL,
        },
    )
    return render_template(
        'admin_ai_console.html',
        transcript=[{'role': 'assistant', 'content': result.get('message', 'Action completed.')}],
        pending_action=None,
        recent_actions=_recent_ai_actions(),
        default_model=DEFAULT_MODEL,
        question='',
    )


@admin_bp.route('/ai/cancel', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_ai_cancel():
    clear_pending_action(session)
    return render_template(
        'admin_ai_console.html',
        transcript=[],
        pending_action=None,
        recent_actions=_recent_ai_actions(),
        default_model=DEFAULT_MODEL,
        question='',
    )
