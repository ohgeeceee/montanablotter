"""
Montana Blotter - Public browse, subscriber tools, and admin operations.
"""

import os
import sqlite3
import hashlib
import json
import csv
import io
import hmac
import secrets
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from functools import wraps
from html import escape
from html.parser import HTMLParser
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, Response, session, abort, g, has_request_context, current_app
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta, timezone
from agency_normalization import normalize_agency_identity
from blotter_auditor import get_pii_spans
import config
from api_auth import after_api_request
from blueprints.api import register_api_blueprint
from blueprints.auth import register_auth_blueprint
from blueprints.payments import register_payments_blueprint
from blueprints.detention import register_detention_blueprint
from blueprints.recovery_ads import recovery_ads_bp
from court_tracker import (
    court_admin_context,
    court_case_detail,
    court_directory_context,
    court_hearing_feed_context,
    ensure_court_tracker_schema,
)
from db import connect_db, connect_page_views, get_db
from dedupe import incident_key_set
from morning_briefing import (
    build_html as build_morning_briefing_html,
    get_posts_for_date as get_morning_briefing_posts,
    send_email as send_morning_briefing_email,
)
from missing_persons import (
    get_missing_person_by_slug,
    missing_person_detail_context,
    missing_person_homepage_context,
    missing_person_public_context,
)
from public_meetings import ensure_public_meeting_schema, meeting_admin_context
from facebook_publisher import (
    load_facebook_settings,
    mask_token,
    publish_queue_item,
    queue_post,
    queue_recent_posts,
    run_facebook_queue,
    save_facebook_settings,
)
from bondsman_command_center import register_bondsman_command_center
from agent_dashboard import register_agent_dashboard

try:
    import stripe
except Exception:
    stripe = None

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
BASE_URL = config.BASE_URL
WINTER_STORM_SUPPORT_BANNER_DEFAULTS = {
    'enabled': False,
    'label': 'Public Safety Alert',
    'headline': 'Help fund Montana winter storm coverage',
    'body': 'Support dispatch monitoring, county emergency follow-up, and fast public-records coverage during severe weather.',
    'donate_href': '/donate?source=winter_storm_banner&campaign=winter_storm',
    'donate_label': 'Support Coverage',
    'subscribe_href': '/subscribe?source=winter_storm_banner',
    'subscribe_label': 'Get Alerts',
}
app.config.update(
    SESSION_COOKIE_HTTPONLY=config.SESSION_COOKIE_HTTPONLY,
    SESSION_COOKIE_SAMESITE=config.SESSION_COOKIE_SAMESITE,
    SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,
)


def _redact_public_pii(value):
    if value is None:
        return value
    text = str(value)
    spans = [
        span for span in get_pii_spans(text)
        if span.get('severity') in {'high', 'medium'}
    ]
    for span in reversed(spans):
        pii_type = str(span.get('pii_type') or 'pii').replace('_', ' ')
        text = text[:span['start']] + f'[redacted {pii_type}]' + text[span['end']:]
    return text


def _public_record_dict(row):
    data = dict(row)
    for key in ('location', 'details', 'officer'):
        if key in data:
            data[key] = _redact_public_pii(data[key])
    return data


def _public_log_dict(row):
    data = dict(row)
    for key in ('officer', 'entry'):
        if key in data:
            data[key] = _redact_public_pii(data[key])
    return data

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


def _slugify_key(value):
    import re as _re
    return _re.sub(r'[^a-z0-9]+', '-', (value or '').strip().lower()).strip('-')


def _parse_admin_timestamp(value):
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


def _format_admin_age(delta):
    hours = int(delta.total_seconds() // 3600)
    if hours < 24:
        return f'{hours}h ago'
    days = hours // 24
    return f'{days}d ago'


def _build_source_coverage_dashboard(conn):
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
        latest_seen_at = _parse_admin_timestamp(row['source_received_at']) if row else None
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
                freshness = _format_admin_age(age)
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


def _humanize_source_type(source_type):
    if not source_type:
        return 'Unknown source'
    return source_type.replace('_', ' ').strip().title()


def _build_ingestion_health_dashboard(conn):
    source_coverage = _build_source_coverage_dashboard(conn)
    coverage_by_type = {
        item['source_type']: item
        for item in source_coverage['entries']
        if item.get('source_type')
    }
    internal_source_meta = {
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

    aggregate_rows = conn.execute(
        """
        SELECT
            sd.source_type,
            COUNT(*) AS document_count,
            COUNT(ij.id) AS job_count,
            SUM(CASE WHEN ij.status = 'published' THEN 1 ELSE 0 END) AS published_count,
            SUM(CASE WHEN ij.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN ij.status NOT IN ('published', 'failed', 'skipped') THEN 1 ELSE 0 END) AS active_count,
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
        WHERE ij.status NOT IN ('published', 'failed', 'skipped')
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
        internal_meta = internal_source_meta.get(source_type, {})
        latest_received_dt = _parse_admin_timestamp(row['latest_received_at'])
        latest_started_dt = _parse_admin_timestamp(latest['started_at']) if latest else None
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
            freshness = _format_admin_age(age)
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
        elif latest_status == 'skipped':
            health_label = 'Ignored'
            health_tone = 'slate'
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
            else internal_meta.get('label', _humanize_source_type(source_type))
        )
        visibility = 'official' if coverage_item else 'internal'
        latest_activity_dt = _parse_admin_timestamp(latest['finished_at']) if latest and latest['finished_at'] else latest_started_dt
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
                else internal_source_meta.get(row['source_type'], {}).get('label', _humanize_source_type(row['source_type'])),
                'started_at': row['started_at'],
                'document_name': row['filename'] or row['source_subject'] or 'Untitled source document',
            }
            for row in stuck_rows
        ],
    }


COUNTY_DIRECTORY = {
    'beaverhead': {'name': 'Beaverhead', 'roster_url': 'https://beaverheadcountymt.gov/departments/sheriff/', 'phone': '406-683-3700', 'has_online_roster': True},
    'big-horn': {'name': 'Big Horn', 'roster_url': 'https://www.bighorncountymt.gov/239/Detention', 'phone': '406-665-9780', 'has_online_roster': True},
    'blaine': {'name': 'Blaine', 'roster_url': None, 'phone': '406-357-3260', 'has_online_roster': False},
    'broadwater': {'name': 'Broadwater', 'roster_url': 'https://www.broadwatercountysheriff.org/roster.php', 'phone': '406-266-3445', 'has_online_roster': True},
    'carbon': {'name': 'Carbon', 'roster_url': 'https://carbonmt.gov/sheriff/', 'phone': '406-446-1234', 'has_online_roster': True},
    'carter': {'name': 'Carter', 'roster_url': None, 'phone': '406-775-8741', 'has_online_roster': False},
    'cascade': {'name': 'Cascade', 'roster_url': 'https://www.cascadecountymt.gov/314/Inmate-Roster', 'phone': '406-454-6840', 'has_online_roster': True},
    'chouteau': {'name': 'Chouteau', 'roster_url': None, 'phone': '406-622-3660', 'has_online_roster': False},
    'custer': {'name': 'Custer', 'roster_url': None, 'phone': '406-874-3300', 'has_online_roster': False},
    'daniels': {'name': 'Daniels', 'roster_url': None, 'phone': '406-487-2691', 'has_online_roster': False},
    'dawson': {'name': 'Dawson', 'roster_url': 'https://www.dawsoncountymontana.com/sheriff', 'phone': '406-377-7600', 'has_online_roster': True},
    'deer-lodge': {'name': 'Deer Lodge', 'roster_url': None, 'phone': '406-563-5421', 'has_online_roster': False},
    'fallon': {'name': 'Fallon', 'roster_url': None, 'phone': '406-778-2879', 'has_online_roster': False},
    'fergus': {'name': 'Fergus', 'roster_url': 'https://fergusmt.gov/detention-center-roster', 'phone': '406-535-3860', 'has_online_roster': True},
    'flathead': {'name': 'Flathead', 'roster_url': 'https://apps.flathead.mt.gov/jailroster/', 'phone': '406-758-5610', 'has_online_roster': True},
    'gallatin': {'name': 'Gallatin', 'roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates', 'phone': '406-582-2100', 'has_online_roster': True},
    'garfield': {'name': 'Garfield', 'roster_url': None, 'phone': '406-557-2540', 'has_online_roster': False},
    'glacier': {'name': 'Glacier', 'roster_url': 'https://glaciercountymt.gov/category/jail-roster/', 'phone': '406-873-4600', 'has_online_roster': True},
    'golden-valley': {'name': 'Golden Valley', 'roster_url': None, 'phone': '406-568-2321', 'has_online_roster': False},
    'granite': {'name': 'Granite', 'roster_url': 'https://granitecountyjail.org/', 'phone': '406-859-3771', 'has_online_roster': True},
    'hill': {'name': 'Hill', 'roster_url': None, 'phone': '406-265-5481', 'has_online_roster': False},
    'jefferson': {'name': 'Jefferson', 'roster_url': 'https://jefferson-so-mt.zuercherportal.com/#/inmates', 'phone': '406-225-4075', 'has_online_roster': True},
    'judith-basin': {'name': 'Judith Basin', 'roster_url': None, 'phone': '406-535-3860', 'has_online_roster': False},
    'lake': {'name': 'Lake', 'roster_url': None, 'phone': '406-883-7301', 'has_online_roster': False},
    'lewis-and-clark': {'name': 'Lewis and Clark', 'roster_url': 'https://www.lccountymt.gov/Sheriff/Detention-Center', 'phone': '406-447-8270', 'has_online_roster': True},
    'liberty': {'name': 'Liberty', 'roster_url': None, 'phone': '406-759-5171', 'has_online_roster': False},
    'lincoln': {'name': 'Lincoln', 'roster_url': None, 'phone': '406-293-0242', 'has_online_roster': False},
    'madison': {'name': 'Madison', 'roster_url': None, 'phone': '406-843-5351', 'has_online_roster': False},
    'mccone': {'name': 'McCone', 'roster_url': None, 'phone': '406-485-3405', 'has_online_roster': False},
    'meagher': {'name': 'Meagher', 'roster_url': None, 'phone': '406-547-3397', 'has_online_roster': False},
    'mineral': {'name': 'Mineral', 'roster_url': 'https://co.mineral.mt.us/departments/sheriff/', 'phone': '406-822-3534', 'has_online_roster': True},
    'missoula': {'name': 'Missoula', 'roster_url': 'https://webapps.missoulacounty.us/jailroster/Inmates', 'phone': '406-258-4780', 'has_online_roster': True},
    'musselshell': {'name': 'Musselshell', 'roster_url': None, 'phone': '406-323-1122', 'has_online_roster': False},
    'park': {'name': 'Park', 'roster_url': 'https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/', 'phone': '406-222-4172', 'has_online_roster': True},
    'petroleum': {'name': 'Petroleum', 'roster_url': None, 'phone': '406-429-6551', 'has_online_roster': False},
    'phillips': {'name': 'Phillips', 'roster_url': 'https://phillipscosheriff.com/inmates/', 'phone': '406-654-2020', 'has_online_roster': True},
    'pondera': {'name': 'Pondera', 'roster_url': 'https://ponderacountyjail.org/inmate-search/', 'phone': '406-271-4100', 'has_online_roster': True},
    'powder-river': {'name': 'Powder River', 'roster_url': None, 'phone': '406-436-2260', 'has_online_roster': False},
    'powell': {'name': 'Powell', 'roster_url': 'https://www.powellcountymt.gov/sheriff/page/detention-facility', 'phone': '406-846-2711', 'has_online_roster': True},
    'prairie': {'name': 'Prairie', 'roster_url': None, 'phone': '406-635-5738', 'has_online_roster': False},
    'ravalli': {'name': 'Ravalli', 'roster_url': 'https://ravallicounty.gov/239/Adult-Detention-Center', 'phone': '406-375-4060', 'has_online_roster': True},
    'richland': {'name': 'Richland', 'roster_url': None, 'phone': '406-433-2919', 'has_online_roster': False},
    'roosevelt': {'name': 'Roosevelt', 'roster_url': None, 'phone': '406-653-6230', 'has_online_roster': False},
    'rosebud': {'name': 'Rosebud', 'roster_url': None, 'phone': '406-346-2715', 'has_online_roster': False},
    'sanders': {'name': 'Sanders', 'roster_url': 'https://sanders-mt.publiclogs.com/', 'phone': '406-827-3584', 'has_online_roster': True},
    'sheridan': {'name': 'Sheridan', 'roster_url': None, 'phone': '406-765-1200', 'has_online_roster': False},
    'silver-bow': {'name': 'Silver Bow', 'roster_url': 'https://co.silverbow.mt.us/3274/Detention-Center', 'phone': '406-497-1120', 'has_online_roster': True},
    'stillwater': {'name': 'Stillwater', 'roster_url': None, 'phone': '406-322-5326', 'has_online_roster': False},
    'sweet-grass': {'name': 'Sweet Grass', 'roster_url': None, 'phone': '406-932-5143', 'has_online_roster': False},
    'teton': {'name': 'Teton', 'roster_url': None, 'phone': '406-466-5781', 'has_online_roster': False},
    'toole': {'name': 'Toole', 'roster_url': None, 'phone': '406-434-5585', 'has_online_roster': False},
    'treasure': {'name': 'Treasure', 'roster_url': None, 'phone': '406-342-5211', 'has_online_roster': False},
    'valley': {'name': 'Valley', 'roster_url': 'https://www.valleycountymt.gov/1288/Jail-Roster', 'phone': '406-228-9355', 'has_online_roster': True},
    'wheatland': {'name': 'Wheatland', 'roster_url': None, 'phone': '406-632-4311', 'has_online_roster': False},
    'wibaux': {'name': 'Wibaux', 'roster_url': None, 'phone': '406-796-2415', 'has_online_roster': False},
    'yellowstone': {'name': 'Yellowstone', 'roster_url': 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp', 'phone': '406-256-2929', 'has_online_roster': True},
}

MAJOR_JAIL_BOOKING_COUNTIES = (
    'yellowstone',
    'missoula',
    'gallatin',
    'flathead',
    'jefferson',   # ADDED: visible on /jail-bookings page
    'sanders',     # ADDED: visible on /jail-bookings page
)
JAIL_BOOKING_STATUS_LABELS = {
    'current': 'Current',
    'released': 'Released',
    'transferred': 'Transferred',
    'archived': 'Archived',
}

NEW_CITY_SLUGS = (
    'laurel',
    'hardin',
    'belgrade',
    'manhattan',
    'whitefish',
    'columbia-falls',
    'east-helena',
    'anaconda',
    'red-lodge',
    'livingston',
)

PATTERN_DEFINITIONS = {
    'dui-activity': {
        'slug': 'dui-activity',
        'label': 'DUI Activity',
        'short_label': 'DUI',
        'hero_label': 'Montana DUI Pattern Page',
        'description': 'Track DUI-related incident activity, repeat locations, and recent public records tied to impaired driving enforcement.',
        'title_statewide': 'Montana DUI Activity and Arrest Records',
        'title_county': '{county} County DUI Activity and Arrest Records',
        'meta_statewide': 'Track Montana DUI activity, impaired-driving arrests, and related blotter records with county-level entry pages and recent linked incidents.',
        'meta_county': 'Track DUI activity in {county} County with recent records, arrest-related coverage, and direct links into county-level Montana public safety pages.',
        'intro': 'These pages surface DUI-related public records so readers can quickly move from a broad pattern into the exact county records behind it.',
        'terms': ['dui', 'driving under the influence', 'impaired driving'],
        'search_targets': ['dui arrests', 'dui blotter', 'impaired driving reports', 'dui activity'],
        'source_note': 'These pages group visible DUI-related public records into a cleaner archive so readers can skip broad county feeds and move directly into impaired-driving activity.',
        'faq_name': 'What does the DUI activity page track?',
        'faq_answer': 'It tracks visible Montana Blotter records that reference DUI or driving-under-the-influence activity in the indexed archive.',
    },
    'warrant-related-arrests': {
        'slug': 'warrant-related-arrests',
        'label': 'Warrant-Related Arrests',
        'short_label': 'Warrants',
        'hero_label': 'Montana Warrant Pattern Page',
        'description': 'Find recent records that reference warrant service, warrant arrests, or active warrant activity in county-level public records.',
        'title_statewide': 'Montana Warrant-Related Arrests and Records',
        'title_county': '{county} County Warrant-Related Arrests and Records',
        'meta_statewide': 'Browse Montana warrant-related arrest records with top counties, recent entries, and direct links into county warrant, jail, and arrest resources.',
        'meta_county': 'Browse warrant-related arrests in {county} County with recent records, county warrant resources, and linked public safety coverage.',
        'intro': 'These pages are built for readers who want a narrower path into warrant-linked activity than a general arrest log provides.',
        'terms': ['warrant', 'warrant arrest', 'arrest warrant', 'bench warrant', 'served warrant'],
        'search_targets': ['warrant arrests', 'bench warrant arrests', 'served warrant records', 'warrant-related arrests'],
        'source_note': 'This pattern page is a bridge between arrest coverage and official warrant resources. It highlights visible records that mention warrant activity but does not replace a county warrant list or court search.',
        'faq_name': 'Does a warrant-related arrest page replace the official warrant list?',
        'faq_answer': 'No. It summarizes visible public records that mention warrant activity. Official county warrant lists and court records remain the final source.',
    },
    'domestic-disturbance': {
        'slug': 'domestic-disturbance',
        'label': 'Domestic Disturbance',
        'short_label': 'Domestic',
        'hero_label': 'Montana Domestic Disturbance Pattern Page',
        'description': 'Follow domestic disturbance and partner-or-family-violence related records across Montana counties and local archives.',
        'title_statewide': 'Montana Domestic Disturbance Activity and Records',
        'title_county': '{county} County Domestic Disturbance Activity and Records',
        'meta_statewide': 'Track domestic disturbance-related public records in Montana with top counties, recent entries, and linked county archive pages.',
        'meta_county': 'Track domestic disturbance activity in {county} County with recent records, related reports, and direct links into county-level archives.',
        'intro': 'These pages group domestic-disturbance-related records into a dedicated local archive for readers following recurring family or household call patterns.',
        'terms': ['domestic', 'domestic violence', 'partner/family', 'partner family', 'family member assault', 'pfma'],
        'search_targets': ['domestic disturbance reports', 'domestic violence blotter', 'pfma records', 'family disturbance calls'],
        'source_note': 'These pages cluster domestic-disturbance-related records into a narrower archive view so users can find family-violence and household-disturbance references without searching every county page manually.',
        'faq_name': 'What counts as domestic disturbance activity here?',
        'faq_answer': 'It includes visible records that reference domestic disturbances, domestic violence, partner or family-related assault language, or PFMA-related terminology in the indexed archive.',
    },
    'theft-and-burglary': {
        'slug': 'theft-and-burglary',
        'label': 'Theft & Burglary',
        'short_label': 'Theft',
        'hero_label': 'Montana Theft & Burglary Pattern Page',
        'description': 'Browse theft, burglary, and shoplifting-related public records from Montana law enforcement agencies.',
        'title_statewide': 'Montana Theft and Burglary Records',
        'title_county': '{county} County Theft and Burglary Records',
        'meta_statewide': 'Browse Montana theft and burglary public records with top counties, recent entries, and direct links into county-level property crime archives.',
        'meta_county': 'Browse theft and burglary records in {county} County with recent incidents, related coverage, and links to county-level Montana public safety pages.',
        'intro': 'These pages collect theft, burglary, and property-crime-related records into a focused archive for readers tracking property crime trends across Montana counties.',
        'terms': ['theft', 'burglary', 'shoplifting', 'robbery', 'larceny', 'stolen', 'burglary of a vehicle'],
        'search_targets': ['theft arrests', 'burglary reports', 'shoplifting blotter', 'property crime records', 'robbery blotter'],
        'source_note': 'These pages surface property-crime-related records from the indexed archive. They do not replace official court records or law enforcement case files.',
        'faq_name': 'What types of property crimes appear on this page?',
        'faq_answer': 'It includes visible records referencing theft, burglary, shoplifting, robbery, larceny, or vehicle burglary in the indexed archive.',
    },
    'drug-charges': {
        'slug': 'drug-charges',
        'label': 'Drug Charges',
        'short_label': 'Drugs',
        'hero_label': 'Montana Drug Charges Pattern Page',
        'description': 'Track drug possession, distribution, and paraphernalia-related enforcement records across Montana counties.',
        'title_statewide': 'Montana Drug Charge Records and Enforcement Activity',
        'title_county': '{county} County Drug Charge Records and Enforcement Activity',
        'meta_statewide': 'Track Montana drug charge records including possession, distribution, and paraphernalia cases with county-level pages and recent linked incidents.',
        'meta_county': 'Track drug charge records in {county} County with recent incidents, enforcement activity, and direct links into county-level Montana public safety archives.',
        'intro': 'These pages group drug-related enforcement records into a focused archive so readers can follow controlled-substance activity without searching the full county blotter.',
        'terms': ['drug', 'possession', 'controlled substance', 'paraphernalia', 'meth', 'methamphetamine', 'heroin', 'fentanyl', 'marijuana', 'distribution of'],
        'search_targets': ['drug arrests', 'drug possession blotter', 'controlled substance records', 'drug enforcement activity'],
        'source_note': 'These pages index drug-related records visible in the public archive. Charges are not convictions and records reflect law enforcement activity only.',
        'faq_name': 'What drug-related records appear on this page?',
        'faq_answer': 'It includes visible records referencing drug possession, controlled substances, paraphernalia, or distribution-related charges in the indexed archive.',
    },
    'weapons-charges': {
        'slug': 'weapons-charges',
        'label': 'Weapons Charges',
        'short_label': 'Weapons',
        'hero_label': 'Montana Weapons Charges Pattern Page',
        'description': 'Find weapons-related enforcement records including unlawful possession, carry violations, and firearm-related incidents across Montana.',
        'title_statewide': 'Montana Weapons Charge Records and Firearm Incidents',
        'title_county': '{county} County Weapons Charge Records and Firearm Incidents',
        'meta_statewide': 'Browse Montana weapons charge records including firearm possession, unlawful carry, and weapons-related incidents with county archive pages.',
        'meta_county': 'Browse weapons charge records in {county} County with recent incidents, firearm-related activity, and links to county-level Montana public safety pages.',
        'intro': 'These pages bring together weapons-related enforcement records from across Montana, making it easier to track firearm and weapons charge activity at the county level.',
        'terms': ['weapon', 'firearm', 'unlawful possession of a weapon', 'carrying concealed', 'felon in possession', 'gun', 'rifle', 'shotgun', 'knife'],
        'search_targets': ['weapons charges', 'firearm arrests', 'unlawful weapon possession', 'gun charges blotter'],
        'source_note': 'These pages index weapons-related records visible in the public archive. Charges are not convictions and records reflect law enforcement activity only.',
        'faq_name': 'What weapons records appear on this page?',
        'faq_answer': 'It includes visible records referencing weapons charges, firearm possession, unlawful carry, or weapons-related incidents in the indexed archive.',
    },
    'assault': {
        'slug': 'assault',
        'label': 'Assault',
        'short_label': 'Assault',
        'hero_label': 'Montana Assault Pattern Page',
        'description': 'Track assault and battery-related public records from Montana law enforcement agencies and county blotters.',
        'title_statewide': 'Montana Assault Records and Incident Activity',
        'title_county': '{county} County Assault Records and Incident Activity',
        'meta_statewide': 'Track Montana assault records including simple assault, aggravated assault, and battery incidents with county-level pages and recent linked blotter entries.',
        'meta_county': 'Track assault records in {county} County with recent incidents, related blotter entries, and direct links to county-level Montana public safety archives.',
        'intro': 'These pages collect assault-related records into a focused archive so readers can follow violent-incident trends without searching the full county blotter feed.',
        'terms': ['assault', 'battery', 'aggravated assault', 'simple assault', 'strangulation', 'intimidation'],
        'search_targets': ['assault arrests', 'assault blotter', 'aggravated assault records', 'battery charges Montana'],
        'source_note': 'These pages index assault-related records visible in the public archive. Charges are not convictions and records reflect law enforcement activity only.',
        'faq_name': 'What assault records appear on this page?',
        'faq_answer': 'It includes visible records referencing assault, battery, aggravated assault, or related violent-incident charges in the indexed archive.',
    },
    'traffic-violations': {
        'slug': 'traffic-violations',
        'label': 'Traffic Violations',
        'short_label': 'Traffic',
        'hero_label': 'Montana Traffic Violations Pattern Page',
        'description': 'Follow traffic stop, reckless driving, and moving violation records from Montana law enforcement agencies.',
        'title_statewide': 'Montana Traffic Violation Records and Enforcement Activity',
        'title_county': '{county} County Traffic Violation Records and Enforcement Activity',
        'meta_statewide': 'Browse Montana traffic violation records including reckless driving, speeding, and suspended license cases with county pages and recent blotter entries.',
        'meta_county': 'Browse traffic violation records in {county} County with recent incidents, enforcement activity, and links to county-level Montana public safety archives.',
        'intro': 'These pages group traffic-enforcement records into a focused archive for readers following road safety and traffic stop activity across Montana counties.',
        'terms': ['reckless driving', 'reckless endangerment', 'speeding', 'suspended license', 'no insurance', 'traffic stop', 'careless driving', 'hit and run', 'fleeing'],
        'search_targets': ['reckless driving blotter', 'traffic violation records', 'suspended license arrests', 'traffic stop activity Montana'],
        'source_note': 'These pages surface traffic-enforcement records from the indexed archive. They do not replace official court records or DMV data.',
        'faq_name': 'What traffic records appear on this page?',
        'faq_answer': 'It includes visible records referencing reckless driving, speeding, suspended license, hit and run, or other traffic enforcement activity in the indexed archive.',
    },
}

# Apply DB migrations at startup
from init_db import migrate as _migrate
_migrate()
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'admin.admin_login'

from utils.auth_constants import (  # moved to utils/auth_constants.py
    ADMIN_ACCESS_ROLES,
    ADMIN_MANAGEMENT_ROLES,
    EMAIL_OPS_SEND_ROLES,
    OPERATIONS_ROLES,
    CONTENT_REVIEW_ROLES,
    AUDIENCE_MANAGEMENT_ROLES,
    ROLE_LABELS,
)
APP_SETTING_SPECS = {
    'admin_login_max_attempts': {
        'label': 'Max failed logins',
        'type': 'int',
        'section': 'authentication',
        'default': config.ADMIN_LOGIN_MAX_ATTEMPTS,
        'min': 1,
        'max': 20,
        'help': 'How many failed login attempts are allowed before a lockout starts.',
    },
    'admin_login_window_minutes': {
        'label': 'Failure window (minutes)',
        'type': 'int',
        'section': 'authentication',
        'default': config.ADMIN_LOGIN_WINDOW_MINUTES,
        'min': 1,
        'max': 240,
        'help': 'How far back failed login attempts are counted.',
    },
    'admin_login_lockout_minutes': {
        'label': 'Lockout length (minutes)',
        'type': 'int',
        'section': 'authentication',
        'default': config.ADMIN_LOGIN_LOCKOUT_MINUTES,
        'min': 1,
        'max': 240,
        'help': 'How long a locked-out admin must wait before trying again.',
    },
    'max_upload_mb': {
        'label': 'Max upload size (MB)',
        'type': 'int',
        'section': 'ingestion',
        'default': config.MAX_UPLOAD_MB,
        'min': 1,
        'max': 100,
        'help': 'Maximum accepted file size for manual admin uploads.',
    },
    'ingest_alert_repeat_hours': {
        'label': 'Alert reminder interval (hours)',
        'type': 'int',
        'section': 'ingestion',
        'default': config.INGEST_ALERT_REPEAT_HOURS,
        'min': 1,
        'max': 168,
        'help': 'How often unresolved ingestion alerts should repeat.',
    },
    'donations_enabled': {
        'label': 'Enable public donations',
        'type': 'bool',
        'section': 'revenue',
        'default': bool(config.DONATIONS_ENABLED),
        'help': 'Controls whether the public donation flow is available.',
    },
    'winter_storm_banner_enabled': {
        'label': 'Enable winter storm banner',
        'type': 'bool',
        'section': 'revenue',
        'default': bool(WINTER_STORM_SUPPORT_BANNER_DEFAULTS['enabled']),
        'help': 'Controls whether the storm support strip appears beside the official banner.',
    },
    'winter_storm_banner_headline': {
        'label': 'Storm banner headline',
        'type': 'text',
        'section': 'revenue',
        'default': WINTER_STORM_SUPPORT_BANNER_DEFAULTS['headline'],
        'max_length': 140,
        'help': 'Short top-line message shown in the storm support strip.',
    },
    'winter_storm_banner_body': {
        'label': 'Storm banner detail copy',
        'type': 'text',
        'section': 'revenue',
        'default': WINTER_STORM_SUPPORT_BANNER_DEFAULTS['body'],
        'max_length': 280,
        'help': 'Expanded explanation shown when visitors open the official banner detail.',
    },
}

# File upload configuration
ALLOWED_EXTENSIONS = {'pdf'}
app.config['UPLOAD_FOLDER'] = config.UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = config.MAX_UPLOAD_MB * 1024 * 1024

_ADMIN_CSRF_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
_SAFE_URL_SCHEMES = {'http', 'https', 'mailto', 'tel'}
_ALLOWED_MARKDOWN_TAGS = {
    'a', 'p', 'br', 'hr',
    'strong', 'em', 'blockquote',
    'ul', 'ol', 'li',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'code', 'pre',
}
_ALLOWED_MARKDOWN_ATTRS = {
    'a': {'href', 'title', 'target', 'rel'},
    'code': {'class'},
    'pre': {'class'},
}


def _csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


class _SafeHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def _append_start_tag(self, tag: str, attrs):
        allowed = _ALLOWED_MARKDOWN_ATTRS.get(tag, set())
        cleaned = {}
        for key, value in attrs:
            if key not in allowed or value is None:
                continue
            attr_val = value.strip()
            if key == 'href':
                parsed = urlparse(attr_val)
                scheme = parsed.scheme.lower()
                if scheme and scheme not in _SAFE_URL_SCHEMES:
                    continue
                if attr_val.lower().startswith(('javascript:', 'data:', 'vbscript:')):
                    continue
            if key == 'target' and attr_val not in {'_blank', '_self'}:
                continue
            cleaned[key] = attr_val

        if tag == 'a' and cleaned.get('target') == '_blank':
            rel_tokens = set((cleaned.get('rel') or '').split())
            rel_tokens.update({'noopener', 'noreferrer'})
            cleaned['rel'] = ' '.join(sorted(rel_tokens))

        attrs_html = ''.join(
            f' {name}="{escape(val, quote=True)}"'
            for name, val in cleaned.items()
        )
        self.parts.append(f'<{tag}{attrs_html}>')

    def handle_starttag(self, tag, attrs):
        if tag in _ALLOWED_MARKDOWN_TAGS:
            self._append_start_tag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _ALLOWED_MARKDOWN_TAGS and tag != 'br' and tag != 'hr':
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        self.parts.append(escape(data))

    def handle_entityref(self, name):
        self.parts.append(f'&{name};')

    def handle_charref(self, name):
        self.parts.append(f'&#{name};')

    def get_html(self):
        return ''.join(self.parts)


def _sanitize_html(html_text: str) -> str:
    sanitizer = _SafeHTMLSanitizer()
    sanitizer.feed(html_text or '')
    sanitizer.close()
    return sanitizer.get_html()


@app.before_request
def enforce_admin_csrf():
    if request.method not in _ADMIN_CSRF_METHODS:
        return
    if not request.path.startswith('/admin'):
        return
    if (
        request.path == '/admin/api/mission-control/heartbeat'
        and (request.headers.get('X-Internal-Mission-Control') or '').strip() == 'local'
    ):
        return

    session_token = session.get('_csrf_token')
    submitted_token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    if not session_token or not submitted_token or not hmac.compare_digest(session_token, submitted_token):
        wants_json = bool(
            request.is_json
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.accept_mimetypes.best == 'application/json'
        )
        if wants_json:
            return jsonify({'ok': False, 'error': 'csrf_validation_failed'}), 400

        flash('Security token validation failed. Refresh the page and try again.', 'error')
        if current_user.is_authenticated:
            return redirect(request.referrer or url_for('admin.admin_dashboard'))
        return redirect(url_for('admin.admin_login'))


@app.before_request
def enforce_admin_access():
    if not request.path.startswith('/admin'):
        return
    if request.path == '/admin/login':
        return
    if not current_user.is_authenticated:
        return
    if getattr(current_user, 'can_access_admin', False):
        return

    logout_user()
    session.clear()
    flash('Your account is not authorized for the admin panel.', 'error')
    return redirect(url_for('admin.admin_login'))


@app.after_request
def apply_security_headers(response):
    if request.path.startswith('/admin'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'

    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', config.X_FRAME_OPTIONS)
    response.headers.setdefault('Referrer-Policy', config.REFERRER_POLICY)
    if config.CONTENT_SECURITY_POLICY:
        response.headers.setdefault('Content-Security-Policy', config.CONTENT_SECURITY_POLICY)
    if request.path.startswith('/api/') and request.method in {'GET', 'HEAD', 'OPTIONS'}:
        allow_origin_raw = (getattr(config, 'API_CORS_ALLOW_ORIGIN', '') or '').strip()
        allowed_origins = [origin.strip() for origin in allow_origin_raw.split(',') if origin.strip()]
        request_origin = (request.headers.get('Origin') or '').strip()

        if '*' in allowed_origins:
            response.headers.setdefault('Access-Control-Allow-Origin', '*')
        elif request_origin and request_origin in allowed_origins:
            response.headers.setdefault('Access-Control-Allow-Origin', request_origin)
            vary = response.headers.get('Vary', '')
            response.headers['Vary'] = 'Origin' if not vary else f'{vary}, Origin'
        elif not request_origin and len(allowed_origins) == 1:
            response.headers.setdefault('Access-Control-Allow-Origin', allowed_origins[0])

        response.headers.setdefault('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        response.headers.setdefault('Access-Control-Allow-Headers', 'Content-Type')
    return response


@app.context_processor
def inject_csrf_token():
    return {
        'csrf_token': _csrf_token,
        'recaptcha_site_key': config.RECAPTCHA_SITE_KEY if config.RECAPTCHA_ENABLED else '',
        # Keep Facebook auth hidden in the UI while the integration is disabled.
        'facebook_login_enabled': False,
    }

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.template_filter('to_iso_date')
def to_iso_date(date_str):
    """Convert MM/DD/YY or MM/DD/YYYY to YYYY-MM-DD for share URLs."""
    for fmt in ('%m/%d/%y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(date_str or '', fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            pass
    return date_str or ''


PUBLIC_USER_SESSION_KEY = 'public_user_id'
PUBLIC_COMMENT_TYPES = {
    'record': 'Incident record',
    'case_journey': 'Case journey',
    'blog_post': 'Blog post',
    'daily_post': 'Daily report',
}
PUBLIC_COMMENT_STATUSES = {'pending', 'approved', 'rejected', 'spam'}


class PublicUser:
    def __init__(
        self,
        id,
        email,
        display_name,
        subscribe_digest=False,
        subscription_counties='',
        is_subscribed=False,
        subscriber_plan='free',
        stripe_subscription_id='',
        subscription_status='',
        subscription_activated_at=None,
        subscription_canceled_at=None,
        is_active=True,
        last_login_at=None,
    ):
        self.id = id
        self.email = email or ''
        self.display_name = display_name or ''
        self.subscribe_digest = bool(subscribe_digest)
        self.subscription_counties = subscription_counties or ''
        self.is_subscribed = bool(is_subscribed)
        self.subscriber_plan = (subscriber_plan or 'free').strip() or 'free'
        self.stripe_subscription_id = stripe_subscription_id or ''
        self.subscription_status = (subscription_status or '').strip()
        self.subscription_activated_at = subscription_activated_at
        self.subscription_canceled_at = subscription_canceled_at
        self.is_active = bool(is_active)
        self.last_login_at = last_login_at

    @property
    def county_list(self):
        return [item.strip() for item in (self.subscription_counties or '').split(',') if item.strip()]

    @classmethod
    def from_row(cls, row):
        return cls(
            row['id'],
            row['email'],
            row['display_name'],
            subscribe_digest=bool(row['subscribe_digest']) if 'subscribe_digest' in row.keys() else False,
            subscription_counties=row['subscription_counties'] if 'subscription_counties' in row.keys() else '',
            is_subscribed=bool(row['is_subscribed']) if 'is_subscribed' in row.keys() else False,
            subscriber_plan=row['subscriber_plan'] if 'subscriber_plan' in row.keys() else 'free',
            stripe_subscription_id=row['stripe_subscription_id'] if 'stripe_subscription_id' in row.keys() else '',
            subscription_status=row['subscription_status'] if 'subscription_status' in row.keys() else '',
            subscription_activated_at=row['subscription_activated_at'] if 'subscription_activated_at' in row.keys() else None,
            subscription_canceled_at=row['subscription_canceled_at'] if 'subscription_canceled_at' in row.keys() else None,
            is_active=bool(row['is_active']) if 'is_active' in row.keys() else True,
            last_login_at=row['last_login_at'] if 'last_login_at' in row.keys() else None,
        )


def verify_recaptcha(token: str, action: str = '') -> bool:
    """Verify a reCAPTCHA v3 token.  Returns True when verification passes or reCAPTCHA is disabled."""
    if not config.RECAPTCHA_ENABLED:
        return True
    if not token:
        return False
    try:
        payload = urllib.parse.urlencode({
            'secret': config.RECAPTCHA_SECRET_KEY,
            'response': token,
        }).encode()
        req = urllib.request.Request(
            'https://www.google.com/recaptcha/api/siteverify',
            data=payload,
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
        if not result.get('success'):
            app.logger.warning('reCAPTCHA verification failed: %s', result.get('error-codes'))
            return False
        score = result.get('score', 0)
        if score < config.RECAPTCHA_SCORE_THRESHOLD:
            app.logger.info('reCAPTCHA score %.2f below threshold %.2f (action=%s)',
                            score, config.RECAPTCHA_SCORE_THRESHOLD, action)
            return False
        if action and result.get('action') != action:
            app.logger.warning('reCAPTCHA action mismatch: expected=%s got=%s', action, result.get('action'))
            return False
        return True
    except Exception as e:
        app.logger.error('reCAPTCHA verification error: %s', e)
        # Fail open — don't block users if Google is unreachable
        return True


def _safe_next_url(raw_value, fallback='/'):
    target = (raw_value or '').strip()
    if not target:
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not target.startswith('/'):
        return fallback
    return target


def _all_subscription_counties(conn):
    counties = [
        row['county']
        for row in conn.execute(
            '''
            SELECT county
            FROM (
                SELECT DISTINCT county AS county FROM posts WHERE county IS NOT NULL AND county != ''
                UNION
                SELECT DISTINCT county AS county FROM records WHERE county IS NOT NULL AND county != ''
            )
            ORDER BY county
            '''
        ).fetchall()
    ]
    return counties


def _selected_counties_from_form():
    selected = []
    for county in request.form.getlist('counties'):
        value = (county or '').strip()
        if value and value not in selected:
            selected.append(value)
    return selected


def _upsert_digest_subscription(conn, email, counties, source='public_account'):
    _ensure_subscriber_schema(conn)
    normalized_email = (email or '').strip().lower()
    if not normalized_email:
        return

    counties_str = ','.join(counties or [])
    token = secrets.token_urlsafe(32)
    existing = conn.execute(
        'SELECT id, token FROM subscribers WHERE email = ?',
        (normalized_email,),
    ).fetchone()
    if existing:
        conn.execute(
            '''
            UPDATE subscribers
            SET counties = ?, active = 1, source = ?, updated_at = datetime('now')
            WHERE id = ?
            ''',
            (counties_str, (source or 'public_account')[:80], existing['id']),
        )
    else:
        conn.execute(
            '''
            INSERT INTO subscribers (email, counties, token, source, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ''',
            (normalized_email, counties_str, token, (source or 'public_account')[:80]),
        )


def _load_public_user(user_id, conn=None):
    if not user_id:
        return None
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        row = conn.execute(
            'SELECT * FROM public_users WHERE id = ? AND COALESCE(is_active, 1) = 1',
            (user_id,),
        ).fetchone()
        return PublicUser.from_row(row) if row else None
    finally:
        if own_conn:
            conn.close()


def _get_public_user():
    cached = getattr(g, 'public_user', None)
    if cached is not None:
        return cached
    user_id = session.get(PUBLIC_USER_SESSION_KEY)
    user = _load_public_user(user_id)
    g.public_user = user
    return user


def _set_public_user_session(user_id):
    session[PUBLIC_USER_SESSION_KEY] = int(user_id)
    g.public_user = None


def _clear_public_user_session():
    session.pop(PUBLIC_USER_SESSION_KEY, None)
    g.public_user = None


register_bondsman_command_center(
    app,
    get_db=get_db,
    get_public_user=_get_public_user,
    base_url=BASE_URL,
)

register_agent_dashboard(
    app,
    get_db=get_db,
    base_url=BASE_URL,
)


def _public_comment_target_exists(conn, content_type, content_id):
    normalized_type = (content_type or '').strip()
    normalized_id = str(content_id or '').strip()
    if not normalized_id or normalized_type not in PUBLIC_COMMENT_TYPES:
        return False

    if normalized_type == 'record':
        row = conn.execute('SELECT 1 FROM records WHERE id = ?', (normalized_id,)).fetchone()
    elif normalized_type == 'case_journey':
        row = conn.execute(
            'SELECT 1 FROM case_journeys WHERE id = ? AND is_published = 1',
            (normalized_id,),
        ).fetchone()
    elif normalized_type == 'blog_post':
        row = conn.execute(
            'SELECT 1 FROM blog_posts WHERE id = ? AND published = 1',
            (normalized_id,),
        ).fetchone()
    elif normalized_type == 'daily_post':
        row = conn.execute('SELECT 1 FROM posts WHERE id = ?', (normalized_id,)).fetchone()
    else:
        row = None
    return bool(row)


def _public_comments_context(conn, content_type, content_id):
    rows = conn.execute(
        '''
        SELECT
            public_comments.*,
            public_users.display_name,
            public_users.email
        FROM public_comments
        JOIN public_users ON public_users.id = public_comments.public_user_id
        WHERE public_comments.content_type = ?
          AND public_comments.content_id = ?
          AND public_comments.status = 'approved'
        ORDER BY public_comments.created_at ASC, public_comments.id ASC
        ''',
        ((content_type or '').strip(), str(content_id)),
    ).fetchall()

    comments = []
    for row in rows:
        comments.append(
            {
                'id': row['id'],
                'display_name': row['display_name'],
                'created_at': row['created_at'],
                'body': row['body'],
            }
        )

    return {
        'content_type': (content_type or '').strip(),
        'content_id': str(content_id),
        'comments': comments,
        'comment_count': len(comments),
    }


def _public_comment_target_path(conn, content_type, content_id):
    normalized_type = (content_type or '').strip()
    normalized_id = str(content_id or '').strip()
    if normalized_type == 'record':
        return f'/record/{normalized_id}'
    if normalized_type == 'daily_post':
        return f'/post/{normalized_id}'
    if normalized_type == 'blog_post':
        row = conn.execute('SELECT slug FROM blog_posts WHERE id = ?', (normalized_id,)).fetchone()
        if row and row['slug']:
            return f"/blog/{row['slug']}"
    if normalized_type == 'case_journey':
        row = conn.execute('SELECT slug FROM case_journeys WHERE id = ?', (normalized_id,)).fetchone()
        if row and row['slug']:
            return f"/case-journeys/{row['slug']}"
    return '/'


def _request_host_name():
    return (request.host or '').split(':', 1)[0].strip().lower()


def _is_agendas_host():
    host = _request_host_name()
    configured = (getattr(config, 'AGENDAS_HOST', '') or '').split(':', 1)[0].strip().lower()
    if configured:
        return host == configured
    return host.startswith('agendas.')


def _public_meetings_href():
    if _is_agendas_host():
        return '/'
    external = (getattr(config, 'AGENDAS_BASE_URL', '') or '').strip()
    return external or '/meetings'


def _client_ip():
    # Cloudflare sets CF-Connecting-IP to the real visitor IP
    cf_ip = request.headers.get('CF-Connecting-IP', '').strip()
    if cf_ip:
        return cf_ip
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return (request.remote_addr or '').strip()


def _parse_sqlite_timestamp(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _login_rate_limited(conn, username: str, ip_address: str):
    window_minutes = _admin_login_window_minutes(conn=conn)
    max_attempts = _admin_login_max_attempts(conn=conn)
    lockout_minutes = _admin_login_lockout_minutes(conn=conn)
    window = f'-{window_minutes} minutes'
    row = conn.execute(
        '''
        SELECT COUNT(*) AS failures, MAX(created_at) AS last_failure
        FROM auth_login_attempts
        WHERE success = 0
          AND created_at >= datetime('now', ?)
          AND (username = ? OR ip_address = ?)
        ''',
        (window, username, ip_address),
    ).fetchone()

    failures = int(row['failures'] or 0)
    if failures < max_attempts:
        return False, 0

    last_failure = _parse_sqlite_timestamp(row['last_failure'])
    if not last_failure:
        return True, lockout_minutes * 60

    unlock_time = last_failure + timedelta(minutes=lockout_minutes)
    remaining = int((unlock_time - datetime.utcnow()).total_seconds())
    if remaining > 0:
        return True, remaining
    return False, 0


def _record_login_attempt(conn, username: str, ip_address: str, success: bool):
    conn.execute(
        '''
        INSERT INTO auth_login_attempts (username, ip_address, success)
        VALUES (?, ?, ?)
        ''',
        (username, ip_address, 1 if success else 0),
    )
    conn.commit()


def _county_slug_for_name(name):
    if not name:
        return None
    target = name.strip().lower()
    for slug, county in COUNTY_DATA.items():
        if county['name'].strip().lower() == target:
            return slug
    return None


def _city_slug_for_name(name):
    if not name:
        return None
    target = name.strip().lower()
    for slug, city in CITY_DATA.items():
        if city['name'].strip().lower() == target:
            return slug
    return None


def _summary_lines(summary):
    return [line.strip() for line in (summary or '').split('\n') if line.strip()]


def _detail_source_pdf_name(row):
    if not row:
        return None
    file_path = (row['file_path'] or '').strip() if 'file_path' in row.keys() else ''
    if file_path:
        return os.path.basename(file_path)

    filename = ''
    for key in ('source_filename', 'blotter_filename'):
        if key in row.keys() and (row[key] or '').strip():
            filename = row[key].strip()
            break
    return os.path.basename(filename) if filename else None


def _charge_explainer_slug_map(conn, records):
    incident_types = []
    for record in records or []:
        incident_type = (record['incident_type'] or '').strip()
        if incident_type and incident_type not in incident_types:
            incident_types.append(incident_type)
    if not incident_types:
        return {}

    placeholders = ','.join('?' for _ in incident_types)
    rows = conn.execute(
        f'''
        SELECT incident_type, slug
        FROM charge_explainers
        WHERE published = 1
          AND incident_type IN ({placeholders})
        ''',
        incident_types,
    ).fetchall()
    return {row['incident_type']: row['slug'] for row in rows}


def _safe_json_loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


from utils.app_settings import (  # moved to utils/app_settings.py
    _app_setting_raw,
    _app_setting_bool,
    _app_setting_int,
    _app_setting_text,
    _save_app_setting,
)


def _admin_login_max_attempts(conn=None) -> int:
    spec = APP_SETTING_SPECS['admin_login_max_attempts']
    return _app_setting_int('admin_login_max_attempts', spec['default'], spec['min'], spec['max'], conn=conn)


def _admin_login_window_minutes(conn=None) -> int:
    spec = APP_SETTING_SPECS['admin_login_window_minutes']
    return _app_setting_int('admin_login_window_minutes', spec['default'], spec['min'], spec['max'], conn=conn)


def _admin_login_lockout_minutes(conn=None) -> int:
    spec = APP_SETTING_SPECS['admin_login_lockout_minutes']
    return _app_setting_int('admin_login_lockout_minutes', spec['default'], spec['min'], spec['max'], conn=conn)


def _max_upload_mb(conn=None) -> int:
    spec = APP_SETTING_SPECS['max_upload_mb']
    return _app_setting_int('max_upload_mb', spec['default'], spec['min'], spec['max'], conn=conn)


def _ingest_alert_repeat_hours(conn=None) -> int:
    spec = APP_SETTING_SPECS['ingest_alert_repeat_hours']
    return _app_setting_int('ingest_alert_repeat_hours', spec['default'], spec['min'], spec['max'], conn=conn)


def _donations_enabled(conn=None):
    return _app_setting_bool('donations_enabled', bool(getattr(config, 'DONATIONS_ENABLED', False)), conn=conn)


def _winter_storm_banner_config(conn=None):
    banner = dict(WINTER_STORM_SUPPORT_BANNER_DEFAULTS)
    banner['enabled'] = _app_setting_bool(
        'winter_storm_banner_enabled',
        bool(WINTER_STORM_SUPPORT_BANNER_DEFAULTS['enabled']),
        conn=conn,
    )
    banner['headline'] = _app_setting_text(
        'winter_storm_banner_headline',
        WINTER_STORM_SUPPORT_BANNER_DEFAULTS['headline'],
        max_length=APP_SETTING_SPECS['winter_storm_banner_headline']['max_length'],
        conn=conn,
    )
    banner['body'] = _app_setting_text(
        'winter_storm_banner_body',
        WINTER_STORM_SUPPORT_BANNER_DEFAULTS['body'],
        max_length=APP_SETTING_SPECS['winter_storm_banner_body']['max_length'],
        conn=conn,
    )
    return banner


def _apply_runtime_app_settings(conn=None) -> None:
    app.config['MAX_CONTENT_LENGTH'] = _max_upload_mb(conn=conn) * 1024 * 1024


def _settings_form_values(conn):
    values = {}
    for key, spec in APP_SETTING_SPECS.items():
        if spec['type'] == 'bool':
            values[key] = _app_setting_bool(key, spec['default'], conn=conn)
        elif spec['type'] == 'int':
            values[key] = _app_setting_int(key, spec['default'], spec.get('min'), spec.get('max'), conn=conn)
        elif spec['type'] == 'text':
            values[key] = _app_setting_text(key, spec['default'], spec.get('max_length'), conn=conn)
    return values


def _coerce_setting_value(key: str, raw_value):
    spec = APP_SETTING_SPECS[key]
    if spec['type'] == 'bool':
        return str(raw_value or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if spec['type'] == 'int':
        try:
            value = int(str(raw_value or '').strip())
        except (TypeError, ValueError):
            raise ValueError(f"{spec['label']} must be a whole number.")
        if spec.get('min') is not None and value < spec['min']:
            raise ValueError(f"{spec['label']} must be at least {spec['min']}.")
        if spec.get('max') is not None and value > spec['max']:
            raise ValueError(f"{spec['label']} must be at most {spec['max']}.")
        return value
    if spec['type'] == 'text':
        value = str(raw_value or '').strip()
        if spec.get('max_length') is not None and len(value) > spec['max_length']:
            raise ValueError(f"{spec['label']} must be {spec['max_length']} characters or fewer.")
        return value
    raise ValueError('Unsupported setting type.')


_apply_runtime_app_settings()


def _donation_currency():
    return (getattr(config, 'DONATION_CURRENCY', 'usd') or 'usd').strip().lower() or 'usd'


def _donation_min_cents():
    try:
        value = int(getattr(config, 'DONATION_MIN_CENTS', 500))
    except (TypeError, ValueError):
        value = 500
    return max(100, value)


def _donation_max_cents():
    return max(_donation_min_cents(), 500000)


def _allowed_donation_amounts():
    raw = getattr(config, 'DONATION_SUGGESTED_AMOUNTS_CENTS', (500, 1500, 2500, 5000))
    amounts = []
    for value in raw:
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if _donation_min_cents() <= amount <= _donation_max_cents():
            amounts.append(amount)
    if not amounts:
        amounts = [amount for amount in (500, 1500, 2500, 5000) if amount >= _donation_min_cents()]
    return tuple(sorted(set(amounts)))


def _donation_campaign_context(raw_campaign=''):
    campaign = (raw_campaign or '').strip().lower()
    if campaign == 'bondsman_command_center':
        return {
            'key': 'bondsman_command_center',
            'badge': 'Bondsman Pro',
            'headline': 'Unlock the Bondsman Command Center',
            'description': 'Activate the subscriber-only bondsman workspace for defendant watchlists, booking alerts, lead claiming, and client check-ins.',
            'funds': [
                'Live defendant watchlist matching against fresh jail bookings',
                'Private lead claiming and active case management',
                'Hosted client check-in links with selfie and GPS collection',
            ],
        }
    if campaign == 'winter_storm':
        return {
            'key': 'winter_storm',
            'badge': 'Winter Storm Response',
            'headline': 'Fund Montana winter storm reporting and emergency follow-up',
            'description': 'Direct support helps Montana Blotter keep storm-related dispatch coverage, county emergency updates, and public-records follow-up visible statewide.',
            'funds': [
                'Dispatch and county emergency monitoring during severe weather',
                'Storm-related county, road-closure, and response coverage',
                'Infrastructure costs to keep public pages online during traffic spikes',
            ],
        }
    return {
        'key': '',
        'badge': 'Support',
        'headline': 'Support Independent Montana Public Safety Coverage',
        'description': 'Your donation helps keep Montana Blotter free, searchable, and available to the public every day.',
        'funds': [
            'Daily data ingestion and archive maintenance',
            'Public search and county-level reporting pages',
            'Reliability, hosting, and operations costs',
        ],
    }


def _stripe_keys():
    return {
        'secret_key': (getattr(config, 'STRIPE_SECRET_KEY', '') or '').strip(),
        'publishable_key': (getattr(config, 'STRIPE_PUBLISHABLE_KEY', '') or '').strip(),
        'webhook_secret': (getattr(config, 'STRIPE_WEBHOOK_SECRET', '') or '').strip(),
    }


def _stripe_ready_for_checkout():
    keys = _stripe_keys()
    return bool(stripe and keys['secret_key'] and keys['publishable_key'])


def _stripe_ready_for_webhooks():
    keys = _stripe_keys()
    return bool(stripe and keys['secret_key'] and keys['webhook_secret'])


def _donation_email_hash(email):
    normalized = (email or '').strip().lower()
    if not normalized:
        return ''
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _is_bondsman_subscription_source(source):
    normalized = (source or '').strip().lower()
    return normalized in {
        'bondsman_command_center',
        'bondsman_account',
        'bondsman_upgrade',
    }


def _set_public_user_subscription_state(
    conn,
    *,
    public_user_id=None,
    email='',
    is_subscribed=False,
    subscriber_plan='free',
    stripe_subscription_id='',
    subscription_status='',
):
    target_user_id = int(public_user_id) if public_user_id else None
    normalized_email = (email or '').strip().lower()
    if target_user_id is None and normalized_email:
        row = conn.execute(
            'SELECT id FROM public_users WHERE email = ?',
            (normalized_email,),
        ).fetchone()
        if row:
            target_user_id = int(row['id'])
    if target_user_id is None:
        return

    conn.execute(
        '''
        UPDATE public_users
        SET is_subscribed = ?,
            subscriber_plan = ?,
            stripe_subscription_id = CASE
                WHEN ? != '' THEN ?
                WHEN ? = 0 THEN ''
                ELSE stripe_subscription_id
            END,
            subscription_status = ?,
            subscription_activated_at = CASE
                WHEN ? = 1 THEN COALESCE(subscription_activated_at, datetime('now'))
                ELSE subscription_activated_at
            END,
            subscription_canceled_at = CASE
                WHEN ? = 0 THEN datetime('now')
                ELSE NULL
            END
        WHERE id = ?
        ''',
        (
            1 if is_subscribed else 0,
            (subscriber_plan or 'free').strip() or 'free',
            (stripe_subscription_id or '').strip(),
            (stripe_subscription_id or '').strip(),
            1 if is_subscribed else 0,
            (subscription_status or '').strip()[:40],
            1 if is_subscribed else 0,
            1 if is_subscribed else 0,
            target_user_id,
        ),
    )


def _sync_public_user_subscription_from_donations(conn, public_user_id, email):
    email_hash = _donation_email_hash(email)
    if not email_hash:
        return
    row = conn.execute(
        '''
        SELECT provider_subscription_id, status, source
        FROM donations
        WHERE email_hash = ?
          AND mode = 'monthly'
          AND status = 'succeeded'
        ORDER BY datetime(updated_at) DESC, datetime(created_at) DESC
        LIMIT 1
        ''',
        (email_hash,),
    ).fetchone()
    if row and _is_bondsman_subscription_source(row['source']):
        _set_public_user_subscription_state(
            conn,
            public_user_id=public_user_id,
            email=email,
            is_subscribed=True,
            subscriber_plan='bondsman_pro',
            stripe_subscription_id=row['provider_subscription_id'] or '',
            subscription_status=row['status'] or 'active',
        )


def _bail_ad_packages():
    return [
        {
            'id': 'exclusive_county_sponsorship',
            'name': 'The Horizon Exclusive',
            'type': 'County Sponsorship',
            'price_monthly_cents': 15000,
            'price_annual_cents': 180000,
            'county_slots': 1,
            'badge': 'County Sponsorship',
            'price_label_monthly': '$150 - $350',
            'short_description': 'Exclusive county feed sponsorship with hyper-local branding.',
            'full_description': 'Reserve a single county feed for one agency only and own the local arrest audience in that market.',
            'pricing_model': 'county_tiered',
            'features': [
                'Exclusive county feed sponsorship',
                'Single agency per county',
                'Hyper-local branding',
            ],
            'cta': 'Select County',
            'highlight': False,
        },
        {
            'id': 'emergency_call_sidebar',
            'name': 'The Summit Sidebar',
            'type': 'Emergency Call Sidebar',
            'price_monthly_cents': 30000,
            'price_annual_cents': 360000,
            'county_slots': 0,
            'badge': 'Emergency Call Sidebar',
            'short_description': 'Sticky 300x600 visibility built for emergency response traffic.',
            'full_description': 'Stay visible through long scroll sessions with a persistent sidebar unit optimized for immediate mobile action.',
            'features': [
                '300x600 sticky sidebar unit',
                'Persistent visibility on scroll',
                'Mobile-first tap-to-call',
            ],
            'cta': 'Claim Sidebar',
            'highlight': False,
        },
        {
            'id': 'featured_bondsman_banner',
            'name': 'The Big Sky Header',
            'type': 'Top Banner Placement',
            'price_monthly_cents': 45000,
            'price_annual_cents': 540000,
            'county_slots': 0,
            'badge': 'Top Banner Placement',
            'short_description': 'Premium statewide header built for first-view visibility.',
            'full_description': 'Own the first thing readers see with a 970x250 placement spanning MontanaBlotter arrest coverage.',
            'features': [
                '970x250 premium header',
                'Top-of-feed statewide visibility',
                'First-view real estate',
            ],
            'cta': 'Secure Header',
            'highlight': False,
        },
        {
            'id': 'gold_bond_bundle',
            'name': 'The Gold Bond Bundle',
            'type': 'Market Dominance',
            'price_monthly_cents': 65000,
            'price_annual_cents': 780000,
            'county_slots': 2,
            'badge': 'Market Dominance',
            'short_description': 'Header, sidebar, and county exclusivity bundled into one featured package.',
            'full_description': 'Take over the highest-intent surfaces across MontanaBlotter with bundled pricing and multi-touch coverage.',
            'features': [
                'Includes Header + Sidebar',
                'Plus 2 Exclusive Counties',
                '15% bundled discount applied',
            ],
            'cta': 'Dominate Market',
            'highlight': True,
        },
        {
            'id': 'silver_link',
            'name': 'The Silver Link',
            'price_monthly_cents': 35000,
            'price_annual_cents': 350000,
            'county_slots': 1,
            'badge': 'Sidebar + one county feed',
            'legacy': True,
            'active': False,
            'features': [
                'Emergency Call sticky sidebar ad placement',
                'Sponsored link placement in one county feed',
                'Mobile-first tap-to-call call-to-action',
            ],
        },
        {
            'id': 'gold_bond',
            'name': 'The Gold Bond',
            'price_monthly_cents': 65000,
            'price_annual_cents': 650000,
            'county_slots': 2,
            'badge': 'Top banner + sidebar + 2 counties',
            'legacy': True,
            'active': False,
            'features': [
                'Featured Bondsman top banner placement',
                'Emergency Call sticky sidebar placement',
                'Sponsored coverage in two county feeds',
            ],
        },
        {
            'id': 'state_power',
            'name': 'The State Power',
            'price_monthly_cents': 150000,
            'price_annual_cents': 1500000,
            'county_slots': len(COUNTY_DATA),
            'all_counties': True,
            'badge': 'Statewide takeover package',
            'legacy': True,
            'active': False,
            'features': [
                'Top banner placement on all pages',
                'Emergency Call sticky sidebar placement',
                'County coverage across all Montana counties',
            ],
        },
    ]


def _bail_ad_public_packages():
    return [pkg for pkg in _bail_ad_packages() if pkg.get('active', True)]


def _format_bail_ad_currency(cents):
    return f"${int(cents or 0) / 100:,.0f}"


def _safe_bail_ad_simulator_image_url(raw_value):
    value = (raw_value or '').strip()[:1000]
    if not value:
        return ''
    parsed = urlparse(value)
    if parsed.scheme in {'http', 'https'} and parsed.netloc:
        return value
    if not parsed.scheme and value.startswith('/'):
        return value
    return ''


def _bail_ad_pricing_cards(package_options):
    cards = []
    for pkg in package_options:
        cards.append(
            {
                'id': pkg['id'],
                'name': pkg.get('name') or '',
                'type': pkg.get('type') or pkg.get('badge') or '',
                'price': pkg.get('price_label_monthly') or _format_bail_ad_currency(pkg.get('price_monthly_cents')),
                'annual': f"{_format_bail_ad_currency(pkg.get('price_annual_cents'))}/yr",
                'features': list(pkg.get('features') or []),
                'cta': pkg.get('cta') or 'Select Package',
                'highlight': bool(pkg.get('highlight')),
                'checkoutUrl': url_for('payments.advertise_bail_bonds_checkout', package=pkg['id'], source='package_card'),
            }
        )
    return cards


def _bail_ad_package_id_for_simulator_view(view_name=''):
    return 'emergency_call_sidebar' if (view_name or '').strip().lower() == 'sidebar' else 'featured_bondsman_banner'


def _bail_ad_simulator_view_for_package(package_id=''):
    return 'sidebar' if _normalize_bail_ad_package_id(package_id) == 'emergency_call_sidebar' else 'banner'


def _bail_ad_simulator_preview(order):
    return {
        'logo_path': _safe_bail_ad_simulator_image_url((order or {}).get('simulator_logo_path') or ''),
        'target_url': ((order or {}).get('simulator_target_url') or '').strip()[:300],
        'share_url': ((order or {}).get('simulator_share_url') or '').strip()[:500],
        'view': ((order or {}).get('simulator_view') or '').strip().lower()[:24],
    }


def _bail_ad_package_aliases():
    return {
        'starter': 'exclusive_county_sponsorship',
        'growth': 'gold_bond_bundle',
        'dominance': 'gold_bond_bundle',
        'featured': 'featured_bondsman_banner',
        'sidebar': 'emergency_call_sidebar',
        'county': 'exclusive_county_sponsorship',
        'gold_bundle': 'gold_bond_bundle',
    }


def _normalize_bail_ad_package_id(raw_value):
    token = (raw_value or '').strip().lower()
    if not token:
        return ''
    return _bail_ad_package_aliases().get(token, token)


def _bail_ad_package_lookup():
    lookup = {pkg['id']: pkg for pkg in _bail_ad_packages()}
    for legacy_id, package_id in _bail_ad_package_aliases().items():
        package = lookup.get(package_id)
        if package:
            lookup[legacy_id] = package
    return lookup


def _bail_ad_addons():
    return [
        {
            'id': 'in_feed_integration',
            'name': 'In-Feed Integration',
            'description': 'Sponsored in-feed placement every 5th or 10th arrest entry.',
            'price_monthly_cents': 20000,
            'price_annual_cents': 200000,
        },
    ]


def _bail_ad_addon_lookup():
    return {addon['id']: addon for addon in _bail_ad_addons()}


def _parse_addon_ids(raw_values):
    lookup = _bail_ad_addon_lookup()
    out = []
    seen = set()
    for raw in raw_values or []:
        token = (raw or '').strip().lower()
        if token in lookup and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _parse_budget_cents(raw_value):
    token = (raw_value or '').strip().replace('$', '').replace(',', '')
    if not token:
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    cents = int(round(value * 100))
    if cents <= 0:
        return None
    return min(cents, 100000000)


def _bail_ad_checkout_ready():
    return _stripe_ready_for_checkout()


def _bail_ad_allowed_asset(filename):
    if not filename or '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return ext in {'png', 'jpg', 'jpeg', 'webp', 'gif'}


def _ensure_bail_ad_simulator_order_columns(conn):
    columns = {
        (row['name'] if isinstance(row, sqlite3.Row) else row[1])
        for row in conn.execute("PRAGMA table_info('bail_ad_orders')").fetchall()
    }
    additions = [
        ('simulator_logo_path', "ALTER TABLE bail_ad_orders ADD COLUMN simulator_logo_path TEXT NOT NULL DEFAULT ''"),
        ('simulator_target_url', "ALTER TABLE bail_ad_orders ADD COLUMN simulator_target_url TEXT NOT NULL DEFAULT ''"),
        ('simulator_share_url', "ALTER TABLE bail_ad_orders ADD COLUMN simulator_share_url TEXT NOT NULL DEFAULT ''"),
        ('simulator_view', "ALTER TABLE bail_ad_orders ADD COLUMN simulator_view TEXT NOT NULL DEFAULT ''"),
    ]
    for column_name, sql in additions:
        if column_name not in columns:
            conn.execute(sql)


def _ensure_bail_ad_simulator_event_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_ad_simulator_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            sim_view TEXT NOT NULL DEFAULT '',
            county TEXT NOT NULL DEFAULT '',
            agency_name TEXT NOT NULL DEFAULT '',
            asset_path TEXT NOT NULL DEFAULT '',
            share_url TEXT NOT NULL DEFAULT '',
            internal_mode INTEGER NOT NULL DEFAULT 0,
            ip_hash TEXT NOT NULL DEFAULT '',
            referrer TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_simulator_events_created ON bail_ad_simulator_events(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_ad_simulator_events_type ON bail_ad_simulator_events(event_type)')


def _record_bail_ad_simulator_event(
    conn,
    event_type,
    source='',
    sim_view='',
    county='',
    agency_name='',
    asset_path='',
    share_url='',
    internal_mode=False,
):
    if not event_type:
        return
    _ensure_bail_ad_simulator_event_schema(conn)
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    conn.execute(
        '''
        INSERT INTO bail_ad_simulator_events (
            event_type, source, sim_view, county, agency_name, asset_path, share_url, internal_mode, ip_hash, referrer
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            (event_type or '').strip()[:40],
            (source or '').strip()[:80],
            (sim_view or '').strip()[:24],
            (county or '').strip()[:80],
            (agency_name or '').strip()[:120],
            (asset_path or '').strip()[:500],
            (share_url or '').strip()[:500],
            1 if internal_mode else 0,
            ip_hash,
            referrer,
        ),
    )


def _parse_county_targets(raw_value):
    raw = (raw_value or '').replace('\n', ',').replace(';', ',')
    county_lookup = {county['name'].lower(): county['name'] for county in COUNTY_DATA.values()}
    slug_lookup = {slug.lower(): county['name'] for slug, county in COUNTY_DATA.items()}
    parsed = []
    seen = set()
    for token in raw.split(','):
        value = token.strip()
        if not value:
            continue
        key = value.lower()
        normalized = county_lookup.get(key) or slug_lookup.get(key) or value[:64]
        slug_key = normalized.lower()
        if slug_key in seen:
            continue
        seen.add(slug_key)
        parsed.append(normalized)
        if len(parsed) >= max(12, len(COUNTY_DATA)):
            break
    return parsed


def _all_bail_counties():
    counties = []
    seen = set()
    for county in getattr(config, 'MONTANA_COUNTIES', []) or []:
        name = (county or '').strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        counties.append(name)
    for county in COUNTY_DATA.values():
        name = (county.get('name') or '').strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        counties.append(name)
    return sorted(counties)


def _normalize_bail_county(raw_value):
    value = (raw_value or '').strip()
    if not value:
        return ''
    lower_map = {county.lower(): county for county in _all_bail_counties()}
    slug_map = {_slugify_key(county): county for county in _all_bail_counties()}
    token = value.lower()
    if token in lower_map:
        return lower_map[token]
    if token in slug_map:
        return slug_map[token]
    slug = _slugify_key(value)
    if slug in slug_map:
        return slug_map[slug]
    return value[:80]


def _format_phone_for_tel(raw_phone):
    token = ''.join(ch for ch in (raw_phone or '') if ch.isdigit())
    if len(token) == 10:
        token = f'1{token}'
    if len(token) < 11:
        return ''
    return f'+{token}'


def _bail_help_contact(default_phone=''):
    phone = (getattr(config, 'BAIL_HELP_PHONE', '') or '').strip()
    sms_number = (getattr(config, 'BAIL_HELP_SMS', '') or '').strip()
    chat_url = (getattr(config, 'BAIL_HELP_CHAT_URL', '') or '').strip()

    if not phone:
        phone = (default_phone or '').strip()
    if not sms_number:
        sms_number = phone

    tel_href = _format_phone_for_tel(phone)
    sms_href = _format_phone_for_tel(sms_number)
    return {
        'phone': phone,
        'phone_display': phone or 'Call',
        'tel_href': f'tel:{tel_href}' if tel_href else '',
        'sms_href': f'sms:{sms_href}' if sms_href else '',
        'chat_url': chat_url,
    }


def _bail_ad_contract_context(onboarding_token: str | None = None):
    configured_url = (getattr(config, 'LETSBAIL_AD_CONTRACT_URL', '') or '').strip()
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or (getattr(config, 'EMAIL_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )
    safe_token = (onboarding_token or '').strip()[:128]
    contract_url = configured_url
    if not contract_url and safe_token:
        contract_url = url_for('payments.advertise_bail_private_contract', token=safe_token)
    return {
        'title': "Affordable Bail Bonds Advertising Contract",
        'partner_name': "Affordable Bail Bonds",
        'url': contract_url,
        'external': bool(configured_url),
        'updated_label': 'Updated March 19, 2026',
        'summary': 'Review placement terms, recurring billing, creative standards, county inventory rules, and cancellation timing before launch.',
        'support_email': support_email,
        'highlights': [
            'All creative is subject to Montana Blotter compliance and quality review before launch.',
            'Subscriptions renew automatically on the selected billing cycle until canceled.',
            'County exclusives and bundled placements remain subject to inventory availability.',
        ],
    }


def _ensure_bail_consumer_lead_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_consumer_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            county TEXT NOT NULL,
            jail_facility TEXT,
            callback_preference TEXT,
            notes TEXT,
            source TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            routed_order_ids TEXT,
            routed_business_names TEXT,
            routed_emails TEXT,
            routed_phones TEXT,
            ip_hash TEXT,
            referrer TEXT,
            review_notes TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_created ON bail_consumer_leads(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_status ON bail_consumer_leads(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_leads_county ON bail_consumer_leads(county)')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_consumer_lead_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            event_type TEXT NOT NULL,
            county TEXT,
            source TEXT,
            ip_hash TEXT,
            referrer TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES bail_consumer_leads(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_created ON bail_consumer_lead_events(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_type ON bail_consumer_lead_events(event_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_consumer_events_county ON bail_consumer_lead_events(county)')


def _record_bail_consumer_event(conn, event_type, county='', source='', lead_id=None):
    safe_event = (event_type or '').strip().lower()[:40]
    if not safe_event:
        return
    safe_county = _normalize_bail_county(county)[:80]
    safe_source = (source or '').strip()[:80]
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    conn.execute(
        '''
        INSERT INTO bail_consumer_lead_events (lead_id, event_type, county, source, ip_hash, referrer)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            int(lead_id) if lead_id else None,
            safe_event,
            safe_county,
            safe_source,
            ip_hash,
            referrer,
        ),
    )


def _bail_lead_notify_recipients():
    recipients = []
    configured = getattr(config, 'BAIL_LEAD_NOTIFY_EMAILS', ()) or ()
    if isinstance(configured, str):
        configured = [part.strip() for part in configured.split(',') if part.strip()]
    for entry in configured:
        email = (entry or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)
    if not recipients:
        fallback = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
        if fallback and '@' in fallback:
            recipients.append(fallback)
    return recipients


def _send_bail_lead_notification_email(to_emails, subject, body):
    recipients = []
    for value in to_emails or []:
        email = (value or '').strip().lower()
        if email and '@' in email and email not in recipients:
            recipients.append(email)
    if not recipients:
        return False

    smtp_user = (getattr(config, 'SMTP_USER', '') or '').strip()
    smtp_password = (getattr(config, 'SMTP_PASSWORD', '') or '').strip()
    smtp_server = (getattr(config, 'SMTP_SERVER', '') or '').strip()
    smtp_port = int(getattr(config, 'SMTP_PORT', 587) or 587)
    if not (smtp_user and smtp_password and smtp_server):
        return False

    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = smtp_user
    msg['To'] = ', '.join(recipients)
    try:
        smtp = smtplib.SMTP(smtp_server, smtp_port, timeout=20)
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(smtp_user, recipients, msg.as_string())
        smtp.quit()
        return True
    except Exception:
        return False


def _post_bail_lead_webhook(payload):
    webhook_url = (getattr(config, 'BAIL_LEAD_WEBHOOK_URL', '') or '').strip()
    if not webhook_url:
        return False
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=8):
            return True
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def _active_bail_ad_listings(conn):
    rows = conn.execute(
        '''
        SELECT
            bail_ad_orders.id,
            bail_ad_orders.business_name,
            bail_ad_orders.phone,
            bail_ad_orders.email,
            bail_ad_orders.website_url,
            bail_ad_orders.county_targets,
            bail_ad_orders.package_id,
            bail_ad_orders.status,
            bail_ad_orders.simulator_logo_path,
            bail_ad_orders.simulator_target_url,
            bail_ad_creatives.headline,
            bail_ad_creatives.body_copy,
            bail_ad_creatives.cta_text,
            bail_ad_creatives.target_url,
            bail_ad_creatives.logo_path
        FROM bail_ad_orders
        LEFT JOIN bail_ad_creatives ON bail_ad_creatives.order_id = bail_ad_orders.id
        WHERE bail_ad_orders.status = 'active'
          AND (bail_ad_creatives.status = 'approved' OR bail_ad_creatives.status IS NULL)
        ORDER BY datetime(bail_ad_orders.paid_at) DESC, datetime(bail_ad_orders.created_at) DESC
        '''
    ).fetchall()

    listings = []
    for row in rows:
        county_list = _bail_ad_county_list(row['county_targets'])
        phone_value = (row['phone'] or '').strip()
        phone_token = _format_phone_for_tel(phone_value)
        listings.append({
            'id': row['id'],
            'business_name': row['business_name'],
            'phone': phone_value,
            'phone_href': f"tel:{phone_token}" if phone_token else '',
            'sms_href': f"sms:{phone_token}" if phone_token else '',
            'email': row['email'],
            'website_url': row['website_url'],
            'counties': county_list,
            'package_id': row['package_id'],
            'status': row['status'],
            'headline': row['headline'] or f"{row['business_name']} Bail Bonds",
            'body_copy': row['body_copy'] or 'Licensed local bail bond support available.',
            'cta_text': row['cta_text'] or 'Contact Now',
            'target_url': row['target_url'] or row['simulator_target_url'] or row['website_url'] or '',
            'logo_path': row['logo_path'] or row['simulator_logo_path'] or '',
        })
    return listings


def _bail_ad_package_supports_banner(package_id=''):
    return _normalize_bail_ad_package_id(package_id) in {
        'featured_bondsman_banner',
        'gold_bond_bundle',
        'state_power',
        'gold_bond',
    }


def _bail_ad_package_supports_sidebar(package_id=''):
    return _normalize_bail_ad_package_id(package_id) in {
        'emergency_call_sidebar',
        'gold_bond_bundle',
        'silver_link',
        'state_power',
        'gold_bond',
    }


def _bail_ad_package_supports_county(package_id=''):
    return _normalize_bail_ad_package_id(package_id) in {
        'exclusive_county_sponsorship',
        'gold_bond_bundle',
        'silver_link',
        'gold_bond',
        'state_power',
    }


def _bail_ad_clone_for_surface(listing, surface, county=''):
    if not listing:
        return None
    item = dict(listing)
    normalized_county = _normalize_bail_county(county)
    item['surface'] = surface
    item['tracking_county'] = normalized_county or (item.get('counties') or [''])[0]
    if surface == 'banner':
        item['surface_label'] = 'Featured Bondsman Banner'
        item['surface_note'] = 'Sponsored statewide placement'
        item['cta_text'] = item.get('cta_text') or 'Visit Sponsor'
    elif surface == 'county':
        item['surface_label'] = f"{normalized_county or 'County'} Sponsor"
        item['surface_note'] = 'Exclusive local sponsor'
        item['cta_text'] = item.get('cta_text') or 'Call Local Sponsor'
    else:
        item['surface_label'] = 'Emergency Call Sidebar'
        item['surface_note'] = 'Persistent sponsored placement'
        item['cta_text'] = item.get('cta_text') or 'Call Now'
    return item


def _bail_ad_public_placements(conn, county=''):
    listings = _active_bail_ad_listings(conn)
    normalized_county = _normalize_bail_county(county)

    def _pick(predicate):
        for listing in listings:
            if predicate(listing):
                return listing
        return None

    county_sponsor = None
    if normalized_county:
        county_sponsor = _pick(
            lambda item: (
                _bail_ad_package_supports_county(item.get('package_id'))
                and normalized_county in (item.get('counties') or [])
            )
        )

    banner = _pick(lambda item: _bail_ad_package_supports_banner(item.get('package_id')))
    sidebar = county_sponsor or _pick(lambda item: _bail_ad_package_supports_sidebar(item.get('package_id')))

    return {
        'banner': _bail_ad_clone_for_surface(banner, 'banner', county=normalized_county),
        'sidebar': _bail_ad_clone_for_surface(sidebar, 'county' if county_sponsor else 'sidebar', county=normalized_county),
        'county_sponsor': _bail_ad_clone_for_surface(county_sponsor, 'county', county=normalized_county),
    }


def _bail_county_sections(listings, selected_county=''):
    selected = _normalize_bail_county(selected_county)
    by_county = {}
    for listing in listings:
        counties = listing.get('counties') or ['Statewide']
        for county in counties:
            normalized_county = _normalize_bail_county(county) or county
            if selected and normalized_county != selected:
                continue
            by_county.setdefault(normalized_county, []).append(listing)
    return [{'county': county, 'listings': values} for county, values in sorted(by_county.items())]


def _bail_lead_routing_targets(listings, county):
    normalized_county = _normalize_bail_county(county)
    targets = []
    for listing in listings:
        target_counties = listing.get('counties') or []
        if target_counties and normalized_county and normalized_county not in target_counties:
            continue
        targets.append(listing)
    return targets[:3]


def _bail_advertiser_attribution_30d(conn, limit=120):
    calls_by_order = {
        int(row['order_id']): int(row['calls'] or 0)
        for row in conn.execute(
            '''
            SELECT order_id, COUNT(*) AS calls
            FROM bail_ad_events
            WHERE order_id IS NOT NULL
              AND event_type IN ('call', 'lead')
              AND created_at >= date('now', '-30 days')
            GROUP BY order_id
            '''
        ).fetchall()
        if row['order_id'] is not None
    }
    texts_by_order = {
        int(row['order_id']): int(row['texts'] or 0)
        for row in conn.execute(
            '''
            SELECT order_id, COUNT(*) AS texts
            FROM bail_ad_events
            WHERE order_id IS NOT NULL
              AND event_type = 'text'
              AND created_at >= date('now', '-30 days')
            GROUP BY order_id
            '''
        ).fetchall()
        if row['order_id'] is not None
    }

    routed_by_order = {}
    for row in conn.execute(
        '''
        SELECT routed_order_ids, status
        FROM bail_consumer_leads
        WHERE created_at >= date('now', '-30 days')
        '''
    ).fetchall():
        order_ids = []
        for token in (row['routed_order_ids'] or '').split(','):
            clean = token.strip()
            if not clean:
                continue
            try:
                value = int(clean)
            except ValueError:
                continue
            if value > 0:
                order_ids.append(value)
        for order_id in sorted(set(order_ids)):
            stats_bucket = routed_by_order.setdefault(order_id, {'routed': 0, 'qualified': 0, 'booked': 0})
            stats_bucket['routed'] += 1
            if (row['status'] or '').strip().lower() in {'qualified', 'booked'}:
                stats_bucket['qualified'] += 1
            if (row['status'] or '').strip().lower() == 'booked':
                stats_bucket['booked'] += 1

    pipeline_order_ids = set(calls_by_order.keys()) | set(texts_by_order.keys()) | set(routed_by_order.keys())
    order_lookup = {}
    if pipeline_order_ids:
        placeholders = ','.join('?' for _ in sorted(pipeline_order_ids))
        for row in conn.execute(
            f'''
            SELECT id, business_name, package_id, status, county_targets
            FROM bail_ad_orders
            WHERE id IN ({placeholders})
            ''',
            tuple(sorted(pipeline_order_ids)),
        ).fetchall():
            order_lookup[int(row['id'])] = dict(row)

    out = []
    for order_id in sorted(pipeline_order_ids):
        order_info = order_lookup.get(order_id) or {}
        routed = int((routed_by_order.get(order_id) or {}).get('routed') or 0)
        qualified = int((routed_by_order.get(order_id) or {}).get('qualified') or 0)
        booked = int((routed_by_order.get(order_id) or {}).get('booked') or 0)
        calls = int(calls_by_order.get(order_id, 0) or 0)
        texts = int(texts_by_order.get(order_id, 0) or 0)
        out.append({
            'order_id': order_id,
            'business_name': order_info.get('business_name') or f'Order #{order_id}',
            'package_id': order_info.get('package_id') or '',
            'status': order_info.get('status') or '',
            'county_targets': order_info.get('county_targets') or '',
            'calls': calls,
            'texts': texts,
            'routed_leads': routed,
            'qualified_leads': qualified,
            'booked_bonds': booked,
            'qualified_rate_pct': (qualified / routed * 100.0) if routed else 0.0,
            'booked_rate_pct': (booked / qualified * 100.0) if qualified else 0.0,
        })
    out.sort(
        key=lambda item: (
            item['booked_bonds'],
            item['qualified_leads'],
            item['routed_leads'],
            item['calls'],
            item['texts'],
        ),
        reverse=True,
    )
    return out[:max(1, int(limit or 120))]


def _humanize_bail_ad_status(status):
    mapping = {
        'checkout_pending': 'Checkout Pending',
        'active': 'Active',
        'active_pending_creative_review': 'Active Pending Creative Review',
        'payment_failed': 'Payment Failed',
        'canceled': 'Canceled',
        'paused': 'Paused',
        'pending': 'Pending',
        'approved': 'Approved',
        'rejected': 'Rejected',
    }
    normalized = (status or '').strip().lower()
    return mapping.get(normalized, normalized.replace('_', ' ').title() or 'Unknown')


def _format_bail_ad_datetime(value, include_time=False, fallback='Pending'):
    parsed = _parse_sqlite_timestamp(value)
    if not parsed:
        return fallback
    fmt = '%b %d, %Y %I:%M %p UTC' if include_time else '%b %d, %Y'
    return parsed.strftime(fmt).replace(' 0', ' ')


def _bail_ad_control_panel_context(conn, token, session_id=''):
    _ensure_bail_ad_simulator_order_columns(conn)
    safe_token = (token or '').strip()[:128]
    if not safe_token:
        return None

    order_row = conn.execute(
        '''
        SELECT
            id,
            business_name,
            contact_name,
            email,
            phone,
            website_url,
            license_number,
            county_targets,
            package_id,
            billing_cycle,
            amount_cents,
            currency,
            status,
            add_on_ids,
            onboarding_token,
            notes,
            simulator_logo_path,
            simulator_target_url,
            simulator_share_url,
            simulator_view,
            paid_at,
            created_at,
            updated_at,
            provider_session_id,
            provider_subscription_id
        FROM bail_ad_orders
        WHERE onboarding_token = ?
        LIMIT 1
        ''',
        (safe_token,),
    ).fetchone()
    if not order_row:
        return None

    package_lookup = _bail_ad_package_lookup()
    addon_lookup = _bail_ad_addon_lookup()
    order = dict(order_row)
    package = package_lookup.get(order.get('package_id') or '') or {}
    order['package_name'] = (package.get('name') if package else '') or (order.get('package_id') or '').replace('_', ' ').title()
    order['status_label'] = _humanize_bail_ad_status(order.get('status'))
    order['billing_label'] = ((order.get('billing_cycle') or 'monthly').replace('_', ' ')).title()
    order['amount_display'] = f"${(int(order.get('amount_cents') or 0) / 100):,.2f}"
    order['currency_display'] = (order.get('currency') or 'usd').upper()
    order['county_list'] = _bail_ad_county_list(order.get('county_targets') or '')
    order['county_count'] = len(order['county_list'])
    order['package_badge'] = package.get('badge') or 'Advertiser Account'
    order['package_features'] = package.get('features') or []
    order['county_slots'] = int(package.get('county_slots') or 0)
    order['add_on_labels'] = [
        addon_lookup[addon_id]['name']
        for addon_id in _parse_addon_ids((order.get('add_on_ids') or '').split(','))
        if addon_id in addon_lookup
    ]
    order['created_label'] = _format_bail_ad_datetime(order.get('created_at'), include_time=True)
    order['updated_label'] = _format_bail_ad_datetime(order.get('updated_at'), include_time=True)
    order['paid_label'] = _format_bail_ad_datetime(
        order.get('paid_at') or order.get('created_at'),
        include_time=True,
        fallback='Pending',
    )

    paid_at = _parse_sqlite_timestamp(order.get('paid_at') or order.get('created_at'))
    cycle = (order.get('billing_cycle') or 'monthly').strip().lower()
    renewal_days = 365 if cycle == 'annual' else 30
    next_renewal_dt = paid_at + timedelta(days=renewal_days) if paid_at else None
    days_to_renewal = None
    if next_renewal_dt:
        days_to_renewal = int((next_renewal_dt - datetime.utcnow()).total_seconds() // 86400)
    order['next_renewal'] = next_renewal_dt.strftime('%b %d, %Y') if next_renewal_dt else 'Pending'
    order['days_to_renewal'] = max(days_to_renewal, 0) if days_to_renewal is not None else None

    creative_row = conn.execute(
        '''
        SELECT
            id,
            headline,
            body_copy,
            cta_text,
            target_url,
            logo_path,
            status,
            review_notes,
            created_at,
            updated_at
        FROM bail_ad_creatives
        WHERE order_id = ?
        LIMIT 1
        ''',
        (order['id'],),
    ).fetchone()
    creative = dict(creative_row) if creative_row else None
    if creative:
        creative['status_label'] = _humanize_bail_ad_status(creative.get('status'))
        creative['updated_label'] = _format_bail_ad_datetime(creative.get('updated_at'), include_time=True)
        creative['created_label'] = _format_bail_ad_datetime(creative.get('created_at'), include_time=True)
    simulator_preview = _bail_ad_simulator_preview(order)

    slot_rows = conn.execute(
        '''
        SELECT county, slot_type, status, starts_at, ends_at, updated_at
        FROM bail_ad_slots
        WHERE order_id = ?
        ORDER BY county ASC, slot_type ASC
        ''',
        (order['id'],),
    ).fetchall()
    slots = []
    for row in slot_rows:
        slot = dict(row)
        slot['status_label'] = _humanize_bail_ad_status(slot.get('status'))
        slot['starts_label'] = _format_bail_ad_datetime(slot.get('starts_at'), fallback='Pending')
        slot['ends_label'] = _format_bail_ad_datetime(slot.get('ends_at'), fallback='Open')
        slots.append(slot)

    perf_row = conn.execute(
        '''
        SELECT
            COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
            COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
            COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads,
            COALESCE(SUM(CASE WHEN event_type = 'call' THEN 1 ELSE 0 END), 0) AS calls,
            COALESCE(SUM(CASE WHEN event_type = 'text' THEN 1 ELSE 0 END), 0) AS texts
        FROM bail_ad_events
        WHERE order_id = ?
          AND created_at >= date('now', '-30 days')
        ''',
        (order['id'],),
    ).fetchone()
    performance_30d = dict(perf_row) if perf_row else {
        'impressions': 0,
        'clicks': 0,
        'leads': 0,
        'calls': 0,
        'texts': 0,
    }
    impressions = float(performance_30d.get('impressions') or 0)
    clicks = float(performance_30d.get('clicks') or 0)
    leads = float(performance_30d.get('leads') or 0)
    calls = float(performance_30d.get('calls') or 0)
    texts = float(performance_30d.get('texts') or 0)
    performance_30d['ctr_pct'] = (clicks / impressions * 100.0) if impressions else 0.0
    performance_30d['lead_rate_pct'] = (leads / clicks * 100.0) if clicks else 0.0
    performance_30d['contact_actions'] = int(calls + texts + leads)

    county_rows = conn.execute(
        '''
        SELECT
            COALESCE(NULLIF(county, ''), 'Statewide') AS county,
            COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
            COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
            COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads,
            COALESCE(SUM(CASE WHEN event_type = 'call' THEN 1 ELSE 0 END), 0) AS calls,
            COALESCE(SUM(CASE WHEN event_type = 'text' THEN 1 ELSE 0 END), 0) AS texts
        FROM bail_ad_events
        WHERE order_id = ?
          AND created_at >= date('now', '-30 days')
        GROUP BY COALESCE(NULLIF(county, ''), 'Statewide')
        ORDER BY clicks DESC, impressions DESC, county ASC
        ''',
        (order['id'],),
    ).fetchall()
    county_performance_30d = []
    for row in county_rows:
        county_metrics = dict(row)
        county_impressions = float(county_metrics.get('impressions') or 0)
        county_clicks = float(county_metrics.get('clicks') or 0)
        county_metrics['ctr_pct'] = (county_clicks / county_impressions * 100.0) if county_impressions else 0.0
        county_performance_30d.append(county_metrics)

    attribution = {
        'calls': 0,
        'texts': 0,
        'routed_leads': 0,
        'qualified_leads': 0,
        'booked_bonds': 0,
        'qualified_rate_pct': 0.0,
        'booked_rate_pct': 0.0,
    }
    for item in _bail_advertiser_attribution_30d(conn, limit=10000):
        if int(item.get('order_id') or 0) == int(order['id']):
            attribution.update(item)
            break

    benchmark_row = conn.execute(
        '''
        SELECT
            COUNT(DISTINCT order_id) AS advertiser_count,
            COALESCE(SUM(CASE WHEN event_type = 'impression' THEN 1 ELSE 0 END), 0) AS impressions,
            COALESCE(SUM(CASE WHEN event_type = 'click' THEN 1 ELSE 0 END), 0) AS clicks,
            COALESCE(SUM(CASE WHEN event_type = 'lead' THEN 1 ELSE 0 END), 0) AS leads,
            COALESCE(SUM(CASE WHEN event_type = 'call' THEN 1 ELSE 0 END), 0) AS calls,
            COALESCE(SUM(CASE WHEN event_type = 'text' THEN 1 ELSE 0 END), 0) AS texts
        FROM bail_ad_events
        WHERE order_id IS NOT NULL
          AND created_at >= date('now', '-30 days')
        '''
    ).fetchone()
    benchmarks = dict(benchmark_row) if benchmark_row else {
        'advertiser_count': 0,
        'impressions': 0,
        'clicks': 0,
        'leads': 0,
        'calls': 0,
        'texts': 0,
    }
    advertiser_count = max(1, int(benchmarks.get('advertiser_count') or 0))
    benchmarks['avg_clicks'] = float(benchmarks.get('clicks') or 0) / advertiser_count
    benchmarks['avg_impressions'] = float(benchmarks.get('impressions') or 0) / advertiser_count
    benchmarks['avg_contact_actions'] = (
        float(benchmarks.get('calls') or 0)
        + float(benchmarks.get('texts') or 0)
        + float(benchmarks.get('leads') or 0)
    ) / advertiser_count
    benchmarks['click_index_pct'] = (
        (float(performance_30d.get('clicks') or 0) / benchmarks['avg_clicks']) * 100.0
        if benchmarks['avg_clicks'] else 0.0
    )
    benchmarks['contact_index_pct'] = (
        (float(performance_30d.get('contact_actions') or 0) / benchmarks['avg_contact_actions']) * 100.0
        if benchmarks['avg_contact_actions'] else 0.0
    )

    top_county = county_performance_30d[0] if county_performance_30d else None
    top_county_share = (
        (float(top_county.get('clicks') or 0) / clicks * 100.0)
        if top_county and clicks else 0.0
    )

    booking_signal_score = min(
        100,
        int(
            float(performance_30d.get('clicks') or 0) * 2
            + float(attribution.get('calls') or 0) * 8
            + float(attribution.get('texts') or 0) * 6
            + float(attribution.get('qualified_leads') or 0) * 18
            + float(attribution.get('booked_bonds') or 0) * 28
        ),
    )
    if booking_signal_score >= 80:
        signal_label = 'Booked'
    elif booking_signal_score >= 55:
        signal_label = 'High Intent'
    elif booking_signal_score >= 30:
        signal_label = 'Active'
    elif booking_signal_score >= 10:
        signal_label = 'Warming'
    else:
        signal_label = 'Cold Start'

    launch_checklist = [
        {
            'title': 'Payment Confirmed',
            'detail': f"{order['amount_display']} {order['currency_display']} recorded on {order['paid_label']}.",
            'complete': bool(order.get('provider_session_id') or session_id),
        },
        {
            'title': 'Creative Submitted',
            'detail': (
                f"Latest submission updated {creative['updated_label']}."
                if creative else
                'Headline, CTA, landing page, and logo still need to be submitted.'
            ),
            'complete': bool(creative),
        },
        {
            'title': 'Compliance Review',
            'detail': (
                'Approved and ready for live placement.'
                if creative and (creative.get('status') or '').lower() == 'approved' else
                'Waiting on review or revisions before full rollout.'
            ),
            'complete': bool(creative and (creative.get('status') or '').lower() == 'approved'),
        },
        {
            'title': 'Tracking Live',
            'detail': (
                f"{int(performance_30d.get('impressions') or 0)} tracked impressions in the last 30 days."
                if performance_30d.get('impressions') else
                'No live delivery recorded yet.'
            ),
            'complete': bool(performance_30d.get('impressions')),
        },
    ]

    priority_actions = []
    if not creative:
        priority_actions.append({
            'title': 'Submit creative assets',
            'detail': 'The account is paid, but ad copy and destination details still need to be loaded before review can finish.',
            'href': url_for('payments.advertise_bail_onboarding', token=safe_token),
            'label': 'Open Onboarding',
        })
    elif (creative.get('status') or '').lower() == 'pending':
        priority_actions.append({
            'title': 'Review queue is in progress',
            'detail': 'Your latest creative is pending moderation. Keep the control panel link handy for notes or revisions.',
            'href': url_for('payments.advertise_bail_onboarding', token=safe_token),
            'label': 'Review Submission',
        })
    elif (creative.get('status') or '').lower() == 'rejected':
        priority_actions.append({
            'title': 'Revise creative now',
            'detail': 'The ad is blocked on compliance notes. Update the headline, copy, or destination to get back into rotation.',
            'href': url_for('payments.advertise_bail_onboarding', token=safe_token),
            'label': 'Fix Creative',
        })

    if performance_30d.get('clicks') and not attribution.get('routed_leads'):
        priority_actions.append({
            'title': 'Tighten your landing path',
            'detail': 'Traffic is clicking but not routing into tracked leads. A direct call page or prefilled SMS path should convert harder.',
            'href': url_for('payments.advertise_bail_onboarding', token=safe_token),
            'label': 'Update CTA',
        })

    if days_to_renewal is not None and days_to_renewal <= 14:
        priority_actions.append({
            'title': 'Renewal window is close',
            'detail': f"Next billing cycle lands in {max(days_to_renewal, 0)} day{'s' if days_to_renewal != 1 else ''}. Review ROI now before the next charge.",
            'href': url_for('payments.advertise_bail_control_panel', token=safe_token),
            'label': 'Check ROI',
        })

    if not priority_actions:
        priority_actions.append({
            'title': 'Account is in a holding pattern',
            'detail': 'No urgent blockers are showing. Use the county radar and booking score below to decide whether to expand coverage.',
            'href': url_for('payments.advertise_bail_control_panel', token=safe_token),
            'label': 'Review Signals',
        })

    if top_county:
        county_radar_body = (
            f"{top_county['county']} is driving {int(round(top_county_share))}% of your 30-day clicks."
            if clicks else
            f"{top_county['county']} is the first county showing live ad delivery."
        )
        county_radar_value = top_county.get('county') or 'Statewide'
    else:
        county_radar_body = 'No county-level delivery is recorded yet. Once impressions start, this radar will isolate the hottest local pocket.'
        county_radar_value = 'Standby'

    if order['county_slots'] > 0 and order['county_list']:
        exclusivity_value = f"{len(order['county_list'])} county lane{'s' if len(order['county_list']) != 1 else ''}"
        exclusivity_body = f"Current county footprint: {', '.join(order['county_list'])}. This package is built to defend local share of voice, not just generate generic clicks."
        exclusivity_title = 'Exclusivity Watch'
    else:
        exclusivity_value = f"{int(performance_30d.get('contact_actions') or 0)} response actions"
        exclusivity_body = 'This placement leans on immediate tap-to-call behavior. Calls, texts, and form leads are grouped here to show buyer urgency, not just page traffic.'
        exclusivity_title = 'Response Pressure'

    if creative and (creative.get('status') or '').lower() == 'approved':
        creative_value = 'Approved'
        creative_body = 'Your ad creative has cleared review and is eligible for full placement rotation.'
    elif creative and (creative.get('status') or '').lower() == 'pending':
        creative_value = 'In Review'
        creative_body = 'Creative is submitted and waiting on moderation. Keep the CTA and landing path stable until review finishes.'
    elif creative and (creative.get('status') or '').lower() == 'rejected':
        creative_value = 'Needs Revision'
        creative_body = 'The current creative is blocked on review notes. Update it before traffic scaling makes sense.'
    else:
        creative_value = 'Not Started'
        creative_body = 'No creative package is attached yet. Payment is complete, but launch is still blocked on onboarding.'

    signature_features = [
        {
            'title': 'County Saturation Radar',
            'value': county_radar_value,
            'body': county_radar_body,
        },
        {
            'title': 'Booking Signal Score',
            'value': f'{booking_signal_score}/100',
            'body': f"{signal_label} demand signal based on clicks, calls, texts, qualified leads, and booked bonds in the last 30 days.",
        },
        {
            'title': exclusivity_title,
            'value': exclusivity_value,
            'body': exclusivity_body,
        },
        {
            'title': 'Creative Approval Pulse',
            'value': creative_value,
            'body': creative_body,
        },
    ]

    return {
        'order': order,
        'package': package,
        'creative': creative,
        'slots': slots,
        'performance_30d': performance_30d,
        'county_performance_30d': county_performance_30d,
        'attribution': attribution,
        'benchmarks': benchmarks,
        'signature_features': signature_features,
        'priority_actions': priority_actions[:3],
        'launch_checklist': launch_checklist,
        'booking_signal_score': booking_signal_score,
        'signal_label': signal_label,
        'simulator_preview': simulator_preview,
    }


_BAIL_OUTREACH_STATUSES = {
    'new',
    'queued',
    'contacted',
    'replied',
    'meeting_scheduled',
    'closed_won',
    'closed_lost',
    'do_not_contact',
}


def _crm_phone_token(raw_phone):
    digits = ''.join(ch for ch in (raw_phone or '') if ch.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def _bail_agency_dedupe_key(agency_name, email, phone):
    agency_token = _slugify_key(agency_name)[:80]
    email_token = (email or '').strip().lower()[:160]
    phone_token = _crm_phone_token(phone)
    if not agency_token:
        return ''
    return f'{agency_token}|{email_token}|{phone_token}'


def _ensure_bail_agency_outreach_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_agency_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL UNIQUE,
            agency_name TEXT NOT NULL,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            counties TEXT,
            source TEXT,
            outreach_status TEXT NOT NULL DEFAULT 'new',
            last_contacted_at TEXT,
            next_follow_up_at TEXT,
            owner TEXT,
            email_subject_template TEXT,
            email_body_template TEXT,
            call_script_template TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_status ON bail_agency_outreach(outreach_status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_followup ON bail_agency_outreach(next_follow_up_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_outreach_name ON bail_agency_outreach(agency_name)')
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS bail_agency_email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agency_id INTEGER,
            agency_name TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            email_kind TEXT NOT NULL,
            subject TEXT,
            body_preview TEXT,
            sent_by TEXT,
            send_status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (agency_id) REFERENCES bail_agency_outreach(id) ON DELETE SET NULL
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_created ON bail_agency_email_logs(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_agency ON bail_agency_email_logs(agency_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_status ON bail_agency_email_logs(send_status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bail_agency_email_logs_kind ON bail_agency_email_logs(email_kind)')


def _log_bail_agency_email(
    conn,
    agency_id,
    agency_name,
    recipient_email,
    email_kind,
    subject,
    body_preview,
    sent_by,
    send_status,
    error_message='',
):
    conn.execute(
        '''
        INSERT INTO bail_agency_email_logs (
            agency_id, agency_name, recipient_email, email_kind, subject, body_preview, sent_by, send_status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            int(agency_id) if agency_id else None,
            (agency_name or '').strip()[:160],
            (recipient_email or '').strip().lower()[:160],
            (email_kind or '').strip().lower()[:32],
            (subject or '').strip()[:500],
            (body_preview or '').strip()[:1200],
            (sent_by or '').strip()[:120],
            (send_status or '').strip().lower()[:32],
            (error_message or '').strip()[:500],
        ),
    )


def _seed_bail_agency_outreach(conn):
    rows = conn.execute(
        '''
        SELECT business_name AS agency_name, contact_name, email, phone, counties_served AS counties, source
        FROM bail_ad_inquiries
        WHERE business_name IS NOT NULL AND business_name != ''
        UNION ALL
        SELECT business_name AS agency_name, contact_name, email, phone, county_targets AS counties, source
        FROM bail_ad_orders
        WHERE business_name IS NOT NULL AND business_name != ''
        '''
    ).fetchall()

    for row in rows:
        agency_name = (row['agency_name'] or '').strip()[:160]
        if not agency_name:
            continue
        contact_name = (row['contact_name'] or '').strip()[:120]
        email = (row['email'] or '').strip().lower()[:160]
        phone = (row['phone'] or '').strip()[:40]
        counties = (row['counties'] or '').strip()[:500]
        source = (row['source'] or '').strip()[:80]
        dedupe_key = _bail_agency_dedupe_key(agency_name, email, phone)
        if not dedupe_key:
            continue

        conn.execute(
            '''
            INSERT INTO bail_agency_outreach (
                dedupe_key, agency_name, contact_name, email, phone, counties, source, outreach_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT(dedupe_key) DO UPDATE SET
                contact_name = CASE
                    WHEN (bail_agency_outreach.contact_name IS NULL OR bail_agency_outreach.contact_name = '')
                         AND excluded.contact_name != '' THEN excluded.contact_name
                    ELSE bail_agency_outreach.contact_name
                END,
                email = CASE
                    WHEN (bail_agency_outreach.email IS NULL OR bail_agency_outreach.email = '')
                         AND excluded.email != '' THEN excluded.email
                    ELSE bail_agency_outreach.email
                END,
                phone = CASE
                    WHEN (bail_agency_outreach.phone IS NULL OR bail_agency_outreach.phone = '')
                         AND excluded.phone != '' THEN excluded.phone
                    ELSE bail_agency_outreach.phone
                END,
                counties = CASE
                    WHEN (bail_agency_outreach.counties IS NULL OR bail_agency_outreach.counties = '')
                         AND excluded.counties != '' THEN excluded.counties
                    ELSE bail_agency_outreach.counties
                END,
                source = CASE
                    WHEN (bail_agency_outreach.source IS NULL OR bail_agency_outreach.source = '')
                         AND excluded.source != '' THEN excluded.source
                    ELSE bail_agency_outreach.source
                END,
                updated_at = datetime('now')
            ''',
            (dedupe_key, agency_name, contact_name, email, phone, counties, source),
        )


def _bail_agency_default_templates(agency):
    agency_name = (agency.get('agency_name') or '').strip() or 'Your Agency'
    counties = (agency.get('counties') or '').strip() or 'your target counties'
    subject = f'Quick lead growth plan for {agency_name}'
    body = (
        f"Hi {{contact_name_or_team}},\n\n"
        f"I run growth partnerships for Montana Blotter. We already have high-intent county traffic around {counties}, "
        f"and I wanted to share a simple 30-day plan for {agency_name}.\n\n"
        f"Plan focus:\n"
        f"- More qualified inbound calls from your target counties\n"
        f"- Better speed-to-lead using call/text routing\n"
        f"- Clear weekly reporting on qualified leads and booked bonds\n\n"
        f"If useful, I can send a 10-minute breakdown specific to your coverage area.\n\n"
        f"Thanks,\n"
        f"{{sender_name}}"
    )
    script = (
        f"Hi {{contact_name_or_team}}, this is {{sender_name}} from Montana Blotter.\n"
        f"We help bail bond agencies increase qualified county-level calls.\n"
        f"Quick question: are you currently looking to improve lead quality, volume, or both?\n\n"
        f"If both, I can share a 30-day plan for {agency_name} in {counties}.\n"
        f"It takes 10 minutes to review."
    )
    return {
        'subject': subject,
        'email_body': body,
        'call_script': script,
    }


def _render_bail_template(template_text, context):
    rendered = template_text or ''
    for key, value in context.items():
        rendered = rendered.replace('{{' + key + '}}', value or '')
    return rendered


def _bail_agency_rendered_templates(agency):
    defaults = _bail_agency_default_templates(agency)
    subject_template = (agency.get('email_subject_template') or '').strip() or defaults['subject']
    email_template = (agency.get('email_body_template') or '').strip() or defaults['email_body']
    script_template = (agency.get('call_script_template') or '').strip() or defaults['call_script']
    context = {
        'agency_name': (agency.get('agency_name') or '').strip(),
        'contact_name': (agency.get('contact_name') or '').strip(),
        'contact_name_or_team': (agency.get('contact_name') or '').strip() or 'team',
        'counties': (agency.get('counties') or '').strip() or 'your target counties',
        'sender_name': 'Montana Blotter Team',
        'today_iso': datetime.utcnow().strftime('%Y-%m-%d'),
    }
    return {
        'subject_template': subject_template,
        'email_template': email_template,
        'script_template': script_template,
        'subject_preview': _render_bail_template(subject_template, context),
        'email_preview': _render_bail_template(email_template, context),
        'script_preview': _render_bail_template(script_template, context),
    }


def _default_bail_test_email():
    username_value = (getattr(current_user, 'username', '') or '').strip().lower()
    if username_value and '@' in username_value:
        return username_value
    notify_recipients = _bail_lead_notify_recipients()
    if notify_recipients:
        return notify_recipients[0]
    smtp_user = (getattr(config, 'SMTP_USER', '') or '').strip().lower()
    if smtp_user and '@' in smtp_user:
        return smtp_user
    return ''


def _exclusive_county_tier_monthly_cents(county_name=''):
    county_key = (county_name or '').strip().lower()
    premium_counties = {'yellowstone', 'missoula', 'gallatin'}
    metro_counties = {'cascade', 'flathead', 'lewis and clark', 'lewis & clark'}
    if county_key in premium_counties:
        return 35000
    if county_key in metro_counties:
        return 25000
    return 15000


def _bail_ad_price_cents(package_id, billing_cycle, county_targets=None):
    package = _bail_ad_package_lookup().get(_normalize_bail_ad_package_id(package_id))
    if not package:
        return None

    monthly_cents = int(package.get('price_monthly_cents') or 0)
    if package.get('pricing_model') == 'county_tiered':
        primary_county = ''
        if isinstance(county_targets, (list, tuple)) and county_targets:
            primary_county = county_targets[0]
        elif isinstance(county_targets, str):
            parsed = _parse_county_targets(county_targets)
            primary_county = parsed[0] if parsed else ''
        monthly_cents = _exclusive_county_tier_monthly_cents(primary_county)

    if billing_cycle == 'annual':
        annual_cents = int(package.get('price_annual_cents') or 0)
        if package.get('pricing_model') == 'county_tiered':
            annual_cents = monthly_cents * 12
        return annual_cents or monthly_cents * 12
    return monthly_cents


def _bail_ad_addon_total_cents(addon_ids, billing_cycle):
    lookup = _bail_ad_addon_lookup()
    total = 0
    for addon_id in addon_ids or []:
        addon = lookup.get(addon_id)
        if not addon:
            continue
        if billing_cycle == 'annual':
            total += int(addon.get('price_annual_cents') or 0) or int(addon['price_monthly_cents']) * 10
        else:
            total += int(addon['price_monthly_cents'])
    return total


def _bail_ad_county_list(value):
    raw = (value or '').replace('\n', ',').replace(';', ',')
    out = []
    seen = set()
    for part in raw.split(','):
        token = part.strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token[:64])
    return out


def _upsert_bail_ad_slot_assignments(conn, order_id, county_targets, slot_count):
    if not order_id or slot_count <= 0:
        return 0
    targets = _parse_county_targets(county_targets)
    if not targets:
        return 0

    created = 0
    for county_name in targets[:slot_count]:
        existing = conn.execute(
            'SELECT id FROM bail_ad_slots WHERE order_id = ? AND county = ? LIMIT 1',
            (order_id, county_name),
        ).fetchone()
        if existing:
            conn.execute(
                '''
                UPDATE bail_ad_slots
                SET status = 'active', starts_at = COALESCE(starts_at, datetime('now')), updated_at = datetime('now')
                WHERE id = ?
                ''',
                (existing['id'],),
            )
            continue
        conn.execute(
            '''
            INSERT INTO bail_ad_slots (order_id, county, slot_type, status, starts_at)
            VALUES (?, ?, 'county_feature', 'active', datetime('now'))
            ''',
            (order_id, county_name),
        )
        created += 1
    return created


def _apply_stripe_bail_ad_event(conn, event):
    _ensure_bail_ad_simulator_order_columns(conn)
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}
    if (metadata.get('flow') or '').strip() != 'bail_ad':
        return

    if event_type not in {'checkout.session.completed', 'checkout.session.async_payment_succeeded', 'checkout.session.expired', 'checkout.session.async_payment_failed'}:
        return

    session_id = (data_object.get('id') or '').strip()
    if not session_id:
        return

    package_id = _normalize_bail_ad_package_id(metadata.get('package_id'))
    billing_cycle = (metadata.get('billing_cycle') or 'monthly').strip().lower()
    if billing_cycle not in {'monthly', 'annual'}:
        billing_cycle = 'monthly'
    package = _bail_ad_package_lookup().get(package_id)
    if not package:
        return

    mapped_status = {
        'checkout.session.completed': 'active',
        'checkout.session.async_payment_succeeded': 'active',
        'checkout.session.expired': 'canceled',
        'checkout.session.async_payment_failed': 'payment_failed',
    }[event_type]

    raw_county_targets = metadata.get('county_targets') or ''
    if package.get('all_counties') and (raw_county_targets or '').strip().lower() in {'all', 'all_counties', 'statewide'}:
        county_target_values = sorted({county['name'] for county in COUNTY_DATA.values()})
    else:
        county_target_values = _parse_county_targets(raw_county_targets)
    amount_cents = int(data_object.get('amount_total') or 0)
    if amount_cents <= 0:
        amount_cents = _bail_ad_price_cents(package_id, billing_cycle, county_target_values) or 0
    currency = (data_object.get('currency') or 'usd').lower()
    business_name = (metadata.get('business_name') or '').strip()[:120]
    contact_name = (metadata.get('contact_name') or '').strip()[:120]
    email = (metadata.get('email') or '').strip().lower()[:160]
    phone = (metadata.get('phone') or '').strip()[:40]
    website_url = (metadata.get('website_url') or '').strip()[:300]
    license_number = (metadata.get('license_number') or '').strip()[:80]
    county_targets = ', '.join(county_target_values)
    source = (metadata.get('source') or 'bail_ad_checkout').strip()[:80]
    add_on_ids = ','.join(_parse_addon_ids((metadata.get('add_on_ids') or '').split(',')))
    onboarding_token = (metadata.get('onboarding_token') or '').strip()[:64]
    simulator_logo_path = _safe_bail_ad_simulator_image_url(metadata.get('simulator_logo_path') or '')
    simulator_target_url = (metadata.get('simulator_target_url') or '').strip()[:300]
    simulator_share_url = (metadata.get('simulator_share_url') or '').strip()[:500]
    simulator_view = (metadata.get('simulator_view') or '').strip().lower()[:24]
    if simulator_view not in {'banner', 'sidebar'}:
        simulator_view = ''
    provider_subscription_id = data_object.get('subscription')
    provider_customer_id = data_object.get('customer')

    existing = conn.execute(
        '''
        SELECT id, onboarding_token
        FROM bail_ad_orders
        WHERE provider_session_id = ?
        LIMIT 1
        ''',
        (session_id,),
    ).fetchone()
    if existing and not onboarding_token:
        onboarding_token = existing['onboarding_token'] or ''
    if not onboarding_token:
        onboarding_token = secrets.token_urlsafe(24)

    conn.execute(
        '''
        INSERT INTO bail_ad_orders (
            business_name, contact_name, email, phone, website_url, license_number,
            county_targets, package_id, billing_cycle, amount_cents, currency, source,
            add_on_ids, status, provider, provider_session_id, provider_subscription_id, provider_customer_id,
            onboarding_token, paid_at, simulator_logo_path, simulator_target_url, simulator_share_url, simulator_view
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stripe', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider_session_id) DO UPDATE SET
            business_name = excluded.business_name,
            contact_name = excluded.contact_name,
            email = excluded.email,
            phone = excluded.phone,
            website_url = excluded.website_url,
            license_number = excluded.license_number,
            county_targets = excluded.county_targets,
            package_id = excluded.package_id,
            billing_cycle = excluded.billing_cycle,
            amount_cents = CASE WHEN excluded.amount_cents > 0 THEN excluded.amount_cents ELSE bail_ad_orders.amount_cents END,
            currency = excluded.currency,
            source = excluded.source,
            add_on_ids = excluded.add_on_ids,
            status = excluded.status,
            provider_subscription_id = COALESCE(excluded.provider_subscription_id, bail_ad_orders.provider_subscription_id),
            provider_customer_id = COALESCE(excluded.provider_customer_id, bail_ad_orders.provider_customer_id),
            onboarding_token = CASE WHEN excluded.onboarding_token != '' THEN excluded.onboarding_token ELSE bail_ad_orders.onboarding_token END,
            paid_at = CASE WHEN excluded.paid_at IS NOT NULL THEN excluded.paid_at ELSE bail_ad_orders.paid_at END,
            simulator_logo_path = CASE WHEN excluded.simulator_logo_path != '' THEN excluded.simulator_logo_path ELSE bail_ad_orders.simulator_logo_path END,
            simulator_target_url = CASE WHEN excluded.simulator_target_url != '' THEN excluded.simulator_target_url ELSE bail_ad_orders.simulator_target_url END,
            simulator_share_url = CASE WHEN excluded.simulator_share_url != '' THEN excluded.simulator_share_url ELSE bail_ad_orders.simulator_share_url END,
            simulator_view = CASE WHEN excluded.simulator_view != '' THEN excluded.simulator_view ELSE bail_ad_orders.simulator_view END,
            updated_at = datetime('now')
        ''',
        (
            business_name,
            contact_name,
            email,
            phone,
            website_url,
            license_number,
            county_targets,
            package_id,
            billing_cycle,
            amount_cents,
            currency,
            source,
            add_on_ids,
            mapped_status,
            session_id,
            provider_subscription_id,
            provider_customer_id,
            onboarding_token,
            datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if mapped_status == 'active' else None,
            simulator_logo_path,
            simulator_target_url,
            simulator_share_url,
            simulator_view,
        ),
    )

    order_row = conn.execute(
        'SELECT id FROM bail_ad_orders WHERE provider_session_id = ? LIMIT 1',
        (session_id,),
    ).fetchone()
    if mapped_status == 'active' and order_row:
        _upsert_bail_ad_slot_assignments(
            conn,
            order_row['id'],
            county_targets,
            int(package.get('county_slots') or 0),
        )


def _donation_launch_snapshot():
    snapshot = {
        'schema_ready': True,
        'donations_enabled': _donations_enabled(),
        'stripe_checkout_ready': _stripe_ready_for_checkout(),
        'stripe_webhook_ready': _stripe_ready_for_webhooks(),
        'webhook_events_24h': 0,
        'webhook_errors_24h': 0,
        'stale_webhook_events_10m': 0,
        'last_webhook_received_at': None,
        'last_webhook_processed_at': None,
        'launch_ready': False,
    }

    conn = get_db()
    try:
        conn.execute('SELECT 1 FROM donations LIMIT 1').fetchone()
        conn.execute('SELECT 1 FROM payment_webhook_events LIMIT 1').fetchone()

        row = conn.execute(
            '''
            SELECT
                COUNT(*) AS webhook_events_24h,
                COALESCE(SUM(CASE WHEN error IS NOT NULL AND error != '' THEN 1 ELSE 0 END), 0) AS webhook_errors_24h,
                COALESCE(SUM(CASE WHEN processed = 0 AND created_at <= datetime('now', '-10 minutes') THEN 1 ELSE 0 END), 0) AS stale_webhook_events_10m,
                MAX(created_at) AS last_webhook_received_at,
                MAX(CASE WHEN processed = 1 AND (error IS NULL OR error = '') THEN processed_at END) AS last_webhook_processed_at
            FROM payment_webhook_events
            WHERE created_at >= datetime('now', '-24 hours')
            '''
        ).fetchone()
        if row:
            snapshot.update(dict(row))
    except sqlite3.OperationalError:
        snapshot['schema_ready'] = False
    finally:
        conn.close()

    snapshot['launch_ready'] = bool(
        snapshot['schema_ready']
        and snapshot['donations_enabled']
        and snapshot['stripe_checkout_ready']
        and snapshot['stripe_webhook_ready']
        and int(snapshot['stale_webhook_events_10m'] or 0) == 0
    )
    return snapshot


def _apply_stripe_event(conn, event, event_source='/webhooks/stripe', event_ip_hash='', event_referrer=''):
    event_type = (event.get('type') or '').strip()
    data_object = (event.get('data') or {}).get('object') or {}
    metadata = data_object.get('metadata') or {}
    if (metadata.get('flow') or '').strip() == 'bail_ad':
        return
    if not event_type:
        return

    if event_type in {'checkout.session.completed', 'checkout.session.async_payment_succeeded', 'checkout.session.expired', 'checkout.session.async_payment_failed'}:
        session_id = (data_object.get('id') or '').strip()
        if session_id:
            stripe_mode = (data_object.get('mode') or '').strip()
            mode = 'monthly' if stripe_mode == 'subscription' else 'one_time'
            mapped_status = {
                'checkout.session.completed': 'succeeded',
                'checkout.session.async_payment_succeeded': 'succeeded',
                'checkout.session.expired': 'canceled',
                'checkout.session.async_payment_failed': 'failed',
            }[event_type]
            amount_cents = int(data_object.get('amount_total') or 0)
            currency = (data_object.get('currency') or _donation_currency()).lower()
            metadata = data_object.get('metadata') or {}
            source = (metadata.get('source') or '').strip()[:80]
            donor_name = (metadata.get('donor_name') or '').strip()[:120]
            public_user_id = (metadata.get('public_user_id') or '').strip()
            customer_details = data_object.get('customer_details') or {}
            customer_email = (customer_details.get('email') or '').strip().lower()
            email_hash = _donation_email_hash(customer_details.get('email') or '')
            payment_intent_id = data_object.get('payment_intent')
            subscription_id = data_object.get('subscription')

            existing = conn.execute(
                'SELECT amount_cents FROM donations WHERE provider_session_id = ?',
                (session_id,),
            ).fetchone()
            if amount_cents <= 0 and existing:
                amount_cents = int(existing['amount_cents'] or 0)

            conn.execute(
                '''
                INSERT INTO donations (
                    provider, mode, status, amount_cents, currency, email_hash, donor_name, source,
                    provider_session_id, provider_payment_intent_id, provider_subscription_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_session_id) DO UPDATE SET
                    mode = excluded.mode,
                    status = excluded.status,
                    amount_cents = CASE
                        WHEN excluded.amount_cents > 0 THEN excluded.amount_cents
                        ELSE donations.amount_cents
                    END,
                    currency = excluded.currency,
                    email_hash = CASE
                        WHEN excluded.email_hash != '' THEN excluded.email_hash
                        ELSE donations.email_hash
                    END,
                    donor_name = CASE
                        WHEN excluded.donor_name != '' THEN excluded.donor_name
                        ELSE donations.donor_name
                    END,
                    source = CASE
                        WHEN excluded.source != '' THEN excluded.source
                        ELSE donations.source
                    END,
                    provider_payment_intent_id = COALESCE(excluded.provider_payment_intent_id, donations.provider_payment_intent_id),
                    provider_subscription_id = COALESCE(excluded.provider_subscription_id, donations.provider_subscription_id),
                    updated_at = datetime('now')
                ''',
                (
                    'stripe',
                    mode,
                    mapped_status,
                    amount_cents,
                    currency,
                    email_hash,
                    donor_name,
                    source,
                    session_id,
                    payment_intent_id,
                    subscription_id,
                ),
            )

            if mode == 'monthly' and _is_bondsman_subscription_source(source):
                if mapped_status == 'succeeded':
                    _set_public_user_subscription_state(
                        conn,
                        public_user_id=int(public_user_id) if public_user_id.isdigit() else None,
                        email=customer_email,
                        is_subscribed=True,
                        subscriber_plan='bondsman_pro',
                        stripe_subscription_id=subscription_id or '',
                        subscription_status='active',
                    )
                elif mapped_status in {'failed', 'canceled'}:
                    _set_public_user_subscription_state(
                        conn,
                        public_user_id=int(public_user_id) if public_user_id.isdigit() else None,
                        email=customer_email,
                        is_subscribed=False,
                        subscriber_plan='free',
                        stripe_subscription_id='',
                        subscription_status=mapped_status,
                    )

            if mapped_status in {'succeeded', 'failed', 'canceled'}:
                conn.execute(
                    '''
                    INSERT INTO donation_events (
                        event_type, source, page_path, ip_hash, referrer, amount_cents
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        'checkout_success' if mapped_status == 'succeeded' else 'checkout_cancel',
                        source,
                        (event_source or '/webhooks/stripe')[:255],
                        (event_ip_hash or '')[:64],
                        (event_referrer or '')[:500],
                        amount_cents if amount_cents > 0 else None,
                    ),
                )

    elif event_type in {'customer.subscription.updated', 'customer.subscription.deleted'}:
        subscription_id = (data_object.get('id') or '').strip()
        status = (data_object.get('status') or '').strip().lower()
        if subscription_id:
            mapped_status = 'succeeded' if status in {'active', 'trialing', 'past_due'} else 'canceled'
            conn.execute(
                '''
                UPDATE donations
                SET status = ?, updated_at = datetime('now')
                WHERE provider = 'stripe' AND provider_subscription_id = ?
                ''',
                (mapped_status, subscription_id),
            )
            if mapped_status == 'canceled':
                conn.execute(
                    '''
                    UPDATE public_users
                    SET is_subscribed = 0,
                        subscriber_plan = 'free',
                        stripe_subscription_id = '',
                        subscription_status = ?,
                        subscription_canceled_at = datetime('now')
                    WHERE stripe_subscription_id = ?
                    ''',
                    (status or 'canceled', subscription_id),
                )
            else:
                conn.execute(
                    '''
                    UPDATE public_users
                    SET is_subscribed = 1,
                        subscriber_plan = 'bondsman_pro',
                        subscription_status = ?,
                        subscription_activated_at = COALESCE(subscription_activated_at, datetime('now'))
                    WHERE stripe_subscription_id = ?
                    ''',
                    (status or 'active', subscription_id),
                )

    elif event_type == 'charge.refunded':
        payment_intent_id = (data_object.get('payment_intent') or '').strip()
        if payment_intent_id:
            conn.execute(
                '''
                UPDATE donations
                SET status = 'refunded', updated_at = datetime('now')
                WHERE provider = 'stripe' AND provider_payment_intent_id = ?
                ''',
                (payment_intent_id,),
            )


def _iso_lastmod(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%m/%d/%y'):
        try:
            return datetime.strptime(value[:19], fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
    return None


def _build_like_clause(columns, terms):
    clauses = []
    params = []
    for term in terms:
        like_value = f'%{term}%'
        clauses.append('(' + ' OR '.join(f'{column} LIKE ?' for column in columns) + ')')
        params.extend([like_value] * len(columns))
    return ' OR '.join(clauses), params


def _pattern_clause(pattern_slug: str, alias: str = 'records'):
    pattern = PATTERN_DEFINITIONS.get(pattern_slug)
    if not pattern:
        raise KeyError(pattern_slug)
    columns = [
        f'LOWER(COALESCE({alias}.incident_type, \'\'))',
        f'LOWER(COALESCE({alias}.incident, \'\'))',
        f'LOWER(COALESCE({alias}.details, \'\'))',
    ]
    clauses = []
    params = []
    for term in pattern['terms']:
        clauses.append('(' + ' OR '.join(f'{column} LIKE ?' for column in columns) + ')')
        params.extend([f'%{term.lower()}%'] * len(columns))
    return '(' + ' OR '.join(clauses) + ')', params


def _pattern_links_for_county(county_slug: str):
    return [
        {
            'label': pattern['label'],
            'short_label': pattern['short_label'],
            'href': f"/patterns/{pattern['slug']}/{county_slug}",
        }
        for pattern in PATTERN_DEFINITIONS.values()
    ]


def _annotate_posts_for_pattern(conn, posts):
    return _annotate_posts_with_confidence(conn, posts)


def _top_pattern_pages(conn, limit=6):
    candidates = []
    for pattern in PATTERN_DEFINITIONS.values():
        where_sql, params = _pattern_clause(pattern['slug'], 'records')
        rows = conn.execute(
            f'''
            SELECT records.county, COUNT(*) AS record_count, MAX(records.date) AS last_seen
            FROM records
            WHERE {where_sql}
              AND records.county IS NOT NULL
              AND records.county != ''
            GROUP BY records.county
            ORDER BY record_count DESC, records.county ASC
            LIMIT 4
            ''',
            params,
        ).fetchall()
        for row in rows:
            county_slug = _county_slug_for_name(row['county'])
            if not county_slug:
                continue
            candidates.append({
                'pattern_slug': pattern['slug'],
                'pattern_label': pattern['label'],
                'pattern_short_label': pattern['short_label'],
                'county': row['county'],
                'county_slug': county_slug,
                'record_count': row['record_count'],
                'last_seen': row['last_seen'],
                'href': f"/patterns/{pattern['slug']}/{county_slug}",
            })

    candidates.sort(
        key=lambda item: (-item['record_count'], item['pattern_label'], item['county'])
    )
    return candidates[:limit]


def _related_pattern_pages_for_post(records, county_slug=None):
    matched_pages = []
    for pattern in PATTERN_DEFINITIONS.values():
        hit_count = 0
        for record in records:
            haystack_parts = [
                record['incident_type'] if 'incident_type' in record.keys() else '',
                record['incident'] if 'incident' in record.keys() else '',
                record['details'] if 'details' in record.keys() else '',
            ]
            haystack = ' '.join(part for part in haystack_parts if part).lower()
            if any(term.lower() in haystack for term in pattern['terms']):
                hit_count += 1

        if hit_count == 0:
            continue

        href = (
            f"/patterns/{pattern['slug']}/{county_slug}"
            if county_slug else
            f"/patterns/{pattern['slug']}"
        )
        matched_pages.append({
            'label': pattern['label'],
            'short_label': pattern['short_label'],
            'description': pattern['description'],
            'hit_count': hit_count,
            'href': href,
        })

    matched_pages.sort(key=lambda item: (-item['hit_count'], item['label']))
    return matched_pages


def _pattern_target_meta(target_path):
    path = (target_path or '').strip()
    if not path:
        return None
    parsed = urlparse(path)
    clean_path = (parsed.path or path).strip()
    parts = [segment for segment in clean_path.split('/') if segment]
    if len(parts) < 2 or parts[0] != 'patterns':
        return None
    pattern_slug = parts[1]
    if pattern_slug not in PATTERN_DEFINITIONS:
        return None
    county_slug = parts[2] if len(parts) > 2 else None
    return {
        'pattern_slug': pattern_slug,
        'county_slug': county_slug,
        'target_path': clean_path,
    }


def _pattern_page_context(conn, pattern_slug: str, county=None):
    pattern = PATTERN_DEFINITIONS.get(pattern_slug)
    if not pattern:
        return None

    record_where, record_params = _pattern_clause(pattern_slug, 'records')
    where_clauses = [record_where]
    where_params = list(record_params)

    county_name = None
    county_slug = None
    if county:
        county_name = county['name']
        county_slug = county['slug']
        where_clauses.append('records.county = ?')
        where_params.append(county_name)

    where_sql = ' AND '.join(where_clauses)

    total_records = conn.execute(
        f'SELECT COUNT(*) FROM records WHERE {where_sql}',
        where_params,
    ).fetchone()[0]

    last_seen_row = conn.execute(
        f'SELECT MAX(date) AS last_seen FROM records WHERE {where_sql}',
        where_params,
    ).fetchone()
    last_seen = last_seen_row['last_seen'] if last_seen_row else None

    top_incidents = conn.execute(
        f'''
        SELECT COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_type,
               COUNT(*) AS count
        FROM records
        WHERE {where_sql}
        GROUP BY COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident')
        ORDER BY count DESC, incident_type ASC
        LIMIT 8
        ''',
        where_params,
    ).fetchall()

    top_counties = []
    if not county:
        top_counties = conn.execute(
            f'''
            SELECT records.county, COUNT(*) AS count
            FROM records
            WHERE {where_sql}
              AND records.county IS NOT NULL
              AND records.county != ''
            GROUP BY records.county
            ORDER BY count DESC, records.county ASC
            LIMIT 8
            ''',
            where_params,
        ).fetchall()

    recent_records = conn.execute(
        f'''
        SELECT
            records.id,
            records.date,
            records.time,
            COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_label,
            records.location,
            posts.id AS post_id
        FROM records
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE {where_sql}
        ORDER BY records.date DESC, records.time DESC, records.id DESC
        LIMIT 12
        ''',
        where_params,
    ).fetchall()
    recent_records = _annotate_recent_records(conn, recent_records)

    post_params = list(record_params)
    post_sql = f'''
        SELECT DISTINCT posts.*, blotters.county AS blotter_county
        FROM posts
        JOIN records ON records.blotter_id = posts.blotter_id
        JOIN blotters ON blotters.id = posts.blotter_id
        WHERE {record_where}
    '''
    if county_name:
        post_sql += ' AND posts.county = ?'
        post_params.append(county_name)
    post_sql += ' ORDER BY posts.incident_date DESC, posts.created_at DESC LIMIT 8'
    related_posts = conn.execute(post_sql, post_params).fetchall()
    related_posts = _annotate_posts_for_pattern(conn, related_posts)

    source_method_sql = f'''
        SELECT COALESCE(NULLIF(blotters.source_type, ''), 'pdf') AS source_type,
               COUNT(DISTINCT posts.id) AS count
        FROM posts
        JOIN records ON records.blotter_id = posts.blotter_id
        JOIN blotters ON blotters.id = posts.blotter_id
        WHERE {record_where}
    '''
    source_method_params = list(record_params)
    if county_name:
        source_method_sql += ' AND posts.county = ?'
        source_method_params.append(county_name)
    source_method_sql += '''
        GROUP BY COALESCE(NULLIF(blotters.source_type, ''), 'pdf')
        ORDER BY count DESC, source_type ASC
        LIMIT 4
    '''
    source_method_rows = conn.execute(source_method_sql, source_method_params).fetchall()

    sample_counties = []
    if top_counties:
        for row in top_counties:
            slug = _county_slug_for_name(row['county'])
            if slug:
                sample_counties.append({
                    'name': row['county'],
                    'slug': slug,
                    'count': row['count'],
                })

    title = pattern['title_county'].format(county=county_name) if county_name else pattern['title_statewide']
    meta_description = pattern['meta_county'].format(county=county_name) if county_name else pattern['meta_statewide']
    canonical_url = (
        f'{BASE_URL}/patterns/{pattern_slug}/{county_slug}'
        if county_slug else
        f'{BASE_URL}/patterns/{pattern_slug}'
    )

    return {
        'pattern': pattern,
        'county': county,
        'county_name': county_name,
        'county_slug': county_slug,
        'title': title,
        'meta_description': meta_description,
        'canonical_url': canonical_url,
        'total_records': total_records,
        'last_seen': last_seen,
        'top_incidents': top_incidents,
        'top_counties': sample_counties,
        'recent_records': recent_records,
        'related_posts': related_posts,
        'source_methods': _source_method_rollup(source_method_rows),
        'page_last_updated': last_seen,
        'pattern_links': _pattern_links_for_county(county_slug) if county_slug else [],
    }


def _source_method_label(source_key):
    labels = {
        'imap_pdf': 'Email PDF',
        'imap_text': 'Email Body',
        'local_pdf': 'Manual Upload',
        'crimemapping': 'CrimeMapping',
        'pdf': 'Imported PDF',
        'text': 'Text Blotter',
    }
    normalized = (source_key or 'pdf').strip() or 'pdf'
    return labels.get(normalized, 'Imported Source')


def _source_method_rollup(rows):
    methods = []
    for row in rows:
        key = row['source_type'] if hasattr(row, 'keys') else row[0]
        count = row['count'] if hasattr(row, 'keys') else row[1]
        methods.append({
            'key': key or 'pdf',
            'label': _source_method_label(key),
            'count': count,
        })
    return methods


def _ranked_county_cards(conn, limit=8, require_online_roster=False):
    post_stats = {
        row['county']: row
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS post_count, MAX(incident_date) AS last_report
            FROM posts
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }
    record_stats = {
        row['county']: row['record_count']
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS record_count
            FROM records
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }

    county_cards = []
    for county_data in COUNTY_DATA.values():
        if require_online_roster and not county_data.get('roster_url'):
            continue
        stats = post_stats.get(county_data['name'])
        record_count = record_stats.get(county_data['name'], 0)
        post_count = stats['post_count'] if stats else 0
        if not record_count and not post_count:
            continue
        county_cards.append({
            **county_data,
            'record_count': record_count,
            'post_count': post_count,
            'last_report': stats['last_report'] if stats else None,
        })

    county_cards.sort(key=lambda item: (-item['record_count'], -item['post_count'], item['name']))
    return county_cards[:limit]


RESOURCE_GUIDES = {
    'how-to-find-montana-police-blotters': {
        'title': 'How to Find Montana Police Blotters',
        'meta_description': 'Learn how to find Montana police blotters using county pages, city pages, sheriff logs, daily activity reports, and linked public-records resources.',
        'hero_label': 'Evergreen Guide',
        'intro': 'This guide is built for the readers who search for Montana police blotters first, then need the fastest path into county pages, city pages, arrests, warrants, and jail rosters without guessing where to click next.',
        'steps': [
            {
                'title': 'Start with the city page when the search is city-first',
                'body': 'Billings, Helena, Great Falls, Kalispell, Whitefish, Bozeman, and Missoula are all better handled through their city pages before branching into county records.',
            },
            {
                'title': 'Use the county page when the archive needs more depth',
                'body': 'County pages are the main archive hubs for blotter searches because they connect recent reports, arrests, warrants, jail rosters, and neighboring cities.',
            },
            {
                'title': 'Cross-check with warrant, arrest, and jail pages',
                'body': 'Blotter searches often turn into arrest, booking, or warrant follow-up. These linked pages make that move faster and keep the search local.',
            },
        ],
        'faq': [
            {
                'question': 'Are police blotters and daily activity reports the same thing?',
                'answer': 'Usually yes. Agencies use different labels, but both terms generally refer to the public incident log that records calls, arrests, and officer activity.',
            },
            {
                'question': 'Should I start with a city page or a county page?',
                'answer': 'Start with a city page if the search is clearly about Billings, Helena, Great Falls, Missoula, Bozeman, Kalispell, or Whitefish. Start with the county page when the search is broader or the city has limited direct publishing.',
            },
        ],
        'page_links': [
            {'href': '/cities', 'label': 'Browse Montana city pages', 'description': 'City-first blotter pages for the strongest local search terms.'},
            {'href': '/counties', 'label': 'Browse county archives', 'description': 'County-level blotter pages with deeper linked record paths.'},
            {'href': '/guides/montana-county-guides', 'label': 'Montana county guides', 'description': 'The strongest county hubs for blotter, arrest, warrant, and jail searches.'},
            {'href': '/arrests', 'label': 'Montana arrest log', 'description': 'Move from blotter searches into arrest-focused records.'},
        ],
    },
    'yellowstone-county-arrests-jail-roster-and-warrant-guide': {
        'title': 'Yellowstone County Arrests, Jail Roster, and Warrant Guide',
        'meta_description': 'A practical Yellowstone County guide for Billings-area arrests, jail roster lookups, warrants, police blotter pages, and the best county follow-up paths.',
        'hero_label': 'County Guide Asset',
        'intro': 'This guide is built for the strongest Yellowstone County searches: Billings police blotter, Yellowstone County arrests, jail roster checks, warrant lookup, and city-to-county follow-up.',
        'steps': [
            {
                'title': 'Start with Yellowstone County for the deepest archive',
                'body': 'Yellowstone County is one of the deepest public-safety archives on the site, so the county page is the best starting point for broad arrest, blotter, and jail-roster searches.',
            },
            {
                'title': 'Use Billings and Laurel when the search is city-first',
                'body': 'Billings and Laurel often deserve their own entry pages before you branch into county detention or warrant tools.',
            },
            {
                'title': 'Move into warrants and detention immediately',
                'body': 'When the search is practical instead of editorial, the Yellowstone County warrant page and detention roster are the next clicks that matter.',
            },
        ],
        'faq': [
            {
                'question': 'Should I start with Billings or Yellowstone County?',
                'answer': 'Start with Billings if the search is clearly city-specific. Start with Yellowstone County if you need the deeper archive, county arrests, jail roster access, or countywide warrant links.',
            },
            {
                'question': 'What makes Yellowstone County one of the best county pages on the site?',
                'answer': 'It combines deep archive coverage with clear city intent around Billings and Laurel plus direct paths into county detention and warrant systems.',
            },
        ],
        'page_links': [
            {'href': '/county/yellowstone', 'label': 'Yellowstone County page', 'description': 'Main county hub for arrests, blotter coverage, jail roster links, and local record search.'},
            {'href': '/city/billings', 'label': 'Billings police blotter', 'description': 'City-first entry page for Yellowstone County search traffic.'},
            {'href': '/city/laurel', 'label': 'Laurel police blotter', 'description': 'Secondary city page inside the Yellowstone County cluster.'},
            {'href': '/warrants/yellowstone', 'label': 'Yellowstone County warrants', 'description': 'County warrant guide with Billings-focused search intent.'},
        ],
        'featured_counties': ['yellowstone', 'carbon', 'stillwater', 'big-horn'],
    },
    'montana-county-guides': {
        'title': 'Montana County Guides for Police Blotters, Arrests, Warrants, and Jail Rosters',
        'meta_description': 'Browse the strongest Montana county guides for police blotters, arrest records, warrant lookup pages, jail rosters, and local public safety resources.',
        'hero_label': 'County Guide Asset',
        'intro': 'This guide collects the county pages most likely to help readers looking for police blotters, jail rosters, arrests, and warrant resources without forcing them to start from a raw statewide directory.',
        'steps': [
            {
                'title': 'Start with the county page',
                'body': 'County pages are the best first stop when the search intent is broad: blotter, arrests, jail roster, recent police activity, or warrant lookup.',
            },
            {
                'title': 'Use the related city page when local intent is narrower',
                'body': 'If the search is really about Billings, Helena, Great Falls, Kalispell, Whitefish, or Bozeman, move from the county guide into the matching city page.',
            },
            {
                'title': 'Branch into warrants, arrests, and jail rosters',
                'body': 'Each county guide should be treated as a hub that routes readers into the legal and detention pages they actually need next.',
            },
        ],
        'faq': [
            {
                'question': 'Why use a county guide instead of a raw search?',
                'answer': 'County guides connect multiple public-records paths in one place: police blotters, arrest coverage, jail rosters, warrant lookup pages, and matching city pages.',
            },
            {
                'question': 'Which county guides should I start with?',
                'answer': 'Start with the counties that already have the deepest visible archives and the strongest supporting warrant, jail, and city pages.',
            },
        ],
        'page_links': [
            {'href': '/counties', 'label': 'Browse all county pages', 'description': 'Open the statewide county directory.'},
            {'href': '/warrants', 'label': 'Open warrant pages', 'description': 'Jump into county-specific warrant lookup resources.'},
            {'href': '/jail-rosters', 'label': 'See jail rosters', 'description': 'Find county detention and inmate lookup pages.'},
        ],
    },
    'flathead-county-police-blotter-warrant-and-jail-guide': {
        'title': 'Flathead County Police Blotter, Warrant, and Jail Guide',
        'meta_description': 'A practical Flathead County guide for Kalispell, Whitefish, and Columbia Falls blotter searches, county warrants, jail roster checks, and local follow-up links.',
        'hero_label': 'County Guide Asset',
        'intro': 'This guide is built for Flathead Valley search traffic that crosses city and county lines: Kalispell police blotter, Whitefish arrests, Flathead warrants, jail roster lookups, and county archive pages.',
        'steps': [
            {
                'title': 'Use Flathead County as the main archive hub',
                'body': 'Flathead County is one of the strongest county pages on the site because warrant, jail, arrest, and blotter intent overlap heavily across the valley.',
            },
            {
                'title': 'Break out into Kalispell, Whitefish, and Columbia Falls',
                'body': 'City pages help when the search is narrower than the county archive, especially for tourism-heavy and neighborhood-specific search behavior.',
            },
            {
                'title': 'Lean on the county warrant and jail tools',
                'body': 'Flathead is one of the better counties for practical follow-up because the county publishes both a warrant list and a jail roster.',
            },
        ],
        'faq': [
            {
                'question': 'Why is Flathead County valuable for search?',
                'answer': 'Flathead County combines strong countywide demand with city-specific interest around Kalispell and Whitefish, which makes the hub stronger than a typical county-only page.',
            },
            {
                'question': 'Should I search Flathead County or Whitefish first?',
                'answer': 'Use Whitefish or Kalispell first when the search is clearly about that city. Use Flathead County first when you need jail, warrants, arrests, or a broader valley-wide archive.',
            },
        ],
        'page_links': [
            {'href': '/county/flathead', 'label': 'Flathead County page', 'description': 'County hub for blotter coverage, arrests, jail roster access, and Flathead Valley search intent.'},
            {'href': '/city/kalispell', 'label': 'Kalispell police blotter', 'description': 'City-first page for Kalispell activity, warrants, and Flathead detention links.'},
            {'href': '/city/whitefish', 'label': 'Whitefish police blotter', 'description': 'Tourism-heavy city page tied into Flathead County follow-up tools.'},
            {'href': '/warrants/flathead', 'label': 'Flathead County warrants', 'description': 'County warrant guide with Kalispell and Whitefish search demand built in.'},
        ],
        'featured_counties': ['flathead', 'lake', 'lincoln', 'sanders'],
    },
    'lewis-and-clark-county-blotter-warrant-and-jail-guide': {
        'title': 'Lewis and Clark County Blotter, Warrant, and Jail Guide',
        'meta_description': 'A practical Lewis and Clark County guide for Helena and East Helena police blotter searches, county warrants, jail resources, and capital-region follow-up paths.',
        'hero_label': 'County Guide Asset',
        'intro': 'This guide is built for capital-region search traffic that overlaps across Helena, East Helena, Lewis and Clark County warrants, jail lookups, and county arrest coverage.',
        'steps': [
            {
                'title': 'Start with Lewis and Clark County for the broad archive',
                'body': 'The county page is the best starting point when the search is not limited to one city and you need county arrests, detention links, and a broader records view.',
            },
            {
                'title': 'Use Helena and East Helena when the search is city-first',
                'body': 'Capital-region search traffic often begins with Helena or East Helena rather than the county name, so city pages need to be part of the workflow immediately.',
            },
            {
                'title': 'Keep the warrant page close',
                'body': 'Lewis and Clark is one of the stronger warrant-follow-up markets because Helena has practical warrant intent and a clear detention path nearby.',
            },
        ],
        'faq': [
            {
                'question': 'Should I start with Helena or Lewis and Clark County?',
                'answer': 'Start with Helena when the search is clearly city-specific. Start with Lewis and Clark County when you need broader arrest coverage, jail links, or countywide public-record follow-up.',
            },
            {
                'question': 'Why is this a strong capital-region guide?',
                'answer': 'It combines city-first searches around Helena with county-level warrant and detention follow-up, which makes it more useful than a raw directory page.',
            },
        ],
        'page_links': [
            {'href': '/county/lewis-and-clark', 'label': 'Lewis and Clark County page', 'description': 'Main county hub for arrests, blotter coverage, jail resources, and capital-region follow-up.'},
            {'href': '/city/helena', 'label': 'Helena police blotter', 'description': 'City-first entry page for the strongest capital-region search term.'},
            {'href': '/city/east-helena', 'label': 'East Helena police blotter', 'description': 'Secondary city page for readers searching outside Helena proper.'},
            {'href': '/warrants/lewis-and-clark', 'label': 'Lewis and Clark warrants', 'description': 'County warrant guide connected to Helena and detention follow-up.'},
        ],
        'featured_counties': ['lewis-and-clark', 'jefferson', 'broadwater', 'meagher'],
    },
    'how-to-find-montana-warrants': {
        'title': 'How to Find Montana Warrants',
        'meta_description': 'Learn how to find active warrants in Montana using county warrant lists, the Montana court portal, and sheriff resources.',
        'hero_label': 'Evergreen Guide',
        'intro': 'This guide is built for the exact searches people make when they need to check active warrants in Montana, whether the search starts with a county, a city, or the statewide court system.',
        'steps': [
            {
                'title': 'Check the county warrant page first',
                'body': 'If the county publishes a warrant list, that is usually the fastest path into active local warrant information.',
            },
            {
                'title': 'Use the Montana Judicial Branch portal',
                'body': 'For counties without a live warrant list, the court portal is the statewide fallback for warrant lookup and case follow-up.',
            },
            {
                'title': 'Confirm detention and arrest context',
                'body': 'Use the jail roster and county arrest pages to understand whether a warrant has already resulted in booking activity or related records.',
            },
        ],
        'faq': [
            {
                'question': 'Does this guide replace an official county warrant list?',
                'answer': 'No. It is a routing guide into the official county and statewide systems that handle warrant publication and court records.',
            },
            {
                'question': 'What is the best starting point if I only know the county?',
                'answer': 'Start with the county warrant page, then move into the statewide court portal if that county does not publish a live list.',
            },
        ],
        'page_links': [
            {'href': '/warrants', 'label': 'Montana warrant search hub', 'description': 'Statewide warrant guide plus county pages.'},
            {'href': '/warrants/yellowstone', 'label': 'Yellowstone County warrants', 'description': 'One of the strongest county warrant entry pages.'},
            {'href': '/warrants/flathead', 'label': 'Flathead County warrants', 'description': 'Strong county-plus-city warrant demand.'},
            {'href': '/warrants/lewis-and-clark', 'label': 'Lewis and Clark warrants', 'description': 'Helena-area warrant searches and county links.'},
        ],
    },
    'how-to-find-montana-arrest-records': {
        'title': 'How to Find Montana Arrest Records',
        'meta_description': 'Learn how to find Montana arrest records using county pages, arrest coverage, jail rosters, court records, and local police blotter archives.',
        'hero_label': 'Evergreen Guide',
        'intro': 'This guide is meant to be a practical starting point for people looking for Montana arrest records, whether they begin with a county, a city, a jail lookup, or a broader arrest search.',
        'steps': [
            {
                'title': 'Start with local arrest coverage',
                'body': 'County and city pages surface the fastest route into recent arrest-related records and linked incident summaries.',
            },
            {
                'title': 'Use the arrest log for broader searches',
                'body': 'When the county or city is not obvious, the statewide arrest log is the best way to search across the archive for arrest-related language.',
            },
            {
                'title': 'Verify with jail and court systems',
                'body': 'Detention and court systems are the next step when you need inmate status, formal case tracking, or more complete booking context.',
            },
        ],
        'faq': [
            {
                'question': 'Are Montana arrest records the same as jail rosters?',
                'answer': 'No. Arrest-related records and jail rosters overlap, but jail rosters focus on current detention status while arrest coverage can include calls, bookings, and linked report summaries.',
            },
            {
                'question': 'What should I use if I only know the city?',
                'answer': 'Start with the city page, then move into the related county arrest, jail, and warrant pages from there.',
            },
        ],
        'page_links': [
            {'href': '/arrests', 'label': 'Montana arrest log', 'description': 'Search arrest-related records across the archive.'},
            {'href': '/county/yellowstone', 'label': 'Yellowstone County guide', 'description': 'Strong county arrests, jail, and warrant hub.'},
            {'href': '/city/billings', 'label': 'Billings police blotter', 'description': 'City-first path into arrest-related local records.'},
            {'href': '/jail-rosters', 'label': 'Montana jail rosters', 'description': 'Official detention links for all counties.'},
        ],
    },
    'how-to-find-montana-jail-rosters': {
        'title': 'How to Find Montana Jail Rosters',
        'meta_description': 'Learn how to find Montana jail rosters, inmate lookup pages, and detention resources using county roster pages and local public-records guides.',
        'hero_label': 'Evergreen Guide',
        'intro': 'This guide turns a simple jail-roster query into a usable workflow by linking official detention tools with the county, warrant, and arrest pages that help readers understand what they are seeing.',
        'steps': [
            {
                'title': 'Use the statewide jail roster directory',
                'body': 'The roster directory is the fastest way to jump into an official county detention or inmate search page.',
            },
            {
                'title': 'Use the matching county guide for context',
                'body': 'Once you know the county, the county page helps connect roster checks to arrests, warrants, and recent report coverage.',
            },
            {
                'title': 'Cross-check city and warrant pages',
                'body': 'For higher-intent searches, city and warrant pages often provide the missing context around why a booking or detention lookup matters.',
            },
        ],
        'faq': [
            {
                'question': 'Do all Montana counties have online jail rosters?',
                'answer': 'No. Some counties publish live inmate tools while others require phone confirmation or rely on statewide notification systems.',
            },
            {
                'question': 'Why pair a jail roster with a county guide?',
                'answer': 'Because roster lookups are stronger when they are connected to county arrests, recent police activity, and warrant resources in the same local area.',
            },
        ],
        'page_links': [
            {'href': '/jail-rosters', 'label': 'Montana jail rosters', 'description': 'Official roster and inmate search links for all 56 counties.'},
            {'href': '/county/flathead', 'label': 'Flathead County guide', 'description': 'Strong roster, warrant, and city-page support.'},
            {'href': '/county/yellowstone', 'label': 'Yellowstone County guide', 'description': 'Billings-area detention and public-records hub.'},
            {'href': '/county/lewis-and-clark', 'label': 'Lewis and Clark County guide', 'description': 'Helena-area detention, warrant, and police activity paths.'},
        ],
    },
}


def _source_channel_meta(post):
    source_key = post['source_type'] or post['blotter_source_type'] or 'pdf'
    labels = {
        'imap_pdf': ('Email PDF', 'Delivered to records@montanablotter.com as a PDF attachment.'),
        'imap_text': ('Email Body', 'Delivered to records@montanablotter.com as text in the email body.'),
        'local_pdf': ('Manual Upload', 'Uploaded manually through the Montana Blotter admin panel.'),
        'crimemapping': ('CrimeMapping', 'Imported from a public CrimeMapping incident feed.'),
        'pdf': ('Imported PDF', 'Imported from a PDF batch in the blotter pipeline.'),
        'text': ('Text Blotter', 'Imported from a plain-text blotter body.'),
    }
    label, description = labels.get(source_key, ('Imported Source', 'Imported into the blotter pipeline.'))
    return {
        'key': source_key,
        'label': label,
        'description': description,
        'received_at': post['source_received_at'] or post['upload_date'],
        'sender': post['source_sender'],
        'subject': post['source_subject'],
        'filename': post['source_filename'] or post['blotter_filename'],
        'extraction_method': post['extraction_method'],
        'warnings': _safe_json_loads(post['extraction_warnings'], []),
    }


def _parse_quality(records, county, extraction_method=None):
    total = len(records)
    if total == 0:
        return {
            'label': 'Unavailable',
            'score': 0,
            'detail': 'No source records were available for this post.',
        }

    unknown_incident = sum(
        1 for record in records
        if not (record['incident_type'] or '').strip()
        or (record['incident_type'] or '').strip().lower() == 'unknown'
    )
    unknown_location = sum(
        1 for record in records
        if not (record['location'] or '').strip()
        or (record['location'] or '').strip().lower() == 'unknown'
    )
    missing_time = sum(1 for record in records if not (record['time'] or '').strip())

    score = 100
    score -= round((unknown_incident / total) * 38)
    score -= round((unknown_location / total) * 28)
    score -= round((missing_time / total) * 14)
    if (county or '').strip().lower() == 'unknown':
        score -= 18
    if extraction_method == 'email_html':
        score -= 10
    score = max(0, min(100, score))

    if score >= 82:
        label = 'High'
    elif score >= 60:
        label = 'Medium'
    else:
        label = 'Low'

    return {
        'label': label,
        'score': score,
        'detail': (
            f"{total - unknown_incident}/{total} incident labels, "
            f"{total - unknown_location}/{total} locations, and "
            f"{total - missing_time}/{total} timestamps parsed cleanly."
        ),
    }


def _infer_summary_method(post, event_details=None):
    details = event_details or {}
    method = details.get('method')
    provider = details.get('provider')
    if method == 'ai_generated':
        provider_label = 'OpenAI' if provider == 'openai' else 'Anthropic' if provider == 'anthropic' else 'LLM'
        return {
            'label': f'{provider_label} summary',
            'detail': 'The summary text was generated from the indexed incidents using the publishing model pipeline.',
        }
    if method == 'skipped_duplicate_post':
        overlap_ratio = details.get('overlap_ratio')
        overlap_pct = f"{round(float(overlap_ratio) * 100)}%" if overlap_ratio is not None else 'high'
        return {
            'label': 'Duplicate post suppressed',
            'detail': f'A near-identical post already existed for this incident set ({overlap_pct} overlap).',
        }

    summary = (post['summary'] or '').strip().lower()
    if 'responded to the following incidents:' in summary:
        return {
            'label': 'Fallback digest',
            'detail': 'Published from the built-in incident formatter without an LLM rewrite.',
        }

    return {
        'label': 'AI-generated summary',
        'detail': 'Published from the structured summarizer path.',
    }


def _cross_source_overlap(conn, post, records):
    keys = incident_key_set(records, county=post['county'])
    if not keys or not post['incident_date'] or not post['county']:
        return {'label': 'Not enough data', 'detail': 'This post does not have enough structured data for overlap scoring.'}

    candidates = conn.execute(
        '''
        SELECT id, title, blotter_id
        FROM posts
        WHERE county = ? AND incident_date = ? AND id != ?
        ORDER BY created_at DESC
        LIMIT 8
        ''',
        (post['county'], post['incident_date'], post['id']),
    ).fetchall()

    best_match = None
    best_overlap = 0.0
    for candidate in candidates:
        sibling_records = conn.execute(
            '''
            SELECT cfs_number, date, time,
                   COALESCE(incident_type, incident, '') AS incident_type,
                   COALESCE(location, '') AS location,
                   COALESCE(details, '') AS details,
                   county
            FROM records
            WHERE blotter_id = ?
            ''',
            (candidate['blotter_id'],),
        ).fetchall()
        sibling_keys = incident_key_set(sibling_records, county=post['county'])
        if not sibling_keys:
            continue
        overlap = len(keys & sibling_keys) / max(len(keys), 1)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = candidate

    if best_overlap >= 0.7 and best_match:
        return {
            'label': 'Potential overlap',
            'detail': f"About {round(best_overlap * 100)}% of this incident set overlaps with post #{best_match['id']}.",
            'status': 'warn',
            'matched_post_id': best_match['id'],
        }
    if best_overlap >= 0.3 and best_match:
        return {
            'label': 'Low overlap',
            'detail': f"Some incident signatures overlap with post #{best_match['id']}, but not enough to treat it as a duplicate.",
            'status': 'ok',
            'matched_post_id': best_match['id'],
        }
    return {
        'label': 'Passed',
        'detail': 'No materially similar public post was found for the same county and incident date.',
        'status': 'ok',
    }


def _build_provenance_card(conn, post, records):
    source_meta = _source_channel_meta(post)
    ingestion_job = None
    pipeline_events = []
    event_map = {}

    if post['source_document_id']:
        ingestion_job = conn.execute(
            '''
            SELECT id, status, retry_count, last_error, started_at, finished_at
            FROM ingestion_jobs
            WHERE source_document_id = ?
            ''',
            (post['source_document_id'],),
        ).fetchone()
        if ingestion_job:
            pipeline_events = conn.execute(
                '''
                SELECT stage, status, details_json, created_at
                FROM pipeline_events
                WHERE ingestion_job_id = ?
                ORDER BY id
                ''',
                (ingestion_job['id'],),
            ).fetchall()
            for event in pipeline_events:
                event_map[event['stage']] = {
                    'status': event['status'],
                    'details': _safe_json_loads(event['details_json'], {}),
                    'created_at': event['created_at'],
                }

    parse_quality = _parse_quality(records, post['county'], source_meta['extraction_method'])
    summary_meta = _infer_summary_method(post, event_map.get('summary_method', {}).get('details'))
    duplicate_meta = _cross_source_overlap(conn, post, records)

    normalize_details = event_map.get('normalize', {}).get('details', {})
    skipped_duplicates = normalize_details.get('duplicate_incidents_skipped')
    if skipped_duplicates is not None and duplicate_meta.get('status') != 'warn':
        duplicate_meta = {
            'label': 'Passed',
            'detail': (
                f"{skipped_duplicates} incident(s) matched earlier records and were skipped before publication."
                if skipped_duplicates
                else 'No incoming incidents matched an earlier source record during publication.'
            ),
            'status': 'ok',
        }

    audit_details = event_map.get('audit', {}).get('details', {})
    flagged_count = int(audit_details.get('flagged', 0) or 0)

    confidence_score = parse_quality['score']
    source_key = source_meta['key']
    if source_key in ('imap_pdf', 'local_pdf', 'crimemapping'):
        confidence_score += 8
    elif source_key in ('imap_text', 'text'):
        confidence_score += 2
    if flagged_count == 0:
        confidence_score += 5
    if duplicate_meta.get('status') == 'warn':
        confidence_score -= 20
    if ingestion_job and ingestion_job['retry_count']:
        confidence_score -= min(10, ingestion_job['retry_count'] * 3)
    if (post['county'] or '').strip().lower() == 'unknown':
        confidence_score -= 10
    confidence_score = max(0, min(100, confidence_score))

    if confidence_score >= 82:
        confidence_label = 'High confidence'
    elif confidence_score >= 60:
        confidence_label = 'Medium confidence'
    else:
        confidence_label = 'Limited confidence'

    return {
        'confidence_label': confidence_label,
        'confidence_score': confidence_score,
        'source': source_meta,
        'parse_quality': parse_quality,
        'duplicate_checks': duplicate_meta,
        'summary_method': summary_meta,
        'audit_flagged_count': flagged_count,
        'ingestion_status': ingestion_job['status'] if ingestion_job else 'published',
        'retry_count': ingestion_job['retry_count'] if ingestion_job else 0,
        'last_error': ingestion_job['last_error'] if ingestion_job else None,
        'stages': event_map,
    }


def _build_record_provenance_card(conn, record, blotter_records):
    record_context = {
        'id': record['post_id'] or record['id'],
        'county': record['county'],
        'incident_date': record['incident_date'] or record['date'],
        'summary': None,
        'source_type': record['source_type'],
        'blotter_source_type': record['blotter_source_type'],
        'source_sender': record['source_sender'],
        'source_subject': record['source_subject'],
        'source_received_at': record['source_received_at'],
        'source_filename': record['source_filename'],
        'blotter_filename': record['blotter_filename'],
        'upload_date': record['upload_date'],
        'source_document_id': record['source_document_id'],
        'extraction_method': record['extraction_method'],
        'extraction_warnings': record['extraction_warnings'],
    }
    card = _build_provenance_card(conn, record_context, blotter_records)
    if record['post_title']:
        card['summary_method']['detail'] += f" Linked report: {record['post_title']}."
    return card


def _confidence_badge_meta(card):
    score = card['confidence_score']
    if score >= 82:
        tone = 'high'
        bg_class = 'bg-emerald-100'
        text_class = 'text-emerald-700'
    elif score >= 60:
        tone = 'medium'
        bg_class = 'bg-amber-100'
        text_class = 'text-amber-700'
    else:
        tone = 'limited'
        bg_class = 'bg-rose-100'
        text_class = 'text-rose-700'
    return {
        'tone': tone,
        'label': card['confidence_label'],
        'score': score,
        'source_label': card['source']['label'],
        'bg_class': bg_class,
        'text_class': text_class,
    }


def _annotate_recent_records(conn, recent_records):
    if not recent_records:
        return []

    record_ids = [int(record['id']) for record in recent_records]
    placeholders = ','.join('?' for _ in record_ids)
    detail_rows = conn.execute(
        f'''
        SELECT records.id,
               records.blotter_id,
               records.county,
               records.date,
               records.time,
               blotters.filename AS blotter_filename,
               blotters.upload_date,
               blotters.source_type AS blotter_source_type,
               blotters.source_document_id,
               posts.id AS post_id,
               posts.title AS post_title,
               posts.incident_date,
               source_documents.source_type,
               source_documents.source_sender,
               source_documents.source_subject,
               source_documents.source_received_at,
               source_documents.filename AS source_filename,
               source_documents.extraction_method,
               source_documents.extraction_warnings
        FROM records
        LEFT JOIN blotters ON blotters.id = records.blotter_id
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        LEFT JOIN source_documents ON source_documents.id = blotters.source_document_id
        WHERE records.id IN ({placeholders})
        ''',
        record_ids,
    ).fetchall()
    detail_map = {int(row['id']): row for row in detail_rows}

    blotter_ids = sorted({int(row['blotter_id']) for row in detail_rows if row['blotter_id'] is not None})
    blotter_cache = {}
    if blotter_ids:
        placeholders = ','.join('?' for _ in blotter_ids)
        batch_rows = conn.execute(
            f'''
            SELECT blotter_id,
                   cfs_number, date, time,
                   COALESCE(incident_type, incident, '') AS incident_type,
                   COALESCE(location, '') AS location,
                   COALESCE(details, '') AS details,
                   county
            FROM records
            WHERE blotter_id IN ({placeholders})
            ORDER BY date, time, id
            ''',
            blotter_ids,
        ).fetchall()
        for row in batch_rows:
            blotter_cache.setdefault(int(row['blotter_id']), []).append(row)

    enriched = []
    for record in recent_records:
        item = dict(record)
        for key in ('location', 'details'):
            if key in item:
                item[key] = _redact_public_pii(item[key])
        detail = detail_map.get(int(record['id']))
        if detail:
            card = _build_record_provenance_card(
                conn,
                detail,
                blotter_cache.get(int(detail['blotter_id']), []),
            )
            item['confidence_badge'] = _confidence_badge_meta(card)
        else:
            item['confidence_badge'] = {
                'tone': 'limited',
                'label': 'Limited confidence',
                'score': 0,
                'source_label': 'Unknown source',
                'bg_class': 'bg-rose-100',
                'text_class': 'text-rose-700',
            }
        enriched.append(item)
    return enriched


def _case_journey_status_label(status):
    return {
        'open': 'Open follow-up',
        'arrested': 'Arrest-related follow-up',
        'monitoring': 'Active timeline',
        'resolved': 'Resolved',
    }.get((status or '').strip().lower(), 'Public timeline')


def _case_journey_rows(conn, *, featured_only=False, limit=None):
    sql = '''
        SELECT cj.*,
               records.location,
               records.cfs_number,
               records.date AS record_date,
               COALESCE(records.incident_type, records.incident, cj.title) AS incident_type,
               posts.id AS post_id,
               posts.title AS post_title,
               COUNT(cje.id) AS event_count
        FROM case_journeys cj
        LEFT JOIN records ON records.id = cj.primary_record_id
        LEFT JOIN posts ON posts.id = cj.primary_post_id
        LEFT JOIN case_journey_events cje ON cje.journey_id = cj.id AND cje.is_public = 1
        WHERE cj.is_published = 1
    '''
    params = []
    if featured_only:
        sql += ' AND cj.is_featured_homepage = 1'
    sql += '''
        GROUP BY cj.id, records.location, records.cfs_number, records.date,
                 records.incident_type, records.incident, posts.id, posts.title
        ORDER BY cj.last_event_at DESC, event_count DESC, cj.id DESC
    '''
    if limit:
        sql += ' LIMIT ?'
        params.append(limit)
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    if not rows:
        return []

    record_rows = []
    record_ids = []
    for row in rows:
        row['status_label'] = _case_journey_status_label(row.get('current_status'))
        row['county_slug'] = _county_slug_for_name(row.get('county'))
        row['timeline_step_count'] = int(row.get('event_count') or 0)
        if row.get('primary_record_id'):
            record_rows.append({'id': row['primary_record_id']})
            record_ids.append(int(row['primary_record_id']))

    confidence_map = {}
    if record_ids:
        confidence_items = _annotate_recent_records(conn, record_rows)
        confidence_map = {
            int(item['id']): item['confidence_badge']
            for item in confidence_items
            if item.get('confidence_badge')
        }
    for row in rows:
        row['confidence_badge'] = confidence_map.get(int(row['primary_record_id'])) if row.get('primary_record_id') else None
    return rows


def _featured_case_journeys(conn, limit=3):
    return _case_journey_rows(conn, featured_only=True, limit=limit)


def _case_journey_detail(conn, slug):
    rows = _case_journey_rows(conn, featured_only=False)
    journey = next((row for row in rows if row['slug'] == slug), None)
    if not journey:
        return None

    journey['events'] = [dict(row) for row in conn.execute(
        '''
        SELECT *
        FROM case_journey_events
        WHERE journey_id = ? AND is_public = 1
        ORDER BY sort_order, id
        ''',
        (journey['id'],)
    ).fetchall()]
    return journey


def _case_journey_for_record(conn, record_id):
    row = conn.execute(
        '''
        SELECT slug, title
        FROM case_journeys
        WHERE is_published = 1
          AND (
            primary_record_id = ?
            OR id IN (
                SELECT journey_id
                FROM case_journey_events
                WHERE source_table = 'records' AND source_row_id = ?
            )
          )
        ORDER BY is_featured_homepage DESC, last_event_at DESC
        LIMIT 1
        ''',
        (record_id, record_id),
    ).fetchone()
    return dict(row) if row else None


def _annotate_posts_with_confidence(conn, posts):
    if not posts:
        return []

    post_ids = [int(post['id']) for post in posts]
    placeholders = ','.join('?' for _ in post_ids)
    detail_rows = conn.execute(
        f'''
        SELECT posts.*,
               blotters.filename AS blotter_filename,
               blotters.file_path,
               blotters.upload_date,
               blotters.incident_count,
               blotters.source_type AS blotter_source_type,
               blotters.source_document_id,
               source_documents.source_type,
               source_documents.source_sender,
               source_documents.source_subject,
               source_documents.source_received_at,
               source_documents.filename AS source_filename,
               source_documents.extraction_method,
               source_documents.extraction_warnings
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        LEFT JOIN source_documents ON source_documents.id = blotters.source_document_id
        WHERE posts.id IN ({placeholders})
        ''',
        post_ids,
    ).fetchall()
    detail_map = {int(row['id']): row for row in detail_rows}

    blotter_ids = sorted({int(row['blotter_id']) for row in detail_rows if row['blotter_id'] is not None})
    blotter_cache = {}
    if blotter_ids:
        placeholders = ','.join('?' for _ in blotter_ids)
        batch_rows = conn.execute(
            f'''
            SELECT blotter_id,
                   id,
                   cfs_number, date, time,
                   COALESCE(incident_type, incident, '') AS incident_type,
                   COALESCE(location, '') AS location,
                   COALESCE(details, '') AS details,
                   county
            FROM records
            WHERE blotter_id IN ({placeholders})
            ORDER BY date, time, id
            ''',
            blotter_ids,
        ).fetchall()
        for row in batch_rows:
            blotter_cache.setdefault(int(row['blotter_id']), []).append(row)

    enriched = []
    for post in posts:
        item = dict(post)
        detail = detail_map.get(int(post['id']))
        blotter_records = blotter_cache.get(int(detail['blotter_id']), []) if detail else []
        if blotter_records:
            lead_record = dict(blotter_records[-1])
            item['lead_record'] = {
                'id': lead_record.get('id'),
                'date': lead_record.get('date'),
                'time': lead_record.get('time'),
                'incident_label': (lead_record.get('incident_type') or '').strip() or 'Incident',
                'location': (lead_record.get('location') or '').strip(),
                'cfs_number': lead_record.get('cfs_number'),
            }
            item['record_count'] = len(blotter_records)
        else:
            item['lead_record'] = None
            item['record_count'] = 0
        if detail:
            card = _build_provenance_card(
                conn,
                detail,
                blotter_records,
            )
            item['confidence_badge'] = _confidence_badge_meta(card)
        else:
            item['confidence_badge'] = {
                'tone': 'limited',
                'label': 'Limited confidence',
                'score': 0,
                'source_label': 'Unknown source',
                'bg_class': 'bg-rose-100',
                'text_class': 'text-rose-700',
            }
        enriched.append(item)
    return enriched


def _latest_weekly_digest(conn):
    return conn.execute(
        """
        SELECT id, title, slug, excerpt, author, created_at
        FROM blog_posts
        WHERE published = 1
          AND slug LIKE 'montana-weekly-county-digest-%'
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()


def _city_directory_listing(conn):
    cities = []
    for city in CITY_DATA.values():
        where_sql, params = _build_like_clause(
            ['posts.city', 'posts.agency_name'],
            city['search_terms'],
        )
        rec_where_sql, rec_params = _build_like_clause(
            ['records.location'],
            city['search_terms'],
        )

        post_count = conn.execute(
            f'SELECT COUNT(*) FROM posts WHERE {where_sql}',
            params,
        ).fetchone()[0]
        last_row = conn.execute(
            f'SELECT incident_date FROM posts WHERE {where_sql} ORDER BY incident_date DESC LIMIT 1',
            params,
        ).fetchone()
        record_count = conn.execute(
            f'SELECT COUNT(*) FROM records WHERE {rec_where_sql}',
            rec_params,
        ).fetchone()[0]

        cities.append({
            **city,
            'post_count': post_count,
            'record_count': record_count,
            'last_report': last_row['incident_date'] if last_row else None,
        })

    cities.sort(key=lambda city: (-city['post_count'], city['name']))
    return cities


def _featured_city_pages(cities, limit=None):
    city_lookup = {city['slug']: city for city in cities}
    featured = [
        city_lookup[slug]
        for slug in NEW_CITY_SLUGS
        if slug in city_lookup
    ]
    featured.sort(
        key=lambda city: (-city['record_count'], -city['post_count'], city['name'])
    )
    if limit is not None:
        return featured[:limit]
    return featured


def _titleize_slug(value):
    parts = [part for part in (value or '').replace('_', '-').split('-') if part]
    return ' '.join(part.capitalize() for part in parts)


def _homepage_recent_records(
    conn,
    *,
    county='',
    city='',
    agency_type='',
    agency='',
    search_query='',
    date_sql_val='',
    status_filter='',
    limit=10,
):
    record_date_sql = _record_roundup_date_sql('records.date')
    sql = f"""
        SELECT
            records.id,
            records.date,
            records.time,
            records.county,
            COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_label,
            COALESCE(NULLIF(records.location, ''), '') AS location,
            COALESCE(records.details, '') AS details,
            posts.id AS post_id,
            posts.title AS post_title,
            COALESCE(NULLIF(posts.agency_name, ''), 'Unknown agency') AS agency_name,
            COALESCE(NULLIF(posts.city, ''), '') AS city,
            COALESCE(posts.case_status, 'pending') AS case_status
        FROM records
        JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE COALESCE(posts.audit_status, 'pending') = 'clean'
    """
    params = []

    if county:
        sql += " AND records.county = ?"
        params.append(county)
    if city:
        sql += " AND COALESCE(posts.city, '') LIKE ?"
        params.append(f'%{city}%')
    if agency_type:
        sql += " AND COALESCE(posts.agency_type, '') = ?"
        params.append(agency_type)
    if agency:
        sql += " AND COALESCE(posts.agency_name, '') = ?"
        params.append(agency)
    if search_query:
        st = f'%{search_query}%'
        sql += """
            AND (
                COALESCE(records.incident_type, '') LIKE ?
                OR COALESCE(records.incident, '') LIKE ?
                OR COALESCE(records.details, '') LIKE ?
                OR COALESCE(records.location, '') LIKE ?
                OR COALESCE(posts.title, '') LIKE ?
                OR COALESCE(posts.summary, '') LIKE ?
            )
        """
        params.extend([st, st, st, st, st, st])
    if date_sql_val:
        sql += " AND records.date = ?"
        params.append(date_sql_val)
    if status_filter in ('active', 'pending', 'resolved'):
        sql += " AND COALESCE(posts.case_status, 'pending') = ?"
        params.append(status_filter)

    sql += f"""
        ORDER BY
            CASE COALESCE(posts.case_status, 'pending')
                WHEN 'active' THEN 0
                WHEN 'pending' THEN 1
                ELSE 2
            END,
            {record_date_sql} DESC,
            COALESCE(records.time, '') DESC,
            records.id DESC
        LIMIT ?
    """
    params.append(limit)

    recent_records = conn.execute(sql, params).fetchall()
    return _annotate_recent_records(conn, recent_records)


def _homepage_most_viewed_pages(conn, limit=5):
    try:
        pv_conn = connect_page_views()
        path_rows = pv_conn.execute(
            """
            SELECT path, COUNT(*) AS view_count, MAX(created_at) AS last_viewed_at
            FROM page_views
            WHERE created_at >= datetime('now', '-7 days')
              AND path IS NOT NULL
              AND TRIM(path) != ''
              AND path != '/'
              AND path NOT LIKE '/admin%'
              AND path NOT LIKE '/api/%'
              AND path NOT LIKE '/static/%'
              AND path NOT LIKE '/uploads/%'
              AND path NOT LIKE '/manifest.json%'
              AND path NOT LIKE '/sw.js%'
              AND path NOT LIKE '/feed.xml%'
            GROUP BY path
            ORDER BY view_count DESC, last_viewed_at DESC
            LIMIT ?
            """,
            (limit * 3,),
        ).fetchall()
        pv_conn.close()
    except (sqlite3.Error, Exception):
        return []

    if not path_rows:
        return []

    post_ids = []
    record_ids = []
    for row in path_rows:
        path = (row['path'] or '').strip()
        if path.startswith('/post/'):
            tail = path.split('/post/', 1)[1].split('/', 1)[0]
            if tail.isdigit():
                post_ids.append(int(tail))
        elif path.startswith('/record/'):
            tail = path.split('/record/', 1)[1].split('/', 1)[0]
            if tail.isdigit():
                record_ids.append(int(tail))

    post_map = {}
    if post_ids:
        placeholders = ','.join('?' for _ in post_ids)
        post_rows = conn.execute(
            f"SELECT id, title, county FROM posts WHERE id IN ({placeholders})",
            post_ids,
        ).fetchall()
        post_map = {
            int(row['id']): {
                'label': row['title'] or f"Report {row['id']}",
                'meta': row['county'] or 'Report',
                'kind': 'Report',
            }
            for row in post_rows
        }

    record_map = {}
    if record_ids:
        placeholders = ','.join('?' for _ in record_ids)
        record_rows = conn.execute(
            f"""
            SELECT
                id,
                county,
                COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident') AS incident_label
            FROM records
            WHERE id IN ({placeholders})
            """,
            record_ids,
        ).fetchall()
        record_map = {
            int(row['id']): {
                'label': row['incident_label'],
                'meta': row['county'] or 'Record',
                'kind': 'Record',
            }
            for row in record_rows
        }

    generic_labels = {
        '/counties': ('Jurisdiction Directory', 'Statewide coverage', 'Directory'),
        '/cities': ('City Coverage', 'Jurisdiction coverage', 'Directory'),
        '/trends': ('Statewide Trends', '7-day movement', 'Trend'),
        '/arrests': ('Arrest Records', 'Recent arrest-linked entries', 'Desk'),
        '/support': ('Support Hub', 'Sponsorships and partnerships', 'Support'),
        '/jail-rosters': ('Jail Rosters', 'County detention lookup', 'Lookup'),
        '/detention': ('Detention Hub', 'County detention lookup', 'Lookup'),
        '/warrants': ('Warrant Resources', 'Court and county warrant access', 'Lookup'),
        '/courts': ('Montana Court Tracker', 'Hearings and public case pages', 'Courts'),
    }

    viewed_pages = []
    for row in path_rows:
        path = (row['path'] or '').strip()
        label = None
        meta = None
        kind = 'Page'

        if path in generic_labels:
            label, meta, kind = generic_labels[path]
        elif path.startswith('/post/'):
            tail = path.split('/post/', 1)[1].split('/', 1)[0]
            if tail.isdigit():
                info = post_map.get(int(tail))
                if info:
                    label = info['label']
                    meta = info['meta']
                    kind = info['kind']
        elif path.startswith('/record/'):
            tail = path.split('/record/', 1)[1].split('/', 1)[0]
            if tail.isdigit():
                info = record_map.get(int(tail))
                if info:
                    label = info['label']
                    meta = info['meta']
                    kind = info['kind']
        elif path.startswith('/county/'):
            slug = path.split('/county/', 1)[1].split('/', 1)[0]
            county_page = COUNTY_DATA.get(slug)
            if county_page:
                label = f"{county_page['name']} County"
                meta = 'County desk'
                kind = 'Jurisdiction'
        elif path.startswith('/city/'):
            slug = path.split('/city/', 1)[1].split('/', 1)[0]
            city_page = CITY_DATA.get(slug)
            if city_page:
                label = city_page['name']
                meta = city_page.get('county_name') or 'City desk'
                kind = 'Jurisdiction'
        elif path.startswith('/laws/charge/'):
            slug = path.split('/laws/charge/', 1)[1].split('/', 1)[0]
            label = _titleize_slug(slug)
            meta = 'Charge explainer'
            kind = 'Reference'
        elif path.startswith('/case-journeys/'):
            slug = path.split('/case-journeys/', 1)[1].split('/', 1)[0]
            label = _titleize_slug(slug)
            meta = 'Case journey'
            kind = 'Timeline'

        if not label:
            clean_path = path.strip('/') or 'home'
            label = _titleize_slug(clean_path)
            meta = 'Public page'

        viewed_pages.append({
            'href': path,
            'label': label,
            'meta': meta,
            'kind': kind,
            'view_count': row['view_count'],
        })
        if len(viewed_pages) >= limit:
            break

    return viewed_pages


def _homepage_newsroom_context(
    conn,
    *,
    county='',
    city='',
    agency_type='',
    agency='',
    search_query='',
    date_sql_val='',
    status_filter='',
):
    recent_records = _homepage_recent_records(
        conn,
        county=county,
        city=city,
        agency_type=agency_type,
        agency=agency,
        search_query=search_query,
        date_sql_val=date_sql_val,
        status_filter=status_filter,
        limit=10,
    )
    return {
        'featured_records': recent_records[:3],
        'live_activity': recent_records[:8],
        'ticker_records': recent_records[:6],
        # The weekly ranking query groups over a very large page_views table and
        # can dominate homepage latency. Keep the sidebar in its empty state
        # until this is replaced with pre-aggregated analytics.
        'most_viewed_pages': [],
    }


def _parse_record_date(value):
    for fmt in ('%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime((value or '').strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _weekly_snapshot(conn, window_days=7):
    raw_dates = {}
    for row in conn.execute(
        """
        SELECT DISTINCT date
        FROM records
        WHERE date IS NOT NULL AND TRIM(date) != ''
        """
    ).fetchall():
        normalized = _parse_record_date(row['date'])
        if normalized is None:
            continue
        raw_dates.setdefault(normalized, set()).add(row['date'])

    if not raw_dates:
        return None

    end_date = max(raw_dates)
    start_date = end_date - timedelta(days=window_days - 1)
    window_raw_dates = sorted(
        raw
        for normalized, originals in raw_dates.items()
        if start_date <= normalized <= end_date
        for raw in originals
    )
    if not window_raw_dates:
        return None

    placeholders = ','.join('?' for _ in window_raw_dates)
    total_records = conn.execute(
        f'SELECT COUNT(*) FROM records WHERE date IN ({placeholders})',
        window_raw_dates,
    ).fetchone()[0]

    top_counties = []
    for row in conn.execute(
        f"""
        SELECT COALESCE(NULLIF(county, ''), 'Unknown') AS county, COUNT(*) AS count
        FROM records
        WHERE date IN ({placeholders})
        GROUP BY COALESCE(NULLIF(county, ''), 'Unknown')
        ORDER BY count DESC, county ASC
        LIMIT 5
        """,
        window_raw_dates,
    ).fetchall():
        top_counties.append({
            'name': row['county'],
            'count': row['count'],
            'slug': _county_slug_for_name(row['county']),
        })

    top_incidents = [
        {'name': row['incident_type'] or 'Incident', 'count': row['count']}
        for row in conn.execute(
            f"""
            SELECT COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident') AS incident_type,
                   COUNT(*) AS count
            FROM records
            WHERE date IN ({placeholders})
            GROUP BY COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident')
            ORDER BY count DESC, incident_type ASC
            LIMIT 6
            """,
            window_raw_dates,
        ).fetchall()
    ]

    return {
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'total_records': total_records,
        'top_counties': top_counties,
        'top_incidents': top_incidents,
    }


def _human_join(items):
    values = [item for item in items if item]
    if not values:
        return ''
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f'{values[0]} and {values[1]}'
    return ', '.join(values[:-1]) + f', and {values[-1]}'


def _pretty_iso_date(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d')
    except (TypeError, ValueError):
        return value or ''
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _weekly_digest_workflow_defaults(conn):
    snapshot = _weekly_snapshot(conn)
    if not snapshot:
        return None

    top_counties = snapshot['top_counties'][:3]
    top_incidents = snapshot['top_incidents'][:3]
    county_names = [item['name'] for item in top_counties]
    county_phrase = _human_join(county_names) or 'Montana counties'
    start_label = _pretty_iso_date(snapshot['start_date'])
    end_label = _pretty_iso_date(snapshot['end_date'])
    lead_incident = top_incidents[0]['name'] if top_incidents else 'incident activity'

    title = (
        f"Montana Weekly Crime Roundup: {county_phrase} Led Activity Through {end_label}"
    )
    slug = f"montana-weekly-county-digest-{snapshot['end_date']}"
    excerpt = (
        f"Montana Blotter reviewed {snapshot['total_records']} public safety records "
        f"from {start_label} through {end_label}. {county_phrase} generated the most "
        f"visible county-level activity this week, with {lead_incident.lower()} appearing often."
    )

    county_lines = []
    for index, county in enumerate(top_counties, start=1):
        if county.get('slug'):
            county_lines.append(
                f"{index}. **[{county['name']} County](/county/{county['slug']})** led with "
                f"**{county['count']}** visible records."
            )
        else:
            county_lines.append(
                f"{index}. **{county['name']} County** led with **{county['count']}** visible records."
            )

    incident_lines = []
    for item in top_incidents:
        incident_lines.append(f"- **{item['name']}:** {item['count']} visible records")

    county_sections = []
    for county in top_counties:
        county_sections.append(
            "\n".join(
                [
                    f"### {county['name']} County",
                    "",
                    f"[Add 2-3 sentences on why {county['name']} County moved this week, "
                    f"what the archive showed, and the best next click for readers.]",
                    "",
                ]
            )
        )

    body_parts = [
        "## What stood out this week",
        "",
        (
            f"Montana Blotter reviewed **{snapshot['total_records']}** public safety records "
            f"indexed between **{start_label}** and **{end_label}**. "
            f"{county_phrase} generated the strongest county-level activity in this archive window."
        ),
        "",
        "## Top counties this week",
        "",
        *county_lines,
        "",
        "## Incident patterns that stood out",
        "",
        *incident_lines,
        "",
        "## County-by-county notes",
        "",
        "\n".join(county_sections) if county_sections else "[Add county notes here]",
        "",
        "## Best next clicks",
        "",
        "- [How to find Montana police blotters](/guides/how-to-find-montana-police-blotters)",
        "- [Yellowstone County arrests, jail roster, and warrant guide](/guides/yellowstone-county-arrests-jail-roster-and-warrant-guide)",
        "- [Montana county guides](/guides/montana-county-guides)",
        "- [Flathead County police blotter, warrant, and jail guide](/guides/flathead-county-police-blotter-warrant-and-jail-guide)",
        "- [How to find Montana warrants](/guides/how-to-find-montana-warrants)",
        "- [How to find Montana arrest records](/guides/how-to-find-montana-arrest-records)",
        "- [How to find Montana jail rosters](/guides/how-to-find-montana-jail-rosters)",
        "- [Montana annual roundups](/annual-roundups)",
        "",
        "## Methodology",
        "",
        (
            f"This roundup summarizes records indexed by Montana Blotter between "
            f"**{start_label}** and **{end_label}**. Counts reflect visible public records "
            f"in the archive during that window and may change as additional reports are ingested "
            f"or source data is corrected."
        ),
    ]

    title_options = [
        title,
        f"{county_phrase} Drove Montana Blotter Activity Through {end_label}",
        f"Montana Public Safety Roundup: {county_phrase} and {lead_incident} Through {end_label}",
    ]

    return {
        'title': title,
        'slug': slug,
        'excerpt': excerpt,
        'body': '\n'.join(body_parts).strip(),
        'author': 'Montana Blotter',
        'published': 0,
        'snapshot': snapshot,
        'top_counties': top_counties,
        'top_incidents': top_incidents,
        'title_options': title_options,
        'start_label': start_label,
        'end_label': end_label,
    }


def _search_console_header_key(value):
    return ' '.join((value or '').strip().lower().replace('_', ' ').split())


def _search_console_cell(row, aliases):
    for key, value in row.items():
        if _search_console_header_key(key) in aliases:
            return (value or '').strip()
    return ''


def _search_console_float(value):
    raw = (value or '').strip().replace(',', '')
    if not raw:
        return 0.0
    if raw.endswith('%'):
        raw = raw[:-1].strip()
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _normalize_search_console_page(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    parsed = urlparse(raw)
    path = parsed.path if (parsed.scheme or parsed.netloc) else raw
    if not path.startswith('/'):
        path = '/' + path
    if len(path) > 1 and path.endswith('/'):
        path = path[:-1]
    return path or '/'


def _parse_search_console_csv(file_storage):
    payload = file_storage.read()
    if not payload:
        raise ValueError('The uploaded Search Console CSV is empty.')

    text = None
    for encoding in ('utf-8-sig', 'utf-8', 'utf-16'):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError('The Search Console CSV could not be decoded as UTF-8 or UTF-16.')

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError('The Search Console CSV is missing headers.')

    query_aliases = {'query', 'queries', 'top query', 'top queries', 'search query', 'search queries'}
    page_aliases = {'page', 'pages', 'top page', 'top pages', 'landing page', 'landing pages'}
    clicks_aliases = {'clicks'}
    impressions_aliases = {'impressions'}
    ctr_aliases = {'ctr'}
    position_aliases = {'position', 'avg position', 'average position'}

    rows = []
    for row in reader:
        query = _search_console_cell(row, query_aliases)
        page = _normalize_search_console_page(_search_console_cell(row, page_aliases))
        if not query and not page:
            continue
        rows.append({
            'query': query,
            'page': page,
            'clicks': _search_console_float(_search_console_cell(row, clicks_aliases)),
            'impressions': _search_console_float(_search_console_cell(row, impressions_aliases)),
            'ctr': _search_console_float(_search_console_cell(row, ctr_aliases)),
            'position': _search_console_float(_search_console_cell(row, position_aliases)),
        })

    if not rows:
        raise ValueError('The Search Console CSV did not contain any query or page rows.')

    has_queries = any(row['query'] for row in rows)
    has_pages = any(row['page'] for row in rows)
    if has_queries and has_pages:
        source_kind = 'mixed'
    elif has_queries:
        source_kind = 'queries'
    elif has_pages:
        source_kind = 'pages'
    else:
        source_kind = 'unknown'

    return source_kind, rows


def _store_search_console_import(conn, source_filename, source_kind, rows):
    cursor = conn.execute(
        'INSERT INTO search_console_imports (source_filename, source_kind, row_count) VALUES (?, ?, ?)',
        (source_filename, source_kind, len(rows)),
    )
    import_id = cursor.lastrowid
    conn.executemany(
        '''
        INSERT INTO search_console_rows (import_id, query, page, clicks, impressions, ctr, position)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        [
            (
                import_id,
                row['query'],
                row['page'],
                row['clicks'],
                row['impressions'],
                row['ctr'],
                row['position'],
            )
            for row in rows
        ],
    )
    return import_id


def _latest_search_console_import(conn, kind=None):
    if kind == 'queries':
        kinds = ('queries', 'mixed')
    elif kind == 'pages':
        kinds = ('pages', 'mixed')
    else:
        kinds = None

    if kinds:
        placeholders = ','.join('?' for _ in kinds)
        return conn.execute(
            f'''
            SELECT id, source_filename, source_kind, row_count, created_at
            FROM search_console_imports
            WHERE source_kind IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            ''',
            kinds,
        ).fetchone()

    return conn.execute(
        '''
        SELECT id, source_filename, source_kind, row_count, created_at
        FROM search_console_imports
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        '''
    ).fetchone()


def _search_console_rows_for_import(conn, import_id):
    return conn.execute(
        '''
        SELECT query, page, clicks, impressions, ctr, position
        FROM search_console_rows
        WHERE import_id = ?
        ORDER BY impressions DESC, clicks DESC, position ASC, id ASC
        ''',
        (import_id,),
    ).fetchall()


def _search_console_query_intents(rows):
    buckets = []
    for row in rows:
        query = (row['query'] or '').lower()
        impressions = float(row['impressions'] or 0)
        if 'warrant' in query:
            buckets.append(('Warrant Search', impressions))
        if 'jail' in query or 'roster' in query or 'inmate' in query:
            buckets.append(('Jail Roster', impressions))
        if 'arrest' in query or 'booking' in query or 'bookings' in query:
            buckets.append(('Arrests', impressions))
        if 'blotter' in query or 'police' in query or 'daily activity' in query:
            buckets.append(('Police Blotter', impressions))

    ranked = {}
    for label, impressions in buckets:
        ranked[label] = ranked.get(label, 0.0) + impressions

    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    return [label for label, _score in ordered]


def _search_console_match_queries(path, rows):
    path = _normalize_search_console_page(path)
    direct = [
        row for row in rows
        if row['query'] and _normalize_search_console_page(row['page']) == path
    ]
    if direct:
        return direct

    fallback = []
    if path == '/':
        fallback = [row for row in rows if row['query']]
    elif path.startswith('/county/'):
        slug = path.split('/')[-1]
        county = COUNTY_DATA.get(slug)
        if county:
            terms = [county['name'].lower()]
            if county.get('seat'):
                terms.append(county['seat'].lower())
            fallback = [
                row for row in rows
                if row['query'] and any(term in row['query'].lower() for term in terms)
            ]
    elif path.startswith('/city/'):
        slug = path.split('/')[-1]
        city = CITY_DATA.get(slug)
        if city:
            terms = [city['name'].lower(), city['county'].lower()]
            fallback = [
                row for row in rows
                if row['query'] and any(term in row['query'].lower() for term in terms)
            ]
    elif path.startswith('/warrants/'):
        slug = path.split('/')[-1]
        county = COUNTY_DATA.get(slug)
        if county:
            terms = [county['name'].lower()]
            if county.get('seat'):
                terms.append(county['seat'].lower())
            fallback = [
                row for row in rows
                if row['query']
                and 'warrant' in row['query'].lower()
                and any(term in row['query'].lower() for term in terms)
            ]

    return fallback


def _search_console_suggest_snippet(path, matched_queries):
    intents = _search_console_query_intents(matched_queries)
    top_queries = [row['query'] for row in matched_queries[:3] if row['query']]

    if path == '/':
        title = 'Montana Police Blotter, Jail Rosters, Warrants, and Arrest Records'
        meta = (
            'Search Montana police blotter pages, jail rosters, warrant guides, and arrest records '
            'using county and city pages built around the queries readers actually use.'
        )
        return title, meta, top_queries

    if path.startswith('/county/'):
        slug = path.split('/')[-1]
        county = COUNTY_DATA.get(slug)
        if county:
            labels = intents[:3] or ['Arrests', 'Jail Roster', 'Police Blotter']
            title = f"{county['name']} County " + _human_join(labels)
            meta = (
                f"Search {county['name']} County public safety pages for "
                f"{', '.join(label.lower() for label in labels[:2])}"
                f"{', and ' + labels[2].lower() if len(labels) > 2 else ''}. "
                f"Built from live query demand and linked into local county resources."
            )
            return title, meta, top_queries

    if path.startswith('/city/'):
        slug = path.split('/')[-1]
        city = CITY_DATA.get(slug)
        if city:
            labels = intents[:3] or ['Police Blotter', 'Arrests', 'Warrant Links']
            title = f"{city['name']} " + _human_join(labels)
            meta = (
                f"Search {city['name']} police blotter coverage, {city['county']} County arrest resources, "
                f"and follow-up warrant or jail links aligned to the searches bringing readers to this page."
            )
            return title, meta, top_queries

    if path.startswith('/warrants/'):
        slug = path.split('/')[-1]
        county = COUNTY_DATA.get(slug)
        if county:
            title = f"{county['name']} County Warrant Search, Jail Roster, and Arrest Links"
            meta = (
                f"Search {county['name']} County warrants with county jail roster links, arrest follow-up paths, "
                f"and search-intent language pulled from recent Search Console exports."
            )
            return title, meta, top_queries

    return '', '', top_queries


def _search_console_workflow_context(conn):
    latest_query_import = _latest_search_console_import(conn, kind='queries')
    latest_page_import = _latest_search_console_import(conn, kind='pages')
    import_history = conn.execute(
        '''
        SELECT id, source_filename, source_kind, row_count, created_at
        FROM search_console_imports
        ORDER BY created_at DESC, id DESC
        LIMIT 6
        '''
    ).fetchall()

    query_rows = _search_console_rows_for_import(conn, latest_query_import['id']) if latest_query_import else []
    page_rows = _search_console_rows_for_import(conn, latest_page_import['id']) if latest_page_import else []

    top_queries = [dict(row) for row in query_rows[:10]]

    aggregated_pages = {}
    for row in page_rows:
        path = _normalize_search_console_page(row['page'])
        if not path:
            continue
        entry = aggregated_pages.setdefault(path, {
            'path': path,
            'clicks': 0.0,
            'impressions': 0.0,
            'ctr': 0.0,
            'position_weight': 0.0,
            'position_impressions': 0.0,
        })
        entry['clicks'] += float(row['clicks'] or 0)
        entry['impressions'] += float(row['impressions'] or 0)
        entry['ctr'] += float(row['ctr'] or 0)
        entry['position_weight'] += float(row['position'] or 0) * max(float(row['impressions'] or 0), 1.0)
        entry['position_impressions'] += max(float(row['impressions'] or 0), 1.0)

    page_opportunities = []
    for entry in sorted(aggregated_pages.values(), key=lambda item: (-item['impressions'], -item['clicks'], item['path']))[:10]:
        matched_queries = _search_console_match_queries(entry['path'], query_rows)[:5]
        suggested_title, suggested_meta, supporting_queries = _search_console_suggest_snippet(entry['path'], matched_queries)
        avg_position = (
            entry['position_weight'] / entry['position_impressions']
            if entry['position_impressions'] else 0.0
        )
        page_opportunities.append({
            'path': entry['path'],
            'clicks': round(entry['clicks'], 2),
            'impressions': round(entry['impressions'], 2),
            'avg_position': round(avg_position, 2),
            'suggested_title': suggested_title,
            'suggested_meta': suggested_meta,
            'supporting_queries': supporting_queries,
        })

    return {
        'latest_query_import': latest_query_import,
        'latest_page_import': latest_page_import,
        'import_history': import_history,
        'top_queries': top_queries,
        'page_opportunities': page_opportunities,
    }


def _meeting_documents_map(conn, meeting_ids):
    documents = defaultdict(list)
    if not meeting_ids:
        return documents

    placeholders = ','.join('?' for _ in meeting_ids)
    rows = conn.execute(
        f'''
        SELECT meeting_id, label, document_type, url, target_type
        FROM meeting_documents
        WHERE meeting_id IN ({placeholders})
        ORDER BY
            CASE document_type
                WHEN 'agenda' THEN 0
                WHEN 'packet' THEN 1
                WHEN 'minutes' THEN 2
                ELSE 3
            END,
            id
        ''',
        list(meeting_ids),
    ).fetchall()
    for row in rows:
        documents[row['meeting_id']].append(dict(row))
    return documents


def _public_meetings_dashboard_context():
    q = (request.args.get('q') or '').strip()
    county = (request.args.get('county') or '').strip()
    city = (request.args.get('city') or '').strip()
    conn = get_db()
    ensure_public_meeting_schema(conn)

    sql = '''
        SELECT
            public_meetings.*,
            meeting_sources.name AS source_name,
            meeting_sources.source_url AS source_listing_url,
            meeting_sources.last_success_at AS source_last_success_at,
            meeting_sources.last_scraped_at AS source_last_scraped_at,
            meeting_locations.display_name AS location_display_name,
            meeting_locations.location_type,
            meeting_locations.county_name,
            meeting_locations.city_name
        FROM public_meetings
        JOIN meeting_sources ON meeting_sources.id = public_meetings.source_id
        LEFT JOIN meeting_locations ON meeting_locations.id = public_meetings.location_id
        WHERE public_meetings.is_current = 1
          AND public_meetings.status = 'upcoming'
    '''
    params = []
    if q:
        token = f'%{q}%'
        sql += '''
          AND (
              public_meetings.title LIKE ?
              OR COALESCE(public_meetings.body_name, '') LIKE ?
              OR COALESCE(public_meetings.location_name, '') LIKE ?
              OR COALESCE(meeting_locations.county_name, '') LIKE ?
              OR COALESCE(meeting_locations.city_name, '') LIKE ?
          )
        '''
        params.extend([token, token, token, token, token])
    if county:
        sql += ' AND COALESCE(meeting_locations.county_name, "") = ?'
        params.append(county)
    if city:
        sql += ' AND COALESCE(meeting_locations.city_name, "") = ?'
        params.append(city)

    sql += '''
        ORDER BY
            CASE WHEN COALESCE(public_meetings.meeting_date, '') = '' THEN 1 ELSE 0 END,
            public_meetings.meeting_date ASC,
            public_meetings.meeting_time ASC,
            public_meetings.title ASC
        LIMIT 150
    '''
    meetings = conn.execute(sql, params).fetchall()
    if not meetings:
        fallback_sql = sql.replace("          AND public_meetings.status = 'upcoming'\n", '')
        meetings = conn.execute(fallback_sql, params).fetchall()
    documents_map = _meeting_documents_map(conn, [row['id'] for row in meetings])
    latest_sync_row = conn.execute(
        '''
        SELECT MAX(last_success_at) AS last_success_at
        FROM meeting_sources
        WHERE is_active = 1
        '''
    ).fetchone()
    counties = [
        row['county_name']
        for row in conn.execute(
            '''
            SELECT DISTINCT county_name
            FROM meeting_locations
            WHERE county_name IS NOT NULL AND county_name != ''
            ORDER BY county_name
            '''
        ).fetchall()
    ]
    cities = [
        row['city_name']
        for row in conn.execute(
            '''
            SELECT DISTINCT city_name
            FROM meeting_locations
            WHERE city_name IS NOT NULL AND city_name != ''
            ORDER BY city_name
            '''
        ).fetchall()
    ]
    source_count = conn.execute(
        'SELECT COUNT(*) AS cnt FROM meeting_sources WHERE is_active = 1'
    ).fetchone()['cnt']
    conn.close()

    meeting_cards = []
    for row in meetings:
        documents = documents_map.get(row['id'], [])
        primary_agenda = next((document for document in documents if document['document_type'] == 'agenda'), None)
        meeting_cards.append(
            {
                **dict(row),
                'documents': documents,
                'primary_agenda': primary_agenda,
                'has_documents': bool(documents),
            }
        )

    canonical_url = (getattr(config, 'AGENDAS_BASE_URL', '') or '').strip()
    if not canonical_url:
        canonical_url = f'{BASE_URL}/meetings'

    return {
        'meetings': meeting_cards,
        'meeting_count': len(meeting_cards),
        'counties': counties,
        'cities': cities,
        'selected_county': county,
        'selected_city': city,
        'q': q,
        'source_count': source_count,
        'latest_sync_at': latest_sync_row['last_success_at'] if latest_sync_row else None,
        'page_title': 'Public Meetings Dashboard',
        'meta_description': 'Search Montana city council and county commission agendas, minutes, packets, and upcoming public meeting schedules in one statewide dashboard.',
        'canonical_url': canonical_url.rstrip('/'),
        'og_title': 'Montana Public Meetings Dashboard',
        'og_description': 'Centralized Montana meeting agendas, minutes, and schedules for cities and counties across the state.',
        'active_nav': 'meetings',
    }


@app.context_processor
def inject_public_nav():
    home_href = BASE_URL if _is_agendas_host() else '/'
    public_user = _get_public_user()
    subscribe_variant = 'subscribe'
    try:
        seed = f"{_client_ip()}|{request.headers.get('User-Agent', '')}"
        subscribe_variant = 'alerts' if (int(hashlib.sha256(seed.encode()).hexdigest(), 16) % 2) else 'subscribe'
    except Exception:
        subscribe_variant = 'subscribe'
    subscribe_nav_label = 'Alerts' if subscribe_variant == 'alerts' else 'Subscribe'
    public_primary_nav_items = [
        {'id': 'home', 'href': home_href, 'label': 'Home'},
        {'id': 'meetings', 'href': _public_meetings_href(), 'label': 'Meetings'},
        {'id': 'courts', 'href': '/courts', 'label': 'Courts'},
        {'id': 'arrests', 'href': '/arrests', 'label': 'Arrests'},
        {'id': 'counties', 'href': '/counties', 'label': 'Counties'},
        {'id': 'jail_rosters', 'href': '/detention', 'label': 'Detention', 'menu_label': 'Jails'},
        {'id': 'bail_bonds', 'href': '/bail-bonds', 'label': 'Bail Bonds', 'menu_label': 'Bail'},
        {'id': 'missing_persons', 'href': '/missing-persons', 'label': 'Missing Persons', 'menu_label': 'Missing'},
        {'id': 'blog', 'href': '/blog', 'label': 'Blog'},
    ]
    public_secondary_nav_items = [
        {'id': 'case_journeys', 'href': '/case-journeys', 'label': 'Case Journeys', 'menu_label': 'Cases'},
        {'id': 'jail_bookings', 'href': '/jail-bookings', 'label': 'New Bookings', 'menu_label': 'Bookings'},
        {'id': 'support', 'href': '/support', 'label': 'Support'},
    ]
    if public_user and getattr(public_user, 'is_subscribed', False):
        public_secondary_nav_items.append(
            {'id': 'bondsman_command_center', 'href': '/bondsman/command-center', 'label': 'Command Center', 'menu_label': 'Command'}
        )
    nav_items_by_id = {
        item['id']: item
        for item in [*public_primary_nav_items, *public_secondary_nav_items]
    }
    public_nav_menu_labels_by_href = {
        item['href']: item.get('menu_label') or item['label']
        for item in [*public_primary_nav_items, *public_secondary_nav_items]
    }
    public_nav_full_labels_by_href = {
        item['href']: item['label']
        for item in [*public_primary_nav_items, *public_secondary_nav_items]
    }
    mobile_legend_ids = ('bail_bonds', 'case_journeys', 'missing_persons')
    public_mobile_short_label_legend = ' · '.join(
        f"{nav_items_by_id[item_id]['menu_label']} = {nav_items_by_id[item_id]['label']}"
        for item_id in mobile_legend_ids
        if item_id in nav_items_by_id and nav_items_by_id[item_id].get('menu_label')
    )
    public_action_labels = {
        'subscribe': subscribe_nav_label,
        'subscribe_full': 'Subscribe',
        'account': 'Account',
        'sign_in': 'Sign In',
        'join': 'Join',
        'sign_out': 'Sign Out',
        'terms': 'Terms',
        'more': 'More',
    }
    public_footer_items = [
        {'href': home_href, 'label': 'Home'},
        {'href': _public_meetings_href(), 'label': 'Meetings'},
        {'href': '/courts', 'label': 'Courts'},
        {'href': '/case-journeys', 'label': 'Case Journeys'},
        {'href': '/missing-persons', 'label': 'Missing Persons'},
        {'href': '/arrests', 'label': 'Arrests'},
        {'href': '/counties', 'label': 'Counties'},
        {'href': '/jail-rosters', 'label': 'Jail Rosters'},
        {'href': '/bail-bonds', 'label': 'Bail Bonds'},
        {'href': '/jail-bookings', 'label': 'New Bookings'},
        {'href': '/bondsman/command-center', 'label': 'Command Center'} if public_user and getattr(public_user, 'is_subscribed', False) else None,
        {'href': '/support', 'label': 'Support'},
        {'href': '/advertise/bail-bonds', 'label': 'Advertise'},
        {'href': '/recovery-centers', 'label': 'Recovery Centers'},
        {'href': '/subscribe', 'label': 'Subscribe'},
        {'href': '#modal-standards', 'label': 'Standards'},
        {'href': '#modal-corrections', 'label': 'Corrections'},
        {'href': '/terms-of-use', 'label': 'Terms'},
        {'href': '/privacy', 'label': 'Privacy'},
    ]
    footer_featured_city_items = []
    return {
        'public_primary_nav_items': public_primary_nav_items,
        'public_secondary_nav_items': public_secondary_nav_items,
        'public_nav_menu_labels_by_href': public_nav_menu_labels_by_href,
        'public_nav_full_labels_by_href': public_nav_full_labels_by_href,
        'public_mobile_short_label_legend': public_mobile_short_label_legend,
        'public_action_labels': public_action_labels,
        'public_nav_experiment': {'subscribe_variant': subscribe_variant},
        'public_footer_items': [item for item in public_footer_items if item],
        'footer_featured_city_items': footer_featured_city_items,
        'current_year': datetime.now().year,
        'public_user': public_user,
        'winter_storm_banner': _winter_storm_banner_config(),
    }

class User(UserMixin):
    def __init__(self, id, username, role='super_admin', email=None, account_active=True, last_login_at=None, mfa_enabled=False):
        self.id = id
        self.username = username
        self.role = (role or 'super_admin').strip() or 'super_admin'
        self.email = email or ''
        self.account_active = bool(account_active)
        self.last_login_at = last_login_at
        self.mfa_enabled = bool(mfa_enabled)

    @property
    def is_active(self):
        return self.account_active

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role.replace('_', ' ').title())

    @property
    def can_access_admin(self):
        return self.account_active and self.role in ADMIN_ACCESS_ROLES

    @classmethod
    def from_row(cls, row):
        return cls(
            row['id'],
            row['username'],
            role=row['role'] if 'role' in row.keys() else 'super_admin',
            email=row['email'] if 'email' in row.keys() else None,
            account_active=bool(row['is_active']) if 'is_active' in row.keys() else True,
            last_login_at=row['last_login_at'] if 'last_login_at' in row.keys() else None,
            mfa_enabled=bool(row['mfa_enabled']) if 'mfa_enabled' in row.keys() else False,
        )

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    res = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    if res and bool(res['is_active']) and (res['role'] or '').strip() in ADMIN_ACCESS_ROLES:
        return User.from_row(res)
    return None


def _log_admin_action(action: str, target_type: str = '', target_id=None, metadata=None, user_id=None, conn=None):
    own_conn = conn is None
    if own_conn:
        conn = get_db()

    try:
        actor_id = user_id
        if actor_id is None and getattr(current_user, 'is_authenticated', False):
            actor_id = current_user.id
        conn.execute(
            '''
            INSERT INTO audit_logs (user_id, action, target_type, target_id, ip_address, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                actor_id,
                (action or '').strip()[:120],
                (target_type or '').strip()[:80] or None,
                str(target_id)[:120] if target_id is not None else None,
                _client_ip()[:128] if has_request_context() else None,
                json.dumps(metadata or {}, sort_keys=True)[:4000] if metadata is not None else None,
            ),
        )
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


# ==========================================
# PAGE VIEW TRACKING
# ==========================================

@app.before_request
def track_page_view():
    """Log public page views for visitor analytics (admin routes and static files excluded)."""
    if request.path.startswith('/admin') or request.path.startswith('/static'):
        return
    if request.path in (
        '/api/pattern-click',
        '/api/subscribe-event',
        '/api/donate-event',
        '/api/donate/create-checkout-session',
        '/webhooks/stripe',
        '/favicon.ico',
        '/feed.xml',
        '/robots.txt',
        '/sitemap.xml',
        '/sitemap-static.xml',
        '/sitemap-locations.xml',
        '/sitemap-posts.xml',
        '/sitemap-blog.xml',
        '/sitemap-charges.xml',
    ):
        return
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]
    conn = None
    try:
        # page_views lives in a separate local SQLite database to avoid
        # bloating the Turso-synced main database.
        conn = connect_page_views(row_factory=None)
        conn.execute(
            'INSERT INTO page_views (path, ip_hash, referrer) VALUES (?, ?, ?)',
            (request.path, ip_hash, referrer)
        )
        conn.commit()
    except (sqlite3.Error, Exception):
        pass  # Never break the site for analytics
    finally:
        if conn is not None:
            try:
                conn.close()
            except (sqlite3.Error, Exception):
                pass


def _record_subscribe_event(event_type, source='', page_path='', email=''):
    event_type = (event_type or '').strip()[:40]
    if not event_type:
        return
    safe_source = (source or '').strip()[:80]
    safe_path = (page_path or request.path or '')[:255]
    referrer = (request.referrer or '')[:500]
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    email_hash = ''
    if email:
        email_hash = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO subscribe_events (
                event_type, source, page_path, ip_hash, referrer, email_hash
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (event_type, safe_source, safe_path, ip_hash, referrer, email_hash),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _subscriber_email_hash(email):
    normalized = (email or '').strip().lower()
    if not normalized:
        return ''
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _table_columns(conn, table_name):
    try:
        return {row[1] for row in conn.execute(f'PRAGMA table_info({table_name})').fetchall()}
    except sqlite3.OperationalError:
        return set()


def _ensure_subscriber_schema(conn):
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            counties TEXT DEFAULT '',
            token TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            source TEXT,
            notes TEXT
        )
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_subscribers_active ON subscribers(active)')
    existing_columns = _table_columns(conn, 'subscribers')
    for col, definition in [
        ('updated_at', 'TEXT'),
        ('source', 'TEXT'),
        ('notes', 'TEXT'),
        ('phone', 'TEXT'),
        ('agency_name', "TEXT NOT NULL DEFAULT ''"),
        ('phone_number_sms', "TEXT NOT NULL DEFAULT ''"),
        ('counties_of_interest', "TEXT NOT NULL DEFAULT '[]'"),
        ('charge_types_of_interest', "TEXT NOT NULL DEFAULT '[\"all\"]'"),
        ('subscription_status', "TEXT NOT NULL DEFAULT 'active'"),
        ('wants_notifications', 'INTEGER NOT NULL DEFAULT 0'),
        ('notification_channels', "TEXT NOT NULL DEFAULT 'email'"),
    ]:
        if col not in existing_columns:
            conn.execute(f'ALTER TABLE subscribers ADD COLUMN {col} {definition}')
            existing_columns.add(col)
    if 'updated_at' in existing_columns:
        conn.execute(
            '''
            UPDATE subscribers
            SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at, datetime('now'))
            WHERE updated_at IS NULL OR updated_at = ''
            '''
        )

    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS subscribe_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            source TEXT,
            page_path TEXT,
            ip_hash TEXT,
            referrer TEXT,
            email_hash TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        '''
    )
    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_subscribers_bail_bonds_active
        ON subscribers(subscription_status, active)
        '''
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_created ON subscribe_events(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_type ON subscribe_events(event_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_subscribe_events_source ON subscribe_events(source)')


def _digest_target_date(raw_value=''):
    raw_value = (raw_value or '').strip()
    if not raw_value:
        return (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    try:
        return datetime.strptime(raw_value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except ValueError as exc:
        raise ValueError('Choose a valid digest date in YYYY-MM-DD format.') from exc


def _digest_support_email():
    for candidate in (
        getattr(current_user, 'email', ''),
        getattr(config, 'SMTP_USER', ''),
        getattr(config, 'EMAIL_USER', ''),
    ):
        email = _normalize_email_ops_recipient(candidate)
        if email:
            return email
    return ''


def _normalize_email_ops_recipient(raw_value):
    email = (raw_value or '').strip().lower()
    if email and '@' in email:
        return email[:160]
    return ''


def _record_digest_run(
    conn,
    *,
    kind,
    target_date,
    audience,
    status,
    subject,
    preview_posts=0,
    preview_subscribers=0,
    initiated_by='',
    notes='',
    created_by_user_id=None,
):
    cursor = conn.execute(
        '''
        INSERT INTO digest_runs (
            kind, target_date, audience, status, subject,
            preview_posts, preview_subscribers, initiated_by, notes,
            created_by_user_id, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''',
        (
            (kind or '').strip()[:40] or 'morning_briefing',
            target_date,
            (audience or '').strip()[:40] or 'subscribers',
            (status or '').strip()[:40] or 'pending',
            (subject or '').strip()[:255],
            int(preview_posts or 0),
            int(preview_subscribers or 0),
            (initiated_by or '').strip()[:120] or None,
            (notes or '').strip()[:500] or None,
            created_by_user_id,
        ),
    )
    return cursor.lastrowid


def _record_digest_run_recipient(conn, run_id, recipient_email, counties, status, post_count=0, error_message=''):
    conn.execute(
        '''
        INSERT INTO digest_run_recipients (
            run_id, recipient_email, counties, status, post_count, error_message
        ) VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (
            run_id,
            (recipient_email or '').strip().lower(),
            (counties or '').strip()[:255],
            (status or '').strip()[:40] or 'pending',
            int(post_count or 0),
            (error_message or '').strip()[:500] or None,
        ),
    )


def _finish_digest_run(conn, run_id, *, status, sent_count=0, skipped_count=0, failed_count=0, notes=''):
    conn.execute(
        '''
        UPDATE digest_runs
        SET status = ?,
            sent_count = ?,
            skipped_count = ?,
            failed_count = ?,
            notes = ?,
            finished_at = datetime('now')
        WHERE id = ?
        ''',
        (
            (status or '').strip()[:40] or 'completed',
            int(sent_count or 0),
            int(skipped_count or 0),
            int(failed_count or 0),
            (notes or '').strip()[:500] or None,
            run_id,
        ),
    )


def _subscriber_counties_list(raw_value):
    return [part.strip() for part in (raw_value or '').split(',') if part.strip()]


def _subscriber_counties_label(raw_value):
    counties = _subscriber_counties_list(raw_value)
    return ', '.join(counties) if counties else 'All counties'


def _digest_subject_for_date(target_date):
    try:
        display = datetime.strptime(target_date, '%Y-%m-%d').strftime('%b %d, %Y')
    except ValueError:
        display = target_date
    return f'Montana Blotter Briefing - {display}'


def _build_email_ops_preview(target_date, selected_run_id=None):
    all_posts = get_morning_briefing_posts(target_date)
    preview_html = build_morning_briefing_html(all_posts, target_date) if all_posts else ''

    conn = get_db()
    _ensure_subscriber_schema(conn)
    subscribers = conn.execute(
        '''
        SELECT id, email, counties, created_at
        FROM subscribers
        WHERE active = 1
        ORDER BY datetime(created_at) DESC, email ASC
        '''
    ).fetchall()

    preview_rows = []
    matching_subscribers = 0
    skipped_subscribers = 0
    posts_cache = {'': all_posts}
    for subscriber in subscribers:
        counties_raw = subscriber['counties'] or ''
        if counties_raw not in posts_cache:
            posts_cache[counties_raw] = get_morning_briefing_posts(
                target_date,
                _subscriber_counties_list(counties_raw) or None,
            )
        post_count = len(posts_cache[counties_raw])
        if post_count:
            matching_subscribers += 1
        else:
            skipped_subscribers += 1
        preview_rows.append({
            'id': subscriber['id'],
            'email': subscriber['email'],
            'counties': counties_raw,
            'counties_label': _subscriber_counties_label(counties_raw),
            'post_count': post_count,
            'created_at': subscriber['created_at'],
        })

    stats = {
        'active_subscribers': conn.execute(
            'SELECT COUNT(*) AS total FROM subscribers WHERE active = 1'
        ).fetchone()['total'],
        'new_subscribers_7d': conn.execute(
            "SELECT COUNT(*) AS total FROM subscribers WHERE datetime(created_at) >= datetime('now', '-7 days')"
        ).fetchone()['total'],
        'unsubscribe_events_30d': conn.execute(
            "SELECT COUNT(*) AS total FROM subscribe_events WHERE event_type = 'unsubscribe' AND datetime(created_at) >= datetime('now', '-30 days')"
        ).fetchone()['total'],
        'subscription_events_30d': conn.execute(
            "SELECT COUNT(*) AS total FROM subscribe_events WHERE event_type IN ('subscribe_success', 'subscribe_update') AND datetime(created_at) >= datetime('now', '-30 days')"
        ).fetchone()['total'],
        'failed_deliveries_30d': conn.execute(
            "SELECT COUNT(*) AS total FROM digest_run_recipients WHERE status = 'failed' AND datetime(created_at) >= datetime('now', '-30 days')"
        ).fetchone()['total'],
    }
    recent_runs = conn.execute(
        '''
        SELECT
            dr.id,
            dr.kind,
            dr.target_date,
            dr.audience,
            dr.status,
            dr.subject,
            dr.preview_posts,
            dr.preview_subscribers,
            dr.sent_count,
            dr.skipped_count,
            dr.failed_count,
            dr.initiated_by,
            dr.created_at,
            u.username AS created_by_username
        FROM digest_runs dr
        LEFT JOIN users u ON u.id = dr.created_by_user_id
        ORDER BY datetime(dr.created_at) DESC, dr.id DESC
        LIMIT 20
        '''
    ).fetchall()
    recent_events = conn.execute(
        '''
        SELECT event_type, source, page_path, created_at
        FROM subscribe_events
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 30
        '''
    ).fetchall()
    selected_run = None
    selected_run_recipients = []
    if selected_run_id:
        selected_run = conn.execute(
            '''
            SELECT
                dr.id,
                dr.kind,
                dr.target_date,
                dr.audience,
                dr.status,
                dr.subject,
                dr.preview_posts,
                dr.preview_subscribers,
                dr.sent_count,
                dr.skipped_count,
                dr.failed_count,
                dr.initiated_by,
                dr.notes,
                dr.started_at,
                dr.finished_at,
                dr.created_at,
                u.username AS created_by_username
            FROM digest_runs dr
            LEFT JOIN users u ON u.id = dr.created_by_user_id
            WHERE dr.id = ?
            ''',
            (int(selected_run_id),),
        ).fetchone()
        if selected_run:
            selected_run_recipients = conn.execute(
                '''
                SELECT recipient_email, counties, status, post_count, error_message, created_at
                FROM digest_run_recipients
                WHERE run_id = ?
                ORDER BY
                    CASE status
                        WHEN 'failed' THEN 0
                        WHEN 'skipped' THEN 1
                        ELSE 2
                    END,
                    datetime(created_at) DESC,
                    id DESC
                LIMIT 150
                ''',
                (int(selected_run_id),),
            ).fetchall()
    recent_failures = conn.execute(
        '''
        SELECT
            drr.run_id,
            drr.recipient_email,
            drr.counties,
            drr.post_count,
            drr.error_message,
            drr.created_at,
            dr.target_date,
            dr.audience,
            dr.status AS run_status
        FROM digest_run_recipients drr
        JOIN digest_runs dr ON dr.id = drr.run_id
        WHERE drr.status = 'failed'
        ORDER BY datetime(drr.created_at) DESC, drr.id DESC
        LIMIT 40
        '''
    ).fetchall()
    conn.close()

    unsubscribe_rate = 0.0
    if stats['subscription_events_30d']:
        unsubscribe_rate = (
            stats['unsubscribe_events_30d'] / stats['subscription_events_30d'] * 100.0
        )

    return {
        'target_date': target_date,
        'subject': _digest_subject_for_date(target_date),
        'all_posts': all_posts,
        'preview_html': preview_html,
        'preview_rows': preview_rows[:30],
        'matching_subscribers': matching_subscribers,
        'skipped_subscribers': skipped_subscribers,
        'stats': stats,
        'unsubscribe_rate_30d': unsubscribe_rate,
        'recent_runs': recent_runs,
        'recent_events': recent_events,
        'selected_run': selected_run,
        'selected_run_recipients': selected_run_recipients,
        'recent_failures': recent_failures,
    }


def _subscriber_admin_context(conn, q='', status_filter='active', county_filter='', source_filter=''):
    _ensure_subscriber_schema(conn)
    q = (q or '').strip()[:120]
    status_filter = (status_filter or 'active').strip().lower()
    if status_filter not in {'active', 'inactive', 'all'}:
        status_filter = 'active'
    county_filter = (county_filter or '').strip()[:80]
    source_filter = (source_filter or '').strip()[:80]

    subscriber_rows = conn.execute(
        '''
        SELECT
            s.id,
            s.email,
            s.counties,
            COALESCE(s.active, 1) AS active,
            s.created_at,
            COALESCE(s.updated_at, s.created_at) AS updated_at,
            COALESCE(NULLIF(s.source, ''), '(unknown)') AS source,
            COALESCE(s.notes, '') AS notes
        FROM subscribers s
        ORDER BY datetime(COALESCE(s.updated_at, s.created_at)) DESC, s.email ASC
        '''
    ).fetchall()

    rows = []
    counties_seen = set()
    sources_seen = set()
    for subscriber in subscriber_rows:
        email_hash = _subscriber_email_hash(subscriber['email'])
        last_event = conn.execute(
            '''
            SELECT event_type, source, created_at
            FROM subscribe_events
            WHERE email_hash = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 1
            ''',
            (email_hash,),
        ).fetchone()
        item = dict(subscriber)
        item['last_event_type'] = last_event['event_type'] if last_event else None
        item['last_event_source'] = last_event['source'] if last_event else None
        item['last_event_at'] = last_event['created_at'] if last_event else None
        item['counties_label'] = _subscriber_counties_label(item['counties'])
        item['county_list'] = _subscriber_counties_list(item['counties'])
        rows.append(item)
        for county in item['county_list']:
            counties_seen.add(county)
        sources_seen.add(item['source'])

    filtered = []
    for row in rows:
        if q:
            haystack = ' '.join([
                row['email'] or '',
                row['source'] or '',
                row['last_event_type'] or '',
                row['last_event_source'] or '',
                row['counties_label'] or '',
                row['notes'] or '',
            ]).lower()
            if q.lower() not in haystack:
                continue
        if status_filter == 'active' and not row['active']:
            continue
        if status_filter == 'inactive' and row['active']:
            continue
        if county_filter:
            if row['county_list'] and county_filter not in row['county_list']:
                continue
        if source_filter and row['source'] != source_filter:
            continue
        filtered.append(row)

    summary = {
        'total': len(rows),
        'active': sum(1 for row in rows if row['active']),
        'inactive': sum(1 for row in rows if not row['active']),
        'all_counties': sum(1 for row in rows if not row['counties']),
    }
    return {
        'rows': filtered,
        'summary': summary,
        'counties': sorted(counties_seen),
        'sources': sorted(source for source in sources_seen if source),
        'q': q,
        'status_filter': status_filter,
        'county_filter': county_filter,
        'source_filter': source_filter,
    }


def _analytics_hub_context(conn, date_from='', date_to='', path_prefix=''):
    import logging
    log = logging.getLogger(__name__)

    date_from = (date_from or '').strip()
    date_to = (date_to or '').strip()
    path_prefix = (path_prefix or '').strip()[:120]

    # --- Build WHERE clauses ---
    # Use raw created_at comparisons (no date() wrapper) so indexes work.
    # Default to 7 days for page_views (table is very large) and 30 days for records.
    date_where = []
    date_params = []
    if date_from:
        date_where.append("created_at >= ?")
        date_params.append(date_from)
    if date_to:
        date_where.append("created_at < date(?, '+1 day')")
        date_params.append(date_to)

    record_where_sql = f"WHERE {' AND '.join(date_where)}" if date_where else "WHERE created_at >= date('now', '-30 days')"
    page_view_where = list(date_where)
    page_view_params = list(date_params)
    if not date_where:
        page_view_where.append("created_at >= date('now', '-7 days')")
    if path_prefix:
        page_view_where.append("path LIKE ?")
        page_view_params.append(f'{path_prefix}%')
    page_view_where_sql = f"WHERE {' AND '.join(page_view_where)}"

    # --- Content queries (records/posts/blotters — small tables, always safe) ---
    daily_rows = conn.execute(
        f'''
        SELECT date(created_at) AS day, COUNT(*) AS cnt
        FROM records
        {record_where_sql}
        GROUP BY day ORDER BY day
        ''',
        date_params,
    ).fetchall()
    type_rows = conn.execute(
        f'''
        SELECT COALESCE(NULLIF(incident_type, ''), 'Unknown') AS itype, COUNT(*) AS cnt
        FROM records
        {record_where_sql}
        GROUP BY itype ORDER BY cnt DESC LIMIT 10
        ''',
        date_params,
    ).fetchall()
    agency_rows = conn.execute(
        '''
        SELECT COALESCE(NULLIF(agency_type, ''), 'Other') AS atype, COUNT(*) AS cnt
        FROM posts
        GROUP BY atype
        ORDER BY cnt DESC, atype ASC
        ''',
    ).fetchall()
    blotter_rows = conn.execute(
        '''
        SELECT strftime('%Y-%m', upload_date) AS mo, COUNT(*) AS cnt
        FROM blotters
        GROUP BY mo
        ORDER BY mo DESC
        LIMIT 12
        '''
    ).fetchall()

    # --- Traffic queries (page_views — large table, wrapped in try/except) ---
    page_daily_rows = []
    top_pages = []
    top_referrers = []
    total_page_views = 0
    unique_visitors = 0
    try:
        pv_conn = connect_page_views()
        total_page_views = pv_conn.execute(
            f'SELECT COUNT(*) FROM page_views {page_view_where_sql}',
            page_view_params,
        ).fetchone()[0]
        page_daily_rows = pv_conn.execute(
            f'''
            SELECT date(created_at) AS day, COUNT(*) AS cnt
            FROM page_views
            {page_view_where_sql}
            GROUP BY day ORDER BY day
            ''',
            page_view_params,
        ).fetchall()
        top_pages = pv_conn.execute(
            f'''
            SELECT path, COUNT(*) AS cnt
            FROM page_views INDEXED BY idx_page_views_created_path
            {page_view_where_sql}
            GROUP BY path ORDER BY cnt DESC, path ASC LIMIT 12
            ''',
            page_view_params,
        ).fetchall()
        top_referrers = pv_conn.execute(
            f'''
            SELECT referrer, COUNT(*) AS cnt
            FROM page_views INDEXED BY idx_page_views_created_referrer
            {page_view_where_sql} AND referrer != ''
            GROUP BY referrer ORDER BY cnt DESC, referrer ASC LIMIT 12
            ''',
            page_view_params,
        ).fetchall()
        unique_visitors = pv_conn.execute(
            f"SELECT COUNT(DISTINCT ip_hash) FROM page_views INDEXED BY idx_page_views_created_ip {page_view_where_sql}",
            page_view_params,
        ).fetchone()[0]
        pv_conn.close()
    except Exception as e:
        log.error("Analytics traffic query failed: %s", e)

    # --- Pattern queries ---
    pattern_rows = conn.execute(
        '''
        SELECT placement, COUNT(*) AS cnt
        FROM pattern_clicks
        WHERE created_at >= date('now', '-30 days')
        GROUP BY placement
        ORDER BY cnt DESC, placement ASC
        ''',
    ).fetchall()
    try:
        pv_conn2 = connect_page_views()
        homepage_page_views_30d = pv_conn2.execute(
            "SELECT COUNT(*) FROM page_views WHERE path = '/' AND created_at >= date('now', '-7 days')"
        ).fetchone()[0]
        pv_conn2.close()
    except Exception:
        homepage_page_views_30d = 0
    homepage_pattern_clicks_30d = conn.execute(
        "SELECT COUNT(*) FROM pattern_clicks WHERE placement = 'homepage_pattern_promos' AND created_at >= date('now', '-7 days')"
    ).fetchone()[0]

    return {
        'filters': {
            'date_from': date_from,
            'date_to': date_to,
            'path_prefix': path_prefix,
        },
        'traffic': {
            'total_page_views': total_page_views,
            'unique_visitors': unique_visitors,
            'daily_labels': [row['day'] for row in page_daily_rows],
            'daily_counts': [row['cnt'] for row in page_daily_rows],
            'top_pages': top_pages,
            'top_referrers': top_referrers,
        },
        'content': {
            'daily_labels': [row['day'] for row in daily_rows],
            'daily_counts': [row['cnt'] for row in daily_rows],
            'type_labels': [row['itype'] for row in type_rows],
            'type_counts': [row['cnt'] for row in type_rows],
            'type_rows': [{'label': row['itype'], 'count': row['cnt']} for row in type_rows],
            'agency_labels': [row['atype'].title() for row in agency_rows],
            'agency_counts': [row['cnt'] for row in agency_rows],
            'agency_rows': [{'label': row['atype'].title(), 'count': row['cnt']} for row in agency_rows],
            'blotter_labels': [row['mo'] for row in reversed(blotter_rows)],
            'blotter_counts': [row['cnt'] for row in reversed(blotter_rows)],
            'blotter_rows': [{'label': row['mo'], 'count': row['cnt']} for row in reversed(blotter_rows)],
        },
        'patterns': {
            'labels': [row['placement'].replace('_', ' ').title() for row in pattern_rows],
            'counts': [row['cnt'] for row in pattern_rows],
            'rows': [{'label': row['placement'].replace('_', ' ').title(), 'count': row['cnt']} for row in pattern_rows],
            'homepage_ctr': (homepage_pattern_clicks_30d / homepage_page_views_30d * 100.0) if homepage_page_views_30d else 0.0,
            'homepage_clicks_30d': homepage_pattern_clicks_30d,
            'homepage_views_30d': homepage_page_views_30d,
        },
    }


def _send_test_digest(target_date, recipient_email, initiated_by):
    if not recipient_email:
        raise ValueError('No valid test recipient email is configured for this admin account.')

    preview = _build_email_ops_preview(target_date)
    if not preview['all_posts']:
        raise ValueError(f'No posts are available for {target_date}.')

    conn = get_db()
    run_id = _record_digest_run(
        conn,
        kind='morning_briefing',
        target_date=target_date,
        audience='test',
        status='running',
        subject=f"[TEST] {preview['subject']}",
        preview_posts=len(preview['all_posts']),
        preview_subscribers=1,
        initiated_by=initiated_by,
        notes='Manual test send from admin email ops.',
        created_by_user_id=current_user.id if current_user.is_authenticated else None,
    )
    conn.commit()
    try:
        send_morning_briefing_email(
            recipient_email,
            f"[TEST] {preview['subject']}",
            preview['preview_html'],
        )
        _record_digest_run_recipient(
            conn,
            run_id,
            recipient_email,
            '',
            'sent',
            post_count=len(preview['all_posts']),
        )
        _finish_digest_run(conn, run_id, status='completed', sent_count=1)
        _log_admin_action(
            'email_ops.test_sent',
            target_type='digest_run',
            target_id=run_id,
            metadata={'target_date': target_date, 'recipient': recipient_email},
            conn=conn,
        )
        conn.commit()
    except Exception as exc:
        _record_digest_run_recipient(
            conn,
            run_id,
            recipient_email,
            '',
            'failed',
            post_count=len(preview['all_posts']),
            error_message=str(exc),
        )
        _finish_digest_run(conn, run_id, status='failed', failed_count=1, notes=str(exc))
        conn.commit()
        conn.close()
        raise
    conn.close()


def _send_digest_to_active_subscribers(target_date, initiated_by):
    preview = _build_email_ops_preview(target_date)
    if not preview['all_posts']:
        raise ValueError(f'No posts are available for {target_date}.')

    conn = get_db()
    subscribers = conn.execute(
        '''
        SELECT email, counties, token
        FROM subscribers
        WHERE active = 1
        ORDER BY datetime(created_at) DESC, email ASC
        '''
    ).fetchall()
    run_id = _record_digest_run(
        conn,
        kind='morning_briefing',
        target_date=target_date,
        audience='subscribers',
        status='running',
        subject=preview['subject'],
        preview_posts=len(preview['all_posts']),
        preview_subscribers=len(subscribers),
        initiated_by=initiated_by,
        notes='Manual subscriber send from admin email ops.',
        created_by_user_id=current_user.id if current_user.is_authenticated else None,
    )
    conn.commit()

    sent_count = 0
    skipped_count = 0
    failed_count = 0
    posts_cache = {'': preview['all_posts']}
    try:
        for subscriber in subscribers:
            counties_raw = subscriber['counties'] or ''
            if counties_raw not in posts_cache:
                posts_cache[counties_raw] = get_morning_briefing_posts(
                    target_date,
                    _subscriber_counties_list(counties_raw) or None,
                )
            subscriber_posts = posts_cache[counties_raw]
            if not subscriber_posts:
                skipped_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    subscriber['email'],
                    counties_raw,
                    'skipped',
                    post_count=0,
                    error_message='No matching posts for subscriber counties.',
                )
                continue

            unsubscribe_url = f"{BASE_URL}/unsubscribe?token={subscriber['token']}"
            html = build_morning_briefing_html(
                subscriber_posts,
                target_date,
                unsubscribe_url=unsubscribe_url,
            )
            try:
                send_morning_briefing_email(subscriber['email'], preview['subject'], html)
                sent_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    subscriber['email'],
                    counties_raw,
                    'sent',
                    post_count=len(subscriber_posts),
                )
            except Exception as exc:
                failed_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    subscriber['email'],
                    counties_raw,
                    'failed',
                    post_count=len(subscriber_posts),
                    error_message=str(exc),
                )

        final_status = 'completed'
        if failed_count and sent_count:
            final_status = 'completed_with_errors'
        elif failed_count and not sent_count:
            final_status = 'failed'
        _finish_digest_run(
            conn,
            run_id,
            status=final_status,
            sent_count=sent_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
        )
        _log_admin_action(
            'email_ops.send_now',
            target_type='digest_run',
            target_id=run_id,
            metadata={
                'target_date': target_date,
                'sent_count': sent_count,
                'skipped_count': skipped_count,
                'failed_count': failed_count,
            },
            conn=conn,
        )
        conn.commit()
    except Exception:
        _finish_digest_run(conn, run_id, status='failed', failed_count=failed_count, notes='Unexpected send error.')
        conn.commit()
        conn.close()
        raise
    conn.close()
    return {'sent_count': sent_count, 'skipped_count': skipped_count, 'failed_count': failed_count}


def _retry_failed_digest_recipients(original_run_id, initiated_by):
    conn = get_db()
    original_run = conn.execute(
        '''
        SELECT id, target_date, subject, failed_count
        FROM digest_runs
        WHERE id = ?
        ''',
        (int(original_run_id),),
    ).fetchone()
    if not original_run:
        conn.close()
        raise ValueError('Digest run not found.')
    if int(original_run['failed_count'] or 0) <= 0:
        conn.close()
        raise ValueError('This digest run has no failed recipients to retry.')

    failed_rows = conn.execute(
        '''
        SELECT DISTINCT lower(recipient_email) AS recipient_email
        FROM digest_run_recipients
        WHERE run_id = ? AND status = 'failed'
        ORDER BY lower(recipient_email) ASC
        ''',
        (int(original_run_id),),
    ).fetchall()
    if not failed_rows:
        conn.close()
        raise ValueError('No failed recipients were recorded for this digest run.')

    preview = _build_email_ops_preview(original_run['target_date'])
    if not preview['all_posts']:
        conn.close()
        raise ValueError(f"No posts are available for {original_run['target_date']}.")

    target_emails = [row['recipient_email'] for row in failed_rows if row['recipient_email']]
    subscribers = []
    if target_emails:
        placeholders = ','.join('?' * len(target_emails))
        subscribers = conn.execute(
            f'''
            SELECT email, counties, token, active
            FROM subscribers
            WHERE lower(email) IN ({placeholders})
            ORDER BY datetime(created_at) DESC, email ASC
            ''',
            target_emails,
        ).fetchall()
    subscriber_map = {str(row['email']).strip().lower(): row for row in subscribers}

    actor_user_id = getattr(current_user, 'id', None) if getattr(current_user, 'is_authenticated', False) else None
    run_id = _record_digest_run(
        conn,
        kind='morning_briefing',
        target_date=original_run['target_date'],
        audience='retry_failed',
        status='running',
        subject=original_run['subject'],
        preview_posts=len(preview['all_posts']),
        preview_subscribers=len(target_emails),
        initiated_by=initiated_by,
        notes=f"Retry failed recipients from digest run #{int(original_run_id)}.",
        created_by_user_id=actor_user_id,
    )
    run_notes = f"Retry failed recipients from digest run #{int(original_run_id)}."
    conn.commit()

    sent_count = 0
    skipped_count = 0
    failed_count = 0
    posts_cache = {'': preview['all_posts']}
    try:
        for row in failed_rows:
            recipient_email = row['recipient_email']
            subscriber = subscriber_map.get(recipient_email)
            if not subscriber or not int(subscriber['active'] or 0):
                skipped_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    recipient_email,
                    '',
                    'skipped',
                    post_count=0,
                    error_message='Subscriber inactive or missing.',
                )
                continue

            counties_raw = subscriber['counties'] or ''
            if counties_raw not in posts_cache:
                posts_cache[counties_raw] = get_morning_briefing_posts(
                    original_run['target_date'],
                    _subscriber_counties_list(counties_raw) or None,
                )
            subscriber_posts = posts_cache[counties_raw]
            if not subscriber_posts:
                skipped_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    recipient_email,
                    counties_raw,
                    'skipped',
                    post_count=0,
                    error_message='No matching posts for subscriber counties.',
                )
                continue

            unsubscribe_url = f"{BASE_URL}/unsubscribe?token={subscriber['token']}"
            html = build_morning_briefing_html(
                subscriber_posts,
                original_run['target_date'],
                unsubscribe_url=unsubscribe_url,
            )
            try:
                send_morning_briefing_email(recipient_email, original_run['subject'], html)
                sent_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    recipient_email,
                    counties_raw,
                    'sent',
                    post_count=len(subscriber_posts),
                )
            except Exception as exc:
                failed_count += 1
                _record_digest_run_recipient(
                    conn,
                    run_id,
                    recipient_email,
                    counties_raw,
                    'failed',
                    post_count=len(subscriber_posts),
                    error_message=str(exc),
                )

        final_status = 'completed'
        if failed_count and sent_count:
            final_status = 'completed_with_errors'
        elif failed_count and not sent_count:
            final_status = 'failed'
        _finish_digest_run(
            conn,
            run_id,
            status=final_status,
            sent_count=sent_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            notes=run_notes,
        )
        _log_admin_action(
            'email_ops.retry_failed',
            target_type='digest_run',
            target_id=run_id,
            metadata={
                'original_run_id': int(original_run_id),
                'target_date': original_run['target_date'],
                'sent_count': sent_count,
                'skipped_count': skipped_count,
                'failed_count': failed_count,
            },
            conn=conn,
        )
        conn.commit()
    except Exception:
        _finish_digest_run(conn, run_id, status='failed', failed_count=failed_count, notes=run_notes)
        conn.commit()
        conn.close()
        raise
    conn.close()
    return {
        'run_id': run_id,
        'target_date': original_run['target_date'],
        'sent_count': sent_count,
        'skipped_count': skipped_count,
        'failed_count': failed_count,
    }


def _record_donation_event(event_type, source='', page_path='', amount_cents=None):
    event_type = (event_type or '').strip()[:40]
    if not event_type:
        return

    safe_source = (source or '').strip()[:80]
    safe_path = (page_path or request.path or '')[:255]
    referrer = (request.referrer or '')[:500]
    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    amount_value = None
    if amount_cents is not None:
        try:
            parsed = int(amount_cents)
            if 0 < parsed <= _donation_max_cents():
                amount_value = parsed
        except (TypeError, ValueError):
            amount_value = None

    try:
        conn = get_db()
        conn.execute(
            '''
            INSERT INTO donation_events (
                event_type, source, page_path, ip_hash, referrer, amount_cents
            ) VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (event_type, safe_source, safe_path, ip_hash, referrer, amount_value),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ==========================================
# PUBLIC ROUTES (No Login Required)
# ==========================================

@app.route('/')
def index():
    """Public homepage — daily activity reports with calendar filter"""
    if _is_agendas_host():
        return render_template('public_meetings.html', **_public_meetings_dashboard_context())

    county       = request.args.get('county', '')
    city         = request.args.get('city', '')
    agency_type  = request.args.get('agency_type', '')
    agency       = request.args.get('agency', '')   # specific agency_name
    search_query = request.args.get('q', '')
    date_filter  = request.args.get('date', '')     # expects YYYY-MM-DD
    status_filter = request.args.get('status', '')  # active | pending | resolved
    page         = max(1, request.args.get('page', 1, type=int))
    per_page     = 10

    conn = get_db()

    # Convert YYYY-MM-DD date_filter → MM/DD/YY for DB match
    date_sql_val = ''
    if date_filter:
        try:
            dt = datetime.strptime(date_filter, '%Y-%m-%d')
            date_sql_val = dt.strftime('%m/%d/%y')
        except ValueError:
            date_filter = ''

    sql = """
        SELECT posts.*, blotters.county AS blotter_county, blotters.file_path AS file_path
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        WHERE COALESCE(posts.audit_status, 'pending') = 'clean'
    """
    params = []

    if county:
        sql += " AND posts.county = ?"
        params.append(county)
    if city:
        sql += " AND posts.city LIKE ?"
        params.append(f'%{city}%')
    if agency_type:
        sql += " AND posts.agency_type = ?"
        params.append(agency_type)
    if agency:
        sql += " AND posts.agency_name = ?"
        params.append(agency)
    if search_query:
        st = f'%{search_query}%'
        sql += " AND (posts.title LIKE ? OR posts.summary LIKE ?)"
        params.extend([st, st])
    if date_sql_val:
        sql += " AND posts.incident_date = ?"
        params.append(date_sql_val)
    if status_filter in ('active', 'pending', 'resolved'):
        sql += " AND COALESCE(posts.case_status, 'pending') = ?"
        params.append(status_filter)

    count_sql = f"SELECT COUNT(*) FROM ({sql}) AS post_listing"
    total = conn.execute(count_sql, params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    sql += " ORDER BY posts.incident_date DESC, posts.created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    posts = conn.execute(sql, params).fetchall()
    posts = _annotate_posts_with_confidence(conn, posts)

    # Filter dropdowns
    counties = [r['county'] for r in conn.execute(
        "SELECT DISTINCT county FROM posts WHERE COALESCE(audit_status, 'pending') = 'clean' ORDER BY county").fetchall()]
    cities = [r['city'] for r in conn.execute(
        "SELECT DISTINCT city FROM posts WHERE city != '' AND COALESCE(audit_status, 'pending') = 'clean' ORDER BY city").fetchall()]

    # Agency directory: each agency with last report date and count
    agency_rows = conn.execute("""
        SELECT agency_name, agency_type, MAX(county) AS county,
               MAX(incident_date) AS last_report,
               COUNT(*) AS report_count
        FROM posts
        WHERE agency_name IS NOT NULL AND agency_name != ''
          AND COALESCE(audit_status, 'pending') = 'clean'
        GROUP BY agency_name
        ORDER BY last_report DESC
    """).fetchall()
    agencies = []
    agency_map = {}
    for row in agency_rows:
        normalized_name, normalized_type = normalize_agency_identity(
            row['agency_name'],
            row['agency_type'],
            county=row['county'],
        )
        display_name = normalized_name or row['agency_name']
        display_type = normalized_type or row['agency_type'] or 'other'
        key = (display_name, display_type)
        if key not in agency_map:
            agency_map[key] = {
                'agency_name': display_name,
                'agency_type': display_type,
                'last_report': row['last_report'],
                'report_count': row['report_count'],
            }
            agencies.append(agency_map[key])
            continue
        agency_map[key]['report_count'] += row['report_count']

    total_records = conn.execute('SELECT COUNT(*) FROM records').fetchone()[0]

    post_stats = {
        row['county']: row
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS post_count, MAX(incident_date) AS last_report
            FROM posts
            WHERE county IS NOT NULL AND county != ''
              AND COALESCE(audit_status, 'pending') = 'clean'
            GROUP BY county
            '''
        ).fetchall()
    }
    record_stats = {
        row['county']: row['record_count']
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS record_count
            FROM records
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }
    top_counties = []
    for county_data in COUNTY_DATA.values():
        stats = post_stats.get(county_data['name'])
        record_count = record_stats.get(county_data['name'], 0)
        post_count = stats['post_count'] if stats else 0
        if not record_count and not post_count:
            continue
        top_counties.append({
            **county_data,
            'record_count': record_count,
            'post_count': post_count,
            'last_report': stats['last_report'] if stats else None,
        })
    top_counties.sort(
        key=lambda item: (-item['record_count'], -item['post_count'], item['name'])
    )
    top_counties = top_counties[:5]
    featured_new_cities = []
    weekly_snapshot = _weekly_snapshot(conn)
    missing_persons_alert = missing_person_homepage_context(conn, limit=3)
    newsroom_context = _homepage_newsroom_context(
        conn,
        county=county,
        city=city,
        agency_type=agency_type,
        agency=agency,
        search_query=search_query,
        date_sql_val=date_sql_val,
        status_filter=status_filter,
    )

    # Leaderboard: most active agencies this week vs last week
    this_week_rows = conn.execute("""
        SELECT COALESCE(county, 'Unknown') AS county,
               COUNT(*) AS cnt
        FROM records
        WHERE created_at >= datetime('now', '-7 days')
        GROUP BY county ORDER BY cnt DESC LIMIT 6
    """).fetchall()
    prev_week_map = {r['county']: r['cnt'] for r in conn.execute("""
        SELECT COALESCE(county, 'Unknown') AS county, COUNT(*) AS cnt
        FROM records
        WHERE created_at >= datetime('now', '-14 days')
          AND created_at < datetime('now', '-7 days')
        GROUP BY county
    """).fetchall()}
    leaderboard = []
    for r in this_week_rows:
        prev = prev_week_map.get(r['county'], 0)
        trend = 'up' if r['cnt'] > prev else ('down' if r['cnt'] < prev else 'same')
        slug = _county_slug_for_name(r['county'])
        delta = r['cnt'] - prev
        county_stats = post_stats.get(r['county'])
        leaderboard.append({
            'county': r['county'],
            'slug': slug,
            'count': r['cnt'],
            'prev': prev,
            'delta': delta,
            'trend': trend,
            'trend_label': 'New this week' if prev == 0 else (
                'Up from last week' if delta > 0 else (
                    'Down from last week' if delta < 0 else 'Steady week over week'
                )
            ),
            'last_report': county_stats['last_report'] if county_stats else None,
        })

    conn.close()

    return render_template('index.html',
                           posts=posts,
                           total=total,
                           total_pages=total_pages,
                           page=page,
                           counties=counties,
                           cities=cities,
                           agencies=agencies,
                           county=county,
                           city=city,
                           agency_type=agency_type,
                           agency=agency,
                           q=search_query,
                           date_filter=date_filter,
                           status_filter=status_filter,
                           total_records=total_records,
                           top_counties=top_counties,
                           featured_new_cities=featured_new_cities,
                           weekly_snapshot=weekly_snapshot,
                           missing_persons_alert=missing_persons_alert,
                           leaderboard=leaderboard,
                           page_title='Real-Time Public Record Reporting',
                           meta_description='Real-time Montana public record reporting with recent incident cards, jurisdiction directories, and statewide trend visibility.',
                           canonical_url=f'{BASE_URL}/',
                           og_title='Montana Blotter | Real-Time Public Record Reporting',
                           og_description='Track recent Montana incident records, browse jurisdictions, and follow statewide public record trends in one newsroom-style homepage.',
                           active_nav='home',
                           **newsroom_context,
                           current_year=datetime.now().year)


@app.route('/posts')
def legacy_posts_redirect():
    query_string = request.query_string.decode().strip()
    target = url_for('index')
    if query_string:
        target = f'{target}?{query_string}'
    return redirect(target, code=302)


@app.route('/meetings')
def public_meetings_dashboard():
    return render_template('public_meetings.html', **_public_meetings_dashboard_context())


@app.route('/public-meetings')
def legacy_public_meetings_redirect():
    return redirect(url_for('public_meetings_dashboard'), code=301)


@app.route('/courts')
def public_courts_directory():
    conn = get_db()
    ensure_court_tracker_schema(conn)
    context = court_directory_context(
        conn,
        q=request.args.get('q'),
        county=request.args.get('county'),
        court_type=request.args.get('court_type'),
    )
    conn.close()
    return render_template(
        'courts_index.html',
        **context,
        page_title='Montana Court Tracker',
        meta_description='Search Montana courts, upcoming hearings, and public case pages in one statewide court tracker.',
        canonical_url=f'{BASE_URL}/courts',
        og_title='Montana Court Tracker',
        og_description='Search Montana courts, upcoming hearings, and public case pages in one statewide court tracker.',
        active_nav='courts',
        current_year=datetime.now().year,
    )


@app.route('/court-hearings')
@app.route('/court-search')
def public_court_hearings():
    search_mode = request.path == '/court-search'
    conn = get_db()
    ensure_court_tracker_schema(conn)
    context = court_hearing_feed_context(
        conn,
        q=request.args.get('q'),
        county=request.args.get('county'),
        court_type=request.args.get('court_type'),
        hearing_date=request.args.get('hearing_date'),
        include_past=search_mode or request.args.get('include_past') in {'1', 'true', 'yes'},
    )
    conn.close()
    page_title = 'Montana Court Search' if search_mode else 'Upcoming Montana Court Hearings'
    meta_description = (
        'Search Montana court records, hearings, and opinion filings across official court feeds.'
        if search_mode
        else 'Track upcoming public Montana court hearings by county, court type, and case.'
    )
    return render_template(
        'court_hearing_feed.html',
        **context,
        search_mode=search_mode,
        page_title=page_title,
        meta_description=meta_description,
        canonical_url=f"{BASE_URL}{'/court-search' if search_mode else '/court-hearings'}",
        og_title=page_title,
        og_description=meta_description,
        active_nav='courts',
        current_year=datetime.now().year,
    )


@app.route('/court-case/<slug>')
def public_court_case_detail(slug):
    conn = get_db()
    ensure_court_tracker_schema(conn)
    case = court_case_detail(conn, slug)
    conn.close()
    if not case:
        return render_template('404.html'), 404
    return render_template(
        'court_case_detail.html',
        case=case,
        page_title=case['caption'],
        meta_description=f"{case['case_number']} in {case['court_name']} with hearings, filings, and public docket context.",
        canonical_url=f"{BASE_URL}/court-case/{case['slug']}",
        og_title=f"{case['caption']} | Montana Court Tracker",
        og_description=f"{case['case_number']} in {case['court_name']} with hearings, filings, and public docket context.",
        active_nav='courts',
        current_year=datetime.now().year,
    )


@app.route('/case-journeys')
def public_case_journey_index():
    conn = get_db()
    journeys = _case_journey_rows(conn)
    conn.close()
    return render_template(
        'case_journey_index.html',
        journeys=journeys,
        page_title='Case Journeys',
        meta_description='Follow Montana incident timelines with command-log updates, source confidence, and direct links back to public records.',
        canonical_url=f'{BASE_URL}/case-journeys',
        og_title='Case Journeys | Montana Blotter',
        og_description='Follow Montana incident timelines with command-log updates, source confidence, and direct links back to public records.',
        active_nav='case_journeys',
        current_year=datetime.now().year,
    )


@app.route('/case-journeys/<slug>')
def public_case_journey_detail(slug):
    conn = get_db()
    journey = _case_journey_detail(conn, slug)
    conn.close()
    if not journey:
        return render_template('404.html'), 404
    return render_template(
        'case_journey_detail.html',
        journey=journey,
        page_title=journey['title'],
        meta_description=journey.get('summary') or f"Public case journey for {journey['county']} County, Montana.",
        canonical_url=f"{BASE_URL}/case-journeys/{journey['slug']}",
        og_title=f"{journey['title']} | Case Journey | Montana Blotter",
        og_description=journey.get('summary') or f"Public case journey for {journey['county']} County, Montana.",
        active_nav='case_journeys',
        current_year=datetime.now().year,
    )


@app.route('/missing-persons')
def missing_persons_index():
    conn = get_db()
    context = missing_person_public_context(
        conn,
        status_filter=request.args.get('status'),
        q=request.args.get('q'),
        county=request.args.get('county'),
        sort=request.args.get('sort'),
    )
    conn.close()
    return render_template(
        'missing_persons.html',
        active_nav='missing_persons',
        page_title='Montana Missing Persons Database',
        meta_description='Statewide missing-person alerts and recent located updates for Montana communities, with last-seen details and contact guidance.',
        canonical_url=f'{BASE_URL}/missing-persons',
        og_title='Montana Missing Persons Database',
        og_description='Track active missing-person alerts and recently located cases across Montana.',
        **context,
        current_year=datetime.now().year,
    )


@app.route('/missing-persons/<slug>')
def missing_person_detail(slug):
    conn = get_db()
    context = missing_person_detail_context(conn, slug)
    conn.close()
    if not context:
        return render_template('404.html'), 404
    person = context['person']
    return render_template(
        'missing_person_detail.html',
        active_nav='missing_persons',
        page_title=person['full_name'],
        meta_description=f"Missing-person record for {person['full_name']} with last-seen details, contact guidance, and Montana public alert context.",
        canonical_url=f"{BASE_URL}{person['public_href']}",
        og_title=f"{person['full_name']} | Montana Missing Persons",
        og_description=person['summary'],
        **context,
        current_year=datetime.now().year,
    )


@app.route('/feed.xml')
def rss_feed():
    """Atom feed of the 20 most recent daily activity reports."""
    conn = get_db()
    posts = conn.execute("""
        SELECT posts.*, blotters.county AS blotter_county
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        WHERE COALESCE(posts.audit_status, 'pending') = 'clean'
        ORDER BY posts.incident_date DESC, posts.created_at DESC
        LIMIT 20
    """).fetchall()
    conn.close()

    # Build RFC-3339 timestamps
    def to_rfc3339(date_str, created_at):
        for fmt in ('%m/%d/%y', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str, fmt).strftime('%Y-%m-%dT00:00:00Z')
            except (ValueError, TypeError):
                pass
        try:
            return datetime.strptime(created_at[:19], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%SZ')
        except (ValueError, TypeError):
            pass
        return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    items = []
    for p in posts:
        pub = to_rfc3339(p['incident_date'], p['created_at'])
        summary_snippet = (p['summary'] or '')[:300].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        title = (p['title'] or 'Daily Activity Report').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        agency = (p['agency_name'] or 'Montana Blotter').replace('&', '&amp;')
        link = f"{BASE_URL}/post/{p['id']}"
        items.append(f"""  <entry>
    <title>{title}</title>
    <link href="{link}"/>
    <id>{link}</id>
    <updated>{pub}</updated>
    <author><name>{agency}</name></author>
    <summary type="text">{summary_snippet}</summary>
  </entry>""")

    updated = to_rfc3339(posts[0]['incident_date'], posts[0]['created_at']) if posts else datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Montana Blotter — Daily Activity Reports</title>
  <subtitle>AI-summarized police blotters from Montana law enforcement agencies</subtitle>
  <link href="{BASE_URL}/feed.xml" rel="self"/>
  <link href="{BASE_URL}/"/>
  <id>{BASE_URL}/feed.xml</id>
  <updated>{updated}</updated>
{chr(10).join(items)}
</feed>"""

    return Response(xml, mimetype='application/atom+xml')


def _render_urlset(urls):
    xml_items = []
    for loc, lastmod in urls:
        if lastmod:
            xml_items.append(
                f"<url><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod></url>"
            )
        else:
            xml_items.append(f"<url><loc>{escape(loc)}</loc></url>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + ''.join(xml_items) +
        '</urlset>'
    )
    return Response(xml, mimetype='application/xml')


def _sitemap_static_urls():
    return [
        (f'{BASE_URL}/', None),
        (f'{BASE_URL}/guides', None),
        (f'{BASE_URL}/guides/how-to-find-montana-police-blotters', None),
        (f'{BASE_URL}/guides/yellowstone-county-arrests-jail-roster-and-warrant-guide', None),
        (f'{BASE_URL}/guides/montana-county-guides', None),
        (f'{BASE_URL}/guides/flathead-county-police-blotter-warrant-and-jail-guide', None),
        (f'{BASE_URL}/guides/lewis-and-clark-county-blotter-warrant-and-jail-guide', None),
        (f'{BASE_URL}/guides/how-to-find-montana-warrants', None),
        (f'{BASE_URL}/guides/how-to-find-montana-arrest-records', None),
        (f'{BASE_URL}/guides/how-to-find-montana-jail-rosters', None),
        (f'{BASE_URL}/annual-roundups', None),
        (f'{BASE_URL}/case-journeys', None),
        (f'{BASE_URL}/meetings', None),
        (f'{BASE_URL}/courts', None),
        (f'{BASE_URL}/court-hearings', None),
        (f'{BASE_URL}/court-search', None),
        (f'{BASE_URL}/counties', None),
        (f'{BASE_URL}/cities', None),
        (f'{BASE_URL}/patterns', None),
        (f'{BASE_URL}/trends', None),
        (f'{BASE_URL}/posts', None),
        (f'{BASE_URL}/arrests', None),
        (f'{BASE_URL}/jail-rosters', None),
        (f'{BASE_URL}/bail-bonds', None),
        (f'{BASE_URL}/donate', None),
        (f'{BASE_URL}/advertise/bail-bonds', None),
        (f'{BASE_URL}/subscribe', None),
        (f'{BASE_URL}/terms-of-use', None),
        (f'{BASE_URL}/privacy', None),
        (f'{BASE_URL}/developers/api', None),
        (f'{BASE_URL}/laws', None),
        (f'{BASE_URL}/alerts', None),
        (f'{BASE_URL}/blog', None),
        (f'{BASE_URL}/warrants', None),
        (f'{BASE_URL}/feed.xml', None),
    ]


@app.route('/manifest.json')
def manifest_json():
    manifest_path = os.path.join(current_app.static_folder, 'manifest.json')
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        abort(404)
    response = jsonify(data)
    response.headers['Content-Type'] = 'application/manifest+json'
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@app.route('/offline')
def offline_page():
    return render_template('offline.html',
        page_title='Offline',
        meta_description='You are offline.',
        canonical_url=None,
    ), 200


@app.route('/sw.js')
def service_worker():
    response = send_from_directory(app.static_folder, 'sw.js')
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response


@app.route('/robots.txt')
def robots_txt():
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        f"Sitemap: {BASE_URL}/sitemap.xml",
        "",
    ])
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_index():
    """Sitemap index for public archive sections."""
    conn = get_db()
    post_lastmod_row = conn.execute(
        "SELECT MAX(created_at) AS lastmod FROM posts WHERE COALESCE(audit_status, 'pending') = 'clean'"
    ).fetchone()
    record_lastmod_row = conn.execute(
        """
        SELECT MAX(records.created_at) AS lastmod
        FROM records
        JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE COALESCE(posts.audit_status, 'pending') = 'clean'
        """
    ).fetchone()
    journey_lastmod_row = conn.execute(
        'SELECT MAX(COALESCE(updated_at, created_at)) AS lastmod FROM case_journeys WHERE is_published = 1'
    ).fetchone()
    blog_lastmod_row = conn.execute(
        'SELECT MAX(COALESCE(updated_at, created_at)) AS lastmod FROM blog_posts WHERE published = 1'
    ).fetchone()
    charges_lastmod_row = conn.execute(
        'SELECT MAX(COALESCE(updated_at, created_at)) AS lastmod FROM charge_explainers WHERE published = 1'
    ).fetchone()
    booking_lastmod_row = conn.execute(
        'SELECT MAX(COALESCE(last_seen_at, first_seen_at, created_at)) AS lastmod FROM jail_bookings WHERE person_name IS NOT NULL AND person_name != \'\''
    ).fetchone()
    conn.close()

    sections = [
        ('static', None),
        ('locations', None),
        ('patterns', None),
        ('posts', _iso_lastmod(post_lastmod_row['lastmod']) if post_lastmod_row else None),
        ('case-journeys', _iso_lastmod(journey_lastmod_row['lastmod']) if journey_lastmod_row else None),
        ('records', _iso_lastmod(record_lastmod_row['lastmod']) if record_lastmod_row else None),
        ('blog', _iso_lastmod(blog_lastmod_row['lastmod']) if blog_lastmod_row else None),
        ('charges', _iso_lastmod(charges_lastmod_row['lastmod']) if charges_lastmod_row else None),
        ('bookings', _iso_lastmod(booking_lastmod_row['lastmod']) if booking_lastmod_row else None),
    ]
    items = []
    for name, lastmod in sections:
        loc = f'{BASE_URL}/sitemap-{name}.xml'
        if lastmod:
            items.append(f"<sitemap><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod></sitemap>")
        else:
            items.append(f"<sitemap><loc>{escape(loc)}</loc></sitemap>")

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + ''.join(items) +
        '</sitemapindex>'
    )
    return Response(xml, mimetype='application/xml')


@app.route('/sitemap-static.xml')
def sitemap_static():
    return _render_urlset(_sitemap_static_urls())


@app.route('/sitemap-locations.xml')
def sitemap_locations():
    urls = []
    for county in COUNTY_DATA.values():
        urls.append((f"{BASE_URL}/county/{county['slug']}", None))
    for county_name in _all_bail_counties():
        urls.append((f"{BASE_URL}/bail-bonds/{_slugify_key(county_name)}", None))
    for city in CITY_DATA.values():
        urls.append((f"{BASE_URL}/city/{city['slug']}", None))
    for county in COUNTY_DATA.values():
        urls.append((f"{BASE_URL}/warrants/{county['slug']}", None))
    return _render_urlset(urls)


@app.route('/sitemap-posts.xml')
def sitemap_posts():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, created_at FROM posts WHERE COALESCE(audit_status, 'pending') = 'clean' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    urls = [(f"{BASE_URL}/post/{row['id']}", _iso_lastmod(row['created_at'])) for row in rows]
    return _render_urlset(urls)


@app.route('/sitemap-records.xml')
def sitemap_records():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT records.id, records.created_at
        FROM records
        JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE COALESCE(posts.audit_status, 'pending') = 'clean'
        ORDER BY records.created_at DESC
        """
    ).fetchall()
    conn.close()
    urls = [(f"{BASE_URL}/record/{row['id']}", _iso_lastmod(row['created_at'])) for row in rows]
    return _render_urlset(urls)


@app.route('/sitemap-bookings.xml')
def sitemap_bookings():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, COALESCE(last_seen_at, first_seen_at, created_at) AS updated_at
        FROM jail_bookings
        WHERE person_name IS NOT NULL AND person_name != ''
        ORDER BY COALESCE(last_seen_at, first_seen_at, created_at) DESC
        LIMIT 5000
        """
    ).fetchall()
    conn.close()
    urls = [(f"{BASE_URL}/booking/{row['id']}", _iso_lastmod(row['updated_at'])) for row in rows]
    return _render_urlset(urls)


@app.route('/sitemap-case-journeys.xml')
def sitemap_case_journeys():
    conn = get_db()
    rows = conn.execute(
        'SELECT slug, COALESCE(updated_at, created_at) AS updated_at FROM case_journeys WHERE is_published = 1 ORDER BY COALESCE(updated_at, created_at) DESC'
    ).fetchall()
    conn.close()
    urls = [(f"{BASE_URL}/case-journeys/{row['slug']}", _iso_lastmod(row['updated_at'])) for row in rows]
    return _render_urlset(urls)


@app.route('/sitemap-patterns.xml')
def sitemap_patterns():
    conn = get_db()
    urls = [(f"{BASE_URL}/patterns", None)]
    for pattern in PATTERN_DEFINITIONS.values():
        urls.append((f"{BASE_URL}/patterns/{pattern['slug']}", None))
        clause, params = _pattern_clause(pattern['slug'], 'records')
        rows = conn.execute(
            f'''
            SELECT records.county, COUNT(*) AS count
            FROM records
            WHERE {clause}
              AND records.county IS NOT NULL
              AND records.county != ''
            GROUP BY records.county
            ORDER BY count DESC, records.county ASC
            ''',
            params,
        ).fetchall()
        for row in rows:
            county_slug = _county_slug_for_name(row['county'])
            if county_slug:
                urls.append((f"{BASE_URL}/patterns/{pattern['slug']}/{county_slug}", None))
    conn.close()
    return _render_urlset(urls)


@app.route('/sitemap-blog.xml')
def sitemap_blog():
    conn = get_db()
    rows = conn.execute(
        'SELECT slug, COALESCE(updated_at, created_at) AS updated_at FROM blog_posts WHERE published = 1 ORDER BY COALESCE(updated_at, created_at) DESC'
    ).fetchall()
    conn.close()
    urls = [(f"{BASE_URL}/blog/{row['slug']}", _iso_lastmod(row['updated_at'])) for row in rows]
    return _render_urlset(urls)


@app.route('/sitemap-charges.xml')
def sitemap_charges():
    conn = get_db()
    rows = conn.execute(
        'SELECT slug, COALESCE(updated_at, created_at) AS updated_at FROM charge_explainers WHERE published = 1 ORDER BY view_count DESC, updated_at DESC'
    ).fetchall()
    conn.close()
    urls = [(f"{BASE_URL}/laws/charge/{row['slug']}", _iso_lastmod(row['updated_at'])) for row in rows]
    return _render_urlset(urls)


@app.route('/arrests')
def arrests():
    """Dedicated arrest log — records where an arrest was made."""
    county = request.args.get('county', '')
    search_query = request.args.get('q', '')
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 25

    conn = get_db()

    arrest_filter = """(
        LOWER(COALESCE(records.details, '')) LIKE '%arrest%'
        OR LOWER(COALESCE(records.incident_type, '')) LIKE '%arrest%'
        OR LOWER(COALESCE(records.incident, '')) LIKE '%arrest%'
    )"""

    sql = f"""
        SELECT records.*,
               COALESCE(blotters.filename, '') AS filename
        FROM records
        LEFT JOIN blotters ON records.blotter_id = blotters.id
        WHERE {arrest_filter}
    """
    params = []

    if county:
        sql += " AND records.county = ?"
        params.append(county)
    if search_query:
        st = f'%{search_query}%'
        sql += " AND (records.incident_type LIKE ? OR records.details LIKE ? OR records.location LIKE ?)"
        params.extend([st, st, st])

    total = conn.execute(
        sql.replace("SELECT records.*,\n               COALESCE(blotters.filename, '') AS filename", "SELECT COUNT(*)"),
        params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    sql += " ORDER BY records.created_at DESC LIMIT ? OFFSET ?"
    params.extend([per_page, (page - 1) * per_page])
    records = conn.execute(sql, params).fetchall()

    counties = [r['county'] for r in conn.execute(
        'SELECT DISTINCT county FROM records ORDER BY county').fetchall()]

    conn.close()
    return render_template('arrests.html',
                           records=records, total=total,
                           total_pages=total_pages, page=page,
                           counties=counties, county=county,
                           q=search_query,
                           current_year=datetime.now().year)


@app.route('/comments', methods=['POST'])
def create_public_comment():
    public_user = _get_public_user()
    content_type = (request.form.get('content_type') or '').strip()
    content_id = str(request.form.get('content_id') or '').strip()
    next_url = _safe_next_url(request.form.get('next') or request.referrer, fallback='/')

    if not public_user:
        flash('Create an account or log in to comment.', 'error')
        return redirect(url_for('auth.public_login', next=next_url))

    body = (request.form.get('body') or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if len(body) < 3:
        flash('Comments must be at least 3 characters long.', 'error')
        return redirect(next_url)
    if len(body) > 5000:
        flash('Comments must be 5,000 characters or fewer.', 'error')
        return redirect(next_url)

    conn = get_db()
    if not _public_comment_target_exists(conn, content_type, content_id):
        conn.close()
        flash('That comment target is no longer available.', 'error')
        return redirect(next_url)

    conn.execute(
        '''
        INSERT INTO public_comments (public_user_id, content_type, content_id, body, status, updated_at)
        VALUES (?, ?, ?, ?, 'pending', datetime('now'))
        ''',
        (public_user.id, content_type, content_id, body),
    )
    conn.commit()
    conn.close()
    flash('Comment submitted for review.', 'success')
    return redirect(next_url)


@app.route('/subscribe', methods=['GET', 'POST'])
def subscribe():
    """Public email digest subscription."""
    import secrets

    conn = get_db()
    _ensure_subscriber_schema(conn)
    all_counties = [r['county'] for r in conn.execute(
        'SELECT DISTINCT county FROM posts ORDER BY county').fetchall()]
    source = (request.values.get('source') or '').strip()[:80]
    source = source or 'subscribe_page'

    if request.method == 'POST':
        # reCAPTCHA v3 verification
        recaptcha_token = request.form.get('g-recaptcha-response', '')
        if not verify_recaptcha(recaptcha_token, action='subscribe'):
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   source=source,
                                   error='Security verification failed. Please try again.',
                                   current_year=datetime.now().year)

        email = request.form.get('email', '').strip().lower()
        selected = request.form.getlist('counties')  # empty list = all counties
        _record_subscribe_event('form_submit', source=source, page_path=request.path, email=email)

        if not email or '@' not in email:
            _record_subscribe_event('invalid_email', source=source, page_path=request.path, email=email)
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   source=source,
                                   error='Please enter a valid email address.',
                                   current_year=datetime.now().year)

        token = secrets.token_urlsafe(32)
        counties_str = ','.join(selected)

        try:
            conn.execute(
                'INSERT INTO subscribers (email, counties, token, source, updated_at) VALUES (?, ?, ?, ?, datetime(\'now\'))',
                (email, counties_str, token, source))
            conn.commit()
            _record_subscribe_event('subscribe_success', source=source, page_path=request.path, email=email)
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   source=source,
                                   success=True, email=email,
                                   current_year=datetime.now().year)
        except Exception:
            # Email already subscribed — update preferences
            conn.execute(
                'UPDATE subscribers SET counties=?, active=1, source=?, updated_at=datetime(\'now\') WHERE email=?',
                (counties_str, source, email))
            conn.commit()
            _record_subscribe_event('subscribe_update', source=source, page_path=request.path, email=email)
            conn.close()
            return render_template('subscribe.html', counties=all_counties,
                                   source=source,
                                   success=True, email=email, updated=True,
                                   current_year=datetime.now().year)

    conn.close()
    return render_template('subscribe.html', counties=all_counties,
                           source=source,
                           current_year=datetime.now().year)


@app.route('/unsubscribe')
def unsubscribe():
    """Unsubscribe via token link in digest emails."""
    token = request.args.get('token', '')
    conn = get_db()
    _ensure_subscriber_schema(conn)
    row = conn.execute('SELECT email FROM subscribers WHERE token=?', (token,)).fetchone()
    if row:
        conn.execute('UPDATE subscribers SET active=0, updated_at=datetime(\'now\') WHERE token=?', (token,))
        conn.commit()
        email = row['email']
        _record_subscribe_event('unsubscribe', source='digest_link', page_path=request.path, email=email)
        conn.close()
        return render_template('subscribe.html', counties=[], unsubscribed=True, email=email,
                               current_year=datetime.now().year)
    conn.close()
    return render_template('subscribe.html', counties=[],
                           error='Invalid or expired unsubscribe link.',
                           current_year=datetime.now().year)


# ==========================================
# COUNTY ALERTS & NAME WATCH
# ==========================================

@app.route('/alerts', methods=['GET', 'POST'])
def alerts_signup():
    """County alert and Name Watch subscription page."""
    from alert_dispatcher import subscribe_county_alert, subscribe_name_watch
    conn = get_db()
    counties = sorted({
        r['county'] for r in conn.execute(
            "SELECT DISTINCT county FROM records WHERE county NOT IN ('Unknown','','45','47') ORDER BY county"
        ).fetchall()
    })
    conn.close()

    source = (request.values.get('source') or 'alerts_page').strip()[:80]
    prefill_county = (request.args.get('county') or '').strip()
    success_type = None
    error = None

    if request.method == 'POST':
        form_type = request.form.get('form_type', 'county_alert')
        email = request.form.get('email', '').strip().lower()

        if not email or '@' not in email:
            error = 'Please enter a valid email address.'
        elif form_type == 'county_alert':
            county = request.form.get('county', '').strip()
            if not county:
                error = 'Please select a county.'
            else:
                raw_types = request.form.getlist('alert_types')
                alert_types = raw_types if raw_types else ['all']
                subscribe_county_alert(email, county, alert_types, source=source)
                success_type = 'county_alert'
                prefill_county = county
        elif form_type == 'name_watch':
            watch_name = request.form.get('watch_name', '').strip()
            county = request.form.get('watch_county', '').strip() or None
            if not watch_name:
                error = 'Please enter a name to watch.'
            elif len(watch_name) < 2:
                error = 'Name must be at least 2 characters.'
            else:
                subscribe_name_watch(email, watch_name, county)
                success_type = 'name_watch'

    return render_template(
        'alerts.html',
        counties=counties,
        prefill_county=prefill_county,
        success_type=success_type,
        error=error,
        source=source,
        current_year=datetime.now().year,
    )


@app.route('/alerts/unsubscribe')
def alerts_unsubscribe():
    from alert_dispatcher import cancel_alert_subscription
    token = request.args.get('token', '')
    email = cancel_alert_subscription(token) if token else None
    return render_template(
        'alerts.html',
        counties=[],
        unsubscribed=True,
        unsubscribed_email=email,
        current_year=datetime.now().year,
    )


@app.route('/alerts/name-watch/cancel')
def name_watch_cancel():
    from alert_dispatcher import cancel_name_watch
    token = request.args.get('token', '')
    email = cancel_name_watch(token) if token else None
    return render_template(
        'alerts.html',
        counties=[],
        watch_cancelled=True,
        unsubscribed_email=email,
        current_year=datetime.now().year,
    )


@app.route('/post/<int:post_id>')
def public_post_detail(post_id):
    conn = get_db()
    post = conn.execute(
        '''
        SELECT posts.*,
               blotters.filename AS blotter_filename,
               blotters.file_path,
               blotters.upload_date,
               blotters.incident_count,
               blotters.source_type AS blotter_source_type,
               blotters.source_document_id,
               source_documents.source_type,
               source_documents.source_sender,
               source_documents.source_subject,
               source_documents.source_received_at,
               source_documents.filename AS source_filename,
               source_documents.extraction_method,
               source_documents.extraction_warnings
        FROM posts
        JOIN blotters ON posts.blotter_id = blotters.id
        LEFT JOIN source_documents ON source_documents.id = blotters.source_document_id
        WHERE posts.id = ?
          AND COALESCE(posts.audit_status, 'pending') = 'clean'
        LIMIT 1
        ''',
        (post_id,),
    ).fetchone()
    if not post:
        conn.close()
        return render_template('404.html'), 404

    records = conn.execute(
        '''
        SELECT records.*
        FROM records
        WHERE records.blotter_id = ?
        ORDER BY records.date DESC, records.time DESC, records.id DESC
        ''',
        (post['blotter_id'],),
    ).fetchall()
    explainer_slugs = _charge_explainer_slug_map(conn, records)
    county_slug = _county_slug_for_name(post['county'])
    city_slug = _city_slug_for_name(post['city'])
    related_posts = conn.execute(
        '''
        SELECT id, title, incident_date, agency_name
        FROM posts
        WHERE county = ?
          AND id != ?
          AND COALESCE(audit_status, 'pending') = 'clean'
        ORDER BY incident_date DESC, created_at DESC
        LIMIT 5
        ''',
        (post['county'], post_id),
    ).fetchall()
    provenance_card = _build_provenance_card(conn, post, records)
    bail_ad_placements = _bail_ad_public_placements(conn, county=post['county'])
    conn.close()

    public_records = [_public_record_dict(record) for record in records]
    summary_lines = _summary_lines(post['summary']) or ['No published summary was included for this report.']
    county_page = COUNTY_DATA.get(county_slug) if county_slug else None

    # SEO: canonical URL and OG image for post detail
    post_canonical = f"https://montanablotter.com/post/{post_id}"
    post_og_image = "https://montanablotter.com/static/icons/og-default.png"

    return render_template(
        'post_detail.html',
        post=post,
        county_slug=county_slug,
        city_slug=city_slug,
        records=public_records,
        total_incident_count=len(records),
        summary_lines=summary_lines,
        provenance_card=provenance_card,
        source_pdf_name=_detail_source_pdf_name(post),
        explainer_slugs=explainer_slugs,
        county_page=county_page,
        related_pattern_pages=_related_pattern_pages_for_post(records, county_slug=county_slug),
        related_posts=related_posts,
        bail_ad_placements=bail_ad_placements,
        current_year=datetime.now().year,
        canonical_url=post_canonical,
        og_image=post_og_image,
    )


@app.route('/record/<int:record_id>')

@app.route('/incident/<int:record_id>')
def public_incident_detail(record_id):
    return public_record_detail(record_id)

def public_record_detail(record_id):
    conn = get_db()
    record = conn.execute(
        '''
        SELECT records.*,
               blotters.filename AS blotter_filename,
               blotters.file_path,
               blotters.upload_date,
               blotters.source_type AS blotter_source_type,
               blotters.source_document_id,
               posts.id AS post_id,
               posts.title AS post_title,
               posts.incident_date,
               source_documents.source_type,
               source_documents.source_sender,
               source_documents.source_subject,
               source_documents.source_received_at,
               source_documents.filename AS source_filename,
               source_documents.extraction_method,
               source_documents.extraction_warnings
        FROM records
        LEFT JOIN blotters ON blotters.id = records.blotter_id
        LEFT JOIN posts ON posts.id = (
            SELECT p.id
            FROM posts p
            WHERE p.blotter_id = records.blotter_id
              AND COALESCE(p.audit_status, 'pending') = 'clean'
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT 1
        )
        LEFT JOIN source_documents ON source_documents.id = blotters.source_document_id
        WHERE records.id = ?
        LIMIT 1
        ''',
        (record_id,),
    ).fetchone()
    if not record or record['post_id'] is None:
        conn.close()
        return render_template('404.html'), 404

    blotter_records = conn.execute(
        '''
        SELECT cfs_number,
               date,
               time,
               COALESCE(incident_type, incident, '') AS incident_type,
               COALESCE(location, '') AS location,
               COALESCE(details, '') AS details,
               county
        FROM records
        WHERE blotter_id = ?
        ORDER BY date DESC, time DESC, id DESC
        ''',
        (record['blotter_id'],),
    ).fetchall()
    logs = conn.execute(
        '''
        SELECT timestamp, officer, entry
        FROM command_logs
        WHERE record_id = ?
        ORDER BY COALESCE(timestamp, created_at) ASC, id ASC
        ''',
        (record_id,),
    ).fetchall()
    sibling_records = conn.execute(
        '''
        SELECT id,
               time,
               COALESCE(incident_type, incident, 'Incident') AS incident_type,
               location
        FROM records
        WHERE blotter_id = ?
          AND id != ?
        ORDER BY date DESC, time DESC, id DESC
        LIMIT 8
        ''',
        (record['blotter_id'], record_id),
    ).fetchall()
    linked_case_journey = _case_journey_for_record(conn, record_id)
    provenance_card = _build_record_provenance_card(conn, record, blotter_records)
    public_record = _public_record_dict(record)
    public_logs = [_public_log_dict(log) for log in logs]
    public_sibling_records = [_public_record_dict(row) for row in sibling_records]
    conn.close()

    return render_template(
        'record_detail.html',
        record=public_record,
        county_slug=_county_slug_for_name(record['county']),
        logs=public_logs,
        provenance_card=provenance_card,
        source_pdf_name=_detail_source_pdf_name(record),
        linked_case_journey=linked_case_journey,
        sibling_records=public_sibling_records,
        current_year=datetime.now().year,
    )


@app.route('/bondsman/felony-alerts')
def bail_bonds_alerts_landing():
    county_key = (request.args.get('county') or 'cascade').strip().lower()
    county_cards = [
        {
            'slug': 'cascade',
            'county_name': 'Cascade',
            'city_name': 'Great Falls',
            'headline_county_name': 'Cascade',
            'launch_note': 'Built for Great Falls bail desks that need the first outbound call.',
            'source_note': 'Cascade County detention activity surfaces some of the highest-intent lead moments in central Montana.',
            'cta_note': "Ideal first outreach targets: Lisa's Family Bail Bonds and A-1 Bail Bonds.",
        },
        {
            'slug': 'missoula',
            'county_name': 'Missoula',
            'city_name': 'Missoula',
            'headline_county_name': 'Missoula',
            'launch_note': 'Built for Missoula agencies competing on responsiveness, not just ad spend.',
            'source_note': 'Missoula County bookings convert best when the agency reaches family members before the rest of the market reacts.',
            'cta_note': 'Ideal first outreach targets: AAA Bail Bonds and Central Montana Bail Bonds.',
        },
    ]
    county_map = {item['slug']: item for item in county_cards}
    selected_county = county_map.get(county_key, county_map['cascade'])
    help_contact = _bail_help_contact()
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or (getattr(config, 'EMAIL_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )
    return render_template(
        'bail_bonds_alerts_landing.html',
        page_title=f"{selected_county['county_name']} County Felony Alerts",
        meta_description=(
            f"Instant felony booking SMS alerts for {selected_county['county_name']} County bail bondsmen. "
            "Get a four-hour speed-to-lead advantage in Great Falls and Missoula."
        ),
        canonical_url=f"{BASE_URL}/bondsman/felony-alerts?county={selected_county['slug']}",
        og_title=f"Instant Felony Alerts for {selected_county['county_name']} County",
        og_description="SMS the moment a felony booking lands. Built for Montana bail agencies that win on speed to lead.",
        active_nav='bondsman_command_center',
        county_cards=county_cards,
        selected_county=selected_county,
        help_contact=help_contact,
        support_email=support_email,
        pricing_amount='$30',
        pricing_term='month',
        current_year=datetime.now().year,
    )


# ==========================================
# BLOG — PUBLIC
# ==========================================

def _slugify(text):
    import re as _re
    text = text.lower().strip()
    text = _re.sub(r'[^\w\s-]', '', text)
    text = _re.sub(r'[\s_-]+', '-', text)
    return text[:80]


@app.template_filter('markdown')
def render_markdown(text):
    import markdown as _md
    # Escape raw HTML first, then sanitize generated Markdown HTML.
    safe_source = escape(text or '')
    rendered = _md.markdown(safe_source, extensions=['extra', 'nl2br'])
    return _sanitize_html(rendered)


def _coerce_jail_booking_datetime(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    candidates = (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M',
        '%m/%d/%Y %H:%M',
        '%m/%d/%Y %I:%M %p',
        '%m/%d/%y %H:%M',
        '%m/%d/%y %I:%M %p',
        '%Y-%m-%d',
    )
    for fmt in candidates:
        try:
            return datetime.strptime(raw, fmt)
        except (TypeError, ValueError):
            continue

    try:
        normalized = raw.replace('Z', '+00:00')
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _normalize_jail_booking_datetime(value):
    parsed = _coerce_jail_booking_datetime(value)
    if not parsed:
        return None
    return parsed.strftime('%Y-%m-%d %H:%M:%S')


def _display_jail_booking_datetime(value):
    parsed = _coerce_jail_booking_datetime(value)
    if not parsed:
        return 'Unknown'
    label = parsed.strftime('%b %d, %Y %I:%M %p')
    return label.replace(' 0', ' ')


def _normalize_jail_booking_status(value):
    normalized = (value or 'current').strip().lower()
    if normalized not in JAIL_BOOKING_STATUS_LABELS:
        return 'current'
    return normalized


def _sync_jail_booking_sources(conn):
    for county_slug, meta in COUNTY_DIRECTORY.items():
        if not meta.get('has_online_roster'):
            conn.execute(
                '''
                UPDATE jail_booking_sources
                SET roster_url = NULL,
                    phone = ?,
                    is_enabled = 0,
                    notes = CASE
                        WHEN COALESCE(notes, '') = '' OR notes = 'No automated county adapter has been added yet.'
                        THEN 'Online roster unavailable or unverified.'
                        ELSE notes
                    END,
                    updated_at = datetime('now')
                WHERE county_slug = ?
                ''',
                (meta.get('phone'), county_slug),
            )
            continue
        facility_name = f"{meta['name']} County Detention Center"
        coverage_tier = 'major' if county_slug in MAJOR_JAIL_BOOKING_COUNTIES else 'standard'
        is_featured = 1 if county_slug in MAJOR_JAIL_BOOKING_COUNTIES else 0
        conn.execute(
            '''
            INSERT OR IGNORE INTO jail_booking_sources (
                county_slug,
                county_name,
                facility_name,
                roster_url,
                phone,
                source_type,
                coverage_tier,
                is_enabled,
                is_featured,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, 'official_roster', ?, 1, ?, datetime('now'))
            ''',
            (
                county_slug,
                meta['name'],
                facility_name,
                meta.get('roster_url'),
                meta.get('phone'),
                coverage_tier,
                is_featured,
            ),
        )
        conn.execute(
            '''
            UPDATE jail_booking_sources
            SET county_name = ?,
                facility_name = ?,
                roster_url = ?,
                phone = ?,
                coverage_tier = ?,
                is_featured = ?,
                is_enabled = 1
            WHERE county_slug = ?
              AND (
                COALESCE(county_name, '') != ?
                OR COALESCE(facility_name, '') != ?
                OR COALESCE(roster_url, '') != COALESCE(?, '')
                OR COALESCE(phone, '') != COALESCE(?, '')
                OR COALESCE(coverage_tier, '') != ?
                OR COALESCE(is_featured, 0) != ?
                OR COALESCE(is_enabled, 1) != 1
              )
            ''',
            (
                meta['name'],
                facility_name,
                meta.get('roster_url'),
                meta.get('phone'),
                coverage_tier,
                is_featured,
                county_slug,
                meta['name'],
                facility_name,
                meta.get('roster_url'),
                meta.get('phone'),
                coverage_tier,
                is_featured,
            ),
        )
    conn.commit()


def _decorate_jail_booking_row(row):
    item = dict(row)
    booking_dt = _coerce_jail_booking_datetime(item.get('booking_at') or item.get('first_seen_at') or item.get('created_at'))
    first_seen_dt = _coerce_jail_booking_datetime(item.get('first_seen_at') or item.get('created_at'))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    event_dt = booking_dt or first_seen_dt
    item['booking_status'] = _normalize_jail_booking_status(item.get('booking_status'))
    item['booking_status_label'] = JAIL_BOOKING_STATUS_LABELS.get(item['booking_status'], 'Current')
    item['booking_at_label'] = _display_jail_booking_datetime(item.get('booking_at') or item.get('first_seen_at') or item.get('created_at'))
    item['first_seen_label'] = _display_jail_booking_datetime(item.get('first_seen_at') or item.get('created_at'))
    item['last_seen_label'] = _display_jail_booking_datetime(item.get('last_seen_at') or item.get('updated_at') or item.get('created_at'))
    item['is_new_24h'] = bool(event_dt and (now - event_dt) <= timedelta(hours=24))
    item['is_new_72h'] = bool(event_dt and (now - event_dt) <= timedelta(hours=72))
    item['charges_summary'] = (item.get('charges_summary') or '').strip()
    item['notes'] = (item.get('notes') or '').strip()
    return item


def _decorate_jail_booking_source_row(row):
    item = dict(row)
    last_checked = _coerce_jail_booking_datetime(item.get('last_checked_at'))
    last_success = _coerce_jail_booking_datetime(item.get('last_success_at'))
    item['last_checked_label'] = _display_jail_booking_datetime(item.get('last_checked_at'))
    item['last_success_label'] = _display_jail_booking_datetime(item.get('last_success_at'))
    item['public_href'] = f"/jail-bookings/{item['county_slug']}"
    if last_checked and not last_success:
        item['sync_state'] = 'attention'
        item['sync_state_label'] = 'Checked, no success yet'
    elif last_checked and last_success and last_checked > last_success:
        item['sync_state'] = 'attention'
        item['sync_state_label'] = 'Needs review'
    elif last_success:
        item['sync_state'] = 'healthy'
        item['sync_state_label'] = 'Healthy'
    else:
        item['sync_state'] = 'pending'
        item['sync_state_label'] = 'Pending'
    if last_success and int(item.get('current_count') or 0) > 0:
        item['coverage_label'] = 'Live feed'
    elif last_success:
        item['coverage_label'] = 'Live sync'
    elif last_checked:
        item['coverage_label'] = 'Checked source'
    else:
        item['coverage_label'] = 'Tracking setup'
    return item


def _decorate_jail_booking_run_row(row):
    item = dict(row)
    item['run_type_label'] = (item.get('run_type') or 'manual').replace('_', ' ').title()
    item['status_label'] = (item.get('status') or 'pending').title()
    item['started_label'] = _display_jail_booking_datetime(item.get('started_at'))
    item['completed_label'] = _display_jail_booking_datetime(item.get('completed_at'))
    return item


def _jail_booking_public_context(conn, county_filter='', status_filter='current', q='', county_page=False):
    _sync_jail_booking_sources(conn)
    county_filter = (county_filter or '').strip().lower()
    q = (q or '').strip()[:120]
    status_filter = (status_filter or 'current').strip().lower()
    if status_filter not in {'current', 'recent', 'released', 'all'}:
        status_filter = 'current'

    sources = conn.execute(
        '''
        SELECT
            s.*,
            (
                SELECT COUNT(*)
                FROM jail_bookings jb
                WHERE jb.source_id = s.id
                  AND COALESCE(jb.is_current, 1) = 1
            ) AS current_count,
            (
                SELECT COUNT(*)
                FROM jail_bookings jb
                WHERE jb.source_id = s.id
                  AND datetime(COALESCE(jb.booking_at, jb.first_seen_at, jb.created_at)) >= datetime('now', '-24 hours')
            ) AS new_24h_count
        FROM jail_booking_sources s
        WHERE COALESCE(s.is_enabled, 1) = 1
          AND COALESCE(s.is_featured, 0) = 1
        ORDER BY COALESCE(s.is_featured, 0) DESC, s.county_name ASC
        '''
    ).fetchall()

    valid_counties = {row['county_slug'] for row in sources}
    if county_filter and county_filter not in valid_counties:
        county_filter = ''

    where = ['1 = 1']
    params = []
    if county_filter:
        where.append('jb.county_slug = ?')
        params.append(county_filter)
    if status_filter == 'current':
        where.append('COALESCE(jb.is_current, 1) = 1')
    elif status_filter == 'recent':
        where.append("datetime(COALESCE(jb.booking_at, jb.first_seen_at, jb.created_at)) >= datetime('now', '-24 hours')")
    elif status_filter == 'released':
        where.append("(COALESCE(jb.is_current, 1) = 0 OR jb.booking_status = 'released')")
    if q:
        like = f'%{q}%'
        where.append(
            '('
            'jb.person_name LIKE ? OR '
            'COALESCE(jb.charges_summary, \'\') LIKE ? OR '
            'COALESCE(jb.booking_number, \'\') LIKE ? OR '
            'COALESCE(jb.arresting_agency, \'\') LIKE ? OR '
            'COALESCE(jb.county_name, \'\') LIKE ?'
            ')'
        )
        params.extend([like, like, like, like, like])

    rows = conn.execute(
        f'''
        SELECT
            jb.*,
            s.roster_url AS roster_url,
            s.phone AS source_phone,
            s.last_success_at,
            s.is_featured
        FROM jail_bookings jb
        LEFT JOIN jail_booking_sources s ON s.id = jb.source_id
        WHERE {' AND '.join(where)}
        ORDER BY datetime(COALESCE(jb.booking_at, jb.first_seen_at, jb.created_at)) DESC, jb.id DESC
        LIMIT 150
        ''',
        params,
    ).fetchall()

    selected_source = next((row for row in sources if row['county_slug'] == county_filter), None) if county_filter else None

    current_count_params = []
    current_count_sql = 'SELECT COUNT(*) FROM jail_bookings WHERE COALESCE(is_current, 1) = 1'
    if county_filter:
        current_count_sql += ' AND county_slug = ?'
        current_count_params.append(county_filter)
    current_count = conn.execute(current_count_sql, current_count_params).fetchone()[0]

    new_24h_params = []
    new_24h_sql = """
        SELECT COUNT(*)
        FROM jail_bookings
        WHERE datetime(COALESCE(booking_at, first_seen_at, created_at)) >= datetime('now', '-24 hours')
    """
    if county_filter:
        new_24h_sql += ' AND county_slug = ?'
        new_24h_params.append(county_filter)
    new_24h_count = conn.execute(new_24h_sql, new_24h_params).fetchone()[0]

    tracked_counties = 1 if county_page and selected_source else sum(1 for row in sources if row['county_slug'] in MAJOR_JAIL_BOOKING_COUNTIES)
    recent_runs = conn.execute(
        '''
        SELECT
            r.*,
            s.county_name
        FROM jail_booking_runs r
        LEFT JOIN jail_booking_sources s ON s.id = r.source_id
        WHERE (? = '' OR s.county_slug = ?)
        ORDER BY datetime(COALESCE(r.completed_at, r.started_at)) DESC, r.id DESC
        LIMIT 6
        ''',
        (county_filter, county_filter),
    ).fetchall()

    decorated_sources = [_decorate_jail_booking_source_row(row) for row in sources]
    decorated_runs = [_decorate_jail_booking_run_row(row) for row in recent_runs]
    selected_source = next((row for row in decorated_sources if row['county_slug'] == county_filter), None) if county_filter else None
    page_title = "New Jail Bookings"
    meta_description = "Track newly posted jail bookings from major Montana county rosters with county filters, booking timestamps, and official source links."
    canonical_url = f"{BASE_URL}/jail-bookings"
    og_title = "New Jail Bookings | Montana Blotter"
    og_description = "Recent Montana jail bookings presented with county context, timestamps, and official source links."
    hero_kicker = "Daily Booking Monitor"
    hero_title = "New jail bookings from Montana's largest county rosters"
    hero_description = (
        "This page tracks newly published jail roster entries from major Montana counties and presents them in a cleaner statewide feed. "
        "These records reflect booking or detention status only and do not imply guilt or conviction."
    )
    reset_href = "/jail-bookings"
    breadcrumb_label = "New Jail Bookings"
    if county_page and selected_source:
        page_title = f"{selected_source['county_name']} County Jail Bookings"
        meta_description = (
            f"Track recent {selected_source['county_name']} County jail bookings with booking timestamps, source freshness, "
            "and links back to the official county roster."
        )
        canonical_url = f"{BASE_URL}/jail-bookings/{selected_source['county_slug']}"
        og_title = f"{selected_source['county_name']} County Jail Bookings | Montana Blotter"
        og_description = (
            f"Recent {selected_source['county_name']} County jail bookings with official roster links and source freshness."
        )
        hero_kicker = f"{selected_source['county_name']} County Monitor"
        hero_title = f"{selected_source['county_name']} County jail bookings"
        hero_description = (
            f"This county page tracks newly published jail roster entries from the {selected_source['facility_name']}. "
            "Booking status does not imply guilt or conviction."
        )
        reset_href = selected_source['public_href']
        breadcrumb_label = f"{selected_source['county_name']} County Jail Bookings"

    return {
        'rows': [_decorate_jail_booking_row(row) for row in rows],
        'sources': decorated_sources,
        'featured_sources': [row for row in decorated_sources if row['county_slug'] in MAJOR_JAIL_BOOKING_COUNTIES],
        'recent_runs': decorated_runs,
        'selected_source': selected_source,
        'county_locked': bool(county_page and selected_source),
        'county_filter': county_filter,
        'status_filter': status_filter,
        'q': q,
        'page_title': page_title,
        'meta_description': meta_description,
        'canonical_url': canonical_url,
        'og_title': og_title,
        'og_description': og_description,
        'hero_kicker': hero_kicker,
        'hero_title': hero_title,
        'hero_description': hero_description,
        'reset_href': reset_href,
        'breadcrumb_label': breadcrumb_label,
        'summary': {
            'tracked_counties': tracked_counties,
            'current_bookings': current_count,
            'new_24h': new_24h_count,
            'featured_sources': len([row for row in sources if row['county_slug'] in MAJOR_JAIL_BOOKING_COUNTIES]),
        },
    }


def _jail_booking_admin_context(conn, county_filter='', status_filter='current', q=''):
    public_context = _jail_booking_public_context(
        conn,
        county_filter=county_filter,
        status_filter=status_filter,
        q=q,
    )
    sources = conn.execute(
        '''
        SELECT
            s.*,
            (
                SELECT COUNT(*)
                FROM jail_bookings jb
                WHERE jb.source_id = s.id
                  AND COALESCE(jb.is_current, 1) = 1
            ) AS current_count,
            (
                SELECT COUNT(*)
                FROM jail_bookings jb
                WHERE jb.source_id = s.id
                  AND datetime(COALESCE(jb.booking_at, jb.first_seen_at, jb.created_at)) >= datetime('now', '-24 hours')
            ) AS new_24h_count
        FROM jail_booking_sources s
        WHERE COALESCE(s.is_enabled, 1) = 1
          AND COALESCE(s.is_featured, 0) = 1
        ORDER BY COALESCE(s.is_featured, 0) DESC, s.county_name ASC
        '''
    ).fetchall()
    public_context['source_rows'] = [_decorate_jail_booking_source_row(row) for row in sources]
    return public_context


def _jail_roster_directory_cards():
    counties = []
    for slug, meta in COUNTY_DIRECTORY.items():
        roster_url = (meta.get('roster_url') or '').strip()
        counties.append({
            'slug': slug,
            'name': meta['name'],
            'roster_url': roster_url or None,
            'phone': meta.get('phone') or 'Call county detention center',
            'has_online_roster': bool(meta.get('has_online_roster')),
            'public_bookings_href': (
                url_for('detention.jail_bookings_county', county_slug=slug)
                if slug in MAJOR_JAIL_BOOKING_COUNTIES
                else None
            ),
        })
    counties.sort(key=lambda item: item['name'])
    online_count = sum(1 for item in counties if item['has_online_roster'])
    return {
        'counties': counties,
        'online_count': online_count,
        'phone_only_count': len(counties) - online_count,
    }


register_detention_blueprint(
    app,
    get_db=get_db,
    booking_context_loader=_jail_booking_public_context,
    roster_directory_loader=_jail_roster_directory_cards,
)


def _parse_roundup_date(value):
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ('%Y-%m-%d', '%m/%d/%y', '%m/%d/%Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except (TypeError, ValueError):
            continue
    return None


def _record_roundup_date_sql(column_name: str) -> str:
    return f"""
        CASE
            WHEN {column_name} GLOB '[0-9][0-9]/[0-9][0-9]/[0-9][0-9]' THEN
                '20' || substr({column_name}, 7, 2) || '-' || substr({column_name}, 1, 2) || '-' || substr({column_name}, 4, 2)
            WHEN {column_name} GLOB '[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]' THEN
                substr({column_name}, 7, 4) || '-' || substr({column_name}, 1, 2) || '-' || substr({column_name}, 4, 2)
            WHEN {column_name} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN
                substr({column_name}, 1, 10)
            ELSE NULL
        END
    """


def _annual_roundup_years(conn):
    record_date_sql = _record_roundup_date_sql('date')
    rows = conn.execute(
        f"""
        SELECT DISTINCT substr(record_date, 1, 4) AS year
        FROM (
            SELECT {record_date_sql} AS record_date
            FROM records
        )
        WHERE record_date IS NOT NULL
          AND substr(record_date, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'
        ORDER BY year DESC
        """
    ).fetchall()
    years = {int(row['year']) for row in rows if row['year']}

    for row in conn.execute(
        """
        SELECT incident_date
        FROM posts
        WHERE incident_date IS NOT NULL
          AND incident_date != ''
        """
    ).fetchall():
        parsed = _parse_roundup_date(row['incident_date'])
        if parsed:
            years.add(parsed.year)

    return sorted(years, reverse=True)


def _annual_roundup_context(conn, year: int):
    year_text = str(year)
    record_date_sql = _record_roundup_date_sql('date')

    post_dates = []
    for row in conn.execute(
        """
        SELECT incident_date
        FROM posts
        WHERE incident_date IS NOT NULL
          AND incident_date != ''
        """
    ).fetchall():
        parsed = _parse_roundup_date(row['incident_date'])
        if parsed and parsed.year == year:
            post_dates.append(parsed)
    post_count = len(post_dates)

    record_count = conn.execute(
        f"SELECT COUNT(*) FROM records WHERE substr({record_date_sql}, 1, 4) = ?",
        (year_text,),
    ).fetchone()[0]
    if not post_count and not record_count:
        return None

    range_row = conn.execute(
        f"""
        SELECT MIN(record_date) AS start_date,
               MAX(record_date) AS end_date
        FROM (
            SELECT {record_date_sql} AS record_date
            FROM records
        )
        WHERE substr(record_date, 1, 4) = ?
        """,
        (year_text,),
    ).fetchone()

    top_counties = []
    for row in conn.execute(
        f"""
        SELECT county, COUNT(*) AS count
        FROM records
        WHERE substr({record_date_sql}, 1, 4) = ?
          AND county IS NOT NULL
          AND county != ''
        GROUP BY county
        ORDER BY count DESC, county ASC
        LIMIT 8
        """,
        (year_text,),
    ).fetchall():
        slug = _county_slug_for_name(row['county'])
        top_counties.append({
            'name': row['county'],
            'slug': slug,
            'count': row['count'],
        })

    top_incidents = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident') AS name,
               COUNT(*) AS count
        FROM records
        WHERE substr({record_date_sql}, 1, 4) = ?
        GROUP BY COALESCE(NULLIF(incident_type, ''), NULLIF(incident, ''), 'Incident')
        ORDER BY count DESC, name ASC
        LIMIT 8
        """,
        (year_text,),
    ).fetchall()

    top_patterns = []
    for pattern in PATTERN_DEFINITIONS.values():
        clause, params = _pattern_clause(pattern['slug'], 'records')
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM records
            WHERE substr({record_date_sql}, 1, 4) = ?
              AND {clause}
            """,
            [year_text, *params],
        ).fetchone()
        count = row['count'] if row else 0
        if count:
            top_patterns.append({
                'label': pattern['label'],
                'slug': pattern['slug'],
                'count': count,
            })
    top_patterns.sort(key=lambda item: (-item['count'], item['label']))
    top_patterns = top_patterns[:6]

    related_posts = conn.execute(
        """
        SELECT slug, title, excerpt, created_at
        FROM blog_posts
        WHERE published = 1
          AND substr(created_at, 1, 4) = ?
        ORDER BY COALESCE(updated_at, created_at) DESC
        LIMIT 6
        """,
        (year_text,),
    ).fetchall()

    current_year = datetime.utcnow().year
    record_start_date = range_row['start_date'] if range_row and range_row['start_date'] else None
    record_end_date = range_row['end_date'] if range_row and range_row['end_date'] else None
    post_start_date = min(post_dates).isoformat() if post_dates else None
    post_end_date = max(post_dates).isoformat() if post_dates else None
    start_date = min(filter(None, [record_start_date, post_start_date]), default=None)
    end_date = max(filter(None, [record_end_date, post_end_date]), default=None)
    partial_label = f"Through {end_date}" if year == current_year and end_date else None

    return {
        'year': year,
        'title': f'Montana {year} Annual Crime and Public Safety Roundup',
        'meta_description': (
            f'Montana {year} annual roundup with top counties, public safety record totals, incident trends, and links into county, warrant, arrest, and pattern pages.'
        ),
        'canonical_url': f'{BASE_URL}/annual-roundups/{year}',
        'post_count': post_count,
        'record_count': record_count,
        'start_date': start_date,
        'end_date': end_date,
        'partial_label': partial_label,
        'top_counties': top_counties,
        'top_incidents': top_incidents,
        'top_patterns': top_patterns,
        'related_posts': related_posts,
    }


@app.route('/guides')
def guides_hub():
    conn = get_db()
    guide_cards = [
        {
            'href': f'/guides/{slug}',
            'label': guide['title'],
            'description': guide.get('meta_description') or guide.get('intro', ''),
        }
        for slug, guide in RESOURCE_GUIDES.items()
    ]
    featured_counties = _ranked_county_cards(conn, limit=6)
    annual_years = _annual_roundup_years(conn)
    conn.close()
    return render_template(
        'guides_hub.html',
        guide_cards=guide_cards,
        featured_counties=featured_counties,
        annual_years=annual_years,
        active_nav='blog',
        current_year=datetime.now().year,
    )


@app.route('/guides/<slug>')
def resource_guide(slug):
    guide = RESOURCE_GUIDES.get(slug)
    if not guide:
        return render_template('404.html'), 404

    conn = get_db()
    county_cards = []
    if guide.get('featured_counties'):
        county_cards = [COUNTY_DATA[county_slug] for county_slug in guide['featured_counties'] if county_slug in COUNTY_DATA]
    elif slug == 'montana-county-guides':
        county_cards = _ranked_county_cards(conn, limit=12)
    elif slug == 'how-to-find-montana-police-blotters':
        county_cards = _ranked_county_cards(conn, limit=10)
    elif slug == 'how-to-find-montana-warrants':
        county_cards = [COUNTY_DATA[s] for s in _WARRANT_COUNTIES if s in COUNTY_DATA]
    elif slug == 'how-to-find-montana-jail-rosters':
        county_cards = _ranked_county_cards(conn, limit=10, require_online_roster=True)
    else:
        county_cards = _ranked_county_cards(conn, limit=8)
    conn.close()

    return render_template(
        'resource_guide.html',
        guide=guide,
        slug=slug,
        county_cards=county_cards,
        active_nav='blog',
        current_year=datetime.now().year,
    )


@app.route('/annual-roundups')
def annual_roundups():
    conn = get_db()
    years = []
    for year in _annual_roundup_years(conn):
        context = _annual_roundup_context(conn, year)
        if context:
            years.append(context)
    conn.close()
    return render_template(
        'annual_roundups.html',
        years=years,
        active_nav='blog',
        current_year=datetime.now().year,
    )


@app.route('/annual-roundups/<int:year>')
def annual_roundup_year(year):
    conn = get_db()
    context = _annual_roundup_context(conn, year)
    conn.close()
    if context is None:
        return render_template('404.html'), 404
    return render_template(
        'annual_roundup_year.html',
        **context,
        active_nav='blog',
        current_year=datetime.now().year,
    )


@app.route('/counties')
def counties_directory():
    conn = get_db()
    post_stats = {
        row['county']: row
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS post_count, MAX(incident_date) AS last_report
            FROM posts
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }
    record_stats = {
        row['county']: row['record_count']
        for row in conn.execute(
            '''
            SELECT county, COUNT(*) AS record_count
            FROM records
            WHERE county IS NOT NULL AND county != ''
            GROUP BY county
            '''
        ).fetchall()
    }
    conn.close()

    counties = []
    for county in COUNTY_DATA.values():
        county_cities = [
            city for city in CITY_DATA.values()
            if city.get('county_slug') == county['slug']
        ]
        stats = post_stats.get(county['name'])
        counties.append({
            **county,
            'post_count': stats['post_count'] if stats else 0,
            'record_count': record_stats.get(county['name'], 0),
            'last_report': stats['last_report'] if stats else None,
            'city_count': len(county_cities),
            'county_cities': county_cities,
        })

    counties.sort(key=lambda county: (-county['post_count'], county['name']))
    return render_template(
        'counties.html',
        counties=counties,
        current_year=datetime.now().year,
    )


# ==========================================
# COUNTY PAGES
# ==========================================

COUNTY_DATA = {
    'yellowstone': {
        'slug': 'yellowstone',
        'name': 'Yellowstone',
        'seat': 'Billings',
        'phone': '406-256-2929',
        'roster_url': 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp',
        'warrant_url': 'https://www.yellowstonecountymt.gov/justicecourt/JCWarrants.asp',
        'seo_title': 'Yellowstone County Arrests, Jail Roster, and Billings Blotter',
        'og_title': 'Yellowstone County Arrests, Jail Roster, and Billings Blotter',
        'warrant_title': 'Yellowstone County Warrant Search, Billings Warrants, and Jail Roster',
        'warrant_og_title': 'Yellowstone County Warrant Search, Billings Warrants, and Jail Roster',
        'warrant_meta_description': 'Yellowstone County warrant search, Billings warrant lookup, active warrant list, and jail roster links for Yellowstone County, Montana.',
        'warrant_hero_summary': (
            "Yellowstone County warrant lookup, Billings warrant resources, active warrant list access, "
            "and jail roster links for the county's highest-intent legal searches."
        ),
        'warrant_search_targets': [
            'Yellowstone County warrants',
            'Billings warrants',
            'Yellowstone County warrant list',
            'Billings warrant lookup',
        ],
        'warrant_source_note_title': 'Billings-led warrant traffic with official county access',
        'warrant_source_note': (
            "This page is built for the searches people actually make around Billings and Yellowstone County warrants. It connects city-intent queries to the official county warrant list, detention roster, and broader Yellowstone County archive."
        ),
        'meta_description': 'Yellowstone County, Montana police blotter, arrest records, jail roster, and warrant resources for Billings, Laurel, and surrounding communities.',
        'og_description': 'Yellowstone County police blotter, arrest records, jail roster, and warrant lookup pages for Billings and the wider Yellowstone Valley.',
        'hero_summary': (
            "Billings-area police blotter coverage, Yellowstone County arrest records, jail roster links, "
            "warrant resources, and recent public safety reports for the county's busiest search market."
        ),
        'description': (
            "Yellowstone County is Montana's most populous county, home to Billings — the state's largest city. "
            "Law enforcement is handled by the Yellowstone County Sheriff's Office, the Billings Police Department, "
            "and the Laurel Police Department, among others. The county seat, Billings, sits at the junction of "
            "Interstate 90 and I-94, making it a key transit corridor that law enforcement monitors closely for "
            "drug trafficking and vehicle crime. The Yellowstone County Detention Facility is located in Billings "
            "and publishes a live inmate roster online. The county also operates a dedicated cold case unit."
        ),
        'seo_summary': [
            "This page is designed to answer the searches people actually make around Billings and Yellowstone County: police blotter, arrests, jail roster, warrants, and recent law-enforcement activity.",
            "Yellowstone is currently one of the deepest county archives on Montana Blotter, so this page can act as both a county landing page and the main internal hub for Billings, Laurel, county arrest coverage, and warrant-related searches.",
        ],
        'search_targets': [
            'Yellowstone County police blotter',
            'Billings police blotter',
            'Yellowstone County arrests',
            'Yellowstone County jail roster',
            'Yellowstone County warrants',
        ],
        'source_note_title': 'Billings-area reports plus county record links',
        'source_note': (
            "This page combines Montana Blotter archive coverage with direct links to Yellowstone County detention and warrant resources, making it the strongest local starting point for Billings and countywide record searches."
        ),
        'agencies': [
            {'name': 'Yellowstone County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-256-2929'},
            {'name': 'Billings Police Department', 'type': 'police', 'phone': '406-657-8460'},
            {'name': 'Laurel Police Department', 'type': 'police', 'phone': '406-628-4040'},
        ],
        'neighbors': [
            {'name': 'Carbon', 'slug': 'carbon'},
            {'name': 'Stillwater', 'slug': 'stillwater'},
            {'name': 'Golden Valley', 'slug': 'golden-valley'},
            {'name': 'Musselshell', 'slug': 'musselshell'},
            {'name': 'Treasure', 'slug': 'treasure'},
            {'name': 'Big Horn', 'slug': 'big-horn'},
        ],
    },
    'gallatin': {
        'slug': 'gallatin',
        'name': 'Gallatin',
        'seat': 'Bozeman',
        'phone': '406-582-2100',
        'roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates',
        'warrant_url': None,
        'seo_title': 'Gallatin County Arrests, Bozeman Blotter, and Jail Roster',
        'og_title': 'Gallatin County Arrests, Bozeman Blotter, and Jail Roster',
        'warrant_title': 'Gallatin County Warrant Search, Bozeman Warrants, and Jail Roster',
        'warrant_og_title': 'Gallatin County Warrant Search, Bozeman Warrants, and Jail Roster',
        'warrant_meta_description': 'Gallatin County warrant search guide for Bozeman, Belgrade, and surrounding communities, with court portal and sheriff contact steps.',
        'warrant_hero_summary': (
            "Gallatin County warrant search guidance for Bozeman-area users, with the fastest path into "
            "state court lookup tools, sheriff contact, and detention follow-up resources."
        ),
        'warrant_search_targets': [
            'Gallatin County warrants',
            'Bozeman warrants',
            'Gallatin County warrant search',
            'Bozeman warrant lookup',
        ],
        'warrant_source_note_title': 'Bozeman-focused warrant guide without a county list',
        'warrant_source_note': (
            "Gallatin County does not currently publish a dedicated county warrant list, so this page is optimized as a step-by-step lookup guide for Bozeman and Gallatin County users who need the court portal, sheriff contact, and jail roster in one place."
        ),
        'meta_description': 'Gallatin County, Montana police blotter, arrest records, jail roster links, and public safety reports for Bozeman, Belgrade, and surrounding communities.',
        'og_description': 'Gallatin County police blotter, arrest records, and jail roster resources for Bozeman and the wider Gallatin Valley.',
        'hero_summary': (
            "Bozeman-area police blotter coverage, Gallatin County arrest records, jail roster links, "
            "and recent public safety reports for one of Montana's fastest-growing counties."
        ),
        'description': (
            "Gallatin County, home to Bozeman and Montana State University, is one of the fastest-growing counties "
            "in the United States. The Gallatin County Sheriff's Office patrols the unincorporated areas of the county, "
            "while the Bozeman Police Department covers the city. West Yellowstone, at the north entrance to Yellowstone "
            "National Park, is also in Gallatin County and sees significant seasonal traffic and related law enforcement "
            "activity. The county has seen a rise in property crime and traffic incidents in step with its rapid population "
            "growth, and drug enforcement has become an increasing priority as meth and fentanyl distribution networks "
            "have expanded into the Bozeman area."
        ),
        'seo_summary': [
            "Gallatin County serves overlapping search intent from users looking for Bozeman police activity, county arrest coverage, jail information, and broader public safety reporting tied to the county's rapid growth.",
            "This page is built to capture those county-level searches while pushing users into Bozeman, Belgrade, and other city pages when they need a narrower local record trail than the county archive alone can provide.",
        ],
        'search_targets': [
            'Gallatin County police blotter',
            'Bozeman police blotter',
            'Gallatin County arrests',
            'Gallatin County jail roster',
            'Belgrade police blotter',
        ],
        'source_note_title': 'Bozeman-led county coverage with detention links',
        'source_note': (
            "Montana Blotter uses Gallatin County report coverage as the main archive layer here, then routes readers into Bozeman-area city pages and the official county detention roster for current inmate checks."
        ),
        'agencies': [
            {'name': 'Gallatin County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-582-2100'},
            {'name': 'Bozeman Police Department', 'type': 'police', 'phone': '406-582-2000'},
            {'name': 'West Yellowstone Police Department', 'type': 'police', 'phone': '406-646-7600'},
        ],
        'neighbors': [
            {'name': 'Park', 'slug': 'park'},
            {'name': 'Meagher', 'slug': 'meagher'},
            {'name': 'Broadwater', 'slug': 'broadwater'},
            {'name': 'Jefferson', 'slug': 'jefferson'},
            {'name': 'Madison', 'slug': 'madison'},
        ],
    },
    'hill': {
        'slug': 'hill',
        'name': 'Hill',
        'seat': 'Havre',
        'phone': '406-265-5481',
        'roster_url': None,
        'warrant_url': None,
        'seo_title': 'Hill County Arrests, Havre Police Blotter, and Jail Roster',
        'og_title': 'Hill County Arrests, Havre Police Blotter, and Jail Roster',
        'warrant_title': 'Hill County Warrant Search, Havre Arrests, and Jail Lookup',
        'warrant_og_title': 'Hill County Warrant Search, Havre Arrests, and Jail Lookup',
        'warrant_meta_description': 'Hill County warrant search guide for Havre-area readers, with statewide court lookup, sheriff contact steps, and jail follow-up resources.',
        'warrant_hero_summary': (
            "Hill County warrant lookup guidance for Havre-area users, "
            "with the court portal, sheriff contact path, and detention follow-up links in one place."
        ),
        'warrant_search_targets': [
            'Hill County warrants',
            'Havre warrants',
            'Hill County warrant search',
            'Havre warrant lookup',
        ],
        'warrant_source_note_title': 'Hi-Line warrant guide tied to Havre search demand',
        'warrant_source_note': (
            "Hill County does not publish a dedicated live warrant list, so this page is built as a practical lookup path for Havre and Hi-Line readers who need court search steps and sheriff contact details."
        ),
        'meta_description': 'Hill County, Montana police blotter, Havre arrests, jail roster links, and public safety reports for Hi-Line readers.',
        'og_description': 'Hill County police blotter, Havre arrests, jail roster resources, and public safety reports for north-central Montana.',
        'hero_summary': (
            "Havre-led police blotter coverage, Hill County arrest records, jail roster links, "
            "and recent public safety reporting from Montana's Hi-Line."
        ),
        'description': (
            "Hill County sits along Montana's Hi-Line near the Canadian border and is anchored by Havre, the county "
            "seat and largest city. The Hill County Sheriff's Office works alongside the Havre Police Department to "
            "cover a large rural footprint, cross-border highway traffic, and the regional hub around Montana State "
            "University-Northern. Montana Blotter receives regular Havre Police activity, making Hill County one of "
            "the most visible county archives on the site today."
        ),
        'seo_summary': [
            "Hill County already has deeper archive coverage than most people would expect, especially because Havre activity feeds directly into the county story.",
            "This page is structured to capture Hi-Line searches around Havre police activity, Hill County arrests, and jail-roster lookups before routing readers into detention and warrant follow-up paths.",
        ],
        'search_targets': [
            'Hill County police blotter',
            'Havre police blotter',
            'Hill County arrests',
            'Hill County jail roster',
            'Havre arrests',
        ],
        'source_note_title': 'Hi-Line county archive with Havre-led local intent',
        'source_note': (
            "Montana Blotter uses Hill County as the county hub for Havre-driven report coverage, then routes readers into detention, warrants, and nearby city pages when they need a narrower follow-up path."
        ),
        'agencies': [
            {'name': 'Hill County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-265-5481'},
            {'name': 'Havre Police Department', 'type': 'police', 'phone': '406-265-4397'},
        ],
        'neighbors': [
            {'name': 'Liberty', 'slug': 'liberty'},
            {'name': 'Chouteau', 'slug': 'chouteau'},
            {'name': 'Blaine', 'slug': 'blaine'},
            {'name': 'Phillips', 'slug': 'phillips'},
        ],
    },
    'carbon': {
        'slug': 'carbon',
        'name': 'Carbon',
        'seat': 'Red Lodge',
        'phone': '406-446-1234',
        'roster_url': 'https://carbonmt.gov/sheriff/',
        'warrant_url': None,
        'seo_title': 'Carbon County Arrests, Red Lodge Blotter, and Jail Resources',
        'og_title': 'Carbon County Arrests, Red Lodge Blotter, and Jail Resources',
        'warrant_title': 'Carbon County Warrant Search, Red Lodge Arrests, and Jail Links',
        'warrant_og_title': 'Carbon County Warrant Search, Red Lodge Arrests, and Jail Links',
        'warrant_meta_description': 'Carbon County warrant search guide for Red Lodge and Bridger readers, with sheriff contact, statewide court lookup, and detention links.',
        'warrant_hero_summary': (
            "Carbon County warrant lookup guidance for Red Lodge-area readers, "
            "with the fastest path into sheriff contact, statewide court search, and jail follow-up."
        ),
        'warrant_search_targets': [
            'Carbon County warrants',
            'Red Lodge warrants',
            'Carbon County warrant search',
            'Red Lodge warrant lookup',
        ],
        'warrant_source_note_title': 'Mountain-corridor warrant guide with county follow-up paths',
        'warrant_source_note': (
            "Carbon County does not publish a dedicated live warrant list, so this page is built to catch Red Lodge and Carbon County warrant searches and route them into the sheriff office, court portal, and detention workflow."
        ),
        'meta_description': 'Carbon County, Montana police blotter, Red Lodge arrests, jail resources, and public safety pages for the Beartooth foothills.',
        'og_description': 'Carbon County police blotter, Red Lodge arrests, jail resources, and public safety pages for south-central Montana.',
        'hero_summary': (
            "Red Lodge-area blotter coverage, Carbon County arrest resources, jail links, "
            "and public safety reporting for the Beartooth foothills and surrounding communities."
        ),
        'description': (
            "Carbon County covers Red Lodge, Bridger, Joliet, and the Beartooth foothills in south-central Montana. "
            "The Carbon County Sheriff's Office works with Red Lodge Police and nearby local agencies across a region "
            "that blends rural highway traffic, tourism, and mountain recreation. Montana Blotter already carries a "
            "small amount of Carbon County activity through CrimeMapping-connected agency coverage, and this county "
            "page creates a stable landing point as that archive grows."
        ),
        'seo_summary': [
            "Carbon County is a strategic second-tier county because Red Lodge and the broader mountain corridor create strong branded search intent even before the archive gets deep.",
            "This page is built to answer Carbon County blotter, Red Lodge arrest, and jail-resource searches first, then grow into a larger county archive over time.",
        ],
        'search_targets': [
            'Carbon County police blotter',
            'Red Lodge police blotter',
            'Carbon County arrests',
            'Carbon County jail roster',
            'Red Lodge arrests',
        ],
        'source_note_title': 'Red Lodge-led county page with detention follow-up',
        'source_note': (
            "Montana Blotter uses this page as the Carbon County hub for Red Lodge and nearby communities, then routes readers into detention tools, neighboring county pages, and city-level follow-up where available."
        ),
        'agencies': [
            {'name': 'Carbon County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-446-1234'},
            {'name': 'Red Lodge Police Department', 'type': 'police', 'phone': '406-446-1234'},
            {'name': 'Bridger Police Department', 'type': 'police', 'phone': '406-662-3676'},
        ],
        'neighbors': [
            {'name': 'Yellowstone', 'slug': 'yellowstone'},
            {'name': 'Stillwater', 'slug': 'stillwater'},
            {'name': 'Big Horn', 'slug': 'big-horn'},
            {'name': 'Park', 'slug': 'park'},
            {'name': 'Sweet Grass', 'slug': 'sweet-grass'},
        ],
    },
    'missoula': {
        'slug': 'missoula',
        'name': 'Missoula',
        'seat': 'Missoula',
        'phone': '406-258-4780',
        'roster_url': 'https://webapps.missoulacounty.us/jailroster/Inmates',
        'warrant_url': None,
        'seo_title': 'Missoula County Arrests, Jail Roster, and Police Blotter',
        'og_title': 'Missoula County Arrests, Jail Roster, and Police Blotter',
        'warrant_title': 'Missoula County Warrant Search, Jail Roster, and Court Lookup',
        'warrant_og_title': 'Missoula County Warrant Search, Jail Roster, and Court Lookup',
        'warrant_meta_description': 'Missoula County warrant search guide with steps for Missoula warrant lookup, court portal searches, and jail roster follow-up.',
        'warrant_hero_summary': (
            "Missoula County warrant lookup guidance, Missoula-area court search steps, "
            "and detention follow-up resources for western Montana users."
        ),
        'warrant_search_targets': [
            'Missoula County warrants',
            'Missoula warrants',
            'Missoula County warrant search',
            'Missoula warrant lookup',
        ],
        'warrant_source_note_title': 'Missoula-first warrant guide with county detention follow-up',
        'warrant_source_note': (
            "Missoula County has strong warrant-related search demand even without a county-published list. This page is designed to capture those searches and route readers into the statewide court portal, Missoula County detention tools, and broader county coverage."
        ),
        'meta_description': 'Missoula County, Montana police blotter, arrest records, jail roster links, and recent public safety reports for Missoula and surrounding communities.',
        'og_description': 'Missoula County police blotter, arrest records, and jail roster resources for western Montana readers.',
        'hero_summary': (
            "Missoula police blotter coverage, Missoula County arrest records, jail roster links, "
            "and recent public safety reports for western Montana's largest urban center."
        ),
        'description': (
            "Missoula County is home to the University of Montana and the city of Missoula, western Montana's largest "
            "urban center. The Missoula County Sheriff's Office and Missoula Police Department are the primary law "
            "enforcement agencies. In 2024, the Missoula Police Department reported a 9.9% decrease in felony violent "
            "crime, including 36 robberies, 357 assaults, and 2 homicides — though theft and disorderly conduct "
            "increased. The county also has an active drug task force that seized nearly 40,000 dosage units of "
            "fentanyl in 2024. The Missoula County Detention Center publishes a real-time inmate roster updated "
            "throughout the day."
        ),
        'seo_summary': [
            "Missoula County has stronger brand and search demand than its current archive depth would suggest, so this page needs to answer the obvious practical searches first: police blotter, arrests, jail roster, and recent county activity.",
            "The goal here is to make Missoula County a durable search landing page now, then let the archive deepen underneath it over time as more local reporting and linked records are added.",
        ],
        'search_targets': [
            'Missoula police blotter',
            'Missoula County police blotter',
            'Missoula County arrests',
            'Missoula County jail roster',
            'Missoula recent arrests',
        ],
        'source_note_title': 'Missoula-centered archive with live detention links',
        'source_note': (
            "This page combines Montana Blotter's Missoula-area reporting with direct access to the Missoula County detention roster so readers can move from summaries into current inmate and court follow-up workflows."
        ),
        'agencies': [
            {'name': 'Missoula County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-258-4780'},
            {'name': 'Missoula Police Department', 'type': 'police', 'phone': '406-552-6300'},
        ],
        'neighbors': [
            {'name': 'Ravalli', 'slug': 'ravalli'},
            {'name': 'Mineral', 'slug': 'mineral'},
            {'name': 'Powell', 'slug': 'powell'},
            {'name': 'Lake', 'slug': 'lake'},
            {'name': 'Sanders', 'slug': 'sanders'},
            {'name': 'Granite', 'slug': 'granite'},
        ],
    },
    'cascade': {
        'slug': 'cascade',
        'name': 'Cascade',
        'seat': 'Great Falls',
        'phone': '406-454-6840',
        'roster_url': 'https://www.cascadecountymt.gov/314/Inmate-Roster',
        'warrant_url': 'https://greatfallsmt.net/municipalcourt/warrants-list',
        'seo_title': 'Cascade County Arrests, Great Falls Warrants, and Jail Roster',
        'og_title': 'Cascade County Arrests, Great Falls Warrants, and Jail Roster',
        'warrant_title': 'Cascade County Warrant Search, Great Falls Warrants, and Jail Roster',
        'warrant_og_title': 'Cascade County Warrant Search, Great Falls Warrants, and Jail Roster',
        'warrant_meta_description': 'Cascade County warrant search, Great Falls warrant list, active warrant lookup, and jail roster links for Cascade County, Montana.',
        'warrant_hero_summary': (
            "Cascade County warrant lookup, Great Falls warrant list access, "
            "and county jail roster links for north-central Montana legal searches."
        ),
        'warrant_search_targets': [
            'Cascade County warrants',
            'Great Falls warrants',
            'Cascade County warrant list',
            'Great Falls warrant lookup',
        ],
        'warrant_source_note_title': 'Great Falls warrant demand backed by a live city list',
        'warrant_source_note': (
            "This is one of the stronger warrant pages on the site because Great Falls has both clear city-level search demand and a live warrant resource. The page ties that into Cascade County detention and county archive links."
        ),
        'meta_description': 'Cascade County, Montana police blotter, arrest records, Great Falls warrant lookup, jail roster links, and recent public safety reports.',
        'og_description': 'Cascade County police blotter, arrest records, Great Falls warrant resources, and jail roster links for north-central Montana.',
        'hero_summary': (
            "Great Falls and Cascade County police blotter coverage, arrest records, jail roster links, "
            "and warrant resources for one of north-central Montana's highest-interest public safety markets."
        ),
        'description': (
            "Cascade County is home to Great Falls, Montana's third-largest city and the county seat. The county is "
            "served by the Cascade County Sheriff's Office and the Great Falls Police Department. Great Falls sits "
            "along the Missouri River and is a regional hub for north-central Montana. Malmstrom Air Force Base, "
            "located within the city, brings a substantial military population to the area. The Great Falls Municipal "
            "Court publishes an active warrant list online. Cascade County Detention Center maintains a public inmate "
            "roster with current bookings."
        ),
        'seo_summary': [
            "Cascade County captures a mix of county-level and Great Falls-specific search intent, so this page is built to serve both users looking for a county archive and users trying to find warrant, arrest, and jail information tied to Great Falls.",
            "Because Cascade has both active warrant and detention links plus a growing report archive, it is one of the better candidates on the site for ranking across multiple practical query types instead of just a single blotter term.",
        ],
        'search_targets': [
            'Cascade County police blotter',
            'Great Falls police blotter',
            'Cascade County arrests',
            'Cascade County jail roster',
            'Great Falls warrants',
        ],
        'source_note_title': 'Great Falls-centered coverage with official county links',
        'source_note': (
            "Montana Blotter surfaces recent Cascade County reporting and connects it to the official Cascade detention roster and Great Falls warrant list so readers can move from summaries into the underlying public-record systems."
        ),
        'agencies': [
            {'name': 'Cascade County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-454-6840'},
            {'name': 'Great Falls Police Department', 'type': 'police', 'phone': '406-455-8500'},
        ],
        'neighbors': [
            {'name': 'Chouteau', 'slug': 'chouteau'},
            {'name': 'Judith Basin', 'slug': 'judith-basin'},
            {'name': 'Meagher', 'slug': 'meagher'},
            {'name': 'Lewis and Clark', 'slug': 'lewis-and-clark'},
            {'name': 'Teton', 'slug': 'teton'},
        ],
    },
    'flathead': {
        'slug': 'flathead',
        'name': 'Flathead',
        'seat': 'Kalispell',
        'phone': '406-758-5610',
        'roster_url': 'https://apps.flathead.mt.gov/jailroster/',
        'warrant_url': 'https://apps.flathead.mt.gov/warrants/warrants_list.php',
        'seo_title': 'Flathead County Blotter, Warrants, Arrests, and Jail Roster',
        'og_title': 'Flathead County Blotter, Warrants, Arrests, and Jail Roster',
        'warrant_title': 'Flathead County Warrant Search, Kalispell Warrants, and Jail Roster',
        'warrant_og_title': 'Flathead County Warrant Search, Kalispell Warrants, and Jail Roster',
        'warrant_meta_description': 'Flathead County warrant search, Kalispell warrant lookup, active warrant list, and jail roster links for Whitefish, Kalispell, and the Flathead Valley.',
        'warrant_hero_summary': (
            "Flathead County warrant lookup, Kalispell-area warrant list access, "
            "and jail roster links for the broader Flathead Valley search market."
        ),
        'warrant_search_targets': [
            'Flathead County warrants',
            'Kalispell warrants',
            'Flathead County warrant list',
            'Whitefish warrants',
        ],
        'warrant_source_note_title': 'Flathead Valley warrant traffic with county list access',
        'warrant_source_note': (
            "Flathead County is one of the best warrant targets on the site because county-level and city-level searches overlap heavily across Kalispell, Whitefish, and Columbia Falls. This page is the county anchor for that traffic."
        ),
        'meta_description': 'Flathead County, Montana police blotter, arrest records, jail roster, and warrant resources for Kalispell, Whitefish, and Columbia Falls.',
        'og_description': 'Flathead County police blotter, arrest records, warrant lookup, and jail roster coverage for Kalispell, Whitefish, and the Flathead Valley.',
        'hero_summary': (
            "Flathead Valley police blotter coverage, arrest records, jail roster links, and warrant resources "
            "for Kalispell, Whitefish, Columbia Falls, and the wider northwest Montana search market."
        ),
        'description': (
            "Flathead County is located in northwest Montana and includes Kalispell, Whitefish, and Columbia Falls. "
            "It borders Glacier National Park to the east and Flathead Lake — the largest natural freshwater lake west "
            "of the Mississippi — to the south. The Flathead County Sheriff's Office and Kalispell Police Department "
            "are the primary agencies. The county's outdoor recreation economy and proximity to the Canadian border "
            "create unique law enforcement challenges. The Flathead Beacon newspaper publishes a detailed daily police "
            "blotter. Flathead County offers a public warrant list and live inmate roster online."
        ),
        'seo_summary': [
            "Flathead County supports some of the strongest local-intent queries in Montana because people often search at both the county and city level for Kalispell, Whitefish, and Columbia Falls police activity.",
            "This page is built as the main Flathead Valley records hub, with internal paths into city pages, county arrest coverage, the live jail roster, and the official county warrant list for users who need more than a headline summary.",
        ],
        'search_targets': [
            'Flathead County police blotter',
            'Kalispell police blotter',
            'Whitefish police blotter',
            'Flathead County jail roster',
            'Flathead County warrants',
        ],
        'source_note_title': 'Flathead Valley archive plus county warrant and roster links',
        'source_note': (
            "This page ties Montana Blotter coverage to Flathead County's official warrant and detention tools while also routing readers into Kalispell, Whitefish, and Columbia Falls pages for narrower local search intent."
        ),
        'agencies': [
            {'name': 'Flathead County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-758-5610'},
            {'name': 'Kalispell Police Department', 'type': 'police', 'phone': '406-758-7780'},
            {'name': 'Whitefish Police Department', 'type': 'police', 'phone': '406-863-2420'},
            {'name': 'Columbia Falls Police Department', 'type': 'police', 'phone': '406-892-2222'},
        ],
        'neighbors': [
            {'name': 'Lake', 'slug': 'lake'},
            {'name': 'Lincoln', 'slug': 'lincoln'},
            {'name': 'Sanders', 'slug': 'sanders'},
            {'name': 'Glacier', 'slug': 'glacier'},
            {'name': 'Pondera', 'slug': 'pondera'},
        ],
    },
    'lewis-and-clark': {
        'slug': 'lewis-and-clark',
        'name': 'Lewis and Clark',
        'seat': 'Helena',
        'phone': '406-447-8270',
        'roster_url': 'https://www.lccountymt.gov/Sheriff/Detention-Center',
        'warrant_url': 'https://www.helenamt.gov/Departments/Municipal-Court/Arrest-Warrants-Defendants-in-Custody',
        'seo_title': 'Lewis and Clark County Blotter, Helena Warrants, and Jail Roster',
        'og_title': 'Lewis and Clark County Blotter, Helena Warrants, and Jail Roster',
        'warrant_title': 'Lewis and Clark County Warrant Search, Helena Warrants, and Jail Roster',
        'warrant_og_title': 'Lewis and Clark County Warrant Search, Helena Warrants, and Jail Roster',
        'warrant_meta_description': 'Lewis and Clark County warrant search, Helena warrant lookup, active warrant list, and jail roster links for the capital region.',
        'warrant_hero_summary': (
            "Lewis and Clark County warrant lookup, Helena warrant list access, "
            "and jail roster links for one of Montana's strongest city-plus-county legal search markets."
        ),
        'warrant_search_targets': [
            'Lewis and Clark County warrants',
            'Helena warrants',
            'Helena warrant list',
            'Lewis and Clark warrant search',
        ],
        'warrant_source_note_title': 'Helena warrant traffic with county detention follow-up',
        'warrant_source_note': (
            "This page serves both Helena and Lewis and Clark County warrant searches, then routes readers into the county detention roster and Helena police coverage for broader follow-up context."
        ),
        'meta_description': 'Lewis and Clark County, Montana police blotter, arrest records, Helena warrant lookup, jail roster links, and recent public safety reports.',
        'og_description': 'Lewis and Clark County police blotter, Helena warrant resources, arrest records, and jail roster links.',
        'hero_summary': (
            "Helena-area police blotter coverage, Lewis and Clark County arrest records, jail roster links, "
            "and warrant resources for Montana's capital-region search traffic."
        ),
        'description': (
            "Lewis and Clark County is the home of Helena, Montana's state capital. As the seat of state government, "
            "the county hosts the Montana Legislature, the Governor's office, and numerous state agencies. The Lewis "
            "and Clark County Sheriff's Office and Helena Police Department are the primary law enforcement agencies. "
            "The Helena Municipal Court publishes a list of active arrest warrants and defendants in custody online. "
            "Montana Blotter currently receives daily activity reports directly from the Helena Police Department, "
            "making Lewis and Clark County one of our most consistently covered areas."
        ),
        'seo_summary': [
            "Lewis and Clark County is one of the site's more consistent archives, and it benefits from a clear overlap between county-level search intent and Helena-specific searches around police activity, warrants, and arrests.",
            "Because the county already has both detention and warrant resources online, this page can compete for more practical high-intent queries than a standard county blotter page that only summarizes reports.",
        ],
        'search_targets': [
            'Lewis and Clark County police blotter',
            'Helena police blotter',
            'Lewis and Clark County arrests',
            'Helena warrants',
            'Lewis and Clark County jail roster',
        ],
        'source_note_title': 'Helena report coverage plus warrant and jail resources',
        'source_note': (
            "Montana Blotter pairs Helena-area report coverage with the Lewis and Clark detention roster and Helena warrant resource so readers can move from daily activity into official follow-up sources."
        ),
        'agencies': [
            {'name': 'Lewis and Clark County Sheriff\'s Office', 'type': 'sheriff', 'phone': '406-447-8270'},
            {'name': 'Helena Police Department', 'type': 'police', 'phone': '406-442-3233'},
            {'name': 'East Helena Police Department', 'type': 'police', 'phone': '406-227-8222'},
        ],
        'neighbors': [
            {'name': 'Cascade', 'slug': 'cascade'},
            {'name': 'Meagher', 'slug': 'meagher'},
            {'name': 'Broadwater', 'slug': 'broadwater'},
            {'name': 'Jefferson', 'slug': 'jefferson'},
            {'name': 'Powell', 'slug': 'powell'},
            {'name': 'Teton', 'slug': 'teton'},
        ],
    },
    'silver-bow': {
        'slug': 'silver-bow',
        'name': 'Silver Bow',
        'seat': 'Butte',
        'phone': '406-497-1120',
        'roster_url': 'https://co.silverbow.mt.us/3274/Detention-Center',
        'warrant_url': None,
        'description': (
            "Silver Bow County is a consolidated city-county government — the City and County of Butte-Silver Bow — "
            "making it one of the few such unified governments in the western United States. Butte has a rich mining "
            "history and is home to the Berkeley Pit Superfund site. The Butte-Silver Bow Law Enforcement Division "
            "handles both municipal and county law enforcement functions. The Silver Bow County Detention Center "
            "maintains inmate booking information. Butte is located at the junction of I-90 and I-15, making it a "
            "significant node in Montana's highway network and a transit point for drug trafficking routes."
        ),
        'agencies': [
            {'name': 'Butte-Silver Bow Law Enforcement', 'type': 'police', 'phone': '406-497-1120'},
        ],
        'neighbors': [
            {'name': 'Deer Lodge', 'slug': 'deer-lodge'},
            {'name': 'Jefferson', 'slug': 'jefferson'},
            {'name': 'Beaverhead', 'slug': 'beaverhead'},
            {'name': 'Madison', 'slug': 'madison'},
            {'name': 'Granite', 'slug': 'granite'},
        ],
    },
}


def _ensure_county_shells():
    for county_name in config.MONTANA_COUNTIES:
        slug = _slugify_key(county_name)
        if slug in COUNTY_DATA:
            continue

        directory = COUNTY_DIRECTORY.get(slug, {})
        roster_url = directory.get('roster_url')
        phone = directory.get('phone')
        roster_sentence = (
            "The county publishes an online jail roster that readers can use as a starting point for inmate status checks."
            if roster_url else
            "The county does not appear to publish a public online jail roster, so readers may need to rely on VINELink or direct sheriff contact for detention information."
        )
        COUNTY_DATA[slug] = {
            'slug': slug,
            'name': county_name,
            'seat': None,
            'phone': phone,
            'roster_url': roster_url,
            'warrant_url': None,
            'description': (
                f"{county_name} County is part of Montana Blotter's statewide public records expansion. "
                f"This county landing page is designed to connect readers to local arrest coverage, jail roster resources, warrant guidance, and any agency reporting that becomes available over time. "
                f"{roster_sentence}"
            ),
            'agencies': [
                {
                    'name': f"{county_name} County Sheriff's Office",
                    'type': 'sheriff',
                    'phone': phone,
                },
            ],
            'neighbors': [],
        }


_ensure_county_shells()


@app.route('/county/<slug>')
def county_page(slug):
    county = COUNTY_DATA.get(slug)
    if not county:
        return render_template('404.html'), 404

    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10

    conn = get_db()

    count_row = conn.execute(
        'SELECT COUNT(*) FROM posts WHERE county = ?', (county['name'],)
    ).fetchone()
    post_count = count_row[0] if count_row else 0
    total_pages = max(1, (post_count + per_page - 1) // per_page)

    posts = conn.execute(
        """SELECT posts.*, blotters.county AS blotter_county
           FROM posts
           JOIN blotters ON posts.blotter_id = blotters.id
           WHERE posts.county = ?
           ORDER BY posts.incident_date DESC, posts.created_at DESC
           LIMIT ? OFFSET ?""",
        (county['name'], per_page, (page - 1) * per_page)
    ).fetchall()

    record_count = conn.execute(
        'SELECT COUNT(*) FROM records WHERE county = ?', (county['name'],)
    ).fetchone()[0]

    top_incidents = conn.execute(
        """
        SELECT COALESCE(NULLIF(incident_type, ''), 'Other') AS incident_type,
               COUNT(*) AS count
        FROM records
        WHERE county = ?
        GROUP BY COALESCE(NULLIF(incident_type, ''), 'Other')
        ORDER BY count DESC, incident_type ASC
        LIMIT 8
        """,
        (county['name'],)
    ).fetchall()

    recent_records = conn.execute(
        """
        SELECT
            records.id,
            records.date,
            records.time,
            COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_label,
            records.location,
            posts.id AS post_id
        FROM records
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE records.county = ?
        ORDER BY records.date DESC, records.time DESC, records.id DESC
        LIMIT 8
        """,
        (county['name'],)
    ).fetchall()
    recent_records = _annotate_recent_records(conn, recent_records)

    agency_coverage = conn.execute(
        """
        SELECT COALESCE(NULLIF(agency_name, ''), 'Unknown agency') AS agency_name,
               COUNT(*) AS report_count
        FROM posts
        WHERE county = ?
        GROUP BY COALESCE(NULLIF(agency_name, ''), 'Unknown agency')
        ORDER BY report_count DESC, agency_name ASC
        LIMIT 8
        """,
        (county['name'],)
    ).fetchall()

    source_method_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(blotters.source_type, ''), 'pdf') AS source_type,
               COUNT(*) AS count
        FROM posts
        JOIN blotters ON blotters.id = posts.blotter_id
        WHERE posts.county = ?
        GROUP BY COALESCE(NULLIF(blotters.source_type, ''), 'pdf')
        ORDER BY count DESC, source_type ASC
        LIMIT 4
        """,
        (county['name'],)
    ).fetchall()

    last_row = conn.execute(
        'SELECT incident_date FROM posts WHERE county = ? ORDER BY incident_date DESC LIMIT 1',
        (county['name'],)
    ).fetchone()
    last_report = last_row['incident_date'] if last_row else None
    latest_weekly_digest = _latest_weekly_digest(conn)
    bail_ad_placements = _bail_ad_public_placements(conn, county=county['name'])

    conn.close()

    county_cities = [
        city for city in CITY_DATA.values()
        if city.get('county_slug') == county['slug']
    ]
    linked_neighbors = [
        COUNTY_DATA[neighbor['slug']]
        for neighbor in county.get('neighbors', [])
        if neighbor.get('slug') in COUNTY_DATA
    ]

    # Build pagination rel links for SEO
    pagination_rels = []
    base_canonical = f"https://montanablotter.com/county/{slug}"
    if page > 1:
        pagination_rels.append({"rel": "prev", "href": f"{base_canonical}?page={page - 1}"})
    if page < total_pages:
        pagination_rels.append({"rel": "next", "href": f"{base_canonical}?page={page + 1}"})

    return render_template(
        'county_page.html',
        county=county,
        posts=posts,
        post_count=post_count,
        record_count=record_count,
        top_incidents=top_incidents,
        recent_records=recent_records,
        agency_coverage=agency_coverage,
        source_methods=_source_method_rollup(source_method_rows),
        county_cities=county_cities,
        pattern_links=_pattern_links_for_county(county['slug']),
        linked_neighbors=linked_neighbors,
        bail_ad_placements=bail_ad_placements,
        last_report=last_report,
        page_last_updated=last_report,
        latest_weekly_digest=latest_weekly_digest,
        page=page,
        total_pages=total_pages,
        current_year=datetime.now().year,
        canonical_url=base_canonical if page == 1 else f"{base_canonical}?page={page}",
        pagination_rels=pagination_rels,
    )


# ==========================================
# CITY PAGES
# ==========================================

CITY_DATA = {
    'billings': {
        'slug': 'billings',
        'name': 'Billings',
        'county': 'Yellowstone',
        'county_slug': 'yellowstone',
        'county_roster_url': 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp',
        'pd_name': 'Billings Police Department',
        'pd_phone': '406-657-8460',
        'pd_url': 'https://www.billingsmt.gov/2564/Police-Department',
        'pd_records_url': 'https://www.billingsmt.gov/2874/Warrants',
        'warrant_url': 'https://www.billingsmt.gov/2874/Warrants',
        'municipal_court_url': None,
        'search_terms': ['Billings', 'Billings Police'],
        'seo_title': 'Billings Police Blotter, Warrants, and Yellowstone Jail Roster',
        'og_title': 'Billings Police Blotter, Warrants, and Yellowstone Jail Roster',
        'meta_description': 'Billings, Montana police blotter, daily activity reports, arrest resources, and warrant links from the Billings Police Department and Yellowstone County.',
        'og_description': 'Billings police blotter, daily activity reports, warrant links, and Yellowstone County arrest resources.',
        'hero_summary': (
            "Billings police blotter coverage, recent activity reports, Yellowstone County arrest resources, "
            "and warrant links for Montana's largest city."
        ),
        'description': (
            "The Billings Police Department serves Montana's largest city, with a population of around 120,000. "
            "Billings sits at the crossroads of Interstate 90 and I-94 in Yellowstone County, making it a major "
            "commercial and transportation hub for the region. The department operates multiple divisions including "
            "patrol, investigations, traffic, and a dedicated drug task force that works alongside county and federal "
            "agencies to combat meth and fentanyl distribution in the Yellowstone Valley. The Billings Police "
            "Department publishes regular crime statistics and participates in the national Project Safe Neighborhoods "
            "initiative."
        ),
        'seo_summary': [
            "Billings is one of the clearest city-level search terms in the state, so this page needs to answer the obvious local queries directly: police blotter, arrests, warrants, and recent daily activity.",
            "The goal is to make this the main Billings entry page while still routing readers into Yellowstone County arrest coverage, the county jail roster, and city warrant tools when they need the next step beyond a summary.",
        ],
        'search_targets': [
            'Billings police blotter',
            'Billings police activity today',
            'Billings arrests',
            'Billings warrants',
            'Yellowstone County jail roster',
        ],
        'source_note_title': 'Billings Police coverage with Yellowstone County links',
        'source_note': (
            "This page combines Billings-focused report coverage with Yellowstone County jail resources and Billings warrant access so city-intent searches have a clear path into official follow-up tools."
        ),
        'nearby': [
            {'name': 'Laurel', 'slug': 'laurel'},
            {'name': 'Hardin', 'slug': 'hardin'},
        ],
    },
    'missoula': {
        'slug': 'missoula',
        'name': 'Missoula',
        'county': 'Missoula',
        'county_slug': 'missoula',
        'county_roster_url': 'https://webapps.missoulacounty.us/jailroster/Inmates',
        'pd_name': 'Missoula Police Department',
        'pd_phone': '406-552-6300',
        'pd_url': 'https://www.ci.missoula.mt.us/212/Police-Department',
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': 'https://www.ci.missoula.mt.us/335/Crime-Activity',
        'search_terms': ['Missoula', 'Missoula Police'],
        'description': (
            "The Missoula Police Department serves a city of approximately 75,000 people in western Montana, home "
            "to the University of Montana. In 2024, the department reported a 9.9% decrease in felony violent crime, "
            "with 36 robberies, 357 assaults, and 2 homicides. Theft and disorderly conduct increased during the "
            "same period. The MPD's drug task force seized nearly 40,000 dosage units of fentanyl in 2024. The "
            "department releases an annual crime report and posts crime activity data online. Missoula Police "
            "work closely with the Missoula County Sheriff's Office and the University of Montana Police."
        ),
        'nearby': [
            {'name': 'Lolo', 'slug': 'lolo'},
        ],
    },
    'bozeman': {
        'slug': 'bozeman',
        'name': 'Bozeman',
        'county': 'Gallatin',
        'county_slug': 'gallatin',
        'county_roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates',
        'pd_name': 'Bozeman Police Department',
        'pd_phone': '406-582-2000',
        'pd_url': 'https://www.bozeman.net/departments/police',
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': 'https://www.bozeman.net/departments/municipal-court',
        'search_terms': ['Bozeman', 'Bozeman Police'],
        'seo_title': 'Bozeman Police Blotter, Arrests, and Gallatin Jail Roster',
        'og_title': 'Bozeman Police Blotter, Arrests, and Gallatin Jail Roster',
        'meta_description': 'Bozeman, Montana police blotter, daily activity reports, arrest resources, and Gallatin County jail information for the Bozeman area.',
        'og_description': 'Bozeman police blotter, daily activity reports, and Gallatin County arrest and jail resources.',
        'hero_summary': (
            "Bozeman police blotter coverage, Gallatin County arrest resources, municipal court links, "
            "and recent public safety reports for Montana's fastest-growing city market."
        ),
        'description': (
            "The Bozeman Police Department serves one of the fastest-growing cities in the United States. Bozeman's "
            "population has grown dramatically over the past decade, driven by an influx of remote workers, tech "
            "companies, and outdoor recreation tourism around Yellowstone National Park. In 2025, the BPD reported "
            "a 60% increase in traffic violations — a direct consequence of the city's rapid growth and increased "
            "vehicle traffic. The department works closely with the Gallatin County Sheriff's Office and Montana "
            "State University Police. Property crime and drug-related offenses have grown alongside the city's "
            "expanding population."
        ),
        'seo_summary': [
            "Bozeman search traffic is city-first even when the underlying records route through Gallatin County systems, so this page is structured to bridge that gap cleanly.",
            "Readers who search for Bozeman police activity should be able to land here first, then move into Gallatin County jail information, municipal court resources, and broader county reporting without starting over.",
        ],
        'search_targets': [
            'Bozeman police blotter',
            'Bozeman police activity',
            'Bozeman arrests',
            'Gallatin County jail roster',
            'Bozeman municipal court',
        ],
        'source_note_title': 'Bozeman coverage with Gallatin County detention links',
        'source_note': (
            "Montana Blotter uses this page as the Bozeman entry point, then links readers into Gallatin County detention and court resources when they need inmate status or case-level follow-up."
        ),
        'nearby': [
            {'name': 'Belgrade', 'slug': 'belgrade'},
            {'name': 'Manhattan', 'slug': 'manhattan'},
        ],
    },
    'great-falls': {
        'slug': 'great-falls',
        'name': 'Great Falls',
        'county': 'Cascade',
        'county_slug': 'cascade',
        'county_roster_url': 'https://www.cascadecountymt.gov/314/Inmate-Roster',
        'pd_name': 'Great Falls Police Department',
        'pd_phone': '406-455-8500',
        'pd_url': 'https://greatfallsmt.net/police',
        'pd_records_url': None,
        'warrant_url': 'https://greatfallsmt.net/municipalcourt/warrants-list',
        'municipal_court_url': 'https://greatfallsmt.net/municipalcourt',
        'search_terms': ['Great Falls', 'Great Falls Police'],
        'seo_title': 'Great Falls Police Blotter, Warrants, and Cascade Jail Roster',
        'og_title': 'Great Falls Police Blotter, Warrants, and Cascade Jail Roster',
        'meta_description': 'Great Falls, Montana police blotter, daily activity reports, warrant lookup, and Cascade County jail resources for local public safety searches.',
        'og_description': 'Great Falls police blotter, warrant lookup, daily activity reports, and Cascade County arrest resources.',
        'hero_summary': (
            "Great Falls police blotter coverage, active warrant links, Cascade County jail resources, "
            "and recent city public safety reports."
        ),
        'description': (
            "The Great Falls Police Department serves Montana's third-largest city, situated along the Missouri River "
            "in Cascade County. The city is home to Malmstrom Air Force Base, a significant presence that affects "
            "the local population and law enforcement dynamics. Great Falls serves as the commercial and medical hub "
            "for a large swath of north-central Montana. The Great Falls Municipal Court publishes an active online "
            "warrant list. The GFPD works alongside the Cascade County Sheriff's Office and Malmstrom's security "
            "forces. The department has a strong community policing focus and participates in regional drug task "
            "force operations."
        ),
        'seo_summary': [
            "Great Falls is valuable because the city term, warrant term, and county detention term all overlap in real search behavior, which makes this page stronger than a standard city archive page.",
            "This page should be the entry point for Great Falls police activity searches and then hand readers off to Cascade County jail resources and the city's live warrant list when they need current status information.",
        ],
        'search_targets': [
            'Great Falls police blotter',
            'Great Falls warrants',
            'Great Falls police activity',
            'Cascade County jail roster',
            'Great Falls arrests',
        ],
        'source_note_title': 'Great Falls reporting plus live warrant and jail links',
        'source_note': (
            "This page pairs Great Falls police coverage with the city's warrant list and Cascade County detention roster so high-intent searches can move directly into official systems."
        ),
        'nearby': [
            {'name': 'Havre', 'slug': 'havre'},
        ],
    },
    'helena': {
        'slug': 'helena',
        'name': 'Helena',
        'county': 'Lewis and Clark',
        'county_slug': 'lewis-and-clark',
        'county_roster_url': 'https://www.lccountymt.gov/Sheriff/Detention-Center',
        'pd_name': 'Helena Police Department',
        'pd_phone': '406-442-3233',
        'pd_url': 'https://www.helenamt.gov/Departments/Police',
        'pd_records_url': None,
        'warrant_url': 'https://www.helenamt.gov/Departments/Municipal-Court/Arrest-Warrants-Defendants-in-Custody',
        'municipal_court_url': 'https://www.helenamt.gov/Departments/Municipal-Court',
        'search_terms': ['Helena', 'Helena Police'],
        'seo_title': 'Helena Police Blotter, Warrants, and Lewis and Clark Jail',
        'og_title': 'Helena Police Blotter, Warrants, and Lewis and Clark Jail',
        'meta_description': 'Helena, Montana police blotter, daily activity reports, active warrant lookup, and Lewis and Clark County jail resources.',
        'og_description': 'Helena police blotter, active warrant lookup, and Lewis and Clark County arrest and jail resources.',
        'hero_summary': (
            "Helena police blotter coverage, active warrant links, Lewis and Clark County jail resources, "
            "and recent daily activity reports from Montana's capital city."
        ),
        'description': (
            "The Helena Police Department serves Montana's state capital, a city of approximately 33,000 people in "
            "Lewis and Clark County. As the seat of state government, Helena is home to the Montana Legislature, "
            "the Governor's office, the Montana Supreme Court, and numerous state agencies. Montana Blotter currently "
            "receives daily activity reports directly from the Helena Police Department, making it one of our most "
            "consistently covered agencies. The HPD's media log is published each weekday and includes all calls for "
            "service, arrests, and notable incidents. The Helena Municipal Court publishes active arrest warrants "
            "and current defendants in custody online."
        ),
        'seo_summary': [
            "Helena is one of the best city pages on the site for practical search intent because police activity, active warrants, and county detention all connect cleanly here.",
            "That makes this page useful both for readers following Helena daily activity and for users who arrive with a narrower warrant or arrest lookup need tied to the capital region.",
        ],
        'search_targets': [
            'Helena police blotter',
            'Helena warrants',
            'Helena police activity',
            'Lewis and Clark County jail roster',
            'Helena arrests',
        ],
        'source_note_title': 'Helena Police reports with capital-region warrant links',
        'source_note': (
            "Montana Blotter pairs Helena report coverage with Helena Municipal Court warrant access and Lewis and Clark detention links so users can move from media-log searches into official follow-up records."
        ),
        'nearby': [
            {'name': 'East Helena', 'slug': 'east-helena'},
            {'name': 'Havre', 'slug': 'havre'},
        ],
    },
    'kalispell': {
        'slug': 'kalispell',
        'name': 'Kalispell',
        'county': 'Flathead',
        'county_slug': 'flathead',
        'county_roster_url': 'https://apps.flathead.mt.gov/jailroster/',
        'pd_name': 'Kalispell Police Department',
        'pd_phone': '406-758-7780',
        'pd_url': 'https://kalispell.com/police',
        'pd_records_url': None,
        'warrant_url': 'https://apps.flathead.mt.gov/warrants/warrants_list.php',
        'municipal_court_url': None,
        'search_terms': ['Kalispell', 'Kalispell Police'],
        'seo_title': 'Kalispell Police Blotter, Flathead Warrants, and Jail Roster',
        'og_title': 'Kalispell Police Blotter, Flathead Warrants, and Jail Roster',
        'meta_description': 'Kalispell, Montana police blotter, daily activity reports, Flathead County warrant lookup, and jail roster resources for the Flathead Valley.',
        'og_description': 'Kalispell police blotter, Flathead County warrants, daily activity reports, and jail roster resources.',
        'hero_summary': (
            "Kalispell police blotter coverage, Flathead County warrant links, jail roster access, "
            "and recent public safety reports for the Flathead Valley."
        ),
        'description': (
            "The Kalispell Police Department serves the county seat of Flathead County in northwest Montana. "
            "Kalispell is the largest city in the region and the commercial hub for the Flathead Valley, which "
            "includes Whitefish and Columbia Falls. The city's proximity to Glacier National Park and Flathead Lake "
            "draws significant seasonal tourism. The KPD works closely with the Flathead County Sheriff's Office, "
            "which maintains both a public jail roster and a public warrant list online. The Flathead Beacon "
            "newspaper provides detailed daily police blotter coverage for the Flathead Valley."
        ),
        'seo_summary': [
            "Kalispell sits at the center of the Flathead Valley search cluster, so this page needs to compete both for pure Kalispell queries and for users comparing city and county public-safety resources.",
            "The combination of Flathead warrants, jail roster access, and nearby city links makes this one of the stronger city pages on the site even before the archive gets deeper.",
        ],
        'search_targets': [
            'Kalispell police blotter',
            'Kalispell police activity',
            'Flathead County warrants',
            'Flathead County jail roster',
            'Kalispell arrests',
        ],
        'source_note_title': 'Kalispell city coverage with Flathead warrant access',
        'source_note': (
            "This page connects Kalispell-focused coverage to Flathead County's official warrant and detention tools so readers can move from city searches into county follow-up records quickly."
        ),
        'nearby': [
            {'name': 'Whitefish', 'slug': 'whitefish'},
            {'name': 'Columbia Falls', 'slug': 'columbia-falls'},
        ],
    },
    'butte': {
        'slug': 'butte',
        'name': 'Butte',
        'county': 'Silver Bow',
        'county_slug': 'silver-bow',
        'county_roster_url': 'https://co.silverbow.mt.us/3274/Detention-Center',
        'pd_name': 'Butte-Silver Bow Law Enforcement',
        'pd_phone': '406-497-1120',
        'pd_url': 'https://co.silverbow.mt.us/192/Law-Enforcement',
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Butte', 'Butte-Silver Bow', 'Butte Silver Bow'],
        'seo_title': 'Butte Police Blotter, Arrests, and Silver Bow Jail Links',
        'og_title': 'Butte Police Blotter, Arrests, and Silver Bow Jail Links',
        'meta_description': 'Butte, Montana police blotter, arrest resources, and Silver Bow detention links for city and county public safety searches.',
        'og_description': 'Butte police blotter, arrest resources, and Silver Bow detention links for southwest Montana searches.',
        'hero_summary': (
            "Butte police blotter coverage, arrest resources, and Silver Bow detention links "
            "for one of Montana's most recognizable city search terms."
        ),
        'description': (
            "Butte-Silver Bow Law Enforcement is the unified law enforcement division of the consolidated "
            "City and County of Butte-Silver Bow — one of the few city-county government consolidations in the "
            "western United States. Butte has a storied mining history and is home to the Berkeley Pit Superfund "
            "site. The city sits at the junction of I-90 and I-15, key corridors for both commerce and, "
            "historically, drug trafficking routes. The consolidated government structure means a single law "
            "enforcement agency handles both municipal and county functions, and the Silver Bow County Detention "
            "Center handles all local inmate booking."
        ),
        'seo_summary': [
            "Butte has stronger brand recognition than most second-tier cities on the site, which makes the city page worth targeting before the archive deepens.",
            "This page is meant to capture Butte police-blotter and arrest searches first, then route readers into Silver Bow detention resources and nearby western Montana pages.",
        ],
        'search_targets': [
            'Butte police blotter',
            'Butte arrests',
            'Butte police activity',
            'Silver Bow jail roster',
            'Butte-Silver Bow police',
        ],
        'source_note_title': 'Recognizable Butte intent with county detention follow-up',
        'source_note': (
            "Montana Blotter uses this page to capture Butte-first searches and connect them to the consolidated Butte-Silver Bow law-enforcement and detention workflow."
        ),
        'nearby': [
            {'name': 'Anaconda', 'slug': 'anaconda'},
            {'name': 'Deer Lodge', 'slug': 'deer-lodge'},
        ],
    },
    'havre': {
        'slug': 'havre',
        'name': 'Havre',
        'county': 'Hill',
        'county_slug': 'hill',
        'county_roster_url': None,
        'pd_name': 'Havre Police Department',
        'pd_phone': '406-265-4397',
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Havre', 'Havre Police', 'HAVRE POLICE'],
        'seo_title': 'Havre Police Blotter, Arrests, and Hill County Jail Links',
        'og_title': 'Havre Police Blotter, Arrests, and Hill County Jail Links',
        'meta_description': 'Havre, Montana police blotter, arrest resources, and Hill County jail links for Hi-Line public safety searches.',
        'og_description': 'Havre police blotter, arrest resources, and Hill County jail links for north-central Montana readers.',
        'hero_summary': (
            "Havre police blotter coverage, Hill County arrest resources, and detention follow-up links "
            "for Montana's Hi-Line."
        ),
        'description': (
            "The Havre Police Department serves Hill County's largest city, located in north-central Montana near "
            "the Canadian border. Havre is home to Montana State University–Northern and serves as a regional hub "
            "for the Hi-Line communities along Highway 2. The city's proximity to the border creates unique law "
            "enforcement challenges including drug smuggling and human trafficking concerns. Montana Blotter "
            "currently receives daily activity reports directly from the Havre Police Department, making it one of "
            "our most consistently covered agencies on the Hi-Line."
        ),
        'seo_summary': [
            "Havre is one of the best second-tier city pages to push because the city already has direct report coverage and unusually strong Hi-Line search recognition.",
            "This page is designed to catch Havre police-activity and arrest queries first, then route readers into Hill County jail, warrant, and county archive pages.",
        ],
        'search_targets': [
            'Havre police blotter',
            'Havre police activity',
            'Havre arrests',
            'Hill County jail roster',
            'Hill County police blotter',
        ],
        'source_note_title': 'Direct Havre reporting with Hill County follow-up',
        'source_note': (
            "Montana Blotter receives Havre Police coverage directly, then uses this page to hand readers into Hill County detention and county-level public-record follow-up."
        ),
        'nearby': [
            {'name': 'Great Falls', 'slug': 'great-falls'},
        ],
    },
    'laurel': {
        'slug': 'laurel',
        'name': 'Laurel',
        'county': 'Yellowstone',
        'county_slug': 'yellowstone',
        'county_roster_url': 'https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp',
        'pd_name': 'Laurel Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Laurel', 'Laurel Police'],
        'seo_title': 'Laurel Police Blotter, Arrests, and Yellowstone Jail Links',
        'og_title': 'Laurel Police Blotter, Arrests, and Yellowstone Jail Links',
        'meta_description': 'Laurel, Montana police blotter, arrest resources, and Yellowstone County jail links for city-first public safety searches.',
        'og_description': 'Laurel police blotter, arrest resources, and Yellowstone County detention links for Billings-metro readers.',
        'hero_summary': (
            "Laurel police blotter coverage, Yellowstone County arrest resources, and detention links "
            "for the western edge of the Billings metro corridor."
        ),
        'description': (
            "Laurel sits west of Billings in Yellowstone County and functions as part of the larger Billings metro corridor. "
            "This city page gives Montana Blotter a dedicated landing page for Laurel-area reports and for readers who want a narrower search than the full Yellowstone County archive."
        ),
        'seo_summary': [
            "Laurel is useful because many readers search for the city specifically even though the broader public-record workflow runs through Yellowstone County.",
            "This page is built to catch Laurel police-blotter and arrest searches first, then route readers into Yellowstone detention, warrant, and county archive pages when they need more depth.",
        ],
        'search_targets': [
            'Laurel police blotter',
            'Laurel arrests',
            'Laurel police activity',
            'Yellowstone County jail roster',
            'Billings metro police blotter',
        ],
        'source_note_title': 'Laurel city intent linked into Yellowstone County tools',
        'source_note': (
            "Montana Blotter uses Laurel as a city-first entry page inside the Yellowstone County cluster, then connects readers to county detention and warrant resources for follow-up."
        ),
        'nearby': [
            {'name': 'Billings', 'slug': 'billings'},
            {'name': 'Red Lodge', 'slug': 'red-lodge'},
        ],
    },
    'hardin': {
        'slug': 'hardin',
        'name': 'Hardin',
        'county': 'Big Horn',
        'county_slug': 'big-horn',
        'county_roster_url': 'https://www.bighorncountymt.gov/239/Detention',
        'pd_name': 'Hardin Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Hardin', 'Hardin Police'],
        'description': (
            "Hardin is the county seat of Big Horn County and a high-interest local search term for southeastern Montana. "
            "This page is built to capture city-level police blotter intent while still linking back to county records and detention resources."
        ),
        'nearby': [
            {'name': 'Billings', 'slug': 'billings'},
            {'name': 'Laurel', 'slug': 'laurel'},
        ],
    },
    'belgrade': {
        'slug': 'belgrade',
        'name': 'Belgrade',
        'county': 'Gallatin',
        'county_slug': 'gallatin',
        'county_roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates',
        'pd_name': 'Belgrade Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Belgrade', 'Belgrade Police'],
        'seo_title': 'Belgrade Police Blotter, Arrests, and Gallatin Jail Links',
        'og_title': 'Belgrade Police Blotter, Arrests, and Gallatin Jail Links',
        'meta_description': 'Belgrade, Montana police blotter, arrest resources, and Gallatin County jail links for city and county public safety searches.',
        'og_description': 'Belgrade police blotter, arrest resources, and Gallatin County jail links for the Bozeman-area search cluster.',
        'hero_summary': (
            "Belgrade police blotter coverage, Gallatin County arrest resources, and detention links "
            "for one of the fastest-growing communities in southwest Montana."
        ),
        'description': (
            "Belgrade is one of the fastest-growing communities in Gallatin County and often gets grouped into broader Bozeman coverage. "
            "This city page gives Belgrade its own entry point for blotter and arrest-related search traffic."
        ),
        'seo_summary': [
            "Belgrade has enough growth and name recognition to justify its own city page instead of being swallowed by Bozeman search demand.",
            "This page is designed to catch Belgrade police-blotter and arrest searches, then route readers into Gallatin County jail and county archive pages for the next step.",
        ],
        'search_targets': [
            'Belgrade police blotter',
            'Belgrade arrests',
            'Belgrade police activity',
            'Gallatin County jail roster',
            'Belgrade Montana police',
        ],
        'source_note_title': 'Belgrade growth-market intent connected to Gallatin County',
        'source_note': (
            "Montana Blotter uses this page to pull Belgrade-specific search traffic out of the broader Bozeman cluster and connect it to Gallatin County detention and follow-up pages."
        ),
        'nearby': [
            {'name': 'Bozeman', 'slug': 'bozeman'},
            {'name': 'Manhattan', 'slug': 'manhattan'},
        ],
    },
    'manhattan': {
        'slug': 'manhattan',
        'name': 'Manhattan',
        'county': 'Gallatin',
        'county_slug': 'gallatin',
        'county_roster_url': 'https://gallatin-so-mt.zuercherportal.com/#/inmates',
        'pd_name': 'Manhattan Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Manhattan', 'Manhattan Police'],
        'description': (
            "Manhattan gives Montana Blotter a city-level page for another Gallatin County community that is usually overshadowed by Bozeman. "
            "That makes it useful for long-tail search intent and local internal linking."
        ),
        'nearby': [
            {'name': 'Belgrade', 'slug': 'belgrade'},
            {'name': 'Bozeman', 'slug': 'bozeman'},
        ],
    },
    'whitefish': {
        'slug': 'whitefish',
        'name': 'Whitefish',
        'county': 'Flathead',
        'county_slug': 'flathead',
        'county_roster_url': 'https://apps.flathead.mt.gov/jailroster/',
        'pd_name': 'Whitefish Police Department',
        'pd_phone': '406-863-2420',
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Whitefish', 'Whitefish Police'],
        'seo_title': 'Whitefish Police Blotter, Arrests, and Flathead Jail Roster',
        'og_title': 'Whitefish Police Blotter, Arrests, and Flathead Jail Roster',
        'meta_description': 'Whitefish, Montana police blotter, daily activity reports, and Flathead County jail and warrant resources for local search traffic.',
        'og_description': 'Whitefish police blotter, daily activity reports, and Flathead County jail and warrant resources.',
        'hero_summary': (
            "Whitefish police blotter coverage, Flathead County jail links, and recent public safety reports "
            "for one of northwest Montana's highest-interest city searches."
        ),
        'description': (
            "Whitefish is one of northwest Montana's highest-interest local search terms thanks to tourism, seasonal traffic, and regional nightlife. "
            "This page gives Whitefish its own blotter landing page separate from the broader Flathead County archive."
        ),
        'seo_summary': [
            "Whitefish has stronger search demand than its archive depth because visitors and residents often search for city-specific police activity rather than countywide records.",
            "This page exists to capture that city-first search intent and then route readers into Flathead County jail and warrant tools when they need broader or more official follow-up resources.",
        ],
        'search_targets': [
            'Whitefish police blotter',
            'Whitefish police activity',
            'Whitefish arrests',
            'Flathead County jail roster',
            'Flathead County warrants',
        ],
        'source_note_title': 'Whitefish search intent connected to Flathead County tools',
        'source_note': (
            "Montana Blotter uses this page as the Whitefish-specific entry point, then connects readers to Flathead County detention and warrant resources when city-level reporting is not enough."
        ),
        'nearby': [
            {'name': 'Kalispell', 'slug': 'kalispell'},
            {'name': 'Columbia Falls', 'slug': 'columbia-falls'},
        ],
    },
    'columbia-falls': {
        'slug': 'columbia-falls',
        'name': 'Columbia Falls',
        'county': 'Flathead',
        'county_slug': 'flathead',
        'county_roster_url': 'https://apps.flathead.mt.gov/jailroster/',
        'pd_name': 'Columbia Falls Police Department',
        'pd_phone': '406-892-2222',
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Columbia Falls', 'Columbia Falls Police'],
        'seo_title': 'Columbia Falls Police Blotter, Arrests, and Flathead Jail Links',
        'og_title': 'Columbia Falls Police Blotter, Arrests, and Flathead Jail Links',
        'meta_description': 'Columbia Falls, Montana police blotter, arrest resources, and Flathead County jail links for Flathead Valley public safety searches.',
        'og_description': 'Columbia Falls police blotter, arrest resources, and Flathead County detention links for the Flathead Valley.',
        'hero_summary': (
            "Columbia Falls police blotter coverage, Flathead County arrest resources, and detention links "
            "for a city-level Flathead Valley search."
        ),
        'description': (
            "Columbia Falls is another Flathead Valley city that benefits from a dedicated page because it captures more specific local-intent traffic than county-level coverage alone."
        ),
        'seo_summary': [
            "Columbia Falls is worth targeting because Flathead Valley search demand is split across multiple city names, not just Kalispell and Whitefish.",
            "This page is designed to capture Columbia Falls police-blotter and arrest searches and then route readers into Flathead County detention, warrant, and county archive pages.",
        ],
        'search_targets': [
            'Columbia Falls police blotter',
            'Columbia Falls arrests',
            'Columbia Falls police activity',
            'Flathead County jail roster',
            'Flathead County warrants',
        ],
        'source_note_title': 'Columbia Falls city intent tied to Flathead County tools',
        'source_note': (
            "Montana Blotter uses this page to capture a narrower Flathead Valley search path and connect it to Flathead County jail, warrant, and county archive resources."
        ),
        'nearby': [
            {'name': 'Whitefish', 'slug': 'whitefish'},
            {'name': 'Kalispell', 'slug': 'kalispell'},
        ],
    },
    'east-helena': {
        'slug': 'east-helena',
        'name': 'East Helena',
        'county': 'Lewis and Clark',
        'county_slug': 'lewis-and-clark',
        'county_roster_url': 'https://www.lccountymt.gov/Sheriff/Detention-Center',
        'pd_name': 'East Helena Police Department',
        'pd_phone': '406-227-8222',
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['East Helena', 'East Helena Police'],
        'seo_title': 'East Helena Police Blotter, Arrests, and Lewis and Clark Jail',
        'og_title': 'East Helena Police Blotter, Arrests, and Lewis and Clark Jail',
        'meta_description': 'East Helena, Montana police blotter, arrest resources, and Lewis and Clark County jail links for city-first searches.',
        'og_description': 'East Helena police blotter, arrest resources, and Lewis and Clark County detention links.',
        'hero_summary': (
            "East Helena police blotter coverage, Lewis and Clark County arrest resources, "
            "and detention links for capital-region readers."
        ),
        'description': (
            "East Helena gives the Lewis and Clark archive an additional city-level landing page for readers looking beyond Helena proper."
        ),
        'seo_summary': [
            "East Helena is a useful second-tier city page because capital-region search intent does not stop at Helena proper.",
            "This page is built to capture East Helena police-blotter and arrest searches first, then route readers into Lewis and Clark County detention and county archive pages.",
        ],
        'search_targets': [
            'East Helena police blotter',
            'East Helena arrests',
            'East Helena police activity',
            'Lewis and Clark County jail roster',
            'Helena area police blotter',
        ],
        'source_note_title': 'Capital-region city page linked into Lewis and Clark County',
        'source_note': (
            "Montana Blotter uses East Helena as a narrower capital-region entry page and connects it to Lewis and Clark County detention and countywide record searches."
        ),
        'nearby': [
            {'name': 'Helena', 'slug': 'helena'},
            {'name': 'Havre', 'slug': 'havre'},
        ],
    },
    'anaconda': {
        'slug': 'anaconda',
        'name': 'Anaconda',
        'county': 'Deer Lodge',
        'county_slug': 'deer-lodge',
        'county_roster_url': None,
        'pd_name': 'Anaconda-Deer Lodge Law Enforcement Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Anaconda', 'Anaconda Police', 'Anaconda-Deer Lodge'],
        'description': (
            "Anaconda is a recognizable western Montana city and a natural city-level expansion for Montana Blotter's location coverage, especially for users searching outside Butte-Silver Bow."
        ),
        'nearby': [
            {'name': 'Butte', 'slug': 'butte'},
        ],
    },
    'red-lodge': {
        'slug': 'red-lodge',
        'name': 'Red Lodge',
        'county': 'Carbon',
        'county_slug': 'carbon',
        'county_roster_url': 'https://carbonmt.gov/sheriff/',
        'pd_name': 'Red Lodge Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Red Lodge', 'Red Lodge Police'],
        'description': (
            "Red Lodge adds a mountain-tourism city to the Montana Blotter city inventory and creates a focused landing page for Carbon County readers."
        ),
        'nearby': [
            {'name': 'Laurel', 'slug': 'laurel'},
            {'name': 'Billings', 'slug': 'billings'},
        ],
    },
    'livingston': {
        'slug': 'livingston',
        'name': 'Livingston',
        'county': 'Park',
        'county_slug': 'park',
        'county_roster_url': 'https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/Inmates-Housed/',
        'pd_name': 'Livingston Police Department',
        'pd_phone': None,
        'pd_url': None,
        'pd_records_url': None,
        'warrant_url': None,
        'municipal_court_url': None,
        'search_terms': ['Livingston', 'Livingston Police'],
        'description': (
            "Livingston is a strong next-step city page for south-central Montana and supports city-level long-tail SEO around Park County public safety searches."
        ),
        'nearby': [
            {'name': 'Bozeman', 'slug': 'bozeman'},
            {'name': 'Red Lodge', 'slug': 'red-lodge'},
        ],
    },
}


@app.route('/cities')
def cities_directory():
    conn = get_db()
    cities = _city_directory_listing(conn)
    featured_new_cities = _featured_city_pages(cities)
    conn.close()
    return render_template(
        'cities.html',
        cities=cities,
        featured_new_cities=featured_new_cities,
        current_year=datetime.now().year,
    )


@app.route('/city/<slug>')
def city_page(slug):
    city = CITY_DATA.get(slug)
    if not city:
        return render_template('404.html'), 404

    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10

    terms = city['search_terms']
    where_sql, params_count = _build_like_clause(
        ['posts.city', 'posts.agency_name'],
        terms,
    )

    conn = get_db()

    count_row = conn.execute(
        f'SELECT COUNT(*) FROM posts WHERE {where_sql}', params_count
    ).fetchone()
    post_count = count_row[0] if count_row else 0
    total_pages = max(1, (post_count + per_page - 1) // per_page)

    params_fetch = params_count + [per_page, (page - 1) * per_page]
    posts = conn.execute(
        f"""SELECT posts.*, blotters.county AS blotter_county
            FROM posts
            JOIN blotters ON posts.blotter_id = blotters.id
            WHERE {where_sql}
            ORDER BY posts.incident_date DESC, posts.created_at DESC
            LIMIT ? OFFSET ?""",
        params_fetch
    ).fetchall()

    rec_where_sql, rec_params = _build_like_clause(['records.location'], terms)
    record_count = conn.execute(
        f'SELECT COUNT(*) FROM records WHERE {rec_where_sql}',
        rec_params
    ).fetchone()[0]

    top_incidents = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(incident_type, ''), 'Other') AS incident_type,
               COUNT(*) AS count
        FROM records
        WHERE {rec_where_sql}
        GROUP BY COALESCE(NULLIF(incident_type, ''), 'Other')
        ORDER BY count DESC, incident_type ASC
        LIMIT 8
        """,
        rec_params
    ).fetchall()

    recent_records = conn.execute(
        f"""
        SELECT
            records.id,
            records.date,
            records.time,
            COALESCE(NULLIF(records.incident_type, ''), NULLIF(records.incident, ''), 'Incident') AS incident_label,
            records.location,
            posts.id AS post_id
        FROM records
        LEFT JOIN posts ON posts.blotter_id = records.blotter_id
        WHERE {rec_where_sql}
        ORDER BY records.date DESC, records.time DESC, records.id DESC
        LIMIT 8
        """,
        rec_params
    ).fetchall()
    recent_records = _annotate_recent_records(conn, recent_records)

    agency_coverage = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(agency_name, ''), 'Unknown agency') AS agency_name,
               COUNT(*) AS report_count
        FROM posts
        WHERE {where_sql}
        GROUP BY COALESCE(NULLIF(agency_name, ''), 'Unknown agency')
        ORDER BY report_count DESC, agency_name ASC
        LIMIT 8
        """,
        params_count
    ).fetchall()

    source_method_rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(blotters.source_type, ''), 'pdf') AS source_type,
               COUNT(*) AS count
        FROM posts
        JOIN blotters ON blotters.id = posts.blotter_id
        WHERE {where_sql}
        GROUP BY COALESCE(NULLIF(blotters.source_type, ''), 'pdf')
        ORDER BY count DESC, source_type ASC
        LIMIT 4
        """,
        params_count
    ).fetchall()

    last_row = conn.execute(
        f'SELECT incident_date FROM posts WHERE {where_sql} ORDER BY incident_date DESC LIMIT 1',
        params_count
    ).fetchone()
    last_report = last_row['incident_date'] if last_row else None
    bail_ad_placements = _bail_ad_public_placements(conn, county=city['county'])

    conn.close()

    linked_nearby = [
        CITY_DATA[nearby['slug']]
        for nearby in city.get('nearby', [])
        if nearby.get('slug') in CITY_DATA
    ]

    return render_template(
        'city_page.html',
        city=city,
        posts=posts,
        post_count=post_count,
        record_count=record_count,
        top_incidents=top_incidents,
        recent_records=recent_records,
        agency_coverage=agency_coverage,
        source_methods=_source_method_rollup(source_method_rows),
        pattern_links=_pattern_links_for_county(city['county_slug']),
        linked_nearby=linked_nearby,
        bail_ad_placements=bail_ad_placements,
        last_report=last_report,
        page_last_updated=last_report,
        page=page,
        total_pages=total_pages,
        current_year=datetime.now().year,
        canonical_url=f"https://montanablotter.com/city/{slug}",
        og_image="https://montanablotter.com/static/icons/og-default.png",
    )


# ==========================================
# PATTERN PAGES
# ==========================================

@app.route('/patterns')
def patterns_hub():
    conn = get_db()
    pattern_cards = []
    for pattern in PATTERN_DEFINITIONS.values():
        clause, params = _pattern_clause(pattern['slug'], 'records')
        total_records = conn.execute(
            f'SELECT COUNT(*) FROM records WHERE {clause}',
            params,
        ).fetchone()[0]
        top_counties = []
        for row in conn.execute(
            f'''
            SELECT records.county, COUNT(*) AS count
            FROM records
            WHERE {clause}
              AND records.county IS NOT NULL
              AND records.county != ''
            GROUP BY records.county
            ORDER BY count DESC, records.county ASC
            LIMIT 5
            ''',
            params,
        ).fetchall():
            county_slug = _county_slug_for_name(row['county'])
            if county_slug:
                top_counties.append({
                    'name': row['county'],
                    'slug': county_slug,
                    'count': row['count'],
                })
        pattern_cards.append({
            **pattern,
            'total_records': total_records,
            'top_counties': top_counties,
        })
    latest_weekly_digest = _latest_weekly_digest(conn)
    conn.close()
    return render_template(
        'patterns_hub.html',
        pattern_cards=pattern_cards,
        latest_weekly_digest=latest_weekly_digest,
        current_year=datetime.now().year,
    )


@app.route('/patterns/<pattern_slug>')
@app.route('/patterns/<pattern_slug>/<county_slug>')
def pattern_page(pattern_slug, county_slug=None):
    pattern = PATTERN_DEFINITIONS.get(pattern_slug)
    if not pattern:
        return render_template('404.html'), 404

    county = None
    if county_slug:
        county = COUNTY_DATA.get(county_slug)
        if not county:
            return render_template('404.html'), 404

    conn = get_db()
    context = _pattern_page_context(conn, pattern_slug, county=county)
    conn.close()
    if context is None:
        return render_template('404.html'), 404

    return render_template(
        'pattern_page.html',
        active_nav='patterns',
        **context,
        current_year=datetime.now().year,
        og_image="https://montanablotter.com/static/icons/og-default.png",
    )


# ==========================================
# WARRANT PAGES
# ==========================================

# Slim list of counties shown on warrant pages — reuses COUNTY_DATA
_WARRANT_COUNTIES = [
    'yellowstone', 'hill', 'gallatin', 'missoula', 'cascade', 'flathead',
    'lewis-and-clark', 'silver-bow',
]


@app.route('/warrants')
def warrants_hub():
    counties = [COUNTY_DATA[s] for s in _WARRANT_COUNTIES if s in COUNTY_DATA]
    return render_template(
        'warrants_hub.html',
        counties=counties,
        current_year=datetime.now().year,
        canonical_url="https://montanablotter.com/warrants",
        og_image="https://montanablotter.com/static/icons/og-default.png",
    )


@app.route('/warrants/<slug>')
def warrant_county(slug):
    county = COUNTY_DATA.get(slug)
    if not county:
        return render_template('404.html'), 404
    all_counties = [COUNTY_DATA[s] for s in _WARRANT_COUNTIES if s in COUNTY_DATA]
    return render_template(
        'warrant_county.html',
        county=county,
        all_counties=all_counties,
        current_year=datetime.now().year,
        canonical_url=f"https://montanablotter.com/warrants/{slug}",
        og_image="https://montanablotter.com/static/icons/og-default.png",
    )


@app.route('/laws')
def montana_laws():
    conn = get_db()
    explainers = conn.execute(
        """SELECT slug, title, excerpt, charge_category, incident_type, view_count
           FROM charge_explainers WHERE published=1
           ORDER BY view_count DESC, charge_category, incident_type"""
    ).fetchall()
    conn.close()
    return render_template('laws.html', explainers=explainers, current_year=datetime.now().year,
                           canonical_url="https://montanablotter.com/laws",
                           og_image="https://montanablotter.com/static/icons/og-default.png")


def _md_to_html_lines(body: str) -> list[str]:
    """Convert markdown body to a list of pre-rendered HTML strings for Jinja."""
    import re as _re
    lines = body.split('\n')
    rendered = []
    for line in lines:
        if line.startswith('## '):
            rendered.append(('h2', escape(line[3:])))
        elif line.startswith('### '):
            rendered.append(('h3', escape(line[4:])))
        elif line.startswith('- '):
            text = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escape(line[2:]))
            rendered.append(('li', text))
        elif line.strip() == '':
            rendered.append(('br', ''))
        else:
            text = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escape(line))
            rendered.append(('p', text))
    return rendered


@app.route('/laws/charge/<slug>')
def charge_explainer(slug):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM charge_explainers WHERE slug=? AND published=1", (slug,)
    ).fetchone()
    if not row:
        conn.close()
        return render_template('404.html'), 404
    conn.execute(
        "UPDATE charge_explainers SET view_count = view_count + 1 WHERE slug=?", (slug,)
    )
    conn.commit()
    recent = conn.execute(
        """SELECT records.id, records.date, records.county, records.location, records.details
           FROM records
           WHERE incident_type = ?
           ORDER BY records.id DESC LIMIT 6""",
        (row['incident_type'],),
    ).fetchall()
    conn.close()
    body_lines = _md_to_html_lines(row['body'] or '')
    return render_template(
        'charge_explainer.html',
        explainer=row,
        body_lines=body_lines,
        recent_records=recent,
        current_year=datetime.now().year,
        canonical_url=f"https://montanablotter.com/laws/charge/{slug}",
        og_image="https://montanablotter.com/static/icons/og-default.png",
    )


@app.route('/support')
def support_hub():
    support_email = (
        (getattr(config, 'SMTP_USER', '') or '').strip()
        or (getattr(config, 'EMAIL_USER', '') or '').strip()
        or 'support@montanablotter.com'
    )
    return render_template(
        'support.html',
        page_title='Support & Partnerships',
        meta_description=(
            'Support Montana Blotter with reader donations, email subscriptions, '
            'and high-intent Montana advertising programs for bail agencies and recovery centers.'
        ),
        canonical_url=f'{BASE_URL}/support',
        og_title='Support & Partnerships | Montana Blotter',
        og_description=(
            'Keep Montana public records free with donations, or explore bail-bonds '
            'and recovery-center partnership programs.'
        ),
        active_nav='support',
        donation_ready=_donations_enabled() and _stripe_ready_for_checkout(),
        donations_enabled=_donations_enabled(),
        bail_checkout_ready=_bail_ad_checkout_ready(),
        bail_package_count=len(_bail_ad_public_packages()),
        support_email=support_email,
        current_year=datetime.now().year,
    )


@app.route('/bail-bonds')
def bail_bonds_directory():
    return _render_bail_bonds_directory()


def _render_bail_bonds_directory(selected_county=''):
    normalized_county = _normalize_bail_county(selected_county)
    county_slug = _slugify_key(normalized_county) if normalized_county else ''
    lead_submitted = request.args.get('lead_submitted') == '1'
    lead_error = (request.args.get('lead_error') or '').strip()[:80]

    conn = get_db()
    try:
        _ensure_bail_consumer_lead_schema(conn)
        listings = _active_bail_ad_listings(conn)
    except sqlite3.OperationalError:
        listings = []
    finally:
        conn.close()

    county_sections = _bail_county_sections(listings, normalized_county)
    visible_listings = []
    for section in county_sections:
        visible_listings.extend(section['listings'])
    if not normalized_county:
        visible_listings = listings

    default_help_phone = next((listing['phone'] for listing in visible_listings if listing.get('phone')), '')
    help_contact = _bail_help_contact(default_phone=default_help_phone)
    county_links = [
        {'name': county_name, 'slug': _slugify_key(county_name)}
        for county_name in _all_bail_counties()
    ]

    if normalized_county:
        page_title = f'{normalized_county} Bail Bonds Directory'
        meta_description = f'Find licensed bail bond agents serving {normalized_county} County with click-to-call, text support, and rapid intake.'
        canonical_url = f'{BASE_URL}/bail-bonds/{county_slug}'
    else:
        page_title = 'Bail Bonds Directory'
        meta_description = 'County-level bail bonds directory for Montana Blotter partner advertisers.'
        canonical_url = f'{BASE_URL}/bail-bonds'

    return render_template(
        'bail_bonds_directory.html',
        listings=visible_listings,
        county_sections=county_sections,
        selected_county=normalized_county,
        selected_county_slug=county_slug,
        county_links=county_links,
        lead_submitted=lead_submitted,
        lead_error=lead_error,
        lead_form={
            'county': normalized_county or '',
            'callback_preference': 'call_now',
            'source': (request.args.get('source') or 'directory').strip()[:80],
        },
        help_contact=help_contact,
        page_title=page_title,
        meta_description=meta_description,
        canonical_url=canonical_url,
        og_title=f'{page_title} | Montana Blotter',
        og_description=meta_description,
        active_nav='bail_bonds',
        current_year=datetime.now().year,
    )


@app.route('/bail-bonds/<county_slug>')
def bail_bonds_directory_county(county_slug):
    normalized_county = _normalize_bail_county(county_slug)
    if not normalized_county or _slugify_key(normalized_county) != _slugify_key(county_slug):
        return render_template('404.html'), 404
    return _render_bail_bonds_directory(selected_county=normalized_county)


@app.route('/bail-bonds/intake', methods=['POST'])
def bail_bonds_intake():
    return_path = (request.form.get('return_path') or '').strip()
    if not return_path.startswith('/bail-bonds') or return_path.startswith('//'):
        return_path = '/bail-bonds'

    def _redirect_with_flag(flag_key, flag_value='1'):
        separator = '&' if '?' in return_path else '?'
        return redirect(f'{return_path}{separator}{flag_key}={flag_value}')

    full_name = (request.form.get('full_name') or '').strip()[:120]
    phone = (request.form.get('phone') or '').strip()[:40]
    email = (request.form.get('email') or '').strip().lower()[:160]
    county = _normalize_bail_county((request.form.get('county') or '').strip()[:80])
    jail_facility = (request.form.get('jail_facility') or '').strip()[:120]
    callback_preference = (request.form.get('callback_preference') or 'call_now').strip().lower()[:32]
    notes = (request.form.get('notes') or '').strip()[:1200]
    source = (request.form.get('source') or 'directory_form').strip()[:80]
    honeypot = (request.form.get('fax_number') or '').strip()

    if honeypot:
        return _redirect_with_flag('lead_submitted')

    if not full_name or not phone or not county:
        return _redirect_with_flag('lead_error', 'missing_required')

    if callback_preference not in {'call_now', 'text_me', 'email_me', 'call_later'}:
        callback_preference = 'call_now'

    ip_hash = hashlib.sha256((_client_ip() or '').encode()).hexdigest()[:16]
    referrer = (request.referrer or '')[:500]

    conn = get_db()
    try:
        _ensure_bail_consumer_lead_schema(conn)
        listings = _active_bail_ad_listings(conn)
        routed_targets = _bail_lead_routing_targets(listings, county)

        routed_order_ids = ','.join(str(item['id']) for item in routed_targets)
        routed_business_names = ', '.join(item['business_name'] for item in routed_targets)
        routed_emails = ','.join(
            sorted({(item.get('email') or '').strip().lower() for item in routed_targets if item.get('email')})
        )
        routed_phones = ', '.join(
            sorted({(item.get('phone') or '').strip() for item in routed_targets if item.get('phone')})
        )

        cursor = conn.execute(
            '''
            INSERT INTO bail_consumer_leads (
                full_name, phone, email, county, jail_facility, callback_preference, notes, source,
                routed_order_ids, routed_business_names, routed_emails, routed_phones, ip_hash, referrer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                full_name,
                phone,
                email,
                county,
                jail_facility,
                callback_preference,
                notes,
                source,
                routed_order_ids,
                routed_business_names,
                routed_emails,
                routed_phones,
                ip_hash,
                referrer,
            ),
        )
        lead_id = int(cursor.lastrowid or 0)
        _record_bail_consumer_event(conn, 'form_submit', county=county, source=source, lead_id=lead_id)
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()
        return _redirect_with_flag('lead_error', 'schema')
    finally:
        conn.close()

    recipients = _bail_lead_notify_recipients()
    for email_value in (routed_emails or '').split(','):
        clean = (email_value or '').strip().lower()
        if clean and '@' in clean and clean not in recipients:
            recipients.append(clean)

    subject = f'New Bail Lead · {county} · {full_name}'
    body = (
        f'Lead ID: {lead_id}\n'
        f'Name: {full_name}\n'
        f'Phone: {phone}\n'
        f'Email: {email or "-"}\n'
        f'County: {county}\n'
        f'Jail Facility: {jail_facility or "-"}\n'
        f'Callback Preference: {callback_preference}\n'
        f'Source: {source}\n'
        f'Routed Advertisers: {routed_business_names or "-"}\n'
        f'Notes: {notes or "-"}\n'
    )
    _send_bail_lead_notification_email(recipients, subject, body)
    _post_bail_lead_webhook({
        'lead_id': lead_id,
        'county': county,
        'name': full_name,
        'phone': phone,
        'email': email,
        'jail_facility': jail_facility,
        'callback_preference': callback_preference,
        'source': source,
        'routed_order_ids': routed_order_ids,
        'routed_business_names': routed_business_names,
        'created_at': datetime.utcnow().isoformat() + 'Z',
    })
    return _redirect_with_flag('lead_submitted')


@app.route('/terms')
def terms():
    return redirect(url_for('terms_of_use'), code=301)


@app.route('/terms-of-use')
def terms_of_use():
    return render_template('terms_of_use.html', current_year=datetime.now().year)


@app.route('/privacy')
def privacy():
    return render_template('privacy.html', current_year=datetime.now().year)


@app.route('/standards')
def editorial_standards():
    return render_template('standards.html', current_year=datetime.now().year)


@app.route('/corrections')
def corrections_policy():
    return render_template('corrections.html', current_year=datetime.now().year)


@app.route('/trends')
def trends():
    conn = get_db()
    weekly_snapshot = _weekly_snapshot(conn)
    latest_weekly_digest = _latest_weekly_digest(conn)
    conn.close()
    return render_template(
        'trends.html',
        weekly_snapshot=weekly_snapshot,
        latest_weekly_digest=latest_weekly_digest,
        current_year=datetime.now().year,
    )


@app.route('/blog')
def blog():
    conn = get_db()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = 10
    latest_weekly_digest = _latest_weekly_digest(conn)
    category = request.args.get('category', '').strip().lower()

    # Check whether the category column exists (it may not be migrated yet)
    bp_columns = {row[1] for row in conn.execute('PRAGMA table_info(blog_posts)')}
    has_category = 'category' in bp_columns

    if category and has_category:
        total = conn.execute(
            'SELECT COUNT(*) FROM blog_posts WHERE published=1 AND lower(category)=?',
            (category,)).fetchone()[0]
        posts = conn.execute(
            'SELECT * FROM blog_posts WHERE published=1 AND lower(category)=? ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (category, per_page, (page - 1) * per_page)).fetchall()
    else:
        category = ''  # normalise so template gets empty string when no-op
        total = conn.execute(
            'SELECT COUNT(*) FROM blog_posts WHERE published=1').fetchone()[0]
        posts = conn.execute(
            'SELECT * FROM blog_posts WHERE published=1 ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (per_page, (page - 1) * per_page)).fetchall()

    total_pages = max(1, (total + per_page - 1) // per_page)
    conn.close()
    return render_template('blog.html', posts=posts, total=total,
                           page=page, total_pages=total_pages,
                           active_category=category,
                           latest_weekly_digest=latest_weekly_digest,
                           current_year=datetime.now().year)


@app.route('/blog/<slug>')
def blog_post(slug):
    conn = get_db()
    post = conn.execute(
        'SELECT * FROM blog_posts WHERE slug=? AND published=1', (slug,)).fetchone()
    if not post:
        conn.close()
        return render_template('404.html'), 404
    comment_thread = _public_comments_context(conn, 'blog_post', post['id'])
    conn.close()
    return render_template('blog_post.html', post=post,
                           comment_thread=comment_thread,
                           current_year=datetime.now().year)


# ==========================================
# ADMIN BLUEPRINT
# ==========================================
from blueprints.admin import register_admin_blueprint
register_admin_blueprint(app)
register_api_blueprint(app)
register_auth_blueprint(app)
register_payments_blueprint(app)
app.register_blueprint(recovery_ads_bp)
app.after_request(after_api_request)


@app.route('/admin/analytics')
@login_required
def admin_analytics():
    conn = get_db()
    context = _analytics_hub_context(
        conn,
        date_from=request.args.get('date_from'),
        date_to=request.args.get('date_to'),
        path_prefix=request.args.get('path_prefix'),
    )
    conn.close()
    return render_template('admin_analytics_hub.html', analytics=context)


# ==========================================
# VISITOR ANALYTICS
# ==========================================

@app.route('/admin/visitors')
@login_required
def admin_visitors():
    return redirect(url_for('admin_analytics', tab='traffic'))


# ==========================================
# ERROR HANDLERS
# ==========================================

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serve files from the uploads directory"""
    return send_from_directory(config.UPLOAD_DIR, filename)


# ==========================================
# PREMIUM PUBLIC PAGES
# ==========================================

@app.route('/crime-map')
def crime_map():
    return render_template('crime_map.html')


@app.route('/safety-scorecards')
def safety_scorecards():
    return render_template('safety_scorecards.html')


@app.route('/my-alerts')
def my_alerts():
    return render_template('alert_profiles.html')


@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

if __name__ == "__main__":
    # Ensure directories exist
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    os.makedirs(config.RECORDS_DIR, exist_ok=True)
    
    # Run on port 5000
    app.run(host='0.0.0.0', port=5000, debug=config.DEBUG)
