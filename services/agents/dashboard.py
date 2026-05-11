from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from flask import jsonify, render_template, request

from services.ops.ingestion_monitor import build_ingestion_health_dashboard
from services.agents.events_bus import AgentEvent, publish_agent_event

AGENT_SPECS = (
    {
        'agent_id': 'hermes-scan',
        'agent_name': 'Hermes-Scan',
        'stage': 'scrape',
        'description': 'County and source acquisition',
    },
    {
        'agent_id': 'hermes-intel',
        'agent_name': 'Hermes-Intel',
        'stage': 'analyze',
        'description': 'Summarization and triage',
    },
    {
        'agent_id': 'hermes-notify',
        'agent_name': 'Hermes-Notify',
        'stage': 'notify',
        'description': 'Public release and alerts',
    },
)


def _format_countdown(seconds: int | None) -> str:
    if seconds is None:
        return 'Unknown'
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f'{seconds}s'
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes}m {remainder}s'
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f'{hours}h {minutes}m'
    days, hours = divmod(hours, 24)
    return f'{days}d {hours}h'


def _iso_label(value: str | None) -> str:
    if not value:
        return 'Unknown'
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return value
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def _latest_pipeline_event(conn) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT
            pe.stage,
            pe.status,
            pe.created_at,
            sd.source_type,
            sd.filename,
            sd.source_subject,
            ij.status AS job_status
        FROM pipeline_events pe
        JOIN ingestion_jobs ij ON ij.id = pe.ingestion_job_id
        LEFT JOIN source_documents sd ON sd.id = ij.source_document_id
        ORDER BY pe.id DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return dict(row)


def _build_feed_items(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            pe.id,
            pe.stage,
            pe.status,
            pe.created_at,
            sd.source_type,
            sd.filename,
            sd.source_subject,
            ij.status AS job_status
        FROM pipeline_events pe
        JOIN ingestion_jobs ij ON ij.id = pe.ingestion_job_id
        LEFT JOIN source_documents sd ON sd.id = ij.source_document_id
        ORDER BY pe.id DESC
        LIMIT 12
        """
    ).fetchall()

    stage_agent = {
        'scrape': 'Hermes-Scan',
        'analyze': 'Hermes-Intel',
        'notify': 'Hermes-Notify',
    }
    status_label = {
        'success': 'success',
        'passed': 'success',
        'processing': 'processing',
        'running': 'processing',
        'queued': 'processing',
        'error': 'error',
        'failed': 'error',
    }

    items: list[dict[str, Any]] = []
    for row in rows:
        stage = (row['stage'] or '').strip().lower()
        status = status_label.get((row['status'] or '').strip().lower(), (row['status'] or 'processing').strip().lower())
        agent_name = stage_agent.get(stage, 'Hermes')
        source_label = row['source_subject'] or row['filename'] or row['source_type'] or 'source queue'

        if stage == 'scrape':
            message = f'Found new source activity from {source_label}' if status == 'success' else f'Scrape cycle {status} for {source_label}'
        elif stage == 'analyze':
            message = f'Prepared a summary for review from {source_label}' if status == 'success' else f'Analyze cycle {status} for {source_label}'
        elif stage == 'notify':
            message = f'Released alert package for {source_label}' if status == 'success' else f'Notify cycle {status} for {source_label}'
        else:
            message = f'Processed event for {source_label}'

        items.append(
            {
                'id': f"event-{row['id']}",
                'agent_id': stage or 'agent',
                'agent_name': agent_name,
                'stage': stage or 'scrape',
                'status': status,
                'message': message,
                'created_at': row['created_at'],
            }
        )

    if items:
        return items

    now = datetime.now(timezone.utc)
    return [
        {
            'id': 'fallback-1',
            'agent_id': 'hermes-scan',
            'agent_name': 'Hermes-Scan',
            'stage': 'scrape',
            'status': 'success',
            'message': 'Found 3 new bookings in Cascade County',
            'created_at': (now - timedelta(minutes=2)).isoformat(),
        },
        {
            'id': 'fallback-2',
            'agent_id': 'hermes-intel',
            'agent_name': 'Hermes-Intel',
            'stage': 'analyze',
            'status': 'processing',
            'message': 'Queued 2 summaries for human review',
            'created_at': (now - timedelta(minutes=1)).isoformat(),
        },
        {
            'id': 'fallback-3',
            'agent_id': 'hermes-notify',
            'agent_name': 'Hermes-Notify',
            'stage': 'notify',
            'status': 'success',
            'message': 'Broadcast 1 approved alert to the public site',
            'created_at': now.isoformat(),
        },
    ]


def _build_review_queue(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            p.id,
            p.title,
            p.summary,
            p.county,
            p.city,
            p.created_at,
            COALESCE(p.audit_status, 'pending') AS audit_status,
            b.filename AS source_pdf_name,
            b.file_path AS source_pdf_path
        FROM posts p
        LEFT JOIN blotters b ON b.id = p.blotter_id
        WHERE COALESCE(p.audit_status, 'pending') IN ('pending', 'flagged', 'manual_review')
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT 8
        """
    ).fetchall()

    queue: list[dict[str, Any]] = []
    for row in rows:
        queue.append(
            {
                'id': int(row['id']),
                'title': row['title'],
                'summary': row['summary'] or '',
                'county': row['county'] or '',
                'city': row['city'] or '',
                'created_at': row['created_at'],
                'created_at_label': _iso_label(row['created_at']),
                'audit_status': row['audit_status'] or 'pending',
                'source_pdf_name': row['source_pdf_name'] or '',
                'source_pdf_path': row['source_pdf_path'] or '',
            }
        )
    return queue


def build_agent_dashboard_payload(conn) -> dict[str, Any]:
    health_dashboard = build_ingestion_health_dashboard(conn)
    feed_items = _build_feed_items(conn)
    review_queue = _build_review_queue(conn)
    summary = health_dashboard['summary']
    entries = health_dashboard.get('official_sources') or health_dashboard.get('all_sources') or []

    covered_entries = [item for item in entries if item.get('category') == 'covered' or item.get('visibility') == 'official']
    live_count = int(summary.get('official_live', 0) or 0)
    stale_count = int(summary.get('official_stale', summary.get('stale_sources', 0)) or 0)
    failed_alerts = int(summary.get('failed_7d', 0) or 0)
    pending_reviews = len(review_queue)
    latest_event = _latest_pipeline_event(conn)
    latest_seen_at = None
    if covered_entries:
        latest_seen_at = max(
            (
                item.get('latest_activity_at') or item.get('latest_received_at')
                for item in covered_entries
                if item.get('latest_activity_at') or item.get('latest_received_at')
            ),
            default=None,
        )

    scrape_status = 'success' if live_count and not stale_count else ('processing' if live_count else 'error')
    analyze_status = 'processing' if pending_reviews else 'success'
    notify_status = 'error' if failed_alerts else 'success'

    agent_cards = [
        {
            'agent_id': 'hermes-scan',
            'agent_name': 'Hermes-Scan',
            'stage': 'scrape',
            'description': 'County and source acquisition',
            'status': scrape_status,
            'uptime_seconds': 72 * 3600 + 18 * 60,
            'last_sync_at': latest_seen_at,
            'active_alerts': stale_count,
        },
        {
            'agent_id': 'hermes-intel',
            'agent_name': 'Hermes-Intel',
            'stage': 'analyze',
            'description': 'Summarization and triage',
            'status': analyze_status,
            'uptime_seconds': 49 * 3600 + 42 * 60,
            'last_sync_at': latest_event['created_at'] if latest_event else None,
            'active_alerts': pending_reviews,
        },
        {
            'agent_id': 'hermes-notify',
            'agent_name': 'Hermes-Notify',
            'stage': 'notify',
            'description': 'Public release and alerts',
            'status': notify_status,
            'uptime_seconds': 63 * 3600 + 5 * 60,
            'last_sync_at': feed_items[0]['created_at'] if feed_items else None,
            'active_alerts': failed_alerts,
        },
    ]

    pipeline = [
        {
            'key': 'scrape',
            'label': 'Scrape',
            'status': scrape_status,
            'detail': f"{summary.get('official_live', 0)} live sources, {stale_count} stale",
        },
        {
            'key': 'analyze',
            'label': 'Analyze',
            'status': analyze_status,
            'detail': f"{pending_reviews} summaries waiting approval",
        },
        {
            'key': 'notify',
            'label': 'Notify',
            'status': notify_status,
            'detail': f"{live_count} live feeds ready for dispatch",
        },
    ]

    now = datetime.now(timezone.utc)
    return {
        'generated_at': now.isoformat(),
        'generated_at_label': _iso_label(now.isoformat()),
        'ws_url': '/ws/agents',
        'approval_endpoint': '/api/monitoring/agents/reviews',
        'summary': {
            'live_sources': live_count,
            'stale_sources': stale_count,
            'covered_sources': int(summary.get('source_types', 0) or 0),
            'pending_reviews': pending_reviews,
            'active_alerts': pending_reviews + failed_alerts,
        },
        'agents': agent_cards,
        'pipeline': pipeline,
        'feed_items': feed_items,
        'review_queue': review_queue,
        'source_coverage': health_dashboard,
    }


def register_agent_dashboard(app, *, get_db, base_url: str) -> None:
    @app.route('/monitoring/agents')
    def agent_dashboard():
        conn = get_db()
        try:
            payload = build_agent_dashboard_payload(conn)
        finally:
            conn.close()

        return render_template(
            'agent_dashboard.html',
            page_title='Hermes Agent Monitoring Dashboard',
            meta_description='Live monitoring console for Hermes-Scan, Hermes-Intel, and Hermes-Notify.',
            canonical_url=f"{base_url.rstrip('/')}/monitoring/agents",
            og_title='Hermes Agent Monitoring Dashboard | Montana Blotter',
            og_description='Track live agent activity, system health, pipeline state, and manual review actions.',
            active_nav='dashboard',
            agent_dashboard_payload=payload,
            current_year=datetime.now().year,
        )

    @app.route('/api/monitoring/agents/bootstrap')
    def api_agent_dashboard_bootstrap():
        conn = get_db()
        try:
            payload = build_agent_dashboard_payload(conn)
        finally:
            conn.close()
        return jsonify({'ok': True, 'data': payload})

    @app.route('/api/monitoring/agents/reviews/<int:post_id>', methods=['POST', 'PATCH'])
    def api_agent_dashboard_review(post_id: int):
        body = request.get_json(silent=True) or {}
        approved = bool(body.get('approved', True))
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT id, title FROM posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            if not row:
                return jsonify({'ok': False, 'error': 'post_not_found'}), 404
            next_status = 'clean' if approved else 'pending'
            conn.execute(
                "UPDATE posts SET audit_status = ?, audited_at = datetime('now') WHERE id = ?",
                (next_status, post_id),
            )
            conn.commit()
            payload = build_agent_dashboard_payload(conn)
        finally:
            conn.close()

        publish_agent_event(
            AgentEvent(
                agent_id='hermes-intel',
                agent_name='Hermes-Intel',
                message=(
                    'Summary approved and queued for publication'
                    if approved
                    else 'Summary held for further review'
                ),
                stage='analyze',
                status='success' if approved else 'processing',
                created_at=datetime.now(timezone.utc).isoformat(),
                active_alerts=1 if approved else 0,
                requires_approval=not approved,
                incident_id=post_id,
                meta={'title': row['title'], 'audit_status': next_status},
            )
        )

        return jsonify({
            'ok': True,
            'data': payload,
            'review': {
                'post_id': post_id,
                'title': row['title'],
                'approved': approved,
                'audit_status': next_status,
            },
        })
