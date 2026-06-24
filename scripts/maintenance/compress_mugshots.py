#!/usr/bin/env python3
"""
Download and compress warrant mugshots so they are served from local storage
instead of hot-linked sheriff URLs. Reduces external dependency and bandwidth.

Run manually or via cron:
    /root/montanablotter/venv/bin/python scripts/maintenance/compress_mugshots.py
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import config

DB_PATH = os.getenv("MB_DB_PATH", "/root/montanablotter/blotter.db").strip() or "/root/montanablotter/blotter.db"
UPLOAD_DIR = Path(config.UPLOAD_DIR)
MUGSHOT_DIR = UPLOAD_DIR / "mugshots"

MAX_WIDTH = 600
MAX_HEIGHT = 600
TARGET_QUALITY = 80
TARGET_MAX_KB = 80
HTTP_TIMEOUT = 20


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_filename(value: str) -> str:
    """Make a filesystem-safe filename from a source record id."""
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    return value.strip("-") or "unknown"


def _compress_image(data: bytes) -> bytes:
    """Resize and re-encode an image to a small JPEG."""
    img = Image.open(BytesIO(data))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    width, height = img.size
    if width > MAX_WIDTH or height > MAX_HEIGHT:
        ratio = min(MAX_WIDTH / width, MAX_HEIGHT / height)
        img = img.resize((int(width * ratio), int(height * ratio)), Image.LANCZOS)

    out = BytesIO()
    quality = TARGET_QUALITY
    img.save(out, "JPEG", quality=quality, optimize=True)

    # Iteratively lower quality until under target size
    while out.tell() > TARGET_MAX_KB * 1024 and quality > 35:
        quality -= 5
        out = BytesIO()
        img.save(out, "JPEG", quality=quality, optimize=True)

    return out.getvalue()


def _cache_mugshot_for_row(row: sqlite3.Row) -> str | None:
    """Download, compress, and store a mugshot. Return new local path or None."""
    url = (row["mugshot_url"] or "").strip()
    if not url or url.startswith("/uploads/"):
        return None

    county = (row["county"] or "unknown").strip().lower().replace(" ", "-")
    source_id = _safe_filename(row["source_record_id"])
    county_dir = MUGSHOT_DIR / county
    county_dir.mkdir(parents=True, exist_ok=True)
    dest = county_dir / f"{source_id}.jpg"

    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0)"
        })
        resp.raise_for_status()
        compressed = _compress_image(resp.content)
        dest.write_bytes(compressed)
    except Exception as exc:
        print(f"  skipped {row['id']} ({url[:60]}...): {exc}")
        return None

    local_url = f"/uploads/mugshots/{county}/{source_id}.jpg"
    print(f"  cached {row['id']} -> {local_url} ({len(compressed) // 1024}kb)")
    return local_url


def run(limit: int | None = None) -> dict:
    MUGSHOT_DIR.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    sql = """
        SELECT id, source_record_id, county, mugshot_url
        FROM warrants
        WHERE COALESCE(mugshot_url, '') != ''
          AND mugshot_url NOT LIKE '/uploads/%'
          AND status = 'active'
        ORDER BY id
    """
    params = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)

    rows = conn.execute(sql, params).fetchall()
    processed = 0
    updated = 0

    print(f"Found {len(rows)} active warrant mugshots to cache")
    for row in rows:
        processed += 1
        local_url = _cache_mugshot_for_row(row)
        if local_url:
            conn.execute(
                "UPDATE warrants SET mugshot_url = ? WHERE id = ?",
                (local_url, row["id"]),
            )
            updated += 1

    conn.commit()
    conn.close()
    print(f"Processed {processed}, cached {updated}")
    return {"processed": processed, "cached": updated}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Compress and cache warrant mugshots")
    parser.add_argument("--limit", type=int, help="Only process N records")
    args = parser.parse_args()
    run(limit=args.limit)
