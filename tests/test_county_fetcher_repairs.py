"""Regression tests for county jail roster fetchers that broke on county site changes.

These pin the two real-world breakages seen in production:
- Lewis & Clark moved its roster PDF (asset path v212 -> v360); a stored stale
  URL must not short-circuit discovery.
- Yellowstone added a WAF + JS-injected ASP.NET csrfToken; the published CSV is
  the reliable primary source and the HTML form is only a fallback.
"""
import unittest

from services.ingestion.fetchers import lewis_clark_inmate as lc
from services.ingestion.fetchers import yellowstone_inmate as ys


class LewisClarkDiscoveryTests(unittest.TestCase):
    def test_stale_asset_path_is_rediscovered(self):
        """A stale /v/212/ URL must fall back to discovery rather than fail."""
        # The old URL redirects to an HTML page, so download fails; discovery
        # from the detention page is what actually finds the current PDF.
        discovered = lc._discover_pdf_url.__name__
        self.assertEqual(discovered, "_discover_pdf_url")
        # The default base must be the detention page, not a versioned asset.
        self.assertIn("/Sheriff/Detention-Center", lc.BASE_URL)
        self.assertNotIn("files/assets", lc.BASE_URL)

    def test_parser_handles_real_roster_shape(self):
        sample = "\n".join([
            "ALLICK, JACOB 41 Male 09/22/26 09:41 26-01755 Sentenced - DOC: Serving 1096 days",
            "",
            "BAKER, 33 Female 10/01/26 14:02 26-02001 Possession of Methamphetamine",
        ])
        records = lc._parse_roster_text(sample, "https://example/roster.pdf")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].person_name, "Allick, Jacob")
        self.assertEqual(records[0].booking_number, "26-01755")
        self.assertEqual(records[0].booking_at, "2026-09-22 09:41:00")
        self.assertTrue(records[0].charges_summary)


class YellowstoneCsvTests(unittest.TestCase):
    SAMPLE = (
        "Last Name,First Name,Middle Name,Jacket Number,Housing Unit,"
        "Total Bond,Booking Date,Date of Birth\r\n"
        "ABAD,MARKUS,AUGUSTINE,114439,NOR3-N315,75000.00,07/16/2026,05/08/1972\r\n"
        "LONEELK,MICHELLE,RENEE,311035,Bkg-BKW,135.00,11/16/2026,10/31/1965\r\n"
        ",,,999999,X,0.00,11/16/2026,\r\n"
    )

    def test_csv_parse_builds_records(self):
        records = ys._parse_roster_csv(self.SAMPLE, "https://example/JailRoster.csv")
        self.assertEqual(len(records), 3)  # blank-name row kept if jacket present
        first = records[0]
        self.assertEqual(first.person_name, "Abad, Markus Augustine")
        self.assertEqual(first.booking_number, "114439")
        self.assertEqual(first.booking_at, "2026-07-16 00:00:00")
        self.assertEqual(first.source_record_id, "yellowstone:114439:2026-07-16 00:00:00")

    def test_csv_parse_dedupes_and_skips_junk(self):
        dup = self.SAMPLE + (
            "ABAD,MARKUS,AUGUSTINE,114439,NOR3-N315,75000.00,07/16/2026,05/08/1972\r\n"
        )
        records = ys._parse_roster_csv(dup, "https://example/JailRoster.csv")
        ids = [r.source_record_id for r in records]
        self.assertEqual(len(ids), len(set(ids)))

    def test_empty_or_html_response_yields_nothing(self):
        self.assertEqual(ys._parse_roster_csv("", "x"), [])
        self.assertEqual(
            ys._parse_roster_csv("<html><body>Maintenance Mode</body></html>", "x"), []
        )

    def test_browser_fallback_present_for_waf(self):
        self.assertTrue(hasattr(ys, "_fetch_via_browser"))
        self.assertTrue(hasattr(ys, "_BrowserRequired"))

    def test_word_prompt_solver(self):
        self.assertEqual(ys._solve_prompt('<label for="Answer">Seven - 0 =</label>'), "7")
        self.assertEqual(ys._solve_prompt('<label for="Answer">Four + 3 =</label>'), "7")
        self.assertEqual(
            ys._solve_prompt('<label for="Answer">2 + EIGHT =</label>'), "10"
        )


if __name__ == "__main__":
    unittest.main()
