import unittest
from unittest import mock

import services.blotter.auditor as blotter_auditor


class BlotterAuditorTests(unittest.TestCase):
    def test_scan_for_pii_context_does_not_store_raw_match(self) -> None:
        flags = blotter_auditor.scan_for_pii(
            "Subject provided SSN 123-45-6789 and a phone number 406-555-1212."
        )

        contexts = " ".join(flag.context for flag in flags)
        self.assertNotIn("123-45-6789", contexts)
        self.assertNotIn("406-555-1212", contexts)
        self.assertIn("89", contexts)

    def test_free_local_audit_passes_when_no_high_pii(self) -> None:
        # By default USE_PAID_LLM is False, so the auditor runs the free local path.
        result = blotter_auditor.audit_post(
            post_id=1,
            raw_text="Traffic stop with no PII.",
            existing_summary="Neutral public summary.",
            agency_name="Example Police Department",
            incident_date="2026-04-21",
            county="Example",
            client=None,
        )

        self.assertTrue(result.audit_passed)
        self.assertTrue(result.pii_clean)
        self.assertTrue(result.tone_ok)
        self.assertEqual(result.public_summary, "Neutral public summary.")

    def test_free_local_audit_flags_high_severity_pii(self) -> None:
        result = blotter_auditor.audit_post(
            post_id=2,
            raw_text="Subject provided SSN 123-45-6789.",
            existing_summary="Public summary.",
            agency_name="Example Police Department",
            incident_date="2026-04-21",
            county="Example",
            client=None,
        )

        self.assertFalse(result.audit_passed)
        self.assertTrue(any(f.severity == "high" and f.pii_type == "ssn" for f in result.pii_flags))
        self.assertIn("HIGH-severity PII flag(s) require manual review", " ".join(result.raw_issues))


if __name__ == "__main__":
    unittest.main()
