"""Playwright-backed Montana county jail-roster fetcher.

Some small-county sheriff sites render their inmate roster entirely in
JavaScript (dmxAppConnect, ASP.NET GridView, Cloudflare-protected pages) so a
plain ``requests`` GET returns only an app shell.  This module drives a
headless Chromium (the system ``google-chrome`` binary) to render the page,
waits for the inmate table/list to populate, then hands the rendered text to
the same tolerant parser used by the static fetcher.

Only used for counties explicitly flagged as browser-rendered.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

from services.ingestion.fetchers.generic_mt_inmate import _extract_from_text
from services.ingestion.models import JailBookingRecord

logger = logging.getLogger(__name__)

# Use the system Chrome if present; otherwise let Playwright find its own.
_CHROME = "/usr/bin/google-chrome"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

# Extra wait (seconds) after load for JS lists to populate.
_RENDER_WAIT = 6


def _make_browser():
    from playwright.sync_api import sync_playwright

    launch_kwargs: dict = {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}
    if os.path.exists(_CHROME):
        launch_kwargs["executable_path"] = _CHROME
    pw = sync_playwright().start()
    browser = pw.chromium.launch(**launch_kwargs)
    return pw, browser


def fetch_playwright_bookings(source_url: str, *, county_slug: str = "") -> list[JailBookingRecord]:
    """Render ``source_url`` in headless Chromium and parse inmate rows.

    Renders with the system Chrome, waits for JS lists to populate, and retries
    once if the first render yields nothing (some ASP.NET/JS rosters only
    populate after a short delay).  Uses the full rendered HTML so table text is
    captured even when ``inner_text`` is sparse.
    """
    last_err: Exception | None = None
    for attempt in range(2):
        pw = None
        try:
            pw, browser = _make_browser()
            page = browser.new_page(user_agent=_USER_AGENT)
            try:
                page.goto(source_url, wait_until="networkidle", timeout=45000)
            except Exception:
                # Some ASP.NET/JS rosters never hit network idle; fall back.
                page.goto(source_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(_RENDER_WAIT * 1000)
            # Visible rendered text captures client-side-populated lists
            # (ASP.NET GridView / dmxAppConnect) better than raw page.content().
            try:
                text = page.inner_text("body")
            except Exception:
                text = page.content()
            recs = _extract_from_text(text, source_url, county_slug)
            if recs:
                return recs
            last_err = None
        except Exception as exc:  # pragma: no cover
            last_err = exc
            logger.warning("Playwright fetch attempt %d failed for %s: %s", attempt + 1, source_url, exc)
        finally:
            try:
                if pw is not None:
                    pw.stop()
            except Exception:
                pass
    if last_err:
        raise last_err  # type: ignore[arg-type]
    return []


def _has_body(page) -> bool:
    try:
        page.query_selector("body")
        return True
    except Exception:
        return False


# Carter County renders its roster via dmxAppConnect: each inmate is a
# ``<div class="card col">`` with the name in ``.card-title`` (FName+' '+LName),
# the booking timestamp in ``.card-footer span`` ("Booked: MM/dd/yyyy hh:mm a"),
# and the BookingID in the card's ``<a dmx-bind:href>``.  The detail pages are
# broken server-side, so we parse the list cards directly.
_CARTER_NAME_RE = re.compile(r"^\s*([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+)+)\s*$", re.MULTILINE)
_CARTER_BOOKED_RE = re.compile(r"Booked:\s*(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s*[ap]m)", re.IGNORECASE)
_CARTER_ID_RE = re.compile(r"inmate\.php\?bookingid=(\d+)")


def fetch_carter_bookings(source_url: str, *, county_slug: str = "carter") -> list[JailBookingRecord]:
    """Render Carter County's inmate-search page and parse the roster cards."""
    last_err: Exception | None = None
    for attempt in range(2):
        pw = None
        try:
            pw, browser = _make_browser()
            page = browser.new_page(user_agent=_USER_AGENT)
            try:
                page.goto(source_url, wait_until="networkidle", timeout=45000)
            except Exception:
                page.goto(source_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(_RENDER_WAIT * 1000)
            cards = page.query_selector_all("div.card.col")
            recs: list[JailBookingRecord] = []
            for card in cards:
                try:
                    name = (card.query_selector(".card-title").inner_text() or "").strip()
                    footer = card.query_selector(".card-footer")
                    booked = footer.inner_text() if footer else ""
                    href_el = card.query_selector("a[href*='inmate.php']")
                    href = href_el.get_attribute("href") if href_el else ""
                except Exception:
                    continue
                if not name:
                    continue
                m_book = _CARTER_BOOKED_RE.search(booked or "")
                booking_at = _normalize_carter_datetime(m_book.group(1)) if m_book else None
                m_id = _CARTER_ID_RE.search(href or "")
                booking_id = m_id.group(1) if m_id else ""
                recs.append(JailBookingRecord(
                    source_record_id=f"{county_slug}-{booking_id or name}",
                    person_name=_title_case_name(name),
                    age=None,
                    booking_number=booking_id,
                    booking_at=booking_at,
                    charges_summary="",
                    source_url=source_url,
                ))
            if recs:
                return recs
            last_err = None
        except Exception as exc:  # pragma: no cover
            last_err = exc
            logger.warning("Carter Playwright fetch attempt %d failed: %s", attempt + 1, exc)
        finally:
            try:
                if pw is not None:
                    pw.stop()
            except Exception:
                pass
    if last_err:
        raise last_err  # type: ignore[arg-type]
    return []


def _title_case_name(raw: str) -> str:
    parts = re.split(r"\s+", raw.strip())
    return " ".join(p[:1].upper() + p[1:].lower() if p else p for p in parts)


def _normalize_carter_datetime(raw: str) -> str:
    # "08/31/2026 02:21 am" -> "2026-08-31 02:21:00"
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})\s*([ap]m)", raw, re.IGNORECASE)
    if not m:
        return raw
    mo, da, yr, hh, mm, ap = m.groups()
    hh = int(hh) % 12 + (12 if ap.lower() == "pm" else 0)
    return f"{yr}-{mo}-{da} {hh:02d}:{mm}:00"
