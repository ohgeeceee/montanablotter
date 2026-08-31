"""Tests for the generic Montana county jail-roster fetcher."""
from __future__ import annotations

import unittest

from services.ingestion.fetchers.generic_mt_inmate import _extract_from_text


class GenericMtInmateTests(unittest.TestCase):
    def test_parses_last_first_with_charge(self) -> None:
        text = (
            "SMITH, JOHN 06/12/2026 Charges: Theft - Bond $500\n"
            "DOE, JANE 06/13/2026 Charges: Assault\n"
        )
        recs = _extract_from_text(text, "https://example.com/roster", "test")
        self.assertEqual(len(recs), 2)
        names = {r.person_name for r in recs}
        self.assertIn("Smith, John", names)
        self.assertIn("Doe, Jane", names)
        smith = next(r for r in recs if r.person_name == "Smith, John")
        self.assertIn("Theft", smith.charges_summary)
        self.assertEqual(smith.booking_at, "2026-06-12 00:00:00")

    def test_rejects_city_state_navigation_garbage(self) -> None:
        # Sheriff landing pages contain "City, ST" addresses with no booking
        # date or charge — these must NOT become inmates.
        text = (
            "Main Street Roundup, MT\n"
            "Contact us at the Sheriff's Office in Big Timber, Montana\n"
            "Services: employment opportunities departments airport\n"
        )
        recs = _extract_from_text(text, "https://example.com", "test")
        self.assertEqual(len(recs), 0)

    def test_rejects_name_without_date_or_charge(self) -> None:
        text = "WALKER, SAM  held at the county jail\n"
        recs = _extract_from_text(text, "https://example.com", "test")
        self.assertEqual(len(recs), 0)

    def test_drops_trailing_age_token(self) -> None:
        text = "GRAY, ANTHONY MICHAEL SCOTT AGE 06/01/2026 Charge: DUI\n"
        recs = _extract_from_text(text, "https://example.com", "test")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].person_name, "Gray, Anthony Michael Scott")
        self.assertIn("DUI", recs[0].charges_summary)


if __name__ == "__main__":
    unittest.main()
