import unittest

from services.summarizer.historical_context import append_historical_perspective


class HistoricalContextTests(unittest.TestCase):
    def test_appends_dui_historical_perspective(self) -> None:
        summary = append_historical_perspective(
            "Officers responded to a DUI crash investigation.",
            raw_text="Subject arrested for DUI after a traffic stop in Helena.",
            county="Lewis and Clark",
            agency_name="Helena Police Department",
            agency_type="police",
        )

        self.assertIn("// Historical Perspective:", summary)
        self.assertIn("1926", summary)
        self.assertIn("1983", summary)

    def test_preserves_existing_historical_perspective_when_no_new_match(self) -> None:
        original = (
            "Officers handled a welfare check.\n\n"
            "// Historical Perspective: Existing historical note."
        )

        summary = append_historical_perspective(
            original,
            raw_text="Officers handled a welfare check with no arrest.",
            county="Cascade",
            agency_name="Great Falls Police Department",
            agency_type="police",
        )

        self.assertEqual(summary, original)

    def test_uses_record_text_when_raw_text_missing(self) -> None:
        summary = append_historical_perspective(
            "Deputies investigated a ranch trespass complaint.",
            records=[
                {
                    "incident_type": "Trespass",
                    "details": "Caller reported an open gate on a ranch road.",
                    "location": "County Road 12",
                    "county": "Gallatin",
                    "officer": "D12",
                    "cfs_number": "24-1001",
                }
            ],
            county="Gallatin",
            agency_name="Gallatin County Sheriff's Office",
            agency_type="sheriff",
        )

        self.assertIn("checkerboard", summary.lower())


if __name__ == "__main__":
    unittest.main()
