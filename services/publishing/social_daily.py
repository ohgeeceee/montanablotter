"""
Daily social media poster — runs at 7:20am after daily_blog_worker.

Pulls the latest published blog_post, generates an image, and posts to
Facebook (via existing queue) and Instagram.
"""

import logging
import sqlite3
import textwrap
from datetime import date
from typing import Optional

import config
import facebook_publisher
import instagram_publisher
from services.publishing.image_generator import generate as generate_image

LOGGER = logging.getLogger(__name__)

DB_PATH = config.DB_PATH

_IG_HASHTAGS = "#MontanaNews #MontanaBlotter #PublicSafety #Montana #LocalNews"


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _get_todays_post(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    today = date.today().isoformat()
    return conn.execute(
        """
        SELECT id, title, slug, excerpt, created_at
        FROM blog_posts
        WHERE published = 1
          AND DATE(created_at) = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (today,),
    ).fetchone()


def _already_posted_today(conn: sqlite3.Connection, platform: str) -> bool:
    today = date.today().isoformat()
    row = conn.execute(
        """
        SELECT id FROM social_posts_log
        WHERE platform = ?
          AND status = 'posted'
          AND DATE(created_at) = ?
        LIMIT 1
        """,
        (platform, today),
    ).fetchone()
    return row is not None


def _log_result(
    conn: sqlite3.Connection,
    blog_post_id: int,
    platform: str,
    status: str,
    image_path: str = "",
    fb_post_id: str = "",
    ig_media_id: str = "",
    error_message: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO social_posts_log
            (blog_post_id, platform, status, fb_post_id, ig_media_id, image_path, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (blog_post_id, platform, status, fb_post_id or None, ig_media_id or None,
         image_path or None, error_message or None),
    )
    conn.commit()


def _build_fb_caption(post: sqlite3.Row, post_url: str) -> str:
    title = post["title"] or ""
    raw_excerpt = post["excerpt"] or ""
    excerpt = raw_excerpt[:280].rstrip()
    if len(raw_excerpt) > 280:
        excerpt += "…"
    return f"{title}\n\n{excerpt}\n\nRead the full report: {post_url}\n\n#MontanaNews #MontanaBlotter #PublicSafety #Montana"


def _build_ig_caption(post: sqlite3.Row, post_url: str) -> str:
    title = post["title"] or ""
    lines = textwrap.wrap(post["excerpt"] or "", width=120)
    short_excerpt = lines[0] if lines else ""
    return f"{title}\n\n{short_excerpt}\n\nLink in bio 🔗\n\n{_IG_HASHTAGS}"


def run() -> None:
    if not getattr(config, "SOCIAL_POSTING_ENABLED", False):
        LOGGER.info("SOCIAL_POSTING_ENABLED is false — skipping")
        print("Social posting disabled (MB_SOCIAL_POSTING_ENABLED not set). Skipping.")
        return

    conn = _connect_db()
    try:
        post = _get_todays_post(conn)
        if not post:
            LOGGER.info("No published blog post found for today — nothing to share")
            print("No blog post published today yet. Skipping social post.")
            return

        blog_post_id = post["id"]
        title = post["title"] or "Montana Blotter Daily"
        excerpt = post["excerpt"] or ""
        slug = post["slug"] or ""
        today_str = date.today().isoformat()

        base_url = (getattr(config, "BASE_URL", "") or "https://montanablotter.com").rstrip("/")
        post_url = f"{base_url}/blog/{slug}"

        LOGGER.info("Generating image for blog_post_id=%d title=%r", blog_post_id, title)
        image_info = generate_image(title=title, county="", post_date=today_str, excerpt=excerpt)
        image_url = image_info["url"]
        image_path = image_info["path"]
        LOGGER.info("Image generated (%s): %s", image_info["type"], image_path)
        print(f"Image: {image_url} ({image_info['type']})")

        # --- Facebook ---
        if _already_posted_today(conn, "facebook"):
            LOGGER.info("Already posted to Facebook today — skipping")
            print("Facebook: already posted today, skipping.")
        else:
            try:
                fb_caption = _build_fb_caption(post, post_url)
                queue_result = facebook_publisher.queue_post(
                    blog_post_id=blog_post_id,
                    content_type="blog",
                    enqueue_source="social_daily",
                    custom_message=fb_caption,
                    link_url=post_url,
                )
                queue_id = queue_result.get("queue_id")
                if queue_id:
                    publish_result = facebook_publisher.publish_queue_item(queue_id)
                    fb_post_id = publish_result.get("post_id", "")
                    if publish_result.get("success"):
                        print(f"Facebook: posted (post_id={fb_post_id})")
                        _log_result(conn, blog_post_id, "facebook", "posted",
                                    image_path=image_path, fb_post_id=fb_post_id)
                    else:
                        err = publish_result.get("error", "unknown error")
                        LOGGER.error("Facebook publish failed: %s", err)
                        print(f"Facebook: FAILED — {err}")
                        _log_result(conn, blog_post_id, "facebook", "failed",
                                    image_path=image_path, error_message=str(err))
                else:
                    err = queue_result.get("error", "queue_post returned no queue_id")
                    LOGGER.error("Facebook queue_post failed: %s", err)
                    _log_result(conn, blog_post_id, "facebook", "failed",
                                image_path=image_path, error_message=str(err))
            except Exception as exc:
                LOGGER.exception("Facebook posting raised exception")
                _log_result(conn, blog_post_id, "facebook", "failed",
                            image_path=image_path, error_message=str(exc))

        # --- Instagram ---
        if _already_posted_today(conn, "instagram"):
            LOGGER.info("Already posted to Instagram today — skipping")
            print("Instagram: already posted today, skipping.")
        else:
            try:
                ig_caption = _build_ig_caption(post, post_url)
                ig_result = instagram_publisher.post_to_instagram(
                    caption=ig_caption,
                    image_url=image_url,
                    conn=conn,
                )
                if ig_result["success"]:
                    print(f"Instagram: posted (media_id={ig_result['media_id']})")
                    _log_result(conn, blog_post_id, "instagram", "posted",
                                image_path=image_path, ig_media_id=ig_result["media_id"] or "")
                else:
                    err = ig_result.get("error", "unknown error")
                    LOGGER.error("Instagram post failed: %s", err)
                    print(f"Instagram: FAILED — {err}")
                    _log_result(conn, blog_post_id, "instagram", "failed",
                                image_path=image_path, error_message=str(err))
            except Exception as exc:
                LOGGER.exception("Instagram posting raised exception")
                _log_result(conn, blog_post_id, "instagram", "failed",
                            image_path=image_path, error_message=str(exc))

    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
