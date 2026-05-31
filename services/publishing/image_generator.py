"""
Generate social media images for Montana Blotter posts.

Text graphics are produced locally with Pillow (no external API).
AI images use fal.ai flux/dev when FAL_API_KEY is set; falls back to text graphic on any error.
"""

import logging
import os
import re
import textwrap
from typing import Optional

import requests

import config

LOGGER = logging.getLogger(__name__)

_FONT_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "../../venv/lib/python3.12/site-packages/ocrmypdf/data/NotoSans-Regular.ttf",
))

_STATIC_SOCIAL = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../../static/social"
))

_BG_NAVY = (26, 39, 68)
_GOLD = (201, 168, 76)
_WHITE = (255, 255, 255)
_DARK_STRIP = (15, 24, 45)


def _is_big_story(title: str, excerpt: str) -> bool:
    text = f"{title} {excerpt}"
    match = re.search(r"(\d+)\s+(?:bookings|incident entries)", text, re.I)
    if match and int(match.group(1)) >= 50:
        return True
    if re.search(r"led montana", text, re.I):
        return True
    return False


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def _text_graphic(title: str, county: str, post_date: str) -> str:
    """Produce a 1080x1080 branded text card. Returns absolute file path."""
    from PIL import Image, ImageDraw, ImageFont

    os.makedirs(_STATIC_SOCIAL, exist_ok=True)
    out_path = os.path.join(_STATIC_SOCIAL, f"{post_date}-{_slug(title)}.jpg")

    img = Image.new("RGB", (1080, 1080), _BG_NAVY)
    draw = ImageDraw.Draw(img)

    try:
        font_brand = ImageFont.truetype(_FONT_PATH, 38)
        font_headline = ImageFont.truetype(_FONT_PATH, 64)
        font_sub = ImageFont.truetype(_FONT_PATH, 34)
    except Exception:
        font_brand = font_headline = font_sub = ImageFont.load_default()

    draw.rectangle([(0, 0), (1080, 8)], fill=_GOLD)
    draw.text((50, 30), "MONTANA BLOTTER", font=font_brand, fill=_GOLD)

    y = 200
    for line in textwrap.wrap(title, width=22)[:4]:
        draw.text((50, y), line, font=font_headline, fill=_WHITE)
        y += 80

    draw.rectangle([(50, y + 20), (1030, y + 24)], fill=_GOLD)

    draw.rectangle([(0, 940), (1080, 1080)], fill=_DARK_STRIP)
    draw.text((50, 960), (county.title() if county else "Montana"), font=font_sub, fill=_GOLD)
    draw.text((50, 1000), post_date, font=font_sub, fill=_WHITE)
    draw.text((700, 1000), "montanablotter.com", font=font_sub, fill=_WHITE)

    img.save(out_path, "JPEG", quality=92)
    return out_path


def _ai_image(title: str, county: str, post_date: str) -> Optional[str]:
    """Generate an AI image via fal.ai. Returns file path or None on failure."""
    api_key = (getattr(config, "FAL_API_KEY", "") or "").strip()
    if not api_key:
        return None

    os.makedirs(_STATIC_SOCIAL, exist_ok=True)
    out_path = os.path.join(_STATIC_SOCIAL, f"{post_date}-{_slug(title)}-ai.jpg")
    location = county.title() if county else "Montana"

    prompt = (
        f"Editorial news photograph: {location} Montana at dawn, "
        "big sky country, snow-capped Rocky Mountains in background, "
        "empty rural highway, patrol car on roadside, cinematic lighting, "
        "photorealistic, high resolution"
    )

    try:
        resp = requests.post(
            "https://fal.run/fal-ai/flux/dev",
            json={"prompt": prompt, "image_size": "square_hd", "num_inference_steps": 28, "num_images": 1},
            headers={"Authorization": f"Key {api_key}", "Content-Type": "application/json"},
            timeout=60,
        )
        resp.raise_for_status()
        image_url = resp.json()["images"][0]["url"]
        img_bytes = requests.get(image_url, timeout=30).content
        with open(out_path, "wb") as fh:
            fh.write(img_bytes)
        _overlay_wordmark(out_path, title, county, post_date)
        return out_path
    except Exception as exc:
        LOGGER.warning("fal.ai image generation failed: %s", exc)
        return None


def _overlay_wordmark(path: str, title: str, county: str, post_date: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(path).resize((1080, 1080))
    draw = ImageDraw.Draw(img, "RGBA")

    try:
        font_brand = ImageFont.truetype(_FONT_PATH, 36)
        font_headline = ImageFont.truetype(_FONT_PATH, 56)
        font_sub = ImageFont.truetype(_FONT_PATH, 30)
    except Exception:
        font_brand = font_headline = font_sub = ImageFont.load_default()

    draw.rectangle([(0, 0), (1080, 80)], fill=(15, 24, 45, 200))
    draw.rectangle([(0, 920), (1080, 1080)], fill=(15, 24, 45, 200))
    draw.rectangle([(0, 0), (1080, 6)], fill=_GOLD)
    draw.text((40, 22), "MONTANA BLOTTER", font=font_brand, fill=_GOLD)

    y = 930
    for line in textwrap.wrap(title, width=26)[:2]:
        draw.text((40, y), line, font=font_headline, fill=_WHITE)
        y += 65

    location = county.title() if county else "Montana"
    draw.text((40, 1040), f"{location} · {post_date}", font=font_sub, fill=_GOLD)
    img.save(path, "JPEG", quality=92)


def generate(title: str, county: str, post_date: str, excerpt: str = "") -> dict:
    """
    Generate the best available image for a social post.

    Returns {"path": str, "url": str, "type": "ai"|"text"}.
    """
    big_story = _is_big_story(title, excerpt)
    path: Optional[str] = None
    img_type = "text"

    if big_story:
        path = _ai_image(title, county, post_date)
        if path:
            img_type = "ai"

    if not path:
        path = _text_graphic(title, county, post_date)

    base_url = (getattr(config, "BASE_URL", "") or "https://montanablotter.com").rstrip("/")
    url = f"{base_url}/static/social/{os.path.basename(path)}"
    return {"path": path, "url": url, "type": img_type}
