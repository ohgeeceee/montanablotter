import unittest
from unittest import mock

import services.ops.smoke_check as ingestion_smoke_check


class IngestionSmokeCheckTests(unittest.TestCase):
    @mock.patch('services.ops.smoke_check._check_crimemapping')
    @mock.patch('services.ops.smoke_check._check_email')
    @mock.patch('services.ops.smoke_check._check_missoula')
    @mock.patch('services.ops.smoke_check._check_bozeman_crime')
    @mock.patch('services.ops.smoke_check._check_bozeman_calls')
    @mock.patch('services.ops.smoke_check._check_whitefish')
    def test_run_smoke_checks_collects_results(
        self,
        whitefish,
        bozeman_calls,
        bozeman_crime,
        missoula,
        email_check,
        crimemapping,
    ) -> None:
        whitefish.return_value = ("pass", "ok", {"selected": 12})
        bozeman_calls.return_value = ("pass", "ok", {"fetched": 0})
        bozeman_crime.return_value = ("pass", "ok", {"fetched": 10})
        missoula.return_value = ("pass", "ok", {"fetched_incidents": 12})
        email_check.return_value = ("pass", "ok", {"unread_count": 0})
        crimemapping.return_value = ("pass", "ok", {"agencies": []})

        results = ingestion_smoke_check.run_smoke_checks()

        self.assertEqual(len(results), 6)
        self.assertTrue(all(result.status == "pass" for result in results))

    def test_format_results_includes_summary(self) -> None:
        results = [
            ingestion_smoke_check.SmokeCheckResult("whitefish", "pass", "ok", {}),
            ingestion_smoke_check.SmokeCheckResult("missoula", "fail", "boom", {"error": "boom"}),
        ]

        output = ingestion_smoke_check._format_results(results)

        self.assertIn("[PASS] whitefish: ok", output)
        self.assertIn("[FAIL] missoula: boom", output)
        self.assertIn("Summary: 1 passed, 1 failed", output)


if __name__ == "__main__":
    unittest.main()
