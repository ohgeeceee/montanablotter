from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from flask import Blueprint, abort, redirect, render_template, request

import config
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
        return render_template(
            'datacenter_records.html',
            **_dataset_context(definition),
            explorer_href=definition.landing_href,
        )

    return redirect(definition.records_href, code=301)
