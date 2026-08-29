from __future__ import annotations

import json
from flask import jsonify, render_template, request, session
from flask_login import login_required, current_user

from services.admin.ai import (
    DEFAULT_MODEL,
    PENDING_ACTION_SESSION_KEY,
    clear_pending_action,
    execute_pending_admin_ai_action,
    get_pending_action,
    run_admin_ai_query,
    save_pending_action,
    validate_pending_action,
)
from services.agents.events_bus import build_agent_events_bootstrap
from blueprints.admin import admin_bp, _log_admin_action, require_role
from utils.auth_constants import ADMIN_ACCESS_ROLES


def _recent_ai_actions(limit: int = 8) -> list[dict]:
    from db import get_db
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


@admin_bp.route('/system/workspace')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_workspace():
    try:
        bootstrap = build_agent_events_bootstrap(limit=20)
    except Exception:
        bootstrap = {'connected': False, 'agents': [], 'feed_items': [], 'channel': 'agent-events'}
    return render_template(
        'admin_workspace.html',
        default_model=DEFAULT_MODEL,
        bootstrap=json.dumps(bootstrap, default=str),
    )


@admin_bp.route('/system/workspace/chat', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_workspace_chat():
    payload = request.get_json(force=True, silent=True) or {}
    question = str(payload.get('question') or '').strip()
    if not question:
        return jsonify({'answer': '', 'transcript': [], 'pending_action': None}), 400

    try:
        result = run_admin_ai_query(question)
    except Exception as exc:
        result = {
            'answer': '',
            'transcript': [{'role': 'assistant', 'content': _friendly_ai_error_message(exc)}],
            'pending_action': None,
        }

    pending_action = result.get('pending_action')
    if pending_action:
        save_pending_action(current_user.id, pending_action)
        session[PENDING_ACTION_SESSION_KEY] = pending_action['token']
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

    return jsonify(result)


@admin_bp.route('/system/workspace/confirm', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_workspace_confirm():
    payload = request.get_json(force=True, silent=True) or {}
    token = str(payload.get('token') or '').strip()
    try:
        action_payload = validate_pending_action(session, token)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    result = execute_pending_admin_ai_action(action_payload, acting_user_id=current_user.id)
    clear_pending_action(session)
    _log_admin_action(
        'admin_ai_action_executed',
        'admin_ai',
        target_id=result.get('target_id') if isinstance(result, dict) else None,
        metadata={
            'tool_name': action_payload.get('tool_name'),
            'arguments': action_payload.get('arguments'),
            'result': result,
            'model': DEFAULT_MODEL,
        },
    )
    return jsonify(result)


@admin_bp.route('/system/workspace/cancel', methods=['POST'])
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_workspace_cancel():
    clear_pending_action(session)
    return jsonify({'ok': True})


@admin_bp.route('/system/workspace/snapshot')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_workspace_snapshot():
    return jsonify(build_agent_events_bootstrap(limit=20))
