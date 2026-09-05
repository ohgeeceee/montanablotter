"""Cascade County jail roster ingestion through the county's public viewer.

Cascade County intentionally disables direct PDF downloads.  This adapter uses
the public SharePoint UI exactly as an anonymous visitor does, opens the
officially offered embed view, renders each page, and OCRs the visible table.
It does not use credentials, private APIs, or extracted SharePoint tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import io
import logging
import re
from urllib.parse import urlparse

from PIL import Image
import pytesseract
from playwright.sync_api import sync_playwright

from services.ingestion.models import JailBookingRecord


logger = logging.getLogger(__name__)

COUNTY_ROSTER_PAGE = "https://www.cascadecountymt.gov/314/Inmate-Roster"
SHAREPOINT_HOST = "ccmtgov-my.sharepoint.com"
VIEWPORT = {"width": 1800, "height": 1400}
MIN_EXPECTED_PAGES = 20
MAX_EXPECTED_PAGES = 100
MIN_EXPECTED_RECORDS = 100
MAX_EXPECTED_RECORDS = 1000

_DATE_RE = re.compile(r"\d{2}/\d{2}/\d{2}")
_TIME_RE = re.compile(r"\d{2}:\d{2}")
_PRINTED_RE = re.compile(
    r"Printed\s+on\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OcrToken:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float


def _tokens_from_image(image: Image.Image) -> list[OcrToken]:
    data = pytesseract.image_to_data(
        image,
        output_type=pytesseract.Output.DICT,
        config="--psm 6",
    )
    tokens: list[OcrToken] = []
    for idx, raw_text in enumerate(data["text"]):
        text = (raw_text or "").strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][idx])
        except (TypeError, ValueError):
            confidence = -1
        tokens.append(
            OcrToken(
                text=text,
                left=int(data["left"][idx]),
                top=int(data["top"][idx]),
                width=int(data["width"][idx]),
                height=int(data["height"][idx]),
                confidence=confidence,
            )
        )
    return tokens


def _line_text(tokens: list[OcrToken], *, y_tolerance: int = 12) -> list[str]:
    lines: list[dict[str, object]] = []
    for token in sorted(tokens, key=lambda item: (item.top, item.left)):
        center_y = token.top + token.height / 2
        target = next(
            (line for line in reversed(lines) if abs(float(line["center_y"]) - center_y) <= y_tolerance),
            None,
        )
        if target is None:
            target = {"center_y": center_y, "tokens": []}
            lines.append(target)
        target_tokens = target["tokens"]
        assert isinstance(target_tokens, list)
        target_tokens.append(token)
    result = []
    for line in lines:
        line_tokens = line["tokens"]
        assert isinstance(line_tokens, list)
        result.append(" ".join(token.text for token in sorted(line_tokens, key=lambda item: item.left)))
    return result


def _normalize_name(lines: list[str]) -> str:
    raw = " ".join(lines)
    raw = re.sub(r"[^A-Za-z' ,.-]", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" ,.-")
    if "," not in raw:
        return ""
    last, first = raw.split(",", 1)
    last = last.strip(" ,.-")
    first = first.strip(" ,.-")
    if not last or not first:
        return ""
    return f"{last.title()}, {first.title()}"


def _normalize_booking_at(date_text: str, time_text: str) -> str:
    return datetime.strptime(
        f"{date_text} {time_text}", "%m/%d/%y %H:%M"
    ).strftime("%Y-%m-%d %H:%M:%S")


def _records_from_tokens(
    tokens: list[OcrToken],
    *,
    image_width: int,
    image_height: int,
    source_url: str,
) -> list[JailBookingRecord]:
    date_tokens = [
        token for token in tokens
        if 0.37 * image_width <= token.left <= 0.48 * image_width
        and _DATE_RE.fullmatch(token.text)
        and 0.20 * image_height <= token.top <= 0.92 * image_height
    ]
    time_tokens = [
        token for token in tokens
        if 0.40 * image_width <= token.left <= 0.50 * image_width
        and _TIME_RE.fullmatch(token.text)
        and 0.20 * image_height <= token.top <= 0.92 * image_height
    ]
    anchors: list[tuple[OcrToken, OcrToken]] = []
    for date_token in sorted(date_tokens, key=lambda item: item.top):
        candidates = [item for item in time_tokens if abs(item.top - date_token.top) <= 16]
        if candidates:
            anchors.append((date_token, min(candidates, key=lambda item: abs(item.top - date_token.top))))

    records: list[JailBookingRecord] = []
    for idx, (date_token, time_token) in enumerate(anchors):
        row_top = max(0, date_token.top - 18)
        row_bottom = (
            max(row_top + 35, anchors[idx + 1][0].top - 18)
            if idx + 1 < len(anchors)
            else int(image_height * 0.92)
        )
        name_tokens = [
            item for item in tokens
            if 0.045 * image_width <= item.left < 0.22 * image_width
            and row_top <= item.top < row_bottom
        ]
        person_name = _normalize_name(_line_text(name_tokens))

        age_candidates = [
            item.text for item in tokens
            if 0.21 * image_width <= item.left < 0.29 * image_width
            and abs(item.top - date_token.top) <= 22
            and re.fullmatch(r"\d{1,3}", item.text)
        ]
        jacket_candidates = [
            item.text for item in tokens
            if 0.285 * image_width <= item.left < 0.39 * image_width
            and abs(item.top - date_token.top) <= 22
            and re.fullmatch(r"\d{5,8}", item.text)
        ]
        if not person_name or not age_candidates or not jacket_candidates:
            continue

        charge_tokens = [
            item for item in tokens
            if 0.50 * image_width <= item.left < 0.75 * image_width
            and row_top <= item.top < row_bottom
        ]
        charge_lines = _line_text(charge_tokens)
        charges_summary = re.sub(r"\s+", " ", "; ".join(charge_lines)).strip(" ;")
        if not charges_summary:
            charges_summary = "Charge details available on the official Cascade County inmate roster."

        booking_at = _normalize_booking_at(date_token.text, time_token.text)
        jacket = jacket_candidates[0]
        records.append(
            JailBookingRecord(
                source_record_id=f"cascade:{jacket}:{booking_at}",
                person_name=person_name,
                age=int(age_candidates[0]),
                booking_number=jacket,
                booking_at=booking_at,
                charges_summary=charges_summary[:1000],
                source_url=source_url,
            )
        )
    return records


def _validate_printed_date(tokens: list[OcrToken]) -> datetime:
    text = " ".join(_line_text(tokens, y_tolerance=20))
    match = _PRINTED_RE.search(text)
    if not match:
        raise RuntimeError("Cascade roster printed date was not readable; refusing a potentially partial import.")
    printed = datetime.strptime(" ".join(match.groups()), "%B %d %Y").replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc).date() - printed.date()).days
    if age_days < -1 or age_days > 7:
        raise RuntimeError(f"Cascade roster is not current (printed date age: {age_days} day(s)).")
    return printed


def _extract_embed_url(page) -> str:
    more = page.locator("#oneupCommandBarOverflow")
    more.wait_for(state="visible", timeout=30_000)
    more.click()
    page.locator("#embedCommand").click()
    field = page.locator('input[value*="<iframe"], textarea')
    field.first.wait_for(state="visible", timeout=15_000)
    embed_code = unescape(field.first.input_value())
    match = re.search(r'<iframe\s+src="([^"]+)"', embed_code, re.IGNORECASE)
    if not match:
        raise RuntimeError("Cascade public viewer did not provide an embed URL.")
    embed_url = match.group(1)
    parsed = urlparse(embed_url)
    if parsed.hostname != SHAREPOINT_HOST or not parsed.path.endswith("/_layouts/15/embed.aspx"):
        raise RuntimeError("Cascade public viewer returned an unexpected embed location.")
    return embed_url


def fetch_cascade_public_viewer_bookings(
    source_url: str = COUNTY_ROSTER_PAGE,
) -> list[JailBookingRecord]:
    """Render and parse the public Cascade roster with fail-closed checks."""
    all_records: list[JailBookingRecord] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path="/usr/bin/google-chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1.5)
            page = context.new_page()
            page.goto(source_url, wait_until="domcontentloaded", timeout=60_000)
            share_links = page.locator(f'a[href*="{SHAREPOINT_HOST}"]')
            if share_links.count() < 1:
                raise RuntimeError("Cascade County page did not expose its public roster link.")
            share_url = share_links.first.get_attribute("href")
            if not share_url:
                raise RuntimeError("Cascade County public roster link was empty.")

            page.goto(share_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(10_000)
            embed_url = _extract_embed_url(page)
            page.goto(embed_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(5_000)

            page_label = page.locator('[aria-label*="Page 1 of "]').first.get_attribute("aria-label") or ""
            page_match = re.search(r"Page 1 of (\d+)", page_label)
            if not page_match:
                raise RuntimeError("Cascade public viewer page count was not available.")
            page_count = int(page_match.group(1))
            if not MIN_EXPECTED_PAGES <= page_count <= MAX_EXPECTED_PAGES:
                raise RuntimeError(f"Cascade roster page count failed validation: {page_count}.")

            viewer = page.locator('[class*="pdfViewer_"]').first
            viewer.wait_for(state="visible", timeout=30_000)
            scroll_height = float(viewer.evaluate("element => element.scrollHeight"))
            page_stride = scroll_height / page_count

            for page_number in range(1, page_count + 1):
                viewer.evaluate(
                    "(element, y) => { element.scrollTop = y; }",
                    (page_number - 1) * page_stride,
                )
                page.wait_for_timeout(1_200)
                canvas = page.locator("canvas").first
                canvas.wait_for(state="visible", timeout=30_000)
                image = Image.open(io.BytesIO(canvas.screenshot())).convert("RGB")
                tokens = _tokens_from_image(image)
                if page_number == 1:
                    _validate_printed_date(tokens)
                page_records = _records_from_tokens(
                    tokens,
                    image_width=image.width,
                    image_height=image.height,
                    source_url=source_url,
                )
                if not page_records:
                    raise RuntimeError(f"Cascade roster page {page_number} produced zero validated records.")
                logger.info(
                    "Cascade public roster page %d/%d: %d record(s)",
                    page_number,
                    page_count,
                    len(page_records),
                )
                all_records.extend(page_records)
        finally:
            browser.close()

    unique = {record.source_record_id for record in all_records}
    if len(unique) != len(all_records):
        raise RuntimeError("Cascade roster OCR produced duplicate jacket/date identifiers.")
    if not MIN_EXPECTED_RECORDS <= len(all_records) <= MAX_EXPECTED_RECORDS:
        raise RuntimeError(f"Cascade roster record count failed validation: {len(all_records)}.")
    logger.info("Cascade public roster validated: %d records", len(all_records))
    return all_records
