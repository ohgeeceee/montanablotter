from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


OFFICIAL_SOURCE_COVERAGE = [
    {
        'agency': 'Whitefish Police Department',
        'category': 'covered',
        'source_type': 'whitefish_pdf',
        'source_url': 'https://www.cityofwhitefish.gov/688/Police-Blotter',
        'notes': 'Official posted blotter. Active ingester.',
        'stale_after_hours': 72,
    },
    {
        'agency': 'Bozeman Police Department',
        'category': 'covered',
        'source_type': 'bozeman_calls_for_service',
        'source_url': 'https://www.bozeman.net/departments/police/crime-information/police-call-logs/30-day-call-log',
        'notes': 'Official calls-for-service dashboard. Active ingester.',
        'stale_after_hours': 24,
    },
    {
        'agency': 'Bozeman Police Department',
        'category': 'covered',
        'source_type': 'bozeman_daily_case_reports',
        'source_url': 'https://bozeman.maps.arcgis.com/apps/dashboards/38247556995340e6b796a9e53c15ae1f',
        'notes': 'Official city-linked crime dashboard. Active ingester.',
        'stale_after_hours': 72,
    },
    {
        'agency': 'Missoula County public report feed',
        'category': 'covered',
        'source_type': 'missoula_public_report',
        'source_url': 'https://webapps.missoulacounty.us/dailypublicreport/',
        'notes': 'Official daily public report feed. Active ingester.',
        'stale_after_hours': 24,
    },
    {
        'agency': "Big Horn County Sheriff's Office",
        'category': 'candidate',
        'source_type': None,
        'source_url': 'https://www.bighorncountymt.gov/176/Sheriff',
        'notes': 'Official sheriff page links to CitizenRIMS, but public incident and case features are disabled.',
        'stale_after_hours': None,
    },
    {
        'agency': 'Billings Police Department',
        'category': 'candidate',
        'source_type': None,
        'source_url': 'https://billingsmt.gov/1773/Crime-Statistics',
        'notes': 'Official dashboard exists, but it appears stale rather than a current rolling feed.',
        'stale_after_hours': None,
    },
    {
        'agency': 'Great Falls Police Department',
        'category': 'candidate',
        'source_type': None,
        'source_url': 'https://greatfallsmt.net/police/welcome-gfpd-message-chief',
        'notes': 'Official site references statistics, but no qualifying public blotter or call-log page is confirmed.',
        'stale_after_hours': None,
    },
    {
        'agency': 'Helena Police Department',
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://www.helenamt.gov/Departments/Police-Department/Support-Services-Records',
        'notes': 'Records are available by request, but no posted public blotter or crime-log page was found.',
        'stale_after_hours': None,
    },
    {
        'agency': 'Kalispell Police Department',
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://www.kalispell.com/260/Police',
        'notes': 'No qualifying public blotter or call-log page found.',
        'stale_after_hours': None,
    },
    {
        'agency': 'Belgrade Police Department',
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://www.belgrademt.gov/158/Police',
        'notes': 'No qualifying public blotter or call-log page found.',
        'stale_after_hours': None,
    },
    {
        'agency': 'Laurel Police Department',
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://cityoflaurelmontana.com/police/custom-contact-page/police-contact-information',
        'notes': 'No qualifying public blotter or call-log page found.',
        'stale_after_hours': None,
    },
    {
        'agency': "Yellowstone County Sheriff's Office",
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://www.yellowstonecountymt.gov/Sheriff/',
        'notes': 'No qualifying public sheriff blotter or crime-log page found.',
        'stale_after_hours': None,
    },
    {
        'agency': "Cascade County Sheriff's Office",
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://www.cascadecountymt.gov/283/Sheriffs-Office',
        'notes': 'No qualifying public sheriff blotter or crime-log page found.',
        'stale_after_hours': None,
    },
    {
        'agency': "Flathead County Sheriff's Office",
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://flatheadcounty.gov/department-directory/sheriffs-office',
        'notes': 'No qualifying public sheriff blotter or crime-log page found.',
        'stale_after_hours': None,
    },
    {
        'agency': "Gallatin County Sheriff's Office",
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://www.gallatinmt.gov/patrol-division/links/crime-reporting',
        'notes': 'Reporting page only; no posted public blotter or call-log page was found.',
        'stale_after_hours': None,
    },
    {
        'agency': "Missoula County Sheriff's Office",
        'category': 'no_source',
        'source_type': None,
        'source_url': 'https://www.missoulacounty.gov/departments/sheriffs-office/',
        'notes': 'No separate posted sheriff blotter or crime-log page was found.',
        'stale_after_hours': None,
    },
]

INTERNAL_SOURCE_META = {
    'imap_pdf': {
        'label': 'Email inbox PDF attachments',
        'notes': 'Attachments fetched by email worker and passed into the PDF parser pipeline.',
        'source_url': None,
        'stale_after_hours': None,
    },
    'imap_text': {
        'label': 'Email inbox text blotters',
        'notes': 'Plain-text blotters fetched from email and published through the text pipeline.',
        'source_url': None,
        'stale_after_hours': None,
    },
}


def parse_admin_timestamp(value):
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def format_admin_age(delta):
    hours = int(delta.total_seconds() // 3600)
    if hours < 24:
        return f'{hours}h ago'
    days = hours // 24
    return f'{days}d ago'


def humanize_source_type(source_type):
    if not source_type:
        return 'Unknown source'
    return source_type.replace('_', ' ').strip().title()


def build_source_coverage_dashboard(conn):
    latest_rows = conn.execute(
        """
        SELECT sd.source_type,
               sd.source_received_at,
               ij.status,
               ij.finished_at
        FROM source_documents sd
        LEFT JOIN ingestion_jobs ij ON ij.source_document_id = sd.id
        INNER JOIN (
            SELECT source_type, MAX(id) AS max_id
            FROM source_documents
            GROUP BY source_type
        ) latest ON latest.max_id = sd.id
        """
    ).fetchall()
    latest_by_type = {row['source_type']: row for row in latest_rows}

    now = datetime.now(timezone.utc)
    items = []
    summary = {'covered': 0, 'live': 0, 'stale': 0, 'candidate': 0, 'no_source': 0}

    for item in OFFICIAL_SOURCE_COVERAGE:
        row = latest_by_type.get(item['source_type']) if item.get('source_type') else None
        latest_seen_at = parse_admin_timestamp(row['source_received_at']) if row else None
        freshness = None
        freshness_tone = 'slate'
        if item['category'] == 'covered':
            summary['covered'] += 1
            if latest_seen_at is None:
                freshness = 'No ingest yet'
                freshness_tone = 'red'
                summary['stale'] += 1
            else:
                age = now - latest_seen_at
                freshness = format_admin_age(age)
                if item['stale_after_hours'] is not None and age.total_seconds() > item['stale_after_hours'] * 3600:
                    freshness_tone = 'amber'
                    summary['stale'] += 1
                else:
                    freshness_tone = 'green'
                    summary['live'] += 1
        elif item['category'] == 'candidate':
            summary['candidate'] += 1
        else:
            summary['no_source'] += 1

        items.append({
            **item,
            'latest_seen_at': latest_seen_at.strftime('%Y-%m-%d %H:%M UTC') if latest_seen_at else None,
            'latest_job_status': row['status'] if row else None,
            'freshness': freshness,
            'freshness_tone': freshness_tone,
        })

    return {
        'summary': summary,
        'entries': items,
    }


def build_ingestion_health_dashboard(conn):
    source_coverage = build_source_coverage_dashboard(conn)
    coverage_by_type = {
        item['source_type']: item
        for item in source_coverage['entries']
        if item.get('source_type')
    }

    aggregate_rows = conn.execute(
        """
        SELECT
            sd.source_type,
            COUNT(*) AS document_count,
            COUNT(ij.id) AS job_count,
            SUM(CASE WHEN ij.status = 'published' THEN 1 ELSE 0 END) AS published_count,
            SUM(CASE WHEN ij.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN ij.status NOT IN ('published', 'failed') THEN 1 ELSE 0 END) AS active_count,
            MAX(COALESCE(sd.source_received_at, sd.created_at)) AS latest_received_at
        FROM source_documents sd
        LEFT JOIN ingestion_jobs ij ON ij.source_document_id = sd.id
        GROUP BY sd.source_type
        ORDER BY sd.source_type
        """
    ).fetchall()
    latest_rows = conn.execute(
        """
        SELECT
            sd.source_type,
            sd.id AS source_document_id,
            sd.filename,
            sd.source_subject,
            sd.source_received_at,
            ij.id AS job_id,
            ij.status,
            ij.retry_count,
            ij.last_error,
            ij.started_at,
            ij.finished_at,
            (
                SELECT pe.stage
                FROM pipeline_events pe
                WHERE pe.ingestion_job_id = ij.id
                ORDER BY pe.id DESC
                LIMIT 1
            ) AS latest_stage,
            (
                SELECT pe.status
                FROM pipeline_events pe
                WHERE pe.ingestion_job_id = ij.id
                ORDER BY pe.id DESC
                LIMIT 1
            ) AS latest_stage_status
        FROM source_documents sd
        LEFT JOIN ingestion_jobs ij ON ij.source_document_id = sd.id
        INNER JOIN (
            SELECT source_type, MAX(id) AS max_id
            FROM source_documents
            GROUP BY source_type
        ) latest ON latest.max_id = sd.id
        """
    ).fetchall()
    latest_by_type = {row['source_type']: row for row in latest_rows}

    jobs_24h = conn.execute(
        """
        SELECT COUNT(*)
        FROM ingestion_jobs
        WHERE COALESCE(finished_at, started_at) >= datetime('now', '-1 day')
        """
    ).fetchone()[0]
    failed_7d = conn.execute(
        """
        SELECT COUNT(*)
        FROM ingestion_jobs
        WHERE status = 'failed'
          AND COALESCE(finished_at, started_at) >= datetime('now', '-7 days')
        """
    ).fetchone()[0]
    published_24h = conn.execute(
        """
        SELECT COUNT(*)
        FROM ingestion_jobs
        WHERE status = 'published'
          AND COALESCE(finished_at, started_at) >= datetime('now', '-1 day')
        """
    ).fetchone()[0]
    stuck_rows = conn.execute(
        """
        SELECT
            ij.id,
            ij.status,
            ij.started_at,
            sd.source_type,
            sd.filename,
            sd.source_subject
        FROM ingestion_jobs ij
        JOIN source_documents sd ON sd.id = ij.source_document_id
        WHERE ij.status NOT IN ('published', 'failed')
          AND ij.finished_at IS NULL
          AND ij.started_at <= datetime('now', '-2 hours')
        ORDER BY ij.started_at ASC
        """
    ).fetchall()

    now = datetime.now(timezone.utc)
    source_rows = []
    stale_sources = 0
    failing_sources = 0
    healthy_sources = 0
    active_sources = 0

    for row in aggregate_rows:
        source_type = row['source_type']
        latest = latest_by_type.get(source_type)
        coverage_item = coverage_by_type.get(source_type)
        internal_meta = INTERNAL_SOURCE_META.get(source_type, {})
        latest_received_dt = parse_admin_timestamp(row['latest_received_at'])
        latest_started_dt = parse_admin_timestamp(latest['started_at']) if latest else None
        stale_after_hours = (
            coverage_item['stale_after_hours']
            if coverage_item
            else internal_meta.get('stale_after_hours')
        )
        freshness = 'No source document'
        freshness_tone = 'slate'
        is_stale = False
        if latest_received_dt:
            age = now - latest_received_dt
            freshness = format_admin_age(age)
            freshness_tone = 'green'
            if stale_after_hours is not None and age.total_seconds() > stale_after_hours * 3600:
                freshness_tone = 'amber'
                is_stale = True

        latest_status = latest['status'] if latest else None
        health_label = 'Healthy'
        health_tone = 'green'
        if latest_status == 'failed':
            health_label = 'Failing'
            health_tone = 'red'
            failing_sources += 1
        elif is_stale:
            health_label = 'Stale'
            health_tone = 'amber'
            stale_sources += 1
        elif row['active_count']:
            health_label = 'Active'
            health_tone = 'blue'
            active_sources += 1
        elif latest_status == 'published':
            healthy_sources += 1
        else:
            health_label = 'Unknown'
            health_tone = 'slate'

        if health_label == 'Healthy' and latest_status != 'published':
            health_label = 'Ready'
            health_tone = 'slate'

        display_name = (
            coverage_item['agency']
            if coverage_item
            else internal_meta.get('label', humanize_source_type(source_type))
        )
        visibility = 'official' if coverage_item else 'internal'
        latest_activity_dt = parse_admin_timestamp(latest['finished_at']) if latest and latest['finished_at'] else latest_started_dt
        source_rows.append({
            'source_type': source_type,
            'display_name': display_name,
            'visibility': visibility,
            'source_url': (coverage_item['source_url'] if coverage_item else internal_meta.get('source_url')),
            'notes': (coverage_item['notes'] if coverage_item else internal_meta.get('notes', 'No notes recorded.')),
            'document_count': row['document_count'] or 0,
            'job_count': row['job_count'] or 0,
            'published_count': row['published_count'] or 0,
            'failed_count': row['failed_count'] or 0,
            'active_count': row['active_count'] or 0,
            'latest_status': latest_status,
            'latest_stage': latest['latest_stage'] if latest else None,
            'latest_stage_status': latest['latest_stage_status'] if latest else None,
            'latest_error': latest['last_error'] if latest else None,
            'latest_document_name': (latest['filename'] or latest['source_subject']) if latest else None,
            'latest_received_at': latest_received_dt.strftime('%Y-%m-%d %H:%M UTC') if latest_received_dt else None,
            'latest_activity_at': latest_activity_dt.strftime('%Y-%m-%d %H:%M UTC') if latest_activity_dt else None,
            'freshness': freshness,
            'freshness_tone': freshness_tone,
            'health_label': health_label,
            'health_tone': health_tone,
        })

    def _source_sort_key(item):
        tone_rank = {'red': 0, 'amber': 1, 'blue': 2, 'green': 3, 'slate': 4}.get(item['health_tone'], 5)
        visibility_rank = 0 if item['visibility'] == 'official' else 1
        latest_activity = item['latest_activity_at'] or ''
        return (tone_rank, visibility_rank, latest_activity)

    source_rows.sort(key=_source_sort_key)

    official_rows = []
    for item in source_coverage['entries']:
        stats = next((row for row in source_rows if row['source_type'] == item.get('source_type')), None)
        official_rows.append({
            **item,
            'job_count': stats['job_count'] if stats else 0,
            'published_count': stats['published_count'] if stats else 0,
            'failed_count': stats['failed_count'] if stats else 0,
            'active_count': stats['active_count'] if stats else 0,
            'latest_stage': stats['latest_stage'] if stats else None,
            'latest_error': stats['latest_error'] if stats else None,
        })

    return {
        'summary': {
            'source_types': len(source_rows),
            'healthy_sources': healthy_sources,
            'stale_sources': stale_sources,
            'failing_sources': failing_sources,
            'active_sources': active_sources,
            'jobs_24h': jobs_24h or 0,
            'published_24h': published_24h or 0,
            'failed_7d': failed_7d or 0,
            'stuck_jobs': len(stuck_rows),
            'official_live': source_coverage['summary']['live'],
            'official_stale': source_coverage['summary']['stale'],
        },
        'official_sources': official_rows,
        'all_sources': source_rows,
        'stuck_jobs': [
            {
                'id': row['id'],
                'status': row['status'],
                'source_type': row['source_type'],
                'source_name': coverage_by_type[row['source_type']]['agency']
                if row['source_type'] in coverage_by_type
                else INTERNAL_SOURCE_META.get(row['source_type'], {}).get('label', humanize_source_type(row['source_type'])),
                'started_at': row['started_at'],
                'document_name': row['filename'] or row['source_subject'] or 'Untitled source document',
            }
            for row in stuck_rows
        ],
    }
