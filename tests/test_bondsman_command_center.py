import os
import sqlite3
import tempfile
import unittest

from app import _donation_email_hash, _sync_public_user_subscription_from_donations
from bondsman_command_center import (
    _booking_probably_bondable,
    _normalize_date_of_birth,
    _normalize_person_name,
    ensure_bondsman_command_center_schema,
    run_watchlist_match_cycle,
)
from init_db import ensure_jail_booking_schema, ensure_public_engagement_schema


class BondsmanCommandCenterTests(unittest.TestCase):
    def test_normalize_person_name_collapses_punctuation(self) -> None:
        self.assertEqual(_normalize_person_name("O'Neil,  Jane-Sue"), "O NEIL JANE SUE")

    def test_normalize_date_of_birth_supports_multiple_formats(self) -> None:
        self.assertEqual(_normalize_date_of_birth("03/18/2026"), "2026-03-18")
        self.assertEqual(_normalize_date_of_birth("03-18-26"), "2026-03-18")

    def test_booking_probably_bondable_blocks_holds(self) -> None:
        self.assertTrue(_booking_probably_bondable("Criminal Mischief | Bond $500"))
        self.assertFalse(_booking_probably_bondable("DOC HOLD"))

    def test_watch_worker_creates_name_and_dob_alert(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            ensure_public_engagement_schema(conn)
            ensure_jail_booking_schema(conn)
            ensure_bondsman_command_center_schema(conn)
            conn.execute(
                """
                INSERT INTO public_users (
                    email, password_hash, display_name, is_subscribed, subscriber_plan
                ) VALUES (?, ?, ?, 1, 'pro')
                """,
                ("bondsman@example.com", "hashed", "Bondsman"),
            )
            user_id = conn.execute("SELECT id FROM public_users").fetchone()[0]
            conn.execute(
                """
                INSERT INTO jail_bookings (
                    county_slug, county_name, facility_name, person_name, date_of_birth, charges_summary, is_current
                ) VALUES ('cascade', 'Cascade', 'Cascade Detention', 'SMITH, JOHN', '1988-01-02', 'Theft', 1)
                """
            )
            conn.execute(
                """
                INSERT INTO bondsman_watchlists (
                    public_user_id, watch_name, normalized_name, date_of_birth
                ) VALUES (?, 'Smith, John', 'SMITH JOHN', '1988-01-02')
                """,
                (user_id,),
            )
            conn.commit()
            conn.close()

            result = run_watchlist_match_cycle(path)

            self.assertEqual(result["created_alerts"], 1)
            verify = sqlite3.connect(path)
            verify.row_factory = sqlite3.Row
            alert = verify.execute("SELECT * FROM bondsman_alerts").fetchone()
            verify.close()
            self.assertIsNotNone(alert)
            self.assertEqual(alert["match_confidence"], "name_and_dob")
        finally:
            os.remove(path)

    def test_sync_public_user_subscription_from_donations_activates_bondsman_plan(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            ensure_public_engagement_schema(conn)
            ensure_bondsman_command_center_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS donations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    amount_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'usd',
                    email_hash TEXT,
                    donor_name TEXT,
                    message TEXT,
                    source TEXT,
                    provider_session_id TEXT UNIQUE,
                    provider_payment_intent_id TEXT UNIQUE,
                    provider_subscription_id TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                INSERT INTO public_users (
                    email, password_hash, display_name
                ) VALUES ('agent@example.com', 'hashed', 'Agent')
                """
            )
            user_id = conn.execute("SELECT id FROM public_users").fetchone()[0]
            conn.execute(
                """
                INSERT INTO donations (
                    provider, mode, status, amount_cents, email_hash, source, provider_subscription_id
                ) VALUES (
                    'stripe', 'monthly', 'succeeded', 4900, ?, 'bondsman_account', 'sub_test_123'
                )
                """,
                (_donation_email_hash('agent@example.com'),),
            )

            _sync_public_user_subscription_from_donations(conn, user_id, 'agent@example.com')
            conn.commit()
            row = conn.execute(
                "SELECT is_subscribed, subscriber_plan, stripe_subscription_id FROM public_users WHERE id = ?",
                (user_id,),
            ).fetchone()
            conn.close()

            self.assertEqual(row["is_subscribed"], 1)
            self.assertEqual(row["subscriber_plan"], "bondsman_pro")
            self.assertEqual(row["stripe_subscription_id"], "sub_test_123")
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
