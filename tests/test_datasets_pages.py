import os
import sys
import tempfile
import unittest
import importlib

from services.datasets.catalog import DATASET_DEFINITIONS, get_dataset_definition
from services.datasets.refresh import refresh_all_dataset_metrics
from services.datasets.schema import ensure_dataset_metrics_schema


class DatasetMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(prefix="mb-datasets-", suffix=".db")
        os.close(fd)
        self.previous_env_db_path = os.environ.get("MB_DB_PATH")

        os.environ["MB_DB_PATH"] = self.db_path
        for name in ["app", "config", "init_db", "db"]:
            sys.modules.pop(name, None)

        self.config = importlib.import_module("config")
        self.init_db = importlib.import_module("init_db")
        self.app_module = importlib.import_module("app")

        self.app_module.app.config["TESTING"] = True
        self.init_db.migrate()

    def tearDown(self) -> None:
        if self.previous_env_db_path is None:
            os.environ.pop("MB_DB_PATH", None)
        else:
            os.environ["MB_DB_PATH"] = self.previous_env_db_path

        for name in ["app", "config", "init_db", "db"]:
            sys.modules.pop(name, None)

        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_ensure_dataset_metrics_schema_creates_table(self) -> None:
        conn = self.app_module.get_db()
        ensure_dataset_metrics_schema(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dataset_metrics'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_dataset_registry_contains_five_core_datasets(self) -> None:
        self.assertEqual(
            list(DATASET_DEFINITIONS.keys()),
            [
                "jail-bookings",
                "warrants",
                "arrests",
                "public-meetings",
                "police-calls",
            ],
        )
        self.assertEqual(get_dataset_definition("warrants").title, "Warrants")
        self.assertEqual(get_dataset_definition("arrests").records_href, "/arrests")

    def test_refresh_all_dataset_metrics_writes_rows(self) -> None:
        conn = self.app_module.get_db()
        ensure_dataset_metrics_schema(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jail_bookings'"
        ).fetchone()
        self.assertIsNotNone(row)
        refresh_all_dataset_metrics(conn)
        rows = conn.execute(
            "SELECT dataset_slug FROM dataset_metrics ORDER BY dataset_slug"
        ).fetchall()
        conn.close()
        self.assertEqual(
            [row["dataset_slug"] for row in rows],
            [
                "arrests",
                "jail-bookings",
                "police-calls",
                "public-meetings",
                "warrants",
            ],
        )
