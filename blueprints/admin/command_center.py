from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from flask import jsonify, redirect, render_template, url_for
from flask_login import login_required

from services.agents.mission_control import build_snapshot, recent_events
from services.agents.registry import build_registry
from services.agents.status import system_snapshot
from blueprints.admin import admin_bp, require_role
from blueprints.admin.agents import _agent_snapshot
from db import connect_db, get_db
from utils.auth_constants import ADMIN_ACCESS_ROLES

_PIPELINE_STAGES = ['extract', 'parse', 'summarize', 'publish']


def _db():
    return connect_db(timeout_seconds=1.0, busy_timeout_ms=1000)


def _pipeline_jobs() -> list[dict]:
    try:
        conn = _db()
        rows = conn.execute(
            """
            SELECT
                ij.id,
                ij.status,
                ij.retry_count,
                ij.last_error,
                ij.started_at,
                ij.finished_at,
                sd.filename,
                sd.source_type
            FROM ingestion_jobs ij
            LEFT JOIN source_documents sd ON sd.id = ij.source_document_id
            ORDER BY ij.id DESC
            LIMIT 20
            """
        ).fetchall()

        ids = [r['id'] for r in rows]
        event_rows = []
        if ids:
            placeholders = ','.join('?' * len(ids))
            event_rows = conn.execute(
                f"""
                SELECT ingestion_job_id, stage, status
                FROM pipeline_events
                WHERE ingestion_job_id IN ({placeholders})
                ORDER BY id ASC
                """,
                ids,
            ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    stages_by_job: dict[int, list[str]] = {}
    for er in event_rows:
        jid = er['ingestion_job_id']
        stage = er['stage'] or ''
        if jid not in stages_by_job:
            stages_by_job[jid] = []
        if stage and stage not in stages_by_job[jid]:
            stages_by_job[jid].append(stage)

    now = datetime.now(timezone.utc)
    jobs = []
    for row in rows:
        jid = int(row['id'])
        started_at = row['started_at'] or ''
        age_s = 0
        if started_at:
            try:
                raw = started_at.strip().replace('Z', '+00:00')
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_s = int((now - dt).total_seconds())
            except (ValueError, TypeError):
                pass

        filename = (row['filename'] or '').strip() or f'job-{jid}'
        stages_done = stages_by_job.get(jid, [])

        jobs.append({
            'id': jid,
            'filename': filename,
            'source_type': row['source_type'] or '',
            'status': row['status'] or 'unknown',
            'stages_done': stages_done,
            'retry_count': int(row['retry_count'] or 0),
            'last_error': (row['last_error'] or '')[:120],
            'started_at': started_at,
            'finished_at': row['finished_at'] or '',
            'age_s': age_s,
        })
    return jobs


def _alert_rollup(conn: sqlite3.Connection) -> dict:
    try:
        rollup = {
            'ingestion': conn.execute(
                "SELECT COUNT(*) FROM ingestion_source_alerts WHERE state = 'open'"
            ).fetchone()[0],
            'courts': conn.execute(
                "SELECT COUNT(*) FROM court_source_alerts WHERE state = 'open'"
            ).fetchone()[0],
            'meetings': conn.execute(
                "SELECT COUNT(*) FROM meeting_source_alerts WHERE state = 'open'"
            ).fetchone()[0],
        }
        rollup['total'] = rollup['ingestion'] + rollup['courts'] + rollup['meetings']
        return rollup
    except sqlite3.Error:
        return {'ingestion': 0, 'courts': 0, 'meetings': 0, 'total': 0}


def _source_coverage_snapshot(conn: sqlite3.Connection) -> dict:
    import app as _app_module

    try:
        coverage = _app_module._build_source_coverage_dashboard(conn)
    except Exception:
        return {'summary': {}, 'entries': []}
    return {
        'summary': coverage.get('summary') or {},
        'entries': [
            {
                'agency': entry.get('agency') or '',
                'category': entry.get('category') or '',
                'freshness': entry.get('freshness') or '',
                'freshness_tone': entry.get('freshness_tone') or 'slate',
            }
            for entry in (coverage.get('entries') or [])[:5]
        ],
    }


def _stats() -> dict:
    try:
        conn = get_db()
        total_records = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        total_blotters = conn.execute('SELECT COUNT(*) FROM blotters').fetchone()[0]
        today_records = conn.execute(
            "SELECT COUNT(*) FROM records WHERE created_at >= date('now')"
        ).fetchone()[0]
        failed_24h = conn.execute(
            "SELECT COUNT(*) FROM ingestion_jobs WHERE status='failed'"
            " AND started_at >= datetime('now','-1 day')"
        ).fetchone()[0]
        total_counties = conn.execute(
            'SELECT COUNT(DISTINCT county) FROM records'
        ).fetchone()[0]
        alert_rollup = _alert_rollup(conn)
        source_coverage = _source_coverage_snapshot(conn)
        conn.close()
    except sqlite3.Error:
        return {
            'total_records': 0,
            'total_blotters': 0,
            'today_records': 0,
            'failed_24h': 0,
            'total_counties': 0,
            'alert_rollup': {'ingestion': 0, 'courts': 0, 'meetings': 0, 'total': 0},
            'source_coverage': {'summary': {}, 'entries': []},
        }
    return {
        'total_records': total_records,
        'total_blotters': total_blotters,
        'today_records': today_records,
        'failed_24h': failed_24h,
        'total_counties': total_counties,
        'alert_rollup': alert_rollup,
        'source_coverage': source_coverage,
    }


def _build_feed() -> dict:
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    try:
        conn = _db()
        snapshot = build_snapshot(conn)
        hermes_agents = snapshot.get('agents', [])
        events = recent_events(conn, limit=40)
        conn.close()
    except Exception:
        hermes_agents = []
        events = []

    clean_events = []
    for ev in events:
        clean_events.append({
            'id': ev.get('id'),
            'agent_id': ev.get('agent_id') or '',
            'event_type': ev.get('event_type') or '',
            'state': ev.get('state') or '',
            'message': (ev.get('message') or '')[:200],
            'tool_name': ev.get('tool_name') or '',
            'created_at': ev.get('created_at') or '',
        })

    oc = _agent_snapshot()
    openclaw_agents = [
        {
            'name': name,
            'status': info.get('status', 'unknown'),
            'last_seen': info.get('last_seen', ''),
            'last_msg': info.get('last_msg', ''),
        }
        for name, info in (oc.get('agents') or {}).items()
    ]

    sys = system_snapshot()
    services_raw = sys.get('services') or []
    services: dict[str, dict] = {}
    if isinstance(services_raw, list):
        for svc in services_raw:
            if isinstance(svc, dict):
                label = svc.get('label') or svc.get('service') or 'unknown'
                active = svc.get('active', False)
                services[label] = {'status': 'active' if active else 'inactive'}
    elif isinstance(services_raw, dict):
        services = {k: v for k, v in services_raw.items()}

    queues = sys.get('queues') or []
    queue_depth = 0
    if isinstance(queues, list):
        queue_depth = sum(int(q.get('count', 0) or 0) for q in queues if isinstance(q, dict))

    return {
        'captured_at': now_iso,
        'hermes_agents': hermes_agents,
        'openclaw_agents': openclaw_agents,
        'recent_events': clean_events,
        'pipeline_jobs': _pipeline_jobs(),
        'system': {
            'services': services,
            'queue_depth': queue_depth,
            'alerts': sys.get('alerts', []),
        },
        'stats': _stats(),
    }


@admin_bp.route('/operations/live')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_command_center():
    return render_template('admin_command_center.html')


@admin_bp.route("/operations/command-center/runbook")
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_command_center_runbook():
    return render_template('admin_mission_control_runbook.html')


@admin_bp.route('/api/command-center/feed')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_command_center_feed():
    return jsonify(_build_feed())


@admin_bp.route('/api/agents/registry')
@login_required
@require_role(*ADMIN_ACCESS_ROLES)
def admin_agents_registry():
    return jsonify(build_registry())
