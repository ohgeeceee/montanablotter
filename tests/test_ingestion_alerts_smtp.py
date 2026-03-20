"""Verify ingestion_alerts uses alerting.send_plaintext_email, not its own SMTP."""
import importlib
import unittest


class IngestionAlertsSmtpTest(unittest.TestCase):
    def test_does_not_define_own_smtp_block(self):
        """ingestion_alerts must not contain a raw smtplib.SMTP() call."""
        import inspect
        import ingestion_alerts
        src = inspect.getsource(ingestion_alerts)
        self.assertNotIn(
            "smtplib.SMTP(",
            src,
            "ingestion_alerts should delegate to alerting.send_plaintext_email, "
            "not call smtplib.SMTP() directly",
        )

    def test_imports_alerting_send(self):
        """ingestion_alerts must import send_plaintext_email from alerting."""
        import inspect
        import ingestion_alerts
        src = inspect.getsource(ingestion_alerts)
        self.assertIn(
            "from alerting import",
            src,
            "ingestion_alerts should import from alerting module",
        )


if __name__ == "__main__":
    unittest.main()
