import unittest
from unittest import mock

import blotter_auditor


class BlotterAuditorTests(unittest.TestCase):
    def test_scan_for_pii_context_does_not_store_raw_match(self) -> None:
        flags = blotter_auditor.scan_for_pii(
            "Subject provided SSN 123-45-6789 and a phone number 406-555-1212."
        )

        contexts = " ".join(flag.context for flag in flags)
        self.assertNotIn("123-45-6789", contexts)
        self.assertNotIn("406-555-1212", contexts)
        self.assertIn("89", contexts)

    def test_regex_only_audit_does_not_auto_clear_post(self) -> None:
        with mock.patch.object(blotter_auditor.config, "ANTHROPIC_API_KEY", "", create=True):
            result = blotter_auditor.audit_post(
                post_id=1,
                raw_text="Traffic stop with no PII.",
                existing_summary="Neutral public summary.",
                agency_name="Example Police Department",
                incident_date="2026-04-21",
                county="Example",
                client=None,
            )

        self.assertFalse(result.audit_passed)
        self.assertTrue(any("Claude audit skipped" in issue for issue in result.raw_issues))


if __name__ == "__main__":
    unittest.main()
