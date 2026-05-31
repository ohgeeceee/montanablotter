#!/usr/bin/env python3
"""Tests for services/ingestion/dashboard_detector.py."""

import pytest

import sys
sys.path.insert(0, "/root/montanablotter")

from services.ingestion.dashboard_detector import (
    detect_dashboards,
    _build_tableau_feed_urls,
    _abs_url,
)


class TestAbsUrl:
    def test_protocol_relative(self):
        assert _abs_url("//cdn.tableau.com/js.js", "https://example.com") == "https://cdn.tableau.com/js.js"

    def test_absolute_preserved(self):
        assert _abs_url("https://tableau.com/viz", "https://example.com") == "https://tableau.com/viz"

    def test_relative_joined(self):
        assert _abs_url("/path/to/viz", "https://example.com") == "https://example.com/path/to/viz"


class TestBuildTableauFeedUrls:
    def test_basic_view(self):
        url = "https://public.tableau.com/views/Workbook/Sheet"
        feeds, screenshot = _build_tableau_feed_urls(url)
        assert any(".csv" in f for f in feeds)
        assert any(".json" in f for f in feeds)
        assert any("format=csv" in f for f in feeds)
        assert any("format=json" in f for f in feeds)
        assert screenshot is not None
        assert "format=png" in screenshot

    def test_view_with_query_params(self):
        url = "https://tableau.example.com/views/Book/Sheet?:embed=y&:showVizHome=no"
        feeds, screenshot = _build_tableau_feed_urls(url)
        for f in feeds:
            assert f.startswith("https://tableau.example.com")


class TestDetectDashboards:
    def test_empty_inputs(self):
        assert detect_dashboards("", "https://example.com") == []
        assert detect_dashboards("<html></html>", "") == []

    def test_tableau_script_tag(self):
        html = '<script src="https://public.tableau.com/javascripts/api/tableau-2.min.js"></script>'
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 1
        assert results[0].platform == "tableau"
        assert results[0].evidence_type == "script"
        assert "tableau-2.min.js" in results[0].url

    def test_powerbi_script_tag(self):
        html = '<script src="https://cdn.powerbi.com/powerbi.min.js"></script>'
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 1
        assert results[0].platform == "powerbi"
        assert results[0].evidence_type == "script"

    def test_tableau_iframe(self):
        html = '<iframe src="https://public.tableau.com/views/CrimeStats/Dashboard" width="800"></iframe>'
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 1
        assert results[0].platform == "tableau"
        assert results[0].evidence_type == "iframe"
        assert len(results[0].feed_urls) > 0
        assert results[0].screenshot_url is not None

    def test_powerbi_iframe(self):
        html = '<iframe src="https://app.powerbi.com/view?r=eyJrIjoiabc" height="600"></iframe>'
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 1
        assert results[0].platform == "powerbi"
        assert results[0].evidence_type == "iframe"
        # PowerBI doesn't currently auto-generate feed URLs
        assert len(results[0].feed_urls) == 0

    def test_tableau_js_init(self):
        html = """
        <script>
            var viz = new tableau.Viz(container, "https://tableau.example.com/views/Book/Sheet");
        </script>
        """
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 1
        assert results[0].platform == "tableau"
        assert results[0].evidence_type == "js_init"
        assert "tableau.example.com/views/Book/Sheet" in results[0].url

    def test_powerbi_js_init(self):
        html = """
        <script>
            powerbi.embed(reportContainer, { embedUrl: "https://app.powerbi.com/reportEmbed?reportId=abc" });
        </script>
        """
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 1
        assert results[0].platform == "powerbi"
        assert results[0].evidence_type == "js_init"
        assert "reportEmbed" in results[0].url

    def test_deduplication_same_url(self):
        html = """
        <script src="https://public.tableau.com/javascripts/api/tableau-2.min.js"></script>
        <iframe src="https://public.tableau.com/views/Book/Sheet"></iframe>
        """
        results = detect_dashboards(html, "https://sheriff.example.com")
        # Should dedupe by platform+url — script and iframe have different URLs here,
        # so we expect 2 findings.  If we forced the same URL, it would be 1.
        urls = [r.url for r in results]
        assert len(urls) == len(set(urls))  # no duplicates

    def test_deduplication_merged_notes(self):
        html = """
        <script>var viz = new tableau.Viz(c, "https://public.tableau.com/views/Book/Sheet");</script>
        <iframe src="https://public.tableau.com/views/Book/Sheet"></iframe>
        """
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 1
        assert "js_init" in results[0].evidence_type
        # Notes from both detections should be merged
        notes = " ".join(results[0].notes)
        assert "tableau.Viz" in notes or "iframe" in notes

    def test_non_dashboard_iframe_ignored(self):
        html = '<iframe src="https://www.youtube.com/embed/abc123"></iframe>'
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 0

    def test_raw_url_match(self):
        html = 'Check out our dashboard at https://public.tableau.com/views/Crime/Stats for more info.'
        results = detect_dashboards(html, "https://sheriff.example.com")
        assert len(results) == 1
        assert results[0].platform == "tableau"
        assert results[0].evidence_type == "url_match"
        assert len(results[0].feed_urls) > 0
