"""Isolated tests for the humor scorer + PII redactor (no app/db needed)."""
import unittest

from services.blotter.humor import (
    DENY_INCIDENT_TYPES,
    is_eligible,
    redact_text,
    score_humor,
)


class TestHumorScoring(unittest.TestCase):
    def test_zero_score_for_denied_incident_type(self):
        # Domestic violence etc. must never score, regardless of text.
        for it in DENY_INCIDENT_TYPES:
            with self.subTest(it=it):
                self.assertEqual(score_humor("goat stuck in mailbox", "loud", it), 0.0)

    def test_eligible_animal_entry_scores(self):
        score = score_humor("Subject reported a goat stuck in a mailbox", "", "traffic")
        self.assertGreater(score, 0.0)

    def test_empty_text_is_ineligible(self):
        self.assertEqual(score_humor("", "", "traffic"), 0.0)
        self.assertFalse(is_eligible("traffic", "", ""))

    def test_short_entry_gets_bonus(self):
        short = score_humor("goose chased a man", "", "suspicious")
        long_ = score_humor("a goose was reported chasing a man down main street for several blocks", "", "suspicious")
        # both contain the pattern; short one also gets the length bonus
        self.assertGreaterEqual(short, long_)

    def test_score_capped(self):
        monster = "goat gnome clown naked inflatable trombone watermelon " * 10
        self.assertLessEqual(score_humor(monster, monster, "traffic"), 25.0)

    def test_keyword_and_pattern_both_count(self):
        # "lawn gnome" hits keyword (3) + pattern (4) + suspicious context (5)
        s = score_humor("suspicious lawn gnome reported in yard", "", "suspicious")
        self.assertGreaterEqual(s, 12.0)


class TestRedaction(unittest.TestCase):
    def test_phone_masked(self):
        out = redact_text("Call 406-555-1234 about the goose")
        self.assertNotIn("406-555-1234", out)
        self.assertIn("goose", out)

    def test_high_severity_collapses(self):
        # SSN is high severity -> [redacted]
        out = redact_text("SSN 123-45-6789 on file")
        self.assertIn("[redacted]", out)
        self.assertNotIn("123-45-6789", out)

    def test_clean_text_unchanged(self):
        txt = "A goat was stuck in a mailbox on Elm Street"
        self.assertEqual(redact_text(txt), txt)

    def test_empty_returns_empty(self):
        self.assertEqual(redact_text(""), "")


if __name__ == "__main__":
    unittest.main()
