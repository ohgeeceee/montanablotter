from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from flask import Response, current_app, jsonify, redirect, render_template, send_from_directory
import config
from flask import stream_with_context
from flask_login import login_required

from services.agents.status import system_snapshot
from blueprints.admin import admin_bp
from db import connect_db, connect_page_views

KNOWN_AGENTS = ['main', 'reporter', 'scout', 'clerk', 'bailbot']
_AGENT_RE = re.compile(r'lane=session:agent:(\w+):')
ERROR_LEVELS = {'ERROR', 'ERR', 'CRITICAL', 'FATAL'}


def _log_path() -> Path:
    return Path(f"/tmp/openclaw/openclaw-{date.today().isoformat()}.log")


def _extract_agent(entry: dict) -> str:
    """Return agent name from a parsed log entry, or 'system'."""
    for key, val in entry.items():
        if key == '_meta':
            continue
        if isinstance(val, str):
            m = _AGENT_RE.search(val)
            if m:
                return m.group(1)
    return 'system'


def _parse_entry(raw: str) -> dict | None:
    """Parse a JSONL log line. Returns None if invalid."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return {'agent': 'system', 'level': 'INFO', 'time': '', 'msg': raw, 'raw': raw}
    agent = _extract_agent(entry)
    meta = entry.get('_meta') or {}
    level = meta.get('logLevelName', 'INFO')
    ts = entry.get('time', '')
    parts = []
    for k in sorted(k for k in entry if k.isdigit()):
        val = entry[k]
        if isinstance(val, str) and val.strip():
            parts.append(val.strip()[:200])
    msg = ' | '.join(parts) if parts else ''
    return {'agent': agent, 'level': level, 'time': ts, 'msg': msg, 'raw': raw}


def _parse_sqlite_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_event_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    parsed = _parse_sqlite_time(raw)
    if parsed is not None:
        return parsed
    try:
        parsed = datetime.strptime(raw[:19].replace('T', ' '), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc).astimezone(timezone.utc)


def _event_iso(value: str | None) -> str:
    parsed = _parse_event_time(value)
    if not parsed:
        return ''
    return parsed.strftime('%Y-%m-%dT%H:%M:%SZ')


def _referrer_host(value: str | None) -> str:
    ref = (value or '').strip()
    if not ref:
        return ''
    try:
        return (urlparse(ref).netloc or '').strip()
    except Exception:
        return ''


def _db():
    return connect_db(timeout_seconds=0.75, busy_timeout_ms=750)


def _client_snapshot(*, window_minutes: int = 15, recent_limit: int = 140, active_limit: int = 12) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    payload: dict[str, object] = {
        'ok': True,
        'window_minutes': window_minutes,
        'active_clients': 0,
        'events_last_minute': 0,
        'events_window': 0,
        'top_paths': [],
        'active_sessions': [],
        'recent_events': [],
        'funnel': {'home': 0, 'login': 0, 'register': 0, 'subscribe': 0},
        'alerts': [],
    }
    try:
        conn = connect_page_views()
        rows = conn.execute(
            '''
            SELECT id, path, ip_hash, referrer, created_at
            FROM page_views
            WHERE created_at >= datetime('now', ?)
            ORDER BY id DESC
            LIMIT ?
            ''',
            (f'-{max(1, int(window_minutes))} minutes', int(max(10, recent_limit))),
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        payload['ok'] = False
        payload['error'] = str(exc)
        return payload

    sessions: dict[str, dict[str, object]] = {}
    path_counts: dict[str, int] = {}
    funnel_sets: dict[str, set[str]] = {'home': set(), 'login': set(), 'register': set(), 'subscribe': set()}
    one_minute_ago = now.timestamp() - 60

    def _funnel_stage(path: str) -> str | None:
        normalized = (path or '').strip().lower()
        if normalized in {'', '/'} or normalized.startswith('/index') or normalized.startswith('/dashboard'):
            return 'home'
        if normalized.startswith('/login'):
            return 'login'
        if normalized.startswith('/register'):
            return 'register'
        if normalized.startswith('/subscribe'):
            return 'subscribe'
        return None

    for row in rows:
        path = (row['path'] or '').strip()[:200] or '/'
        ip_hash = (row['ip_hash'] or '').strip()[:16] or 'anonymous'
        created_at = _event_iso(row['created_at'])
        ref_host = _referrer_host(row['referrer'])
        parsed = _parse_sqlite_time(row['created_at'])
        ts = parsed.timestamp() if parsed else 0

        payload['recent_events'].append(
            {
                'id': int(row['id']),
                'path': path,
                'ip_hash': ip_hash,
                'referrer_host': ref_host,
                'created_at': created_at,
            }
        )
        path_counts[path] = path_counts.get(path, 0) + 1
        if ts >= one_minute_ago:
            payload['events_last_minute'] = int(payload['events_last_minute']) + 1
        stage = _funnel_stage(path)
        if stage:
            funnel_sets[stage].add(ip_hash)

        current = sessions.get(ip_hash)
        if current is None:
            sessions[ip_hash] = {
                'ip_hash': ip_hash,
                'hits': 1,
                'last_path': path,
                'last_seen': created_at,
                '_last_seen_ts': ts,
            }
        else:
            current['hits'] = int(current['hits']) + 1
            if ts >= float(current['_last_seen_ts']):
                current['last_path'] = path
                current['last_seen'] = created_at
                current['_last_seen_ts'] = ts

    session_rows = sorted(sessions.values(), key=lambda item: float(item['_last_seen_ts']), reverse=True)
    for item in session_rows[: max(1, int(active_limit))]:
        item.pop('_last_seen_ts', None)
        payload['active_sessions'].append(item)

    payload['events_window'] = len(rows)
    payload['active_clients'] = len(sessions)
    payload['funnel'] = {stage: len(values) for stage, values in funnel_sets.items()}
    payload['top_paths'] = [
        {'path': path, 'count': count}
        for path, count in sorted(path_counts.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    baseline = (payload['events_window'] - payload['events_last_minute']) / max(1, int(window_minutes) - 1)
    if payload['events_last_minute'] >= max(12, int(baseline * 2.2)):
        payload['alerts'].append(
            {
                'type': 'traffic_spike',
                'severity': 'warning',
                'title': 'Traffic spike',
                'message': f"{payload['events_last_minute']} events in the last minute (baseline {baseline:.1f}/min).",
            }
        )
    return payload


def _agent_snapshot() -> dict:
    now = datetime.now(timezone.utc)
    log_path = _log_path()

    agents = {name: {'status': 'unknown', 'last_seen': '', 'last_msg': ''} for name in KNOWN_AGENTS}
    history: dict[str, list] = {name: [] for name in KNOWN_AGENTS}
    productivity: dict[str, dict[str, float | int]] = {
        name: {
            'events_last_minute': 0,
            'events_last_5m': 0,
            'errors_last_5m': 0,
            'events_per_minute': 0.0,
            'error_rate_5m': 0.0,
        }
        for name in KNOWN_AGENTS
    }
    alerts: list[dict[str, object]] = []

    if not log_path.exists():
        return {'agents': agents, 'history': history, 'productivity': productivity, 'alerts': alerts}

    all_entries: list[dict] = []
    try:
        with log_path.open(encoding='utf-8', errors='replace') as fh:
            for line in fh:
                entry = _parse_entry(line)
                if entry:
                    all_entries.append(entry)
    except OSError:
        return {'agents': agents, 'history': history, 'productivity': productivity, 'alerts': alerts}

    now_ts = now.timestamp()
    for entry in all_entries:
        name = entry.get('agent')
        if name not in KNOWN_AGENTS:
            continue
        parsed = _parse_event_time(entry.get('time'))
        if parsed is None:
            continue
        age_seconds = now_ts - parsed.timestamp()
        if age_seconds <= 60:
            productivity[name]['events_last_minute'] = int(productivity[name]['events_last_minute']) + 1
        if age_seconds <= 300:
            productivity[name]['events_last_5m'] = int(productivity[name]['events_last_5m']) + 1
            if (entry.get('level') or '').upper() in ERROR_LEVELS:
                productivity[name]['errors_last_5m'] = int(productivity[name]['errors_last_5m']) + 1

    for entry in reversed(all_entries):
        name = entry['agent']
        if name not in KNOWN_AGENTS:
            continue
        ts_str = entry['time']
        if ts_str and not agents[name]['last_seen']:
            agents[name]['last_seen'] = ts_str
            agents[name]['last_msg'] = entry['msg'][:60]
            try:
                ts = _parse_event_time(ts_str)
                diff = (now - ts).total_seconds() if ts else 3600
                if diff < 30:
                    agents[name]['status'] = 'active'
                elif diff < 600:
                    agents[name]['status'] = 'idle'
                else:
                    agents[name]['status'] = 'seen'
            except Exception:
                agents[name]['status'] = 'seen'

    for entry in reversed(all_entries):
        name = entry['agent']
        if name in KNOWN_AGENTS and len(history[name]) < 100:
            history[name].append(entry)

    for name, stats in productivity.items():
        events_5m = int(stats['events_last_5m'])
        errors_5m = int(stats['errors_last_5m'])
        stats['events_per_minute'] = round(events_5m / 5.0, 2)
        stats['error_rate_5m'] = round((errors_5m / events_5m) * 100, 1) if events_5m else 0.0
        if agents[name]['status'] in {'seen', 'unknown'}:
            alerts.append(
                {
                    'type': 'idle_agent',
                    'severity': 'warning',
                    'title': f"Agent idle: {name}",
                    'message': f"{name} is {agents[name]['status']} (no recent activity).",
                }
            )
        if errors_5m >= 3 and stats['error_rate_5m'] >= 20:
            alerts.append(
                {
                    'type': 'agent_errors',
                    'severity': 'critical',
                    'title': f"Error burst: {name}",
                    'message': f"{errors_5m} errors in last 5m ({stats['error_rate_5m']}% error rate).",
                }
            )

    return {'agents': agents, 'history': history, 'productivity': productivity, 'alerts': alerts}


@admin_bp.route('/agents')
@admin_bp.route('/system/agents')
@login_required
def admin_agents():
    from flask import redirect, url_for

    return redirect(url_for('admin.admin_command_center'))


@admin_bp.route('/office')
@admin_bp.route('/operations/office')
@login_required
def admin_office():
    return redirect(url_for('admin.admin_office_view'))


@admin_bp.route('/office/')
@admin_bp.route('/operations/office/')
@login_required
def admin_office_view():
    # VPS canvas office — live probes of every service on the box.
    static_root = current_app.static_folder or 'static'
    return send_from_directory(static_root, 'office/vps-office.html')


@admin_bp.route('/system/agents/client-snapshot')
@login_required
def admin_agents_client_snapshot():
    return jsonify(_client_snapshot())


@admin_bp.route('/system/agents/snapshot')
@login_required
def admin_agents_snapshot():
    snapshot = _agent_snapshot()
    sys_snapshot = system_snapshot()
    merged_alerts = [
        *(snapshot.get('alerts') or []),
        *(sys_snapshot.get('alerts') or []),
    ]
    return jsonify(
        {
            'agents': snapshot.get('agents') or {},
            'productivity': snapshot.get('productivity') or {},
            'alerts': merged_alerts,
            'system': sys_snapshot,
        }
    )


@admin_bp.route('/system/agents/system-snapshot')
@login_required
def admin_agents_system_snapshot():
    return jsonify(system_snapshot())


@admin_bp.route('/system/agents/stream')
@login_required
def admin_agents_stream():
    def generate():
        payload = {
            'error': 'openclaw disabled',
            'detail': 'Live agent stream is disabled on this host.',
        }
        yield f'data: {json.dumps(payload)}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@admin_bp.route('/system/agents/client-stream')
@login_required
def admin_agents_client_stream():
    def generate():
        snapshot = _client_snapshot(window_minutes=10, recent_limit=40, active_limit=10)
        events = snapshot.get('recent_events') or []
        for row in reversed(events):
            payload = {
                'id': int(row.get('id') or 0),
                'path': (row.get('path') or '').strip()[:200] or '/',
                'ip_hash': (row.get('ip_hash') or '').strip()[:16] or 'anonymous',
                'referrer_host': (row.get('referrer_host') or '').strip()[:120],
                'created_at': _event_iso(row.get('created_at')),
            }
            yield f'data: {json.dumps(payload)}\n\n'
        yield f'event: pulse\ndata: {json.dumps(snapshot)}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
