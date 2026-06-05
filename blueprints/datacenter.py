from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from urllib.parse import urlencode

from flask import Blueprint, abort, redirect, render_template, request

import config
from db import get_db
from services.datasets.catalog import DATASET_DEFINITIONS, get_dataset_definition


datacenter_bp = Blueprint('datacenter', __name__)


def register_datacenter_blueprint(app) -> None:
    app.register_blueprint(datacenter_bp)


def _base_url() -> str:
    base = (getattr(config, 'BASE_URL', '') or '').strip()
    if base:
        return base.rstrip('/')
    return request.host_url.rstrip('/')


def _dataset_cards() -> list[dict]:
    return [asdict(definition) for definition in DATASET_DEFINITIONS.values()]


def _dataset_context(definition) -> dict:
    return {
        'dataset': definition,
        'current_year': datetime.now().year,
        'page_title': f'{definition.title} | Montana Public Data Center',
        'meta_description': definition.summary,
        'canonical_url': f'{_base_url()}/datasets/{definition.slug}',
        'og_title': f'{definition.title} | Montana Public Data Center',
        'og_description': definition.summary,
        'active_nav': 'data_center',
    }


def _non_empty(value) -> str:
    return (value or '').strip()


def _truncate_text(value: str | None, limit: int = 180) -> str:
    text = _non_empty(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + '…'


def _police_calls_href(*, q: str = '', county: str = '', incident_type: str = '', page: int = 1) -> str:
    params: dict[str, str | int] = {}
    if q:
        params['q'] = q
    if county:
        params['county'] = county
    if incident_type:
        params['type'] = incident_type
    if page > 1:
        params['page'] = page
    query = urlencode(params)
    return '/datasets/police-calls/records' + (f'?{query}' if query else '')


def _police_calls_context(definition) -> dict:
    return {
        **_dataset_context(definition),
        'page_title': f'{definition.title} Explorer | Montana Public Data Center',
        'meta_description': 'Search public call-for-service records by county, type, location, and CFS number.',
        'canonical_url': f'{_base_url()}/datasets/{definition.slug}/records',
        'og_title': f'{definition.title} Explorer | Montana Public Data Center',
        'og_description': 'Search Montana call-for-service records with county and type filters.',
    }


def _police_calls_search_clause() -> str:
    return "COALESCE(NULLIF(cfs_number, ''), '') != ''"


def _police_calls_query(conn, *, q: str, county: str, incident_type: str, page: int, per_page: int = 20) -> dict:
    where = [_police_calls_search_clause()]
    params: list[str] = []

    if county:
        where.append("county = ?")
        params.append(county)

    if incident_type:
        where.append("COALESCE(incident_type, '') = ?")
        params.append(incident_type)

    if q:
        token = f'%{q}%'
        where.append(
            "("
            "COALESCE(incident_type, '') LIKE ? OR "
            "COALESCE(incident, '') LIKE ? OR "
            "COALESCE(details, '') LIKE ? OR "
            "COALESCE(location, '') LIKE ? OR "
            "COALESCE(county, '') LIKE ? OR "
            "COALESCE(cfs_number, '') LIKE ?"
            ")"
        )
        params.extend([token] * 6)

    where_sql = ' AND '.join(where)

    total = int(conn.execute(f"SELECT COUNT(*) AS total FROM records WHERE {where_sql}", params).fetchone()['total'])
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    offset = (page - 1) * per_page

    rows = conn.execute(
        f"""
        SELECT id, date, time, incident_type, incident, location, county, officer, cfs_number, details
        FROM records
        WHERE {where_sql}
        ORDER BY date DESC, time DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()

    counties = [
        row['county']
        for row in conn.execute(
            """
            SELECT DISTINCT county
            FROM records
            WHERE COALESCE(NULLIF(cfs_number, ''), '') != '' AND COALESCE(NULLIF(county, ''), '') != ''
            ORDER BY county ASC
            """
        ).fetchall()
    ]
    incident_types = [
        row['incident_type']
        for row in conn.execute(
            """
            SELECT DISTINCT COALESCE(NULLIF(incident_type, ''), incident) AS incident_type
            FROM records
            WHERE COALESCE(NULLIF(cfs_number, ''), '') != '' AND COALESCE(NULLIF(COALESCE(incident_type, incident), ''), '') != ''
            ORDER BY incident_type ASC
            """
        ).fetchall()
    ]

    total_calls = int(
        conn.execute(
            f"SELECT COUNT(*) AS total FROM records WHERE {_police_calls_search_clause()}"
        ).fetchone()['total']
    )
    county_count = int(
        conn.execute(
            """
            SELECT COUNT(DISTINCT county) AS total
            FROM records
            WHERE COALESCE(NULLIF(cfs_number, ''), '') != '' AND COALESCE(NULLIF(county, ''), '') != ''
            """
        ).fetchone()['total']
    )
    type_count = int(
        conn.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(NULLIF(incident_type, ''), incident)) AS total
            FROM records
            WHERE COALESCE(NULLIF(cfs_number, ''), '') != '' AND COALESCE(NULLIF(COALESCE(incident_type, incident), ''), '') != ''
            """
        ).fetchone()['total']
    )

    normalized_records = []
    for row in rows:
        normalized_records.append(
            {
                'id': int(row['id']),
                'date': row['date'] or '',
                'time': row['time'] or '—',
                'incident_type': row['incident_type'] or row['incident'] or 'Incident',
                'location': row['location'] or '—',
                'county': row['county'] or '—',
                'officer': row['officer'] or '',
                'cfs_number': row['cfs_number'] or '',
                'details_snippet': _truncate_text(row['details'], 180) or 'No incident details were recorded.',
            }
        )

    return {
        'records': normalized_records,
        'counties': counties,
        'incident_types': incident_types,
        'summary': {
            'total_calls': total_calls,
            'filtered_calls': total,
            'county_count': county_count,
            'incident_type_count': type_count,
        },
        'page': page,
        'total_pages': total_pages,
        'pagination_prev_href': _police_calls_href(q=q, county=county, incident_type=incident_type, page=max(1, page - 1)) if page > 1 else '',
        'pagination_next_href': _police_calls_href(q=q, county=county, incident_type=incident_type, page=page + 1) if page < total_pages else '',
        'q': q,
        'county': county,
        'incident_type': incident_type,
    }


@datacenter_bp.route('/datacenter')
@datacenter_bp.route('/datasets')
def datacenter_index():
    return render_template(
        'datacenter_index.html',
        datasets=_dataset_cards(),
        current_year=datetime.now().year,
        page_title='Montana Public Data Center',
        meta_description='A statewide directory for Montana Blotter public datasets, with shared search and records entry points.',
        canonical_url=f'{_base_url()}/datacenter',
        og_title='Montana Public Data Center',
        og_description='Browse the core Montana public datasets from one shared directory.',
        active_nav='data_center',
    )


@datacenter_bp.route('/datasets/<slug>')
def datacenter_dataset(slug: str):
    try:
        definition = get_dataset_definition(slug)
    except KeyError:
        abort(404)

    if slug == 'police-calls':
        return render_template(
            'datacenter_dataset.html',
            **_dataset_context(definition),
            explorer_href=f'/datasets/{slug}/records',
        )

    return render_template(
        'datacenter_dataset.html',
        **_dataset_context(definition),
        explorer_href=definition.records_href,
    )


@datacenter_bp.route('/datasets/<slug>/records')
def datacenter_dataset_records(slug: str):
    try:
        definition = get_dataset_definition(slug)
    except KeyError:
        abort(404)

    if slug == 'police-calls':
        conn = get_db()
        q = _non_empty(request.args.get('q'))
        county = _non_empty(request.args.get('county'))
        incident_type = _non_empty(request.args.get('type'))
        page = request.args.get('page', 1, type=int)
        payload = _police_calls_query(conn, q=q, county=county, incident_type=incident_type, page=page)
        conn.close()
        return render_template(
            'datacenter_records.html',
            **_police_calls_context(definition),
            **payload,
            explorer_href=definition.landing_href,
        )

    return redirect(definition.records_href, code=301)
