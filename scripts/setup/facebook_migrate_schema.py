#!/usr/bin/env python3
"""
facebook_migrate_schema.py — Migrate facebook_post_queue to support multiple content types.

Adds:
  - content_type (blotter | blog | custom)
  - blog_post_id (nullable FK-like reference to blog_posts.id)
  - link_url (nullable direct link for custom posts)
  - Makes post_id nullable

Backs up the table first. Safe to re-run (idempotent).
"""

import os
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/root/montanablotter/blotter.db")
DB_TIMEOUT = float(os.getenv("DB_TIMEOUT_SECONDS", "30"))
BUSY_TIMEOUT_MS = int(os.getenv("DB_BUSY_TIMEOUT_MS", "30000"))


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def migrate() -> None:
    conn = _connect_db()
    try:
        # Check current schema
        cols = {row[1] for row in conn.execute("PRAGMA table_info(facebook_post_queue)")}

        if "content_type" in cols and "blog_post_id" in cols and "link_url" in cols:
            print("Schema already migrated. Nothing to do.")
            return

        # Backup existing table
        backup_name = f"facebook_post_queue_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        conn.execute(f"ALTER TABLE facebook_post_queue RENAME TO {backup_name}")
        print(f"Backed up existing table to {backup_name}")

        # Create new table with expanded schema
        conn.execute(
            """
            CREATE TABLE facebook_post_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content_type TEXT NOT NULL DEFAULT 'blotter',
                post_id INTEGER,
                blog_post_id INTEGER,
                dedupe_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'queued',
                custom_message TEXT,
                link_url TEXT,
                enqueue_source TEXT DEFAULT 'manual',
                scheduled_for TEXT DEFAULT (datetime('now')),
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                facebook_post_id TEXT,
                last_error TEXT,
                created_by_user_id INTEGER,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                posted_at TEXT
            )
            """
        )

        # Migrate old data: all existing rows were blotter posts
        conn.execute(
            f"""
            INSERT INTO facebook_post_queue
                (id, content_type, post_id, blog_post_id, dedupe_key, status, custom_message,
                 link_url, enqueue_source, scheduled_for, attempts, max_attempts,
                 facebook_post_id, last_error, created_by_user_id, created_at, updated_at, posted_at)
            SELECT
                id, 'blotter', post_id, NULL, dedupe_key, status, custom_message,
                NULL, enqueue_source, scheduled_for, attempts, max_attempts,
                facebook_post_id, last_error, created_by_user_id, created_at, updated_at, posted_at
            FROM {backup_name}
            """
        )
        print(f"Migrated {conn.total_changes} rows from backup.")

        # Drop old indexes if they exist, then recreate
        for idx in [
            "idx_fb_queue_status_time",
            "idx_fb_queue_post",
            "idx_fb_queue_blog",
            "idx_fb_queue_created",
            "idx_fb_queue_content_type",
        ]:
            conn.execute(f"DROP INDEX IF EXISTS {idx}")

        conn.execute(
            "CREATE INDEX idx_fb_queue_status_time ON facebook_post_queue(status, scheduled_for)"
        )
        conn.execute(
            "CREATE INDEX idx_fb_queue_post ON facebook_post_queue(post_id) WHERE post_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX idx_fb_queue_blog ON facebook_post_queue(blog_post_id) WHERE blog_post_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX idx_fb_queue_created ON facebook_post_queue(created_at)"
        )
        conn.execute(
            "CREATE INDEX idx_fb_queue_content_type ON facebook_post_queue(content_type, status)"
        )

        conn.commit()
        print("Migration complete. facebook_post_queue now supports blotter, blog, and custom content.")
    except Exception as exc:
        conn.rollback()
        print(f"Migration failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
