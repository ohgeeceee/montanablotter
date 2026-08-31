"""Tests for the Playwright-backed Montana county jail-roster fetcher.

The real fetcher drives headless Chromium, which isn't available in unit tests.
These tests mock the browser layer and assert the rendered-text parsing path
delegates to the same tolerant parser as the static fetcher.
"""
from __future__ import annotations

import unittest
from unittest import mock

from services.ingestion.fetchers import playwright_mt_inmate as pwf


class PlaywrightMtInmateTests(unittest.TestCase):
    def test_carter_cards_parsed(self) -> None:
        # Simulate dmxAppConnect roster cards (name in .card-title, booked-at in footer).
        card_html = (
            '<div class="card col"><a href="inmate.php?bookingid=37405"></a>'
            '<div class="card-body"><h2 class="h5 card-title" dmx-text="FName+\' \'+LName">'
            "CHRISTOPHER KIRBY</h2></div>"
            '<div class="card-footer text-body-secondary">'
            "<span>Booked: 08/31/2026 02:21 am</span></div></div>"
        )
        fake_card = mock.MagicMock()
        fake_card.query_selector.side_effect = lambda sel: (
            mock.MagicMock(inner_text=lambda: "CHRISTOPHER KIRBY")
            if "card-title" in sel
            else (mock.MagicMock(inner_text=lambda: "Booked: 08/31/2026 02:21 am")
                  if "card-footer" in sel
                  else mock.MagicMock(get_attribute=lambda a: "inmate.php?bookingid=37405"))
        )
        fake_page = mock.MagicMock()
        fake_page.query_selector_all.return_value = [fake_card]
        fake_browser = mock.MagicMock()
        fake_browser.new_page.return_value = fake_page
        fake_pw = mock.MagicMock()
        fake_pw.chromium.launch.return_value = fake_browser

        with mock.patch.object(pwf, "_make_browser", return_value=(fake_pw, fake_browser)):
            recs = pwf.fetch_carter_bookings("https://example.com/inmate-search", county_slug="carter")

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].person_name, "Christopher Kirby")
        self.assertEqual(recs[0].booking_at, "2026-08-31 02:21:00")
        self.assertEqual(recs[0].booking_number, "37405")

    def test_rendered_text_parsed_like_static(self) -> None:
        # Simulate a rendered page body whose visible text contains inmates.
        rendered = (
            "Fields, Jameeka 08/30/2026 Inmate listed\n"
            "Hernandez, Omar 08/29/2026 Inmate listed\n"
        )
        fake_page = mock.MagicMock()
        fake_page.inner_text.return_value = rendered
        fake_browser = mock.MagicMock()
        fake_browser.new_page.return_value = fake_page
        fake_pw = mock.MagicMock()
        fake_pw.chromium.launch.return_value = fake_browser

        with mock.patch.object(pwf, "_make_browser", return_value=(fake_pw, fake_browser)):
            recs = pwf.fetch_playwright_bookings("https://example.com/roster", county_slug="prairie")

        self.assertEqual(len(recs), 2)
        self.assertIn("Fields, Jameeka", {r.person_name for r in recs})

    def test_empty_render_returns_no_records(self) -> None:
        fake_page = mock.MagicMock()
        fake_page.inner_text.return_value = "No inmates currently listed."
        fake_browser = mock.MagicMock()
        fake_browser.new_page.return_value = fake_page
        fake_pw = mock.MagicMock()
        fake_pw.chromium.launch.return_value = fake_browser

        with mock.patch.object(pwf, "_make_browser", return_value=(fake_pw, fake_browser)):
            recs = pwf.fetch_playwright_bookings("https://example.com", county_slug="prairie")

        self.assertEqual(len(recs), 0)


if __name__ == "__main__":
    unittest.main()
