#!/usr/bin/env python3
"""Tests for format_drift_repair — run with: python3 -m pytest tests/test_format_drift_repair.py"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on the path
import sys
sys.path.insert(0, "/root/montanablotter")

from bin import format_drift_repair as fdr


@pytest.fixture
def tmp_db(tmp_path):
    """Create a minimal test database with the relevant schema."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE blotters (
            id INTEGER PRIMARY KEY,
            filename TEXT,
            county TEXT,
            incident_count INTEGER DEFAULT 0,
            file_path TEXT,
            source_document_id INTEGER,
            upload_date DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE source_documents (
            id INTEGER PRIMARY KEY,
            source_type TEXT NOT NULL,
            filename TEXT,
            content_sha256 TEXT NOT NULL,
            storage_path TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE ingestion_jobs (
            id INTEGER PRIMARY KEY,
            source_document_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            retry_count INTEGER DEFAULT 0,
            last_error TEXT,
            started_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT,
            source_key TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE pipeline_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ingestion_job_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            details_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # Insert test data: Hill County with 10 ok + 2 transient errors
    now = datetime.now(timezone.utc)
    for i in range(10):
        sd_id = i + 100
        conn.execute(
            "INSERT INTO source_documents (id, source_type, content_sha256, filename) VALUES (?, 'email', ?, ?)",
            (sd_id, f"hash{i:040d}", f"blotter-{i}.pdf"),
        )
        conn.execute(
            "INSERT INTO ingestion_jobs (id, source_document_id, status) VALUES (?, ?, 'published')",
            (sd_id, sd_id),
        )
        conn.execute(
            "INSERT INTO blotters (filename, county, incident_count, file_path, source_document_id) VALUES (?, 'Hill', 25, ?, ?)",
            (f"blotter-{i}.pdf", f"/tmp/test/blotter-{i}.pdf", sd_id),
        )
        ts = (now - timedelta(hours=i)).isoformat()
        conn.execute(
            "INSERT INTO pipeline_events (ingestion_job_id, stage, status, details_json, created_at) VALUES (?, 'parse', 'ok', ?, ?)",
            (sd_id, json.dumps({"incident_count": 25, "county": "Hill"}), ts),
        )

    # 2 transient errors
    for i in range(2):
        sd_id = 200 + i
        conn.execute(
            "INSERT INTO source_documents (id, source_type, content_sha256, filename) VALUES (?, 'email', ?, ?)",
            (sd_id, f"hash-e{i:040d}", f"error-{i}.pdf"),
        )
        conn.execute(
            "INSERT INTO ingestion_jobs (id, source_document_id, status) VALUES (?, ?, 'failed')",
            (sd_id, sd_id),
        )
        conn.execute(
            "INSERT INTO blotters (filename, county, incident_count, file_path, source_document_id) VALUES (?, 'Hill', 0, ?, ?)",
            (f"error-{i}.pdf", f"/tmp/test/error-{i}.pdf", sd_id),
        )
        ts = (now - timedelta(days=30 + i)).isoformat()
        conn.execute(
            "INSERT INTO pipeline_events (ingestion_job_id, stage, status, details_json, created_at) VALUES (?, 'parse', 'error', ?, ?)",
            (sd_id, json.dumps({"error": "database is locked"}), ts),
        )

    conn.commit()
    conn.close()

    # Patch config to use the temp db
    with patch.object(fdr, "DB_PATH", db_path):
        yield db_path

    db_path.unlink(missing_ok=True)


class TestDetectDrift:
    def test_no_drift_when_all_ok(self, tmp_db):
        """100% success rate → no drift detected."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        # Patch thresholds
        orig_min = fdr.MIN_SAMPLES
        fdr.MIN_SAMPLES = 3
        try:
            result = fdr.detect_drift(conn)
            # All errors are transient → excluded → 100% success
            assert len(result) == 0
        finally:
            fdr.MIN_SAMPLES = orig_min
            conn.close()

    def test_drift_detected_with_non_transient_errors(self, tmp_db):
        """Non-transient errors should trigger drift detection."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row

        # Add a non-transient error
        conn.execute(
            "INSERT INTO source_documents (id, source_type, content_sha256, filename) VALUES (999, 'email', 'hash-nontransient', 'bad.pdf')"
        )
        conn.execute(
            "INSERT INTO ingestion_jobs (id, source_document_id, status) VALUES (999, 999, 'failed')"
        )
        conn.execute(
            "INSERT INTO blotters (filename, county, incident_count, file_path, source_document_id) VALUES ('bad.pdf', 'Hill', 0, '/tmp/bad.pdf', 999)"
        )
        conn.execute(
            "INSERT INTO pipeline_events (ingestion_job_id, stage, status, details_json) VALUES (999, 'parse', 'error', ?)",
            (json.dumps({"error": "regex mismatch: no date found in header line"}),),
        )
        conn.commit()

        orig_threshold = fdr.DRIFT_SUCCESS_THRESHOLD
        orig_min = fdr.MIN_SAMPLES
        fdr.DRIFT_SUCCESS_THRESHOLD = 0.95
        fdr.MIN_SAMPLES = 3
        try:
            result = fdr.detect_drift(conn)
            # Should detect Hill County below 95%
            counties = [r["county"] for r in result]
            assert "hill" in counties
        finally:
            fdr.DRIFT_SUCCESS_THRESHOLD = orig_threshold
            fdr.MIN_SAMPLES = orig_min
            conn.close()

    def test_transient_errors_excluded(self, tmp_db):
        """Database-locked errors should be excluded from success rate."""
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row

        orig_threshold = fdr.DRIFT_SUCCESS_THRESHOLD
        orig_min = fdr.MIN_SAMPLES
        fdr.DRIFT_SUCCESS_THRESHOLD = 0.99
        fdr.MIN_SAMPLES = 3
        try:
            result = fdr.detect_drift(conn)
            # Both errors are transient → Hill County shows 100% success
            counties = [r["county"] for r in result]
            assert "hill" not in counties
        finally:
            fdr.DRIFT_SUCCESS_THRESHOLD = orig_threshold
            fdr.MIN_SAMPLES = orig_min
            conn.close()


class TestIsTransientError:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("database is locked", True),
            ("Deadlock detected while waiting for lock", True),
            ("Connection timeout after 30s", True),
            ("regex mismatch: no date found in header line", False),
            ("OCR failed for scanned PDF", False),
            ("", False),
            (None, False),
        ],
    )
    def test_classification(self, text, expected):
        assert fdr._is_transient_error(text) == expected


class TestGetSamplePdfs:
    def test_excludes_transient_errors(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        result = fdr.get_sample_pdfs(conn, "hill", limit=5)
        # Both errors are transient → no samples returned
        assert len(result) == 0
        conn.close()

    def test_returns_non_transient_errors(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row

        # Add a non-transient error
        conn.execute(
            "INSERT INTO source_documents (id, source_type, content_sha256, filename) VALUES (998, 'email', 'hash-nont', 'bad.pdf')"
        )
        conn.execute(
            "INSERT INTO ingestion_jobs (id, source_document_id, status) VALUES (998, 998, 'failed')"
        )
        conn.execute(
            "INSERT INTO blotters (filename, county, incident_count, file_path, source_document_id) VALUES ('bad.pdf', 'Hill', 0, '/root/test/bad.pdf', 998)"
        )
        conn.execute(
            "INSERT INTO pipeline_events (ingestion_job_id, stage, status, details_json) VALUES (998, 'parse', 'error', ?)",
            (json.dumps({"error": "regex mismatch"}),),
        )
        conn.commit()

        result = fdr.get_sample_pdfs(conn, "hill", limit=5)
        assert len(result) == 1
        # blotter_id is the row id from blotters table; verify filename matches
        assert result[0]["filename"] == "bad.pdf"
        conn.close()


class TestWriteQueueItems:
    def test_red_tier_proposal(self, tmp_path):
        with patch.object(fdr, "QUEUE_ROOT", tmp_path / "queue"):
            fdr.QUEUE_ROOT.mkdir(parents=True, exist_ok=True)
            path = fdr.write_red_tier_proposal(
                county="test-county",
                drift_info={"total": 100, "ok": 90, "success_pct": 90.0},
                diagnosis="Test diagnosis",
                diff="--- a/parser.py\n+++ b/parser.py\n@@ -1 +1 @@\n-old\n+new",
                confidence=0.92,
                risk="low",
                test_case="TEST DATA",
            )
            assert path.exists()
            content = path.read_text()
            assert "profile: blotter-dev" in content
            assert "tier: red" in content
            assert "Test diagnosis" in content
            assert "confidence: 0.92" in content
            # Cleanup
            shutil.rmtree(path.parent)

    def test_dev_escalation(self, tmp_path):
        with patch.object(fdr, "QUEUE_ROOT", tmp_path / "queue"):
            fdr.QUEUE_ROOT.mkdir(parents=True, exist_ok=True)
            evidence = {
                "drift": {"total": 100, "ok": 90, "success_pct": 90.0},
                "bad_samples": [{"blotter_id": 1}],
                "good_samples": [{"blotter_id": 2}],
                "claude_diagnosis": "test",
                "claude_confidence": 0.65,
            }
            path = fdr.write_dev_escalation(
                county="test-county",
                drift_info={"total": 100, "ok": 90, "success_pct": 90.0},
                diagnosis="Low confidence test",
                evidence=evidence,
            )
            assert path.exists()
            assert (path.parent / "attachments" / "evidence.json").exists()
            content = path.read_text()
            assert "profile: blotter-dev" in content
            assert "tier: green" in content
            # Cleanup
            shutil.rmtree(path.parent)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
