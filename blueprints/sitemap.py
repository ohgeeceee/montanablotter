"""SEO sitemap blueprint.

``app.py`` defines every ``/sitemap-<section>.xml`` route directly except
``/sitemap-seo.xml`` (referenced from the sitemap index, robots.txt, and the
sign-in-wall exemption list), which lives here. Kept as a blueprint rather
than folded into ``app.py`` so future SEO-only landing-page sitemaps can be
added without touching the monolith.

Currently returns an empty-but-valid urlset — matches the existing
``/sitemap-images.xml`` pattern in ``app.py`` for a section with no content
yet — so the sitemap index entry always resolves to something a crawler can
parse. Fill in real ``<url>`` entries here once dedicated SEO landing pages
are added.
"""
from __future__ import annotations

from flask import Blueprint, Response

sitemap_bp = Blueprint('sitemap', __name__)


@sitemap_bp.route('/sitemap-seo.xml')
def sitemap_seo():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        '</urlset>'
    )
    return Response(xml, mimetype='application/xml')
