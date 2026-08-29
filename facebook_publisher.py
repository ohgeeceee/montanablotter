import hashlib
import hmac
import logging
import os
import re
import sqlite3
from typing import Any, Dict, Optional

import requests

import config

LOGGER = logging.getLogger(__name__)

DB_PATH = config.DB_PATH
DB_TIMEOUT_SECONDS = float(getattr(config, "DB_TIMEOUT_SECONDS", 30))
DB_BUSY_TIMEOUT_MS = int(getattr(config, "DB_BUSY_TIMEOUT_MS", 30000))

DEFAULT_BASE_URL = (os.getenv("BASE_URL") or getattr(config, "BASE_URL", "") or "https://montanablotter.com").rstrip("/")
GRAPH_API_VERSION = getattr(config, "FACEBOOK_GRAPH_API_VERSION", "v22.0")


def _unsplash_image_for_location(conn: sqlite3.Connection, city: str, county: str) -> Optional[str]:
    """Return a cached Unsplash image URL for the given city/county, fetching if needed."""
    access_key = (os.getenv("MB_UNSPLASH_ACCESS_KEY") or getattr(config, "UNSPLASH_ACCESS_KEY", "")).strip()
    if not access_key:
        return None

    location = (city or county or "").strip()
    if not location:
        return None

    slug = re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-")

    cached = conn.execute(
        "SELECT image_url FROM unsplash_image_cache WHERE slug = ?", (slug,)
    ).fetchone()
    if cached:
        return cached["image_url"]

    query = f"{location} Montana"
    try:
        resp = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {access_key}"},
            timeout=10,
        )
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        photo = results[0]
        image_url = photo.get("urls", {}).get("regular", "")
        photographer = photo.get("user", {}).get("name", "")
        photographer_url = photo.get("user", {}).get("links", {}).get("html", "")

        if image_url:
            conn.execute(
                """
                INSERT INTO unsplash_image_cache (slug, image_url, photographer, photographer_url)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    image_url=excluded.image_url,
                    photographer=excluded.photographer,
                    photographer_url=excluded.photographer_url,
                    fetched_at=datetime('now')
                """,
                (slug, image_url, photographer, photographer_url),
            )
            return image_url
    except Exception as exc:
        LOGGER.warning("Unsplash fetch failed for %r: %s", location, exc)

    return None

DEFAULT_FACEBOOK_TEMPLATE = (
    "{title}\n\n"
    "{summary_clean}\n\n"
    "Read the full report: {post_url}\n\n"
    "{hashtags}"
)


class _SafeMap(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn


def _to_bool(raw: Any, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    return default


def _get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return row["value"] if row["value"] is not None else default


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now')
        """,
        (key, value),
    )


def load_facebook_settings(conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    owns_conn = conn is None
    if owns_conn:
        conn = _connect_db()

    try:
        page_id = _get_setting(conn, "facebook_page_id", "").strip() or (os.getenv("FACEBOOK_PAGE_ID") or getattr(config, "FACEBOOK_PAGE_ID", "")).strip()
        access_token = _get_setting(conn, "facebook_page_access_token", "").strip() or (os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN") or getattr(config, "FACEBOOK_PAGE_ACCESS_TOKEN", "")).strip()
        template = _get_setting(conn, "facebook_template", "").strip() or DEFAULT_FACEBOOK_TEMPLATE
        base_url = _get_setting(conn, "facebook_base_url", "").strip() or DEFAULT_BASE_URL
        enabled = _to_bool(_get_setting(conn, "facebook_enabled", "0"), default=False)
        auto_enqueue_enabled = _to_bool(_get_setting(conn, "facebook_auto_enqueue_enabled", "1"), default=True)
        auto_publish_enabled = _to_bool(_get_setting(conn, "facebook_auto_publish_enabled", "0"), default=False)
        max_per_run_raw = _get_setting(conn, "facebook_max_per_run", "3").strip()
        try:
            max_per_run = max(1, min(25, int(max_per_run_raw)))
        except ValueError:
            max_per_run = 3

        return {
            "page_id": page_id,
            "access_token": access_token,
            "template": template,
            "base_url": base_url.rstrip("/"),
            "enabled": enabled,
            "auto_enqueue_enabled": auto_enqueue_enabled,
            "auto_publish_enabled": auto_publish_enabled,
            "max_per_run": max_per_run,
        }
    finally:
        if owns_conn and conn is not None:
            conn.close()


def save_facebook_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    conn = _connect_db()
    try:
        _set_setting(conn, "facebook_page_id", (payload.get("page_id") or "").strip())
        _set_setting(conn, "facebook_page_access_token", (payload.get("access_token") or "").strip())
        _set_setting(conn, "facebook_template", (payload.get("template") or "").strip())
        _set_setting(conn, "facebook_base_url", (payload.get("base_url") or DEFAULT_BASE_URL).strip())
        _set_setting(conn, "facebook_enabled", "1" if _to_bool(payload.get("enabled")) else "0")
        _set_setting(conn, "facebook_auto_enqueue_enabled", "1" if _to_bool(payload.get("auto_enqueue_enabled"), default=False) else "0")
        _set_setting(conn, "facebook_auto_publish_enabled", "1" if _to_bool(payload.get("auto_publish_enabled")) else "0")

        max_per_run = payload.get("max_per_run", 3)
        try:
            max_per_run_val = max(1, min(25, int(max_per_run)))
        except (TypeError, ValueError):
            max_per_run_val = 3
        _set_setting(conn, "facebook_max_per_run", str(max_per_run_val))
        conn.commit()
        return load_facebook_settings(conn)
    finally:
        conn.close()


def _fetch_post(conn: sqlite3.Connection, post_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, title, summary, county, city, agency_name, agency_type, incident_date, audit_status
        FROM posts
        WHERE id = ?
          AND COALESCE(audit_status, 'pending') = 'clean'
        """,
        (post_id,),
    ).fetchone()


def _summary_snippet(summary: str, max_chars: int = 280) -> str:
    compact = re.sub(r"\s+", " ", (summary or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _post_url(post_id: int, settings: Dict[str, Any]) -> str:
    base_url = (settings.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    return f"{base_url}/post/{post_id}"


def _county_hashtags(county: str, city: str, agency_name: str) -> str:
    """Build a short, county-relevant hashtag line. Uses city when available."""
    tags = ["#MontanaBlotter", "#PublicSafety"]
    # City hashtag (preferred — most recognizable)
    if city:
        slug = re.sub(r"[^a-zA-Z0-9]+", "", city).strip()
        if slug and slug.upper() not in [t.lstrip("#").upper() for t in tags]:
            tags.append(f"#{slug}")
    # County hashtag
    if county:
        slug = re.sub(r"[^a-zA-Z0-9]+", "", county).strip()
        if slug and slug.upper() not in [t.lstrip("#").upper() for t in tags]:
            tags.append(f"#{slug}")
    # Agency hashtag: last significant word, skip filler
    name = (agency_name or "").strip()
    filler = {"of", "the", "county", "state", "montana", "mt"}
    significant = {"pd", "sheriff", "police", "department", "office", "bureau", "trooper", "detention", "corrections", "marshals", "hso", "spps", "gcso", "mcso", "fccso", "lcso"}
    short = ""
    # Agency hashtag: skip when city already identifies the location
    if name and not city:
        words = re.findall(r"[A-Za-z]+", name)
        for word in reversed(words):
            lw = word.lower()
            if lw in significant:
                short = word[:15]
                break
            if lw not in filler and not short:
                short = word[:15]
    if not short and county:
        compound = re.sub(r"[^a-zA-Z0-9]+", "", county).strip()
        if len(compound) >= 4:
            short = compound[:15]
    if short and len(short) <= 15 and short.upper() not in [t.lstrip("#").upper() for t in tags]:
        tags.append(f"#{short}")
    return "  ".join(tags)


def _strip_historical_blocks(text: str) -> str:
    """Remove editorial '// Historical Perspective:' blocks — not for social."""
    return re.sub(r"\s*//\s*Historical\s+Perspective:.*?(?=\n\n|\Z)", "", text or "", flags=re.DOTALL).strip()


def _post_snippet(summary: str, max_chars: int = 240) -> str:
    """Clean, concise snippet for FB — strips historical blocks and trims."""
    clean = _strip_historical_blocks(summary or "")
    compact = re.sub(r"\s+", " ", clean).strip()
    if len(compact) <= max_chars:
        return compact
    cut = compact[: max_chars - 3].rstrip()
    # Try not to cut mid-sentence
    last_period = cut.rfind(". ")
    if last_period > max_chars // 2:
        cut = cut[:last_period + 1]
    return cut + "..."


def _render_message(post: sqlite3.Row, settings: Dict[str, Any], custom_message: Optional[str] = None) -> str:
    if custom_message and custom_message.strip():
        rendered = custom_message.strip()
    else:
        mapping = _SafeMap(
            title=(post["title"] or "").strip(),
            summary=(post["summary"] or "").strip(),
            summary_clean=_post_snippet(post["summary"] or ""),
            county=(post["county"] or "").strip(),
            city=(post["city"] or "").strip(),
            agency_name=(post["agency_name"] or "").strip(),
            agency_type=(post["agency_type"] or "").strip(),
            incident_date=(post["incident_date"] or "").strip(),
            post_id=str(post["id"]),
            post_url=_post_url(int(post["id"]), settings),
            hashtags=_county_hashtags(
                post["county"] or "", post["city"] or "", post["agency_name"] or ""
            ),
        )
        template = settings.get("template") or DEFAULT_FACEBOOK_TEMPLATE
        try:
            rendered = template.format_map(mapping).strip()
        except Exception:
            LOGGER.warning("facebook template parse failed; using default template")
            rendered = DEFAULT_FACEBOOK_TEMPLATE.format_map(mapping).strip()

    # Keep margin under Facebook's large limits so admins don't accidentally blast giant payloads.
    if len(rendered) > 5000:
        rendered = rendered[:4997].rstrip() + "..."
    return rendered


def queue_post(
    post_id: Optional[int] = None,
    blog_post_id: Optional[int] = None,
    content_type: str = "blotter",
    created_by_user_id: Optional[int] = None,
    scheduled_for: Optional[str] = None,
    enqueue_source: str = "manual",
    custom_message: Optional[str] = None,
    link_url: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    if content_type not in ("blotter", "blog", "custom"):
        content_type = "blotter"
    owns_conn = conn is None
    if owns_conn:
        conn = _connect_db()
    assert conn is not None

    try:
        if content_type == "blotter":
            if not post_id:
                return {"ok": False, "error": "post_id_required_for_blotter"}
            post = _fetch_post(conn, post_id)
            if not post:
                return {"ok": False, "error": "post_not_found"}
            dedupe_key = f"facebook:blotter:{post_id}"
        elif content_type == "blog":
            if not blog_post_id:
                return {"ok": False, "error": "blog_post_id_required_for_blog"}
            blog_post = conn.execute(
                "SELECT id, title, excerpt, body, slug, published FROM blog_posts WHERE id = ?",
                (blog_post_id,),
            ).fetchone()
            if not blog_post:
                return {"ok": False, "error": "blog_post_not_found"}
            if not blog_post["published"]:
                return {"ok": False, "error": "blog_post_not_published"}
            dedupe_key = f"facebook:blog:{blog_post_id}"
        else:  # custom
            dedupe_key = f"facebook:custom:{hash((custom_message or '') + (link_url or ''))}"

        existing = conn.execute(
            "SELECT id, status FROM facebook_post_queue WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()

        if existing:
            if existing["status"] in ("failed", "skipped"):
                update_sql = """
                    UPDATE facebook_post_queue
                    SET status = 'queued',
                        last_error = NULL,
                        enqueue_source = ?,
                        custom_message = COALESCE(?, custom_message),
                        link_url = COALESCE(?, link_url),
                        updated_at = datetime('now')
                """
                params: list = [enqueue_source, custom_message, link_url]
                if scheduled_for:
                    update_sql += ", scheduled_for = ?"
                    params.append(scheduled_for)
                update_sql += " WHERE id = ?"
                params.append(existing["id"])
                conn.execute(update_sql, params)
                if owns_conn:
                    conn.commit()
                return {"ok": True, "created": False, "requeued": True, "queue_id": int(existing["id"]), "status": "queued"}
            return {"ok": True, "created": False, "requeued": False, "queue_id": int(existing["id"]), "status": existing["status"]}

        insert_sql = """
            INSERT INTO facebook_post_queue
                (content_type, post_id, blog_post_id, dedupe_key, status, custom_message,
                 link_url, enqueue_source, scheduled_for, created_by_user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """
        cursor = conn.execute(
            insert_sql,
            (content_type, post_id, blog_post_id, dedupe_key, custom_message, link_url, enqueue_source, scheduled_for, created_by_user_id),
        )
        if owns_conn:
            conn.commit()
        return {"ok": True, "created": True, "requeued": False, "queue_id": int(cursor.lastrowid or 0), "status": "queued"}
    finally:
        if owns_conn:
            conn.close()


def queue_recent_posts(
    limit: int = 10,
    created_by_user_id: Optional[int] = None,
    enqueue_source: str = "bulk_recent",
) -> Dict[str, int]:
    conn = _connect_db()
    try:
        limit = max(1, min(100, int(limit)))
        rows = conn.execute(
            """
            SELECT id
            FROM posts
            WHERE COALESCE(audit_status, 'pending') = 'clean'
            ORDER BY incident_date DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        created = 0
        requeued = 0
        skipped = 0
        for row in rows:
            result = queue_post(
                post_id=int(row["id"]),
                content_type="blotter",
                created_by_user_id=created_by_user_id,
                enqueue_source=enqueue_source,
                conn=conn,
            )
            if not result.get("ok"):
                skipped += 1
            elif result.get("created"):
                created += 1
            elif result.get("requeued"):
                requeued += 1
            else:
                skipped += 1

        conn.commit()
        return {"created": created, "requeued": requeued, "skipped": skipped}
    finally:
        conn.close()


def _mark_failed(conn: sqlite3.Connection, queue_id: int, error_message: str) -> None:
    conn.execute(
        """
        UPDATE facebook_post_queue
        SET status = 'failed',
            attempts = attempts + 1,
            last_error = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (error_message[:1200], queue_id),
    )


def publish_queue_item(queue_id: int) -> Dict[str, Any]:
    conn = _connect_db()
    try:
        claimed = conn.execute(
            """
            UPDATE facebook_post_queue
            SET status = 'processing',
                updated_at = datetime('now')
            WHERE id = ? AND status = 'queued'
            """,
            (queue_id,),
        )
        if claimed.rowcount == 0:
            row = conn.execute("SELECT id, status FROM facebook_post_queue WHERE id = ?", (queue_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "queue_not_found"}
            return {"ok": False, "error": f"not_queueable:{row['status']}"}

        queue_row = conn.execute("SELECT * FROM facebook_post_queue WHERE id = ?", (queue_id,)).fetchone()
        if not queue_row:
            _mark_failed(conn, queue_id, "queue_row_missing")
            conn.commit()
            return {"ok": False, "error": "queue_row_missing"}

        content_type = queue_row["content_type"] or "blotter"
        message = ""
        post_url = ""

        if content_type == "blotter":
            post_row = conn.execute(
                """
                SELECT p.id, p.title, p.summary, p.county, p.city, p.agency_name, p.agency_type, p.incident_date
                FROM posts p
                WHERE p.id = ? AND COALESCE(p.audit_status, 'pending') = 'clean'
                """,
                (queue_row["post_id"],),
            ).fetchone()
            if not post_row:
                _mark_failed(conn, queue_id, "post_missing_or_not_clean")
                conn.commit()
                return {"ok": False, "error": "post_missing_or_not_clean"}
            message = _render_message(post_row, settings=load_facebook_settings(conn), custom_message=queue_row["custom_message"])
            post_url = _post_url(int(post_row["id"]), load_facebook_settings(conn))
        elif content_type == "blog":
            blog_row = conn.execute(
                "SELECT id, title, excerpt, body, slug FROM blog_posts WHERE id = ? AND published = 1",
                (queue_row["blog_post_id"],),
            ).fetchone()
            if not blog_row:
                _mark_failed(conn, queue_id, "blog_post_missing_or_not_published")
                conn.commit()
                return {"ok": False, "error": "blog_post_missing_or_not_published"}
            settings = load_facebook_settings(conn)
            base_url = settings.get("base_url", DEFAULT_BASE_URL).rstrip("/")
            post_url = f"{base_url}/blog/{blog_row['slug']}"
            snippet = _summary_snippet(blog_row["excerpt"] or blog_row["body"])
            if queue_row["custom_message"]:
                message = queue_row["custom_message"]
            else:
                message = f"{blog_row['title']}\n\n{snippet}\n\nRead more: {post_url}\n\n#Montana #MontanaBlotter #PublicSafety"
        else:  # custom
            message = queue_row["custom_message"] or ""
            post_url = queue_row["link_url"] or ""

        settings = load_facebook_settings(conn)
        if not settings.get("enabled"):
            _mark_failed(conn, queue_id, "facebook_disabled")
            conn.commit()
            return {"ok": False, "error": "facebook_disabled"}
        if not settings.get("page_id") or not settings.get("access_token"):
            _mark_failed(conn, queue_id, "missing_page_or_access_token")
            conn.commit()
            return {"ok": False, "error": "missing_credentials"}

        access_token = settings["access_token"]
        app_secret = (os.getenv("MB_FACEBOOK_APP_SECRET") or getattr(config, "FACEBOOK_APP_SECRET", "")).strip()
        appsecret_proof = ""
        if app_secret:
            appsecret_proof = hmac.new(
                app_secret.encode("utf-8"),
                access_token.encode("utf-8"),
                "sha256",
            ).hexdigest()

        # Generate a branded text-card image for blotter posts
        image_path_for_fb: Optional[str] = None
        image_url_for_fb: str = ""
        if content_type == "blotter" and post_row:
            from services.publishing.image_generator import generate as generate_image
            today_str = __import__("datetime").date.today().isoformat()
            image_info = generate_image(
                title=post_row["title"] or "Montana Blotter Daily",
                county=post_row["county"] or "",
                post_date=today_str,
                excerpt=post_row["summary"] or "",
            )
            image_path_for_fb = image_info.get("path", "")
            image_url_for_fb = image_info.get("url", "")

        base_payload: Dict[str, Any] = {
            "access_token": access_token,
            "message": message,
        }
        if appsecret_proof:
            base_payload["appsecret_proof"] = appsecret_proof

        if image_url_for_fb:
            # Photo post — branded image card in the feed
            graph_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings['page_id']}/photos"
            payload = {**base_payload, "url": image_url_for_fb, "caption": message}
        else:
            # Fallback: plain link post
            graph_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings['page_id']}/feed"
            payload = {**base_payload}
            if post_url:
                payload["link"] = post_url
        try:
            response = requests.post(graph_url, data=payload, timeout=35)
            data = response.json() if response.content else {}
        except Exception as exc:
            _mark_failed(conn, queue_id, f"request_error:{exc}")
            conn.commit()
            return {"ok": False, "error": "request_error"}

        if response.ok and isinstance(data, dict) and data.get("id"):
            conn.execute(
                """
                UPDATE facebook_post_queue
                SET status = 'posted',
                    attempts = attempts + 1,
                    facebook_post_id = ?,
                    last_error = NULL,
                    posted_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (str(data.get("id")), queue_id),
            )
            conn.commit()
            return {"ok": True, "facebook_post_id": str(data.get("id"))}

        error_text = "facebook_api_error"
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                error_text = f"facebook_api_error:{err.get('message') or err.get('type') or 'unknown'}"
        _mark_failed(conn, queue_id, error_text)
        conn.commit()
        return {"ok": False, "error": error_text}
    finally:
        conn.close()


def run_facebook_queue(max_items: Optional[int] = None, manual_trigger: bool = False) -> Dict[str, Any]:
    conn = _connect_db()
    try:
        settings = load_facebook_settings(conn)
        if not settings.get("enabled"):
            return {"ok": True, "processed": 0, "posted": 0, "failed": 0, "skipped_reason": "facebook_disabled"}
        if not manual_trigger and not settings.get("auto_publish_enabled"):
            return {"ok": True, "processed": 0, "posted": 0, "failed": 0, "skipped_reason": "auto_publish_disabled"}
        if not settings.get("page_id") or not settings.get("access_token"):
            return {"ok": False, "processed": 0, "posted": 0, "failed": 0, "error": "missing_credentials"}

        if max_items is None:
            max_items = settings.get("max_per_run", 3)
        max_items = max(1, min(100, int(max_items)))

        rows = conn.execute(
            """
            SELECT id
            FROM facebook_post_queue
            WHERE status = 'queued'
              AND datetime(COALESCE(scheduled_for, datetime('now'))) <= datetime('now')
            ORDER BY datetime(COALESCE(scheduled_for, datetime('now'))) ASC, id ASC
            LIMIT ?
            """,
            (max_items,),
        ).fetchall()
    finally:
        conn.close()

    posted = 0
    failed = 0
    for row in rows:
        result = publish_queue_item(int(row["id"]))
        if result.get("ok"):
            posted += 1
        else:
            failed += 1

    return {"ok": True, "processed": len(rows), "posted": posted, "failed": failed}


def auto_queue_post_if_enabled(post_id: int) -> Dict[str, Any]:
    conn = _connect_db()
    try:
        settings = load_facebook_settings(conn)
        if not settings.get("enabled"):
            return {"ok": True, "queued": False, "reason": "facebook_disabled"}
        if not settings.get("auto_enqueue_enabled"):
            return {"ok": True, "queued": False, "reason": "auto_enqueue_disabled"}
        result = queue_post(
            post_id=post_id,
            content_type="blotter",
            enqueue_source="auto_ingestion",
            conn=conn,
        )
        conn.commit()
        return {"ok": bool(result.get("ok")), "queued": bool(result.get("created") or result.get("requeued")), **result}
    except Exception as exc:
        LOGGER.warning("auto_queue_post_if_enabled failed for post_id=%s: %s", post_id, exc)
        return {"ok": False, "queued": False, "error": str(exc)}
    finally:
        conn.close()


def mask_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return token[:4] + ("*" * (len(token) - 8)) + token[-4:]
