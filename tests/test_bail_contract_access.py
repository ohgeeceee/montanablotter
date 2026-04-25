import os
import sqlite3
import tempfile
import unittest

import app as app_module
import config
import init_db


class BailContractAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-bail-contract-", suffix=".db")
        os.close(fd)
        self.previous_db_path = config.DB_PATH
        self.previous_init_db_path = init_db.DB_PATH
        self.previous_app_db_path = app_module.config.DB_PATH

        config.DB_PATH = self.db_path
        init_db.DB_PATH = self.db_path
        app_module.config.DB_PATH = self.db_path
        app_module.app.config["TESTING"] = True

        bootstrap_conn = sqlite3.connect(self.db_path)
        bootstrap_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                counties TEXT DEFAULT '',
                token TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        bootstrap_conn.commit()
        bootstrap_conn.close()

        init_db.init_database()
        init_db.migrate()

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("ALTER TABLE bail_ad_orders ADD COLUMN simulator_logo_path TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bail_ad_orders ADD COLUMN simulator_target_url TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            INSERT INTO bail_ad_orders (
                business_name, contact_name, email, phone, website_url, license_number,
                county_targets, package_id, billing_cycle, amount_cents, currency, source,
                add_on_ids, notes, status, provider, provider_session_id, provider_subscription_id,
                provider_customer_id, onboarding_token
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Affordable Bail Bonds",
                "Account Owner",
                "owner@example.com",
                "406-555-0100",
                "https://affordable.example.com",
                "LIC-123",
                "Yellowstone",
                "gold_bond_bundle",
                "monthly",
                65000,
                "usd",
                "test",
                "",
                "",
                "active",
                "stripe",
                "cs_test_contract",
                "sub_test_contract",
                "cus_test_contract",
                "private-contract-token",
            ),
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        config.DB_PATH = self.previous_db_path
        init_db.DB_PATH = self.previous_init_db_path
        app_module.config.DB_PATH = self.previous_app_db_path
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_public_contract_route_is_not_available(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/advertise/bail-bonds/contract")

        self.assertEqual(response.status_code, 404)

    def test_private_contract_route_requires_valid_token(self) -> None:
        client = app_module.app.test_client()
        response = client.get("/advertise/bail-bonds/control-panel/private-contract-token/contract")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Affordable Bail Bonds Advertising Contract", html)
        self.assertIn("Agreement Summary", html)

    def test_public_advertiser_pages_do_not_expose_contract_route(self) -> None:
        client = app_module.app.test_client()

        advertise_response = client.get("/advertise/bail-bonds")
        checkout_response = client.get("/advertise/bail-bonds/checkout")

        self.assertNotIn("/advertise/bail-bonds/contract", advertise_response.get_data(as_text=True))
        self.assertNotIn("/advertise/bail-bonds/contract", checkout_response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
