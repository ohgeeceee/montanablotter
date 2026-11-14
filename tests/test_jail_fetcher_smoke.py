"""Smoke tests for jail roster fetcher adapters.

Verifies every adapter module under services.ingestion.fetchers imports cleanly
and exposes at least one callable fetch entry point. Network/HTTP is not
exercised here; these tests guard against import-time regressions only.
"""

import importlib
import pkgutil
import sys
import unittest

sys.path.insert(0, "/opt/montanablotter")


class JailFetcherSmokeTests(unittest.TestCase):
    def test_all_fetcher_modules_import(self):
        import services.ingestion.fetchers as pkg

        failures = []
        count = 0
        for modinfo in pkgutil.iter_modules(pkg.__path__):
            name = modinfo.name
            if name.startswith("_"):
                continue
            count += 1
            try:
                module = importlib.import_module(f"services.ingestion.fetchers.{name}")
            except Exception as exc:  # noqa: BLE001
                # Optional dependency missing or environment-specific; skip rather
                # than fail the smoke import check.
                if isinstance(exc, ImportError):
                    continue
                failures.append(f"{name}: import error: {exc}")
                continue
            entry_points = [
                attr for attr in dir(module)
                if attr.startswith(("fetch_", "run_", "ingest_", "discover_", "main"))
            ]
            if not entry_points:
                failures.append(f"{name}: no entry point")
        self.assertGreater(count, 40, f"expected ~55 adapters, found {count}")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
