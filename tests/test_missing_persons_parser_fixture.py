import unittest
from pathlib import Path

import services.persons.missing as missing_persons as missing_persons_module


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "missing_persons_results_2026_04_23.html"


class MissingPersonsParserFixtureTests(unittest.TestCase):
    def test_current_doj_results_fixture_parses_counts_and_cards(self) -> None:
        html = FIXTURE_PATH.read_text(encoding="utf-8")

        counts = missing_persons_module._parse_official_counts(html)
        records = missing_persons_module._parse_official_card_records(html)

        self.assertEqual(counts["total_active"], 158)
        self.assertEqual(counts["missing_less_than_year"], 42)
        self.assertEqual(counts["missing_more_than_year"], 116)
        self.assertEqual(len(records), 2)

        self.assertEqual(records[0]["source_person_id"], "45879")
        self.assertEqual(records[0]["full_name"], "Buckman, Isabel Rose")
        self.assertEqual(records[0]["investigating_agency"], "Billings Police Department")
        self.assertEqual(records[0]["is_child"], 1)

        self.assertEqual(records[1]["source_person_id"], "45896")
        self.assertEqual(records[1]["full_name"], "Kennedy, Jazmine Jo")
        self.assertEqual(records[1]["aliases"], "Kennedy, Babygirl")
        self.assertEqual(records[1]["is_indigenous"], 1)


if __name__ == "__main__":
    unittest.main()
