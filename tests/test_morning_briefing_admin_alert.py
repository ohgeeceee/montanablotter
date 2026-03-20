"""Verify morning_briefing admin failure path uses alerting, not raw smtplib."""
import inspect
import unittest


class MorningBriefingAdminAlertTest(unittest.TestCase):
    def test_imports_alerting_for_admin_failures(self):
        import morning_briefing
        src = inspect.getsource(morning_briefing)
        self.assertIn(
            "from alerting import",
            src,
            "morning_briefing should import alerting for admin failure notifications",
        )


if __name__ == "__main__":
    unittest.main()
