"""Schema.org JSON-LD builders for public-facing pages.

Each function returns a plain ``dict`` shaped as JSON-LD; callers are
responsible for passing it to a template and serializing it (e.g. via
Jinja's ``| tojson`` filter inside a ``<script type="application/ld+json">``
block). Nothing here talks to the DB or Flask request context directly so
these stay easy to unit test with plain dicts.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import config

BASE_URL = config.BASE_URL.rstrip('/')
PUBLISHER_NAME = 'Montana Blotter'
PUBLISHER_LOGO = f'{BASE_URL}/static/logo.png'


def _publisher() -> dict:
    return {
        '@type': 'Organization',
        'name': PUBLISHER_NAME,
        'url': BASE_URL,
        'logo': {
            '@type': 'ImageObject',
            'url': PUBLISHER_LOGO,
        },
    }


def _record_url(record: Mapping[str, Any]) -> str:
    record_id = record.get('id')
    return f"{BASE_URL}/record/{record_id}" if record_id is not None else BASE_URL


def _record_headline(record: Mapping[str, Any]) -> str:
    incident = record.get('incident') or record.get('incident_type') or 'Incident report'
    county = record.get('county')
    return f"{incident} — {county} County" if county else incident


def item_page_ld(record: Mapping[str, Any], *, county_name: Optional[str] = None) -> dict:
    """ItemPage structured data for a single public record detail page."""
    county = county_name or record.get('county')
    return {
        '@context': 'https://schema.org',
        '@type': 'ItemPage',
        'url': _record_url(record),
        'name': _record_headline(record),
        'description': record.get('details') or record.get('incident') or None,
        'datePublished': record.get('date') or record.get('created_at'),
        'isPartOf': {
            '@type': 'WebSite',
            'name': PUBLISHER_NAME,
            'url': BASE_URL,
        },
        'about': {
            '@type': 'Place',
            'name': f"{county} County, Montana" if county else 'Montana',
        },
        'publisher': _publisher(),
    }


def news_article_ld(record: Mapping[str, Any], *, county_name: Optional[str] = None) -> dict:
    """NewsArticle structured data for records republished as a blog post.

    Only meaningful when the record has an associated post
    (``post_id`` / ``post_seo_slug``) — callers gate on that before invoking.
    """
    county = county_name or record.get('county')
    post_slug = record.get('post_seo_slug')
    article_url = f"{BASE_URL}/post/{post_slug}" if post_slug else _record_url(record)
    date_published = record.get('date') or record.get('created_at')
    return {
        '@context': 'https://schema.org',
        '@type': 'NewsArticle',
        'headline': _record_headline(record),
        'url': article_url,
        'mainEntityOfPage': {
            '@type': 'WebPage',
            '@id': article_url,
        },
        'description': record.get('details') or None,
        'datePublished': date_published,
        'dateModified': record.get('created_at') or date_published,
        'articleSection': record.get('incident_type') or 'Police Blotter',
        'contentLocation': {
            '@type': 'Place',
            'name': f"{county} County, Montana" if county else 'Montana',
        },
        'publisher': _publisher(),
        'author': _publisher(),
    }


def government_organization_ld(*, county_name: str, slug: str, county_data: Optional[Mapping[str, Any]] = None) -> dict:
    """GovernmentOrganization structured data for a county hub page."""
    county_data = county_data or {}
    ld: dict = {
        '@context': 'https://schema.org',
        '@type': 'GovernmentOrganization',
        'name': f"{county_name} County, Montana",
        'url': f"{BASE_URL}/county/{slug}",
        'areaServed': {
            '@type': 'AdministrativeArea',
            'name': f"{county_name} County, Montana",
        },
    }
    seat = county_data.get('seat')
    if seat:
        ld['location'] = {
            '@type': 'Place',
            'name': seat,
            'address': {
                '@type': 'PostalAddress',
                'addressLocality': seat,
                'addressRegion': 'MT',
                'addressCountry': 'US',
            },
        }
    phone = county_data.get('phone')
    if phone:
        ld['telephone'] = phone
    return ld


def dataset_ld(payload: Mapping[str, Any]) -> dict:
    """Dataset structured data for a county's public-records archive page.

    ``payload`` keys: name, description, url, county, record_count,
    last_report (ISO-ish timestamp string or None), keywords (list[str]).
    """
    ld: dict = {
        '@context': 'https://schema.org',
        '@type': 'Dataset',
        'name': payload.get('name'),
        'description': payload.get('description'),
        'url': payload.get('url'),
        'creator': _publisher(),
        'spatialCoverage': {
            '@type': 'Place',
            'name': f"{payload.get('county')} County, Montana" if payload.get('county') else 'Montana',
        },
        'keywords': payload.get('keywords') or [],
        'license': f'{BASE_URL}/terms',
    }
    record_count = payload.get('record_count')
    if record_count is not None:
        ld['size'] = f"{record_count} records"
    last_report = payload.get('last_report')
    if last_report:
        ld['dateModified'] = last_report
    return ld
